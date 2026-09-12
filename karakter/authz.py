"""Object-level authorization helpers for the **main** (`/graphql`) schema.

`api.management.authz` does this job for the management endpoint. That endpoint
authenticates by session and scopes to "any organization the caller is a member
of". This module is its counterpart for the token-authenticated main schema,
which speaks a different dialect: the caller's organization is the one named by
their JWT's `org` claim (the organization pk), resolved onto the request by
`authapp.extension.AuthAppExtension` — which already refuses to resolve a
membership the user does not hold. So `request.organization` is a *trusted*
value here, and scoping to it is the correct tenant boundary.

Several resolvers on this schema fetched by bare pk and never read `info` at
all, letting any principal mutate any tenant's objects. These helpers exist so a
guard is one line, matching the ergonomics of the management side.
"""

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from graphql import GraphQLError

# Deliberately identical to `api.management.authz.DENIED` so a denial cannot be
# distinguished from a genuine not-found and used as an existence oracle.
DENIED = "Not found, or you are not authorized to access it."


def get_user(info):
    """Return the authenticated caller, or fail closed.

    kante's `UniversalRequest.user` *raises* when no principal was set rather
    than returning None, so this is a try/except rather than a getattr default.
    """
    try:
        user = info.context.request.user
    except Exception:
        raise GraphQLError(DENIED)
    if user is None or not getattr(user, "is_authenticated", False):
        raise GraphQLError(DENIED)
    return user


def get_organization(info):
    """Return the caller's active organization, or fail closed.

    Resolved from the token's `org` claim (the organization pk), and already
    validated against the caller's memberships by `AuthAppExtension`.
    """
    try:
        organization = info.context.request.organization
    except Exception:
        raise GraphQLError(DENIED)
    if organization is None:
        raise GraphQLError(DENIED)
    return organization


# Everything a caller-supplied lookup can blow up with *before* the row is even
# looked at: a missing row, a pk that is not an int (`ValueError`), or a value
# that does not validate for the column (`ValidationError` — e.g. a non-UUID for
# a `UUIDField`). All three must read as the same uniform denial rather than a
# 500 or a Django error message.
_LOOKUP_ERRORS = (ObjectDoesNotExist, ValueError, ValidationError)


def get_or_denied(manager, *args, **lookup):
    """Fetch a single object by an arbitrary lookup, or fail closed.

    `manager` may be a manager or a queryset (so callers can pre-apply
    `.distinct()` or their own narrowing). Positional `Q` objects are passed
    through. Any lookup failure — missing row, malformed id, value that does not
    validate for the column — raises the uniform `DENIED` error so the resolver
    never surfaces a 500 or a Django error message, and the error text cannot be
    used as an existence oracle.
    """
    try:
        return manager.get(*args, **lookup)
    except _LOOKUP_ERRORS:
        raise GraphQLError(DENIED)


def get_scoped_or_denied(manager, info, *args, field="organization", **lookup):
    """Fetch a single object, narrowed to the caller's organization.

    Returns the object, or raises the uniform `DENIED` error if it does not
    exist *or* belongs to another tenant — the two cases are deliberately
    indistinguishable to the caller. Malformed ids are denied the same way.
    """
    organization = get_organization(info)
    return get_or_denied(manager, *args, **{field: organization}, **lookup)


def build_prescoped_queryset(info, queryset, field="organization"):
    """Narrow a list queryset to the caller's active organization.

    Used by the `get_queryset` classmethods of the main schema's tenant-owned
    types. `field` is the ORM path from the model to its `Organization`
    (e.g. `"organization"`, `"service__organization"`, `"hub__organization"`).

    Note: strawberry-django only runs a type's `get_queryset` when a resolver
    returns a *QuerySet* — single-object resolvers that `.get()` by pk bypass it
    entirely and must scope themselves (see `get_scoped_or_denied`).

    `filters` may legitimately arrive as an explicit `null` variable, which
    `variable_values.get("filters", {})` would hand back as `None`; hence the
    `or {}`.
    """
    filters = info.variable_values.get("filters") or {}
    if not isinstance(filters, dict) or filters.get("scope") is None:
        return queryset.filter(**{field: get_organization(info)})

    raise GraphQLError("Custom scopes are not implemented yet")


def build_user_scoped_queryset(info, queryset, field="user"):
    """Narrow a list queryset to rows owned by the calling user.

    The per-user counterpart of `build_prescoped_queryset`, for objects that
    belong to a person rather than a tenant (stashes, comments, messages).
    """
    return queryset.filter(**{field: get_user(info)})


def assert_owns_object(info, obj, field="organization"):
    """Require that ``obj`` belongs to the caller's active organization."""
    organization = get_organization(info)
    if getattr(obj, f"{field}_id", None) != organization.id:
        raise GraphQLError(DENIED)


def assert_is_self(info, user_id):
    """Require that ``user_id`` is the calling user (for per-user objects)."""
    user = get_user(info)
    if str(user_id) != str(user.id):
        raise GraphQLError(DENIED)


def resolve_own_media_store(info, media_store_id, model):
    """Resolve a `MediaStore` the caller is entitled to attach.

    Uploads are namespaced under `users/{user.id}/` by `request_media_upload`, so
    ownership is read off the key prefix — without this, an arbitrary pk let you
    attach someone else's media to your profile.

    Deliberately tolerant of *legacy* keys: namespacing only started with this
    change, so every row created before it has an unprefixed key and belongs to
    nobody in particular. Rejecting those would break editing for every existing
    profile. So the rule is "not namespaced to a **different** user" rather than
    "namespaced to me" — which still blocks the actual attack (pointing at
    another user's upload) while leaving old rows usable.
    """
    user = get_user(info)
    try:
        store = model.objects.get(pk=media_store_id)
    except model.DoesNotExist:
        raise GraphQLError(DENIED)

    key = store.key or ""
    if key.startswith("users/") and not key.startswith(f"users/{user.id}/"):
        raise GraphQLError(DENIED)
    return store

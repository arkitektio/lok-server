"""Object-level authorization helpers for management query resolvers.

The single-object query resolvers historically fetched by primary key with a bare
``Model.objects.get(id=id)`` and therefore skipped the per-user ``get_queryset``
filter that the corresponding list fields apply. That let an authenticated user
read any object across tenants (IDOR). ``get_scoped`` routes a single-object
lookup through the same type-level ``get_queryset`` so the object is only
returned when the caller is allowed to see it, and returns a not-found error
otherwise (rather than leaking existence).
"""

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Q
from graphql import GraphQLError

# Reused for every authorization denial. Deliberately identical to the not-found
# message so a caller cannot use the error text to probe whether an id exists in
# another tenant (an existence oracle).
DENIED = "Not found, or you are not authorized to access it."

# Adding a hub provisions service instances, roles, scopes and clients into a
# tenant, so it is an owner/admin operation. Ordinary members get this sentence
# (not :data:`DENIED`) — they *can* see the organization, they just have to ask
# someone who may act on it. Shared by every add-a-hub entry point so the UI can
# say the same thing everywhere.
HUB_ADMIN_REQUIRED = "Only organization admins can add a hub. Ask an admin of this organization to do it."


def get_user(info):
    """Return the authenticated caller.

    ``RequireAuthenticationExtension`` already rejects anonymous callers on every
    non-public root field, so this is a belt-and-braces read that fails closed if
    a resolver is ever reached without a principal.
    """
    request = getattr(info.context, "request", None)
    try:
        user = request.user if request is not None else None
    except Exception:
        user = None
    if user is None or not getattr(user, "is_authenticated", False):
        raise GraphQLError("Authentication required to access the management API.")
    return user


def is_owner(user, organization) -> bool:
    """Whether ``user`` owns ``organization``. False for a missing organization."""
    if organization is None:
        return False
    return organization.owner_id == user.id


def is_owner_or_admin(user, organization) -> bool:
    """Whether ``user`` owns ``organization`` or holds its ``admin`` role.

    The predicate behind :func:`assert_owner_or_admin`, exposed separately so
    resolvers that want to raise their own domain-specific message can share the
    check rather than re-deriving it. This is the bar for privileged operations
    (role grants, membership changes, deletes, minting credentials).
    """
    if organization is None:
        return False
    if is_owner(user, organization):
        return True
    return organization.memberships.filter(user=user, roles__identifier="admin").exists()


def owner_or_admin_q(user, prefix: str) -> Q:
    """Queryset analogue of :func:`is_owner_or_admin`, for ``get_queryset`` policies.

    ``prefix`` is the lookup path from the queryset's model to the Organization
    (e.g. ``"membership__organization"``). Both membership conditions live in the
    *same* ``Q`` so Django keeps them on one joined row — split across two filter
    calls they would match a user who is a member and *someone else* who holds
    ``admin``. Callers must ``.distinct()``: the admin branch joins memberships.
    """
    return Q(**{f"{prefix}__owner": user}) | Q(
        **{
            f"{prefix}__memberships__user": user,
            f"{prefix}__memberships__roles__identifier": "admin",
        }
    )


def assert_member(info, organization) -> None:
    """Require that the caller belongs to ``organization``.

    ``organization`` is ``None`` for objects that hang off a *global* (org-less)
    parent — e.g. ``ServiceInstance.organization`` is nullable for instances shared
    across tenants. Those are deliberately not editable through this endpoint: an
    org-less object belongs to no tenant, so no tenant member may claim it. Fail
    closed rather than dereferencing ``None`` (a 500) or filtering on it (which
    would match and *allow*).
    """
    if organization is None:
        raise GraphQLError(DENIED)
    user = get_user(info)
    if not organization.memberships.filter(user=user).exists():
        raise GraphQLError(DENIED)


def assert_owner(info, organization) -> None:
    """Require that the caller owns ``organization``."""
    if not is_owner(get_user(info), organization):
        raise GraphQLError(DENIED)


def assert_owner_or_admin(info, organization) -> None:
    """Require that the caller owns ``organization`` or holds its ``admin`` role.

    This is the bar for privileged operations (role grants, deletes, minting
    credentials) as opposed to ordinary membership.
    """
    if not is_owner_or_admin(get_user(info), organization):
        raise GraphQLError(DENIED)


def get_scoped(type_cls, queryset, info):
    """Return the single object in ``queryset`` visible to the caller.

    ``type_cls`` is the strawberry-django type whose ``get_queryset(qs, info)``
    encodes the tenant-scoping policy. If the type has no such method the
    queryset is used as-is. Raises if nothing visible matches.
    """
    get_queryset = getattr(type_cls, "get_queryset", None)
    if get_queryset is not None:
        queryset = get_queryset(queryset, info)

    try:
        obj = queryset.first()
    except (ValueError, ValidationError, TypeError):
        # A malformed id (``"abc"`` for an integer pk) must not be a 500.
        raise GraphQLError(DENIED)
    if obj is None:
        raise GraphQLError(DENIED)
    return obj


def get_scoped_lookup(type_cls, manager, info, **lookup):
    """:func:`get_scoped` for a ``manager.filter(**lookup)`` built here.

    Django validates lookup values when the filter is *built*, so
    ``Model.objects.filter(id="abc")`` raises ``ValueError`` before
    :func:`get_scoped` ever sees a queryset. Building the filter inside the
    guard turns that into the same not-found/denied error.
    """
    try:
        queryset = manager.filter(**lookup)
    except (ValueError, ValidationError, TypeError):
        raise GraphQLError(DENIED)
    return get_scoped(type_cls, queryset, info)


def get_or_denied(manager, **lookup):
    """``manager.get(**lookup)`` that fails closed with :data:`DENIED`.

    A bare ``Model.objects.get(id=...)`` in a resolver turns a miss into a 500 and
    a malformed id (``"abc"`` for an integer pk, a non-UUID for a ``UUIDField``)
    into a ``ValueError``/``ValidationError`` 500. Both surface as
    "Internal server error" to the caller and as noise in the logs, and the
    distinction between them is itself an existence oracle. Collapse all three
    into the same not-found/denied GraphQL error.

    Authorization is NOT implied by a successful fetch: callers still have to
    check the returned object against the caller (``assert_member`` etc.).
    """
    try:
        return manager.get(**lookup)
    except (ObjectDoesNotExist, ValueError, ValidationError, TypeError):
        raise GraphQLError(DENIED)

"""Object-level authorization helpers for management query resolvers.

The single-object query resolvers historically fetched by primary key with a bare
``Model.objects.get(id=id)`` and therefore skipped the per-user ``get_queryset``
filter that the corresponding list fields apply. That let an authenticated user
read any object across tenants (IDOR). ``get_scoped`` routes a single-object
lookup through the same type-level ``get_queryset`` so the object is only
returned when the caller is allowed to see it, and returns a not-found error
otherwise (rather than leaking existence).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Manager, Model, Q, QuerySet
from graphql import GraphQLError

from karakter import roles

if TYPE_CHECKING:
    from kante.context import User as Principal
    from kante.types import Info

    from api.management.types import ManagementOrganization
    from karakter.models import Membership, Organization

    #: An organization as a policy is handed one: the model, or a resolver's ``self``, which
    #: strawberry types as the GraphQL type though it is the model.
    OrganizationArg = Organization | ManagementOrganization | None

M = TypeVar("M", bound=Model)


class Scoped(Protocol):
    """A type that says which rows of its model the caller may see: a strawberry-django type's
    ``get_queryset(queryset, info)``, the policy its list fields apply. :func:`get_scoped` holds a
    single-object lookup to the same policy."""

    @classmethod
    def get_queryset(cls, queryset: QuerySet[Any], info: Info, /) -> QuerySet[Any]: ...


T = TypeVar("T", bound=Scoped)


def _organization(organization: OrganizationArg) -> Organization | None:
    return cast("Organization | None", organization)


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


def get_user(info: Info) -> Principal:
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
    return cast("Principal", user)


def is_owner(user: Principal, organization: OrganizationArg) -> bool:
    """Whether ``user`` owns ``organization``. False for a missing organization."""
    held = _organization(organization)
    return held is not None and held.owner_id == user.id


def is_owner_or_admin(user: Principal, organization: OrganizationArg) -> bool:
    """Whether ``user`` owns ``organization`` or holds its ``admin`` role.

    The predicate behind :func:`assert_owner_or_admin`, exposed separately so
    resolvers that want to raise their own domain-specific message can share the
    check rather than re-deriving it. This is the bar for privileged operations
    (role grants, membership changes, deletes, minting credentials). Who is an
    admin is :mod:`karakter.roles`' to say.
    """
    return roles.is_owner_or_admin(user, _organization(organization))


def owner_or_admin_q(user: Principal, prefix: str) -> Q:
    """Queryset analogue of :func:`is_owner_or_admin`, for ``get_queryset`` policies.

    ``prefix`` is the lookup path from the queryset's model to the Organization
    (e.g. ``"membership__organization"``). Callers must ``.distinct()``: the admin
    branch joins memberships. See :func:`karakter.roles.owner_or_admin_q`.
    """
    return roles.owner_or_admin_q(user, prefix)


def assert_member(info: Info, organization: OrganizationArg) -> Membership:
    """Require that the caller is a full member of ``organization``; return their membership.

    ``organization`` is ``None`` for objects that hang off a *global* (org-less)
    parent — e.g. ``ServiceInstance.organization`` is nullable for instances shared
    across tenants. Those are deliberately not editable through this endpoint: an
    org-less object belongs to no tenant, so no tenant member may claim it. Fail
    closed rather than dereferencing ``None`` (a 500) or filtering on it (which
    would match and *allow*).
    """
    held = _organization(organization)
    if held is None:
        raise GraphQLError(DENIED)
    user = get_user(info)
    # A full member: a guest of some collaborations (a scoped membership) does
    # not act for the organization.
    membership = held.memberships.filter(user=user, collaboration_grants__isnull=True).first()
    if membership is None:
        raise GraphQLError(DENIED)
    return membership


def assert_owner(info: Info, organization: OrganizationArg) -> None:
    """Require that the caller owns ``organization``."""
    if not is_owner(get_user(info), organization):
        raise GraphQLError(DENIED)


def assert_owner_or_admin(info: Info, organization: OrganizationArg) -> None:
    """Require that the caller owns ``organization`` or holds its ``admin`` role.

    This is the bar for privileged operations (role grants, deletes, minting
    credentials) as opposed to ordinary membership.
    """
    if not is_owner_or_admin(get_user(info), organization):
        raise GraphQLError(DENIED)


def get_scoped(type_cls: type[T], queryset: QuerySet[Any], info: Info) -> T:
    """Return the single object in ``queryset`` visible to the caller, as ``type_cls``.

    ``type_cls`` is the strawberry-django type whose ``get_queryset(qs, info)``
    encodes the tenant-scoping policy (:class:`Scoped`). A type without one is
    refused rather than read unscoped: an object no policy covers is never handed
    out by accident. The row is the type's at runtime, as strawberry-django
    resolves it, so a resolver returning ``type_cls`` returns this as it is.
    Raises if nothing visible matches.
    """
    get_queryset = getattr(type_cls, "get_queryset", None)
    if not callable(get_queryset):
        raise TypeError(f"{type_cls.__name__} says no get_queryset: a scoped lookup needs the policy of the type it returns")
    queryset = cast("QuerySet[Any]", get_queryset(queryset, info))

    try:
        obj = queryset.first()
    except (ValueError, ValidationError, TypeError):
        # A malformed id (``"abc"`` for an integer pk) must not be a 500.
        raise GraphQLError(DENIED)
    if obj is None:
        raise GraphQLError(DENIED)
    return cast(T, obj)


def get_scoped_lookup(type_cls: type[T], manager: Manager[Any] | QuerySet[Any], info: Info, **lookup: object) -> T:
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


def get_or_denied(manager: Manager[M] | QuerySet[M], **lookup: object) -> M:
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


def assert_member_or_guest(info: Info, organization: OrganizationArg) -> Membership:
    """Require that the caller belongs to ``organization``, as a full member or a guest; return their membership.

    For the few things a guest of some collaborations (a scoped membership,
    ``karakter.collaboration_grants``) does for themselves, like approving an app
    login: everything that acts *for* the organization stays behind
    :func:`assert_member`. A guest counts only while one of their grants is live
    (unexpired, from a granter who is still a full member); one whose grants all
    lapsed is refused like a non-member. ``organization`` ``None`` fails closed,
    as in :func:`assert_member`.
    """
    from karakter.collaboration_grants import live_grants

    held = _organization(organization)
    if held is None:
        raise GraphQLError(DENIED)
    user = get_user(info)
    membership = held.memberships.filter(user=user).first()
    if membership is None:
        raise GraphQLError(DENIED)
    if membership.is_scoped and not live_grants(membership).exists():
        raise GraphQLError(DENIED)
    return membership

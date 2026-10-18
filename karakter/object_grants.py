"""Sharing single objects with people who are not members.

An *object invite* (``Invite.shared_objects``) shares a few objects of an organization.
Whoever accepts it gets a **scoped membership**: a ``Membership`` with no roles
and one ``ObjectGrant`` per object. Their tokens then carry the grants beside the
organization (see ``authapp.token_generators``), and each service decides what a
grant lets them read.

An object is unique by its hub, its structure (unique within a hub) and its id
there, the three a smartlink names too. As a grant string, hub first::

    <hub>/<structure>:<id>[#<reach>]      3/@mikro/image:42   3/@mikro/image:42#rois

``<hub>`` is the lok hub id, and must be a hub of the sharing organization.
``reach`` is the hosting service's word for how much related data comes with the
object; ``self`` (the object and what is needed to show it) when left out. lok
stores and forwards it without interpreting it.

A grant reaches no further than whoever shared it (``granted_by``): it is live only
while they are a full member of the organization, and a token names them with each
grant, ``@mikro/image:42#rois~8``, so that a service can narrow it by what it knows
of them too. Stored grants (an invite's objects) carry no sharer: it is added when
a token is minted.

Whether a membership is scoped is not a flag: it is scoped exactly when it has
grants. Two rules keep that from failing open, and both are enforced here and by
the signals below rather than left to callers:

- a scoped membership never holds roles (adding one raises), and
- removing the last grant removes the membership, so it can never be left as a
  role-less *full* member. Making someone a full member is ``upgrade_to_full``.
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.db.models.signals import m2m_changed, post_delete
from django.dispatch import receiver
from django.utils import timezone

from karakter import models

MAX_OBJECTS_PER_INVITE = 50
# Tokens list every live grant; past this the token grows unreasonably, and a
# member with this much shared should be a member.
MAX_GRANTS_PER_MEMBERSHIP = 200
DEFAULT_REACH = "self"

HUB_RE = re.compile(r"^[0-9]{1,18}$")
STRUCTURE_RE = re.compile(r"^@[a-z0-9][a-z0-9_.-]{0,63}/[a-z0-9][a-z0-9_.-]{0,127}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
REACH_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
# Who shared a grant, as a token names them: their sub (`str(user.id)`).
SHARER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")


@dataclass(frozen=True)
class GrantRef:
    hub: str
    structure: str
    object_id: str
    reach: str = DEFAULT_REACH

    @property
    def scoped(self) -> str:
        """The grant without its hub, as a token lists it under the hub."""
        base = f"{self.structure}:{self.object_id}"
        return base if self.reach == DEFAULT_REACH else f"{base}#{self.reach}"

    def minted(self, sharer: str) -> str:
        """The grant as a token lists it: :attr:`scoped`, and ``~<sharer>``."""
        if not SHARER_RE.match(sharer):
            raise ValueError(f"Not a sharer: {sharer!r}")
        return f"{self.scoped}~{sharer}"

    def __str__(self) -> str:
        return f"{self.hub}/{self.scoped}"


_FORMAT = "'<hub>/<structure>:<id>[#<reach>]', e.g. '3/@mikro/image:42' or '3/@mikro/image:42#rois'"


def parse_grant(raw: str) -> GrantRef:
    """``3/@mikro/image:42#rois`` → ``GrantRef``. Raises ``ValidationError``."""
    if not isinstance(raw, str):
        raise ValidationError(f"An object must be given as {_FORMAT}.")
    value = raw.strip()
    hub, slash, value = value.partition("/")
    value, hashed, reach = value.partition("#")
    structure, sep, object_id = value.rpartition(":")
    if not hashed:
        reach = DEFAULT_REACH
    if (
        not slash
        or not sep
        or not HUB_RE.match(hub)
        or not STRUCTURE_RE.match(structure)
        or not OBJECT_ID_RE.match(object_id)
        or not REACH_RE.match(reach)
    ):
        raise ValidationError(f"'{raw}' is not an object. Use {_FORMAT}.")
    return GrantRef(hub=str(int(hub)), structure=structure, object_id=object_id, reach=reach)


def normalize_grants(raw: list[str] | None, organization=None) -> list[str]:
    """Validate an invite's objects and return them in canonical form, deduplicated.

    With ``organization`` each object's hub must be one of its hubs: an
    organization can only share what lives on its own hubs.
    """
    if not raw:
        raise ValidationError("An object invite must share at least one object.")
    refs: dict[tuple[str, str, str], GrantRef] = {}
    for item in raw:
        ref = parse_grant(item)
        refs[(ref.hub, ref.structure, ref.object_id)] = ref
    if len(refs) > MAX_OBJECTS_PER_INVITE:
        raise ValidationError(f"An invite can share at most {MAX_OBJECTS_PER_INVITE} objects.")
    if organization is not None:
        from fakts.models import Hub

        hubs = {ref.hub for ref in refs.values()}
        known = {str(pk) for pk in Hub.objects.filter(organization=organization, pk__in=hubs).values_list("pk", flat=True)}
        unknown = sorted(hubs - known, key=int)
        if unknown:
            raise ValidationError(f"Not a hub of this organization: {', '.join(unknown)}.")
    return [str(ref) for ref in refs.values()]


def is_scoped(membership: models.Membership) -> bool:
    return membership.object_grants.exists()


def full_member_q(prefix: str, user) -> Q:
    """Rows whose organization (reached via ``prefix``) ``user`` is a *full* member of.

    Both conditions sit in one ``Q`` so Django puts them on the same joined
    membership row. Callers must ``.distinct()``. ``prefix=""`` is for a queryset
    of organizations itself.
    """
    path = f"{prefix}__memberships" if prefix else "memberships"
    return Q(**{f"{path}__user": user, f"{path}__object_grants__isnull": True})


def is_full_member(user, organization) -> bool:
    if organization is None or user is None:
        return False
    return organization.memberships.filter(user=user, object_grants__isnull=True).exists()


def live_grants_by_hub(membership: models.Membership) -> dict[str, list[str]]:
    """The grants a token lists, keyed by hub id, oldest first: ``{"3": ["@mikro/image:42#rois~8"]}``.

    Keyed by hub so a hub is not repeated per object, and each service reads only
    its own hub's list. Each names its sharer (``~8``, their sub). Expired grants
    are left out, not deleted, and so are the grants of a sharer who is no longer a
    full member of the organization (or whose account is gone): a share reaches no
    further than whoever made it. With none left the result is ``{}``, which reaches
    nothing.
    """
    now = timezone.now()
    # Whoever may share here: the organization's full members, with a live account.
    sharers = models.Membership.objects.filter(
        organization_id=membership.organization_id, object_grants__isnull=True, user__is_active=True
    ).values("user_id")
    grants = (
        membership.object_grants.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .filter(granted_by_id__in=sharers)
        .order_by("created_at", "id")
        .values_list("hub_id", "structure", "object_id", "reach", "granted_by_id")[:MAX_GRANTS_PER_MEMBERSHIP]
    )
    by_hub: dict[str, list[str]] = {}
    for hub_id, structure, object_id, reach, sharer_id in grants:
        by_hub.setdefault(str(hub_id), []).append(GrantRef(str(hub_id), structure, object_id, reach).minted(str(sharer_id)))
    return by_hub


def grant_objects(membership: models.Membership, objects: list[str], *, invite=None, granted_by=None, expires_at=None) -> bool:
    """Add grants to a scoped (or new, role-less) membership. Returns whether anything changed.

    An object already granted keeps its reach unless the new one is not ``self``
    and differs (the newer, explicit reach wins); a plain ``self`` never narrows it.
    """
    changed = False
    if membership.roles.exists():
        raise ValidationError("This person is already a member; there is nothing to share with them.")
    existing = membership.object_grants.count()
    for raw in objects:
        ref = parse_grant(raw)
        grant, created = models.ObjectGrant.objects.get_or_create(
            membership=membership,
            hub_id=int(ref.hub),
            structure=ref.structure,
            object_id=ref.object_id,
            defaults={"reach": ref.reach, "granted_through": invite, "granted_by": granted_by, "expires_at": expires_at},
        )
        if created:
            changed = True
            existing += 1
            if existing > MAX_GRANTS_PER_MEMBERSHIP:
                raise ValidationError(f"At most {MAX_GRANTS_PER_MEMBERSHIP} objects can be shared with one person.")
        elif grant.reach != ref.reach and ref.reach != DEFAULT_REACH:
            changed = True
            grant.reach = ref.reach
            grant.granted_through = invite
            grant.granted_by = granted_by
            grant.save(update_fields=["reach", "granted_through", "granted_by"])
    return changed


# Set while a membership is being made a full member: its grants go, the
# membership stays.
_upgrading: contextvars.ContextVar[int | None] = contextvars.ContextVar("lok_upgrading_membership", default=None)


@transaction.atomic
def upgrade_to_full(membership: models.Membership) -> models.Membership:
    """Make a scoped member a full member: their grants go, roles may follow."""
    token = _upgrading.set(membership.pk)
    try:
        for grant in list(membership.object_grants.all()):
            grant.delete()
    finally:
        _upgrading.reset(token)
    # Now a full member: they may log their own devices into the tailnet.
    from ionscale.sync import schedule_resync

    schedule_resync(membership.organization_id)
    return membership


@transaction.atomic
def revoke_grant(grant: models.ObjectGrant) -> bool:
    """Remove one grant. Returns True when it was the last and the membership went with it."""
    membership_id = grant.membership_id
    grant.delete()
    return not models.Membership.objects.filter(pk=membership_id).exists()


@transaction.atomic
def revoke_invite_grants(invite: models.Invite) -> int:
    """Remove every grant an object invite handed out. Returns how many."""
    grants = list(models.ObjectGrant.objects.filter(granted_through=invite))
    for grant in grants:
        grant.delete()
    return len(grants)


def _origin_model(origin):
    return origin.model if isinstance(origin, QuerySet) else type(origin)


@receiver(post_delete, sender=models.ObjectGrant)
def _drop_membership_without_grants(sender, instance, origin=None, **kwargs):
    """A scoped membership never outlives its last grant (else it would be a full member).

    Not when the grant goes because its membership, organization or user is
    being deleted: that delete takes the membership anyway, and deleting it
    again from inside the cascade would run its side effects twice. A hub being
    deleted is the cascade that must still clean up.
    """
    if _upgrading.get() == instance.membership_id:
        return
    if _origin_model(origin) in (models.Membership, models.Organization, models.User):
        return
    membership = models.Membership.objects.filter(pk=instance.membership_id).first()
    if membership is not None and not membership.object_grants.exists() and not membership.roles.exists():
        membership.delete()


@receiver(m2m_changed, sender=models.Membership.roles.through)
def _no_roles_for_scoped_members(sender, instance, action, reverse, **kwargs):
    if action != "pre_add":
        return
    memberships = [instance] if not reverse else list(models.Membership.objects.filter(pk__in=kwargs.get("pk_set") or ()))
    for membership in memberships:
        if isinstance(membership, models.Membership) and membership.object_grants.exists():
            raise ValidationError(
                "This person only has shared objects. Make them a full member before giving them roles."
            )

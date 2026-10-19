"""Granting collaborations of a service instance to people who are not members.

A *collaboration* lives in a service (a mikro instance, say) and holds that
service's objects; lok does not know what is in it. A *collaboration invite*
(``Invite.shared_collaborations``) grants a few collaborations of an
organization's service instances. Whoever accepts it gets a **scoped
membership** (a guest): a ``Membership`` with no roles of its own and one
``CollaborationGrant`` per collaboration. Their tokens then carry the
collaborations beside the organization (``collabs``, see
``authapp.token_generators``), and each service decides what they may do there.

A collaboration is unique by its service instance and its id there. An invite
names it as an entry, with the roles that hold for the guest inside it::

    {"instance": "7", "collaboration": "12", "roles": ["editor"]}

``instance`` is the lok ``ServiceInstance`` id and must be an instance on a hub of
the inviting organization. ``roles`` are identifiers of roles that exist in the
organization; none means the guest may only view the collaboration. lok stores
and forwards the collaboration id without interpreting it.

A grant reaches no further than whoever granted it (``granted_by``): it is live
only while they are a full member of the organization, and its roles are cut to
the roles they hold there now. Stored grants (an invite's collaborations) are
not cut: that happens when a token is minted.

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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from karakter import models

MAX_COLLABORATIONS_PER_INVITE = 50
# Tokens list every live grant; past this the token grows unreasonably, and a
# guest of this many collaborations should be a member.
MAX_GRANTS_PER_MEMBERSHIP = 200

INSTANCE_RE = re.compile(r"^[0-9]{1,18}$")
COLLABORATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
# As long as a Role.identifier may be.
MAX_ROLE_LENGTH = 1000


@dataclass(frozen=True)
class CollaborationRef:
    instance: str
    collaboration: str
    roles: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.instance, self.collaboration)

    def widened(self, roles: Iterable[str]) -> CollaborationRef:
        """The same collaboration with ``roles`` added to its own."""
        return CollaborationRef(self.instance, self.collaboration, tuple(sorted(set(self.roles) | set(roles))))

    def as_entry(self) -> dict:
        """The collaboration as an invite stores it."""
        return {"instance": self.instance, "collaboration": self.collaboration, "roles": list(self.roles)}

    def __str__(self) -> str:
        return f"{self.instance}/{self.collaboration}"


_FORMAT = "{instance, collaboration, roles}, e.g. {instance: 7, collaboration: '12', roles: ['editor']}"


def parse_collaboration(raw) -> CollaborationRef:
    """``{"instance": "7", "collaboration": "12", "roles": ["editor"]}`` → ``CollaborationRef``. Raises ``ValidationError``.

    Only the form is checked here; whether the instance and the roles belong to
    the organization is for ``normalize_collaborations`` and ``grant_collaborations``.
    """
    if not isinstance(raw, Mapping):
        raise ValidationError(f"A collaboration must be given as {_FORMAT}.")
    instance = raw.get("instance")
    collaboration = raw.get("collaboration")
    roles = raw.get("roles") or []
    instance = str(instance).strip() if isinstance(instance, (str, int)) and not isinstance(instance, bool) else ""
    collaboration = collaboration.strip() if isinstance(collaboration, str) else ""
    if not INSTANCE_RE.match(instance):
        raise ValidationError(f"'{raw.get('instance')}' is not a service instance id.")
    if not COLLABORATION_RE.match(collaboration):
        raise ValidationError(f"'{raw.get('collaboration')}' is not a collaboration id. Use letters, digits, '_', '.' and '-' (at most 200).")
    if isinstance(roles, str) or not isinstance(roles, Iterable):
        raise ValidationError("A collaboration's roles must be a list of role identifiers.")
    cleaned = set()
    for role in roles:
        if not isinstance(role, str) or not role.strip() or len(role.strip()) > MAX_ROLE_LENGTH:
            raise ValidationError(f"'{role}' is not a role identifier.")
        cleaned.add(role.strip())
    return CollaborationRef(instance=str(int(instance)), collaboration=collaboration, roles=tuple(sorted(cleaned)))


def _merge(raw: Iterable) -> dict[tuple[str, str], CollaborationRef]:
    """Parse entries; the same collaboration given twice holds the roles of both."""
    refs: dict[tuple[str, str], CollaborationRef] = {}
    for item in raw:
        ref = parse_collaboration(item)
        refs[ref.key] = refs[ref.key].widened(ref.roles) if ref.key in refs else ref
    return refs


def _check_instances(instances: set[str], organization) -> None:
    from fakts.models import ServiceInstance

    known = {
        str(pk)
        for pk in ServiceInstance.objects.filter(hub__organization=organization, pk__in=[int(i) for i in instances]).values_list("pk", flat=True)
    }
    unknown = sorted(instances - known, key=int)
    if unknown:
        raise ValidationError(f"Not a service instance of this organization: {', '.join(unknown)}.")


def normalize_collaborations(raw: list | None, organization=None) -> list[dict]:
    """Validate an invite's collaborations and return them in canonical form, deduplicated.

    With ``organization`` each collaboration's instance must run on one of its
    hubs (an organization can only grant what lives on its own hubs), and each
    role must be one of its roles.
    """
    if not raw:
        raise ValidationError("A collaboration invite must grant at least one collaboration.")
    refs = _merge(raw)
    if len(refs) > MAX_COLLABORATIONS_PER_INVITE:
        raise ValidationError(f"An invite can grant at most {MAX_COLLABORATIONS_PER_INVITE} collaborations.")
    if organization is not None:
        _check_instances({ref.instance for ref in refs.values()}, organization)
        roles = {role for ref in refs.values() for role in ref.roles}
        known = set(models.Role.objects.filter(organization=organization, identifier__in=roles).values_list("identifier", flat=True))
        unknown = sorted(roles - known)
        if unknown:
            raise ValidationError(f"Not a role of this organization: {', '.join(unknown)}.")
    return [ref.as_entry() for ref in refs.values()]


def is_scoped(membership: models.Membership) -> bool:
    return membership.collaboration_grants.exists()


def full_member_q(prefix: str, user) -> Q:
    """Rows whose organization (reached via ``prefix``) ``user`` is a *full* member of.

    Both conditions sit in one ``Q`` so Django puts them on the same joined
    membership row. Callers must ``.distinct()``. ``prefix=""`` is for a queryset
    of organizations itself.
    """
    path = f"{prefix}__memberships" if prefix else "memberships"
    return Q(**{f"{path}__user": user, f"{path}__collaboration_grants__isnull": True})


def is_full_member(user, organization) -> bool:
    if organization is None or user is None:
        return False
    return organization.memberships.filter(user=user, collaboration_grants__isnull=True).exists()


def live_grants(membership: models.Membership) -> QuerySet:
    """The grants of ``membership`` that hold now: unexpired, from a granter who is still a full member.

    A granter who is no longer a full member of the organization (or whose
    account is gone or inactive) takes their grants along; expired grants are
    left in place, not deleted. What a token lists (``live_collaborations``) and
    what reaches the mesh (``mesh_hub_ids``) both start from here.
    """
    granters = models.Membership.objects.filter(
        organization_id=membership.organization_id, collaboration_grants__isnull=True, user__is_active=True
    ).values("user_id")
    return (
        models.CollaborationGrant.objects.filter(membership_id=membership.pk)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .filter(granted_by_id__in=granters)
    )


def mesh_hub_ids(membership: models.Membership) -> set[int]:
    """The hubs a guest's apps may reach on the mesh: those running an instance of a live grant that allows it.

    Empty for a guest whose grants allow no mesh (or hold no longer), and for a
    full member, who has no grants: callers tell the two apart with ``is_scoped``.
    """
    return set(live_grants(membership).filter(mesh=True, instance__hub__isnull=False).values_list("instance__hub_id", flat=True))


def live_collaborations(membership: models.Membership, audiences: Iterable[str]) -> dict[str, dict[str, list[str]]]:
    """The collaborations a token lists (``collabs``), by instance id and collaboration id, with the roles held there.

    ``{"7": {"12": ["editor"], "9": []}}``. Only the instances in ``audiences``
    (the instance ids the token is minted for) are listed: a service reads its
    own. Expired grants are left out, not deleted, and so are the grants of a
    granter who is no longer a full member of the organization (or whose account
    is gone). The roles of each are cut to the roles its granter holds in the
    organization now, so a guest never holds more than whoever let them in; a
    collaboration left with none is still listed (view only). With nothing left
    the result is ``{}``, which grants nothing.
    """
    instance_ids = sorted({int(a) for a in (str(a) for a in audiences) if INSTANCE_RE.match(a)})
    if not instance_ids:
        return {}
    grants = list(
        live_grants(membership)
        .filter(instance_id__in=instance_ids)
        .order_by("created_at", "id")
        .values_list("instance_id", "collaboration_id", "roles", "granted_by_id")[:MAX_GRANTS_PER_MEMBERSHIP]
    )
    held: dict[int, set[str]] = {}
    for user_id, identifier in models.Membership.objects.filter(
        organization_id=membership.organization_id, user_id__in={g[3] for g in grants}
    ).values_list("user_id", "roles__identifier"):
        held.setdefault(user_id, set())
        if identifier:
            held[user_id].add(identifier)
    collaborations: dict[str, dict[str, list[str]]] = {}
    for instance_id, collaboration_id, roles, granted_by_id in sorted(grants, key=lambda g: (str(g[0]), g[1])):
        collaborations.setdefault(str(instance_id), {})[collaboration_id] = sorted(set(roles or []) & held.get(granted_by_id, set()))
    return collaborations


def grant_collaborations(
    membership: models.Membership, entries: list, *, invite=None, granted_by=None, expires_at=None, mesh: bool = False
) -> bool:
    """Add grants to a scoped (or new, role-less) membership. Returns whether anything changed.

    A collaboration already granted keeps its roles and gains the new ones (the
    union), and keeps mesh access once it was given (``mesh`` is or-ed in); it
    changes, and takes the new granter and invite, only when that adds a role or
    the mesh.
    """
    if membership.roles.exists():
        raise ValidationError("This person is already a member; there is nothing to grant them.")
    refs = _merge(entries)
    if not refs:
        raise ValidationError("There is no collaboration to grant.")
    # Checked again here, not only when the invite was made: an instance may have
    # moved or gone since, and callers other than an invite pass entries too.
    _check_instances({ref.instance for ref in refs.values()}, membership.organization)
    changed = False
    existing = membership.collaboration_grants.count()
    for ref in refs.values():
        grant, created = models.CollaborationGrant.objects.get_or_create(
            membership=membership,
            instance_id=int(ref.instance),
            collaboration_id=ref.collaboration,
            defaults={
                "roles": list(ref.roles),
                "granted_through": invite,
                "granted_by": granted_by,
                "expires_at": expires_at,
                "mesh": mesh,
            },
        )
        if created:
            changed = True
            existing += 1
            if existing > MAX_GRANTS_PER_MEMBERSHIP:
                raise ValidationError(f"At most {MAX_GRANTS_PER_MEMBERSHIP} collaborations can be granted to one person.")
            continue
        roles = sorted(set(grant.roles or []) | set(ref.roles))
        if roles != sorted(grant.roles or []) or (mesh and not grant.mesh):
            changed = True
            grant.roles = roles
            grant.mesh = grant.mesh or mesh
            grant.granted_through = invite
            grant.granted_by = granted_by
            grant.save(update_fields=["roles", "mesh", "granted_through", "granted_by"])
    return changed


# Set while a membership is being made a full member: its grants go, the
# membership stays.
_upgrading: contextvars.ContextVar[int | None] = contextvars.ContextVar("lok_upgrading_membership", default=None)


@transaction.atomic
def upgrade_to_full(membership: models.Membership) -> models.Membership:
    """Make a guest a full member: their grants go, roles may follow."""
    token = _upgrading.set(membership.pk)
    try:
        for grant in list(membership.collaboration_grants.all()):
            grant.delete()
    finally:
        _upgrading.reset(token)
    # Now a full member: they may log their own devices into the tailnet.
    from ionscale.sync import schedule_resync

    schedule_resync(membership.organization_id)
    return membership


@transaction.atomic
def revoke_grant(grant: models.CollaborationGrant) -> bool:
    """Remove one grant. Returns True when it was the last and the membership went with it."""
    membership_id = grant.membership_id
    grant.delete()
    return not models.Membership.objects.filter(pk=membership_id).exists()


@transaction.atomic
def revoke_invite_grants(invite: models.Invite) -> int:
    """Remove every grant a collaboration invite handed out. Returns how many."""
    grants = list(models.CollaborationGrant.objects.filter(granted_through=invite))
    for grant in grants:
        grant.delete()
    return len(grants)


def _origin_model(origin):
    return origin.model if isinstance(origin, QuerySet) else type(origin)


@receiver(post_delete, sender=models.CollaborationGrant)
def _drop_membership_without_grants(sender, instance, origin=None, **kwargs):
    """A scoped membership never outlives its last grant (else it would be a full member).

    Not when the grant goes because its membership, organization or user is
    being deleted: that delete takes the membership anyway, and deleting it
    again from inside the cascade would run its side effects twice. A service
    instance (or its hub) being deleted is the cascade that must still clean up.
    """
    if _upgrading.get() == instance.membership_id:
        return
    if _origin_model(origin) in (models.Membership, models.Organization, models.User):
        return
    membership = models.Membership.objects.filter(pk=instance.membership_id).first()
    if membership is not None and not membership.collaboration_grants.exists() and not membership.roles.exists():
        membership.delete()


@receiver(post_save, sender=models.CollaborationGrant)
@receiver(post_delete, sender=models.CollaborationGrant)
def _reapply_acl_on_grant_change(sender, instance, origin=None, **kwargs):
    """A guest's app sidecar reaches the hubs of their mesh grants (``ionscale.acl``): rewrite it when they change.

    A grant that merely expires changes nothing here; ``reconcile_meshes --sidecars``
    rewrites the policy and cuts it off. When the membership itself goes, its
    enrollments' deletes rewrite the policy anyway.
    """
    from ionscale.acl import schedule_acl_apply

    organization_id = models.Membership.objects.filter(pk=instance.membership_id).values_list("organization_id", flat=True).first()
    if organization_id is not None:
        schedule_acl_apply(organization_id)


@receiver(m2m_changed, sender=models.Membership.roles.through)
def _no_roles_for_scoped_members(sender, instance, action, reverse, **kwargs):
    if action != "pre_add":
        return
    memberships = [instance] if not reverse else list(models.Membership.objects.filter(pk__in=kwargs.get("pk_set") or ()))
    for membership in memberships:
        if isinstance(membership, models.Membership) and membership.collaboration_grants.exists():
            raise ValidationError(
                "This person is only a guest of some collaborations. Make them a full member before giving them roles."
            )

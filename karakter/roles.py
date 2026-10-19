"""Who is an organization's admin: the one place that says it.

An admin holds the organization's ``admin`` role (:data:`ADMIN`). Asking that through the roles
relation joins the role table into every check, so each membership caches the answer
(``Membership.is_admin``). The cache is never set by hand: :func:`sync_admin` recomputes it from
the roles, and the receivers below call it on every write that can change it:

- a membership's roles changing, from either side (``membership.roles.add``, ``role.memberships.add``);
- a role renamed to or from ``admin``;
- a role deleted, whose rows go without ``m2m_changed``.

A write that bypasses signals (``QuerySet.update`` of a role's identifier, a bulk write to the
through table) calls :func:`sync_admin` itself. Every build resyncs all of them (the
``syncadmins`` setup job), so a release that did not keep the cache, running in a rollback, leaves
nothing stale behind.

Policies ask through :func:`owner_or_admin_q` (querysets) and :func:`is_owner_or_admin`
(objects), never through a role lookup of their own.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from django.db.models import Exists, F, OuterRef, Q
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from karakter import models

if TYPE_CHECKING:
    # Whoever a policy is asked about: the request's authenticated principal, as kante types it.
    from kante.context import User as Principal

#: The identifier of the role that makes a member an admin of their organization.
ADMIN = "admin"


def _path(prefix: str, field: str) -> str:
    return f"{prefix}__{field}" if prefix else field


def admin_membership_q(user: Principal, memberships: str = "") -> Q:
    """Rows reached through the memberships at ``memberships`` (``""`` for a membership queryset
    itself) where ``user`` is an admin. Both conditions sit in one ``Q``, so Django matches them
    on one joined membership row; a queryset that joins memberships needs ``.distinct()``."""
    return Q(**{_path(memberships, "user"): user, _path(memberships, "is_admin"): True})


def owner_or_admin_q(user: Principal, organization: str) -> Q:
    """Rows whose organization (at the lookup path ``organization``, e.g. ``"created_for"``)
    ``user`` owns or administers. Callers must ``.distinct()``: the admin branch joins memberships."""
    return Q(**{f"{organization}__owner": user}) | admin_membership_q(user, f"{organization}__memberships")


def is_owner_or_admin(user: Principal, organization: models.Organization | None) -> bool:
    """Whether ``user`` owns ``organization`` or administers it. False for a missing organization."""
    if organization is None:
        return False
    if organization.owner_id == user.id:
        return True
    return organization.memberships.filter(admin_membership_q(user)).exists()


def sync_admin(membership_ids: Iterable[int]) -> None:
    """Recompute ``is_admin`` of these memberships from their roles."""
    ids = list(membership_ids)
    if not ids:
        return
    holds = models.Membership.roles.through.objects.filter(membership_id=OuterRef("pk"), role__identifier=ADMIN)
    models.Membership.objects.filter(pk__in=ids).update(is_admin=Exists(holds))


def sync_all() -> int:
    """Recompute ``is_admin`` of every membership; the number whose flag was wrong."""
    holds = models.Membership.roles.through.objects.filter(membership_id=OuterRef("pk"), role__identifier=ADMIN)
    stale = models.Membership.objects.annotate(holds=Exists(holds)).exclude(is_admin=F("holds"))
    ids = list(stale.values_list("pk", flat=True))
    sync_admin(ids)
    return len(ids)


# --- keeping it in sync -------------------------------------------------------------------

#: What a role's ``pre_`` signal saw, for its ``post_`` half: its identifier before a save, and
#: the memberships it held before its rows went (a clear or a delete).
_identifier_before: WeakKeyDictionary[models.Role, str | None] = WeakKeyDictionary()
_held_before: WeakKeyDictionary[models.Role, list[int]] = WeakKeyDictionary()


def _memberships_of(role: models.Role) -> list[int]:
    return list(role.memberships.values_list("pk", flat=True))


@receiver(m2m_changed, sender=models.Membership.roles.through)
def _roles_changed(sender: Any, instance: models.Membership | models.Role, action: str, reverse: bool, pk_set: set[int] | None, **kwargs: Any) -> None:
    if isinstance(instance, models.Role):
        # From the role's side: `role.memberships.add(...)`, `.remove(...)`, `.clear()`.
        if action == "pre_clear":
            _held_before[instance] = _memberships_of(instance)
        elif action == "post_clear":
            sync_admin(_held_before.pop(instance, []))
        elif action in ("post_add", "post_remove"):
            sync_admin(pk_set or ())
        return
    if action in ("post_add", "post_remove", "post_clear"):
        sync_admin([instance.pk])
        # The instance in hand says it too, for whoever reads it next.
        instance.is_admin = models.Membership.objects.values_list("is_admin", flat=True).get(pk=instance.pk)


@receiver(pre_save, sender=models.Role)
def _role_renaming(sender: Any, instance: models.Role, **kwargs: Any) -> None:
    if instance.pk is not None:
        _identifier_before[instance] = models.Role.objects.filter(pk=instance.pk).values_list("identifier", flat=True).first()


@receiver(post_save, sender=models.Role)
def _role_renamed(sender: Any, instance: models.Role, **kwargs: Any) -> None:
    if (_identifier_before.pop(instance, None) == ADMIN) != (instance.identifier == ADMIN):
        sync_admin(_memberships_of(instance))


@receiver(pre_delete, sender=models.Role)
def _role_deleting(sender: Any, instance: models.Role, **kwargs: Any) -> None:
    if instance.identifier == ADMIN:
        _held_before[instance] = _memberships_of(instance)


@receiver(post_delete, sender=models.Role)
def _role_deleted(sender: Any, instance: models.Role, **kwargs: Any) -> None:
    sync_admin(_held_before.pop(instance, []))


__all__ = ["ADMIN", "admin_membership_q", "is_owner_or_admin", "owner_or_admin_q", "sync_admin", "sync_all"]

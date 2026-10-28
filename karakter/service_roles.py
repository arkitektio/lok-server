"""The roles lok hands out by itself.

A service declares one role that lets a member do everything in it (``is_admin_role``,
``mikro_admin``) and may declare one every member holds (``is_default_role``,
``mikro_editor``). Lok keeps them where they belong:

- whoever administers the organization (its owner, and every member holding ``admin``) holds
  each service's admin role;
- every full member holds each service's default role, unless an admin took it from them;
- a guest (a membership with collaboration grants) holds none: what a guest may do is what
  their collaborations hold.

What lok gave by itself is remembered on the membership (``automatic_roles``), so that it takes
back only that: an admin role an admin handed to someone by hand stays when the organization's
admins change. A default role an admin took away is remembered too (``withheld_roles``), and
not given back until an admin gives it again.

The memberships are synced when their roles change, when they are made (once the transaction
that made them is committed, so a collaboration invite's grants are there by then), when an
organization changes hands, and when a service registers its roles (``sync_organization``).
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from karakter import models
from karakter.roles import ADMIN

#: Set while lok changes roles itself: those changes are not an admin taking a role away.
_syncing: contextvars.ContextVar[bool] = contextvars.ContextVar("service_roles_syncing", default=False)


@contextmanager
def _by_lok() -> Iterator[None]:
    token = _syncing.set(True)
    try:
        yield
    finally:
        _syncing.reset(token)


def _sync(membership: models.Membership) -> None:
    organization = membership.organization
    service_roles = models.Role.objects.filter(Q(is_admin_role=True) | Q(is_default_role=True), organization=organization)
    admin_roles = {role.pk for role in service_roles if role.is_admin_role}
    default_roles = {role.pk for role in service_roles if role.is_default_role}
    held = set(membership.roles.values_list("pk", flat=True))
    automatic = set(membership.automatic_roles.values_list("pk", flat=True))
    withheld = set(membership.withheld_roles.values_list("pk", flat=True))

    administers = membership.user_id == organization.owner_id or membership.roles.filter(identifier=ADMIN).exists()
    wanted = (admin_roles if administers else set()) | (default_roles - withheld)
    give = wanted - held
    take = (automatic & held) - wanted

    if give:
        membership.roles.add(*give)
        membership.automatic_roles.add(*give)
    if take:
        membership.roles.remove(*take)
    if take or automatic - held:
        membership.automatic_roles.remove(*(take | (automatic - held)))


def sync_service_roles(membership_ids: Iterable[int]) -> None:
    """Give and take the service roles lok hands out by itself, for these memberships."""
    ids = list(membership_ids)
    if not ids or _syncing.get():
        return
    with _by_lok():
        # A guest holds no roles (karakter.collaboration_grants refuses them).
        for membership in models.Membership.objects.filter(pk__in=ids, collaboration_grants__isnull=True).select_related("organization").distinct():
            _sync(membership)


def sync_organization(organization: models.Organization) -> None:
    """Sync every membership of ``organization``: its owner, its admins or its services changed."""
    sync_service_roles(organization.memberships.values_list("pk", flat=True))


# --- keeping it in sync -------------------------------------------------------------------

#: The default roles a membership held before its roles were cleared, for the ``post_clear``.
_held_before: dict[int, set[int]] = {}


def _default_roles(pks: Iterable[int]) -> set[int]:
    return set(models.Role.objects.filter(pk__in=list(pks), is_default_role=True).values_list("pk", flat=True))


def _taken_by_an_admin(membership_ids: Iterable[int], role_ids: set[int]) -> None:
    """An admin took these roles: a default one is withheld from now on, none is lok's any more."""
    defaults = _default_roles(role_ids)
    for membership in models.Membership.objects.filter(pk__in=list(membership_ids)):
        membership.automatic_roles.remove(*role_ids)
        if defaults:
            membership.withheld_roles.add(*defaults)


def _given_by_an_admin(membership_ids: Iterable[int], role_ids: set[int]) -> None:
    """An admin gave these roles: a withheld default one is not withheld any more."""
    for membership in models.Membership.objects.filter(pk__in=list(membership_ids)):
        membership.withheld_roles.remove(*role_ids)


@receiver(m2m_changed, sender=models.Membership.roles.through)
def _roles_changed(sender: Any, instance: models.Membership | models.Role, action: str, reverse: bool, pk_set: set[int] | None, **kwargs: Any) -> None:
    if _syncing.get():
        return
    if isinstance(instance, models.Role):
        # From the role's side: `role.memberships.add(...)`, `.remove(...)`.
        memberships, roles = set(pk_set or ()), {instance.pk}
        if action == "post_remove":
            _taken_by_an_admin(memberships, roles)
        elif action == "post_add":
            _given_by_an_admin(memberships, roles)
        if action in ("post_add", "post_remove"):
            sync_service_roles(memberships)
        return
    if action == "pre_clear":
        _held_before[instance.pk] = _default_roles(instance.roles.values_list("pk", flat=True))
    elif action == "post_clear":
        _taken_by_an_admin([instance.pk], _held_before.pop(instance.pk, set()))
    elif action == "post_remove":
        _taken_by_an_admin([instance.pk], set(pk_set or ()))
    elif action == "post_add":
        _given_by_an_admin([instance.pk], set(pk_set or ()))
    if action in ("post_add", "post_remove", "post_clear"):
        sync_service_roles([instance.pk])


@receiver(post_save, sender=models.Membership)
def _membership_made(sender: Any, instance: models.Membership, created: bool, **kwargs: Any) -> None:
    if created:
        pk = instance.pk
        transaction.on_commit(lambda: sync_service_roles([pk]))


__all__ = ["sync_organization", "sync_service_roles"]

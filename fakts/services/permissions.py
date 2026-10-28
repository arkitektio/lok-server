"""The scopes and roles a service instance registers, as its hub's manifest says them.

What the instance says now is what is known: a description it rewrote is rewritten here, a flag
(``is_all_scope``, ``is_admin_role``, ``is_default_role``) it set or dropped is set or dropped,
and a scope or a role it stopped declaring stops being its. One no instance declares any more
goes, when a service made it — never lok's own (``admin``, ``guest``, the OIDC scopes) — and a
scope only once no app holds it and no mandate pins it.

Then the organization's memberships are synced (``karakter.service_roles``): its owner and
admins hold each admin role, every full member each default one.
"""

from __future__ import annotations

import logging

from fakts import base_models, models
from karakter import models as karakter_models
from karakter.service_roles import sync_organization

logger = logging.getLogger(__name__)


def _scope_is_held(scope: karakter_models.Scope) -> bool:
    if scope.clients.exists():
        return True
    return models.Mandate.objects.filter(organization=scope.organization, subject_manifest__scopes__contains=[scope.identifier]).exists()


def register_permissions(instance: models.ServiceInstance, manifest: base_models.ServiceManifest) -> None:
    """Make the instance's organization know its scopes and roles as ``manifest`` says them."""
    organization = instance.organization
    roles = manifest.roles or []
    scopes = manifest.scopes or []

    for declared_role in roles:
        role, _ = karakter_models.Role.objects.update_or_create(
            organization=organization,
            identifier=declared_role.key,
            defaults={
                "description": declared_role.description or "",
                "is_admin_role": declared_role.is_admin_role,
                "is_default_role": declared_role.is_default_role,
            },
            create_defaults={
                "description": declared_role.description or "",
                "is_admin_role": declared_role.is_admin_role,
                "is_default_role": declared_role.is_default_role,
                "creating_instance": instance,
            },
        )
        role.used_by.add(instance)

    for declared_scope in scopes:
        scope, _ = karakter_models.Scope.objects.update_or_create(
            organization=organization,
            identifier=declared_scope.key,
            defaults={"description": declared_scope.description or "", "is_all_scope": declared_scope.is_all_scope},
            create_defaults={"description": declared_scope.description or "", "is_all_scope": declared_scope.is_all_scope, "creating_instance": instance},
        )
        scope.used_by.add(instance)

    for role in karakter_models.Role.objects.filter(organization=organization, used_by=instance).exclude(identifier__in=[r.key for r in roles]):
        role.used_by.remove(instance)
        if role.creating_instance_id is not None and not role.is_builtin and not role.used_by.exists():
            logger.info("Removing role %s: no service of %s declares it any more", role.identifier, organization)
            role.delete()

    for scope in karakter_models.Scope.objects.filter(organization=organization, used_by=instance).exclude(identifier__in=[s.key for s in scopes]):
        scope.used_by.remove(instance)
        if scope.creating_instance_id is not None and not scope.is_builtin and not scope.used_by.exists() and not _scope_is_held(scope):
            logger.info("Removing scope %s: no service of %s declares it any more", scope.identifier, organization)
            scope.delete()

    sync_organization(organization)


__all__ = ["register_permissions"]

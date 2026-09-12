"""Hub lifecycle: building hubs from manifests/partners.

Includes the partner pre-authorization webhook and the auto-configuration of
kommunity partners for an organization.
"""

import logging
import secrets

import requests
from django.db import transaction

from fakts import models
from fakts.base_models import HubManifest
from fakts.services import aliases
from fakts.services.tokens import create_api_token  # noqa: F401  (kept for shim parity)
from ionscale.repo import get_ionscale_repo
from ionscale.manager import get_org_mesh
from karakter import models as karakter_models

logger = logging.getLogger(__name__)


class PartnerPreAuthorizationError(Exception):
    """Raised when a partner pre-authorization hook rejects a hub."""


def run_partner_pre_authorize_hook(
    partner: models.KommunityPartner,
    organization: karakter_models.Organization,
    hub: models.Hub,
    hub_config: dict | None,
    license_signature: str | None = None,
) -> None:
    """Call an optional partner pre-authorization hook and require an explicit OK response."""
    if not partner.pre_authorize_hook:
        return

    headers = {
        "Content-Type": "application/json",
    }
    if partner.pre_authorize_token:
        headers["Authorization"] = f"Bearer {partner.pre_authorize_token}"

    payload = {
        "partner": {
            "id": str(partner.pk),
            "identifier": partner.identifier,
            "name": partner.name,
        },
        "organization": {
            "id": str(organization.pk),
            "slug": organization.slug,
            "name": organization.name,
        },
        "hub": {
            "id": str(hub.pk),
            "identifier": hub.identifier,
            "name": hub.name,
            "token": hub.token,
        },
        "hub_config": hub_config,
    }
    if license_signature:
        payload["license_signature"] = license_signature

    try:
        response = requests.post(
            partner.pre_authorize_hook,
            json=payload,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PartnerPreAuthorizationError(
            f"Partner approval failed for '{partner.name}'. The approval hook could not be reached."
        ) from exc

    approval_message = None
    try:
        response_data = response.json()
    except ValueError:
        response_text = response.text.strip().lower()
        if response_text == "ok":
            return
        approval_message = response.text.strip() or None
    else:
        if isinstance(response_data, dict):
            if response_data.get("ok") is True:
                return

            for key in ("status", "answer", "result"):
                value = response_data.get(key)
                if isinstance(value, str) and value.strip().lower() == "ok":
                    return

            approval_message = response_data.get("message") or response_data.get("error") or response_data.get("detail")
        elif isinstance(response_data, str) and response_data.strip().lower() == "ok":
            return
        elif isinstance(response_data, str):
            approval_message = response_data.strip() or None

    raise PartnerPreAuthorizationError(
        approval_message or f"Partner approval failed for '{partner.name}'. The approval hook did not return ok."
    )


@transaction.atomic
def create_hub_from_manifest(
    manifest: HubManifest,
    organization: karakter_models.Organization,
) -> models.Hub:
    """Create or update a hub (and its instances/roles/scopes/aliases) from a manifest.

    The hub token is a full-entropy random secret. It was previously
    ``uuid5(NAMESPACE_DNS, f"{identifier}:{org_slug}")`` — *derivable* by anyone
    who knew the hub name and org slug, and /f/claimhub/ hands out instance
    private keys against it.
    """
    hub, created = models.Hub.objects.update_or_create(
        identifier=manifest.identifier,
        organization=organization,
        defaults={
            "name": manifest.identifier or "Unnamed Hub",
            "description": manifest.description or "Auto-configured hub",
            "organization": organization,
            "creator": organization.owner,
        },
    )
    if created:
        # Only on create — rotating on every re-configure would break running
        # hub servers. (Existing derivable uuid5 tokens are rotated once by
        # migration.)
        hub.token = secrets.token_urlsafe(32)
        hub.save(update_fields=["token"])

    logger.info(f"{'Created' if created else 'Updated'} hub '{hub.name}' for org '{organization.slug}'")

    for instance_request in manifest.instances:
        service_manifest = instance_request.manifest

        service, _ = models.Service.objects.get_or_create(identifier=service_manifest.identifier, organization=organization, defaults={"name": service_manifest.identifier})

        release, _ = models.ServiceRelease.objects.get_or_create(service=service, version=service_manifest.version)

        instance, inst_created = models.ServiceInstance.objects.update_or_create(
            token=instance_request.identifier,
            hub=hub,
            defaults={
                "steward": organization.owner,
                "release": release,
                "organization": organization,
                "template": "{}",
                "instance_id": instance_request.identifier,
            },
        )

        logger.info(f"  {'Created' if inst_created else 'Updated'} instance: {instance.token}")

        if service_manifest.roles:
            for role_config in service_manifest.roles:
                role, role_created = karakter_models.Role.objects.get_or_create(organization=organization, identifier=role_config.key, defaults={"description": role_config.description, "creating_instance": instance})
                role.used_by.add(instance)
                logger.info(f"    {'Created' if role_created else 'Updated'} role: {role.identifier}")

        if service_manifest.scopes:
            for scope_config in service_manifest.scopes:
                scope, scope_created = karakter_models.Scope.objects.get_or_create(organization=organization, identifier=scope_config.key, defaults={"description": scope_config.description, "creating_instance": instance})
                scope.used_by.add(instance)
                logger.info(f"    {'Created' if scope_created else 'Updated'} scope: {scope.identifier}")

        for alias in instance_request.aliases:
            alias_obj, alias_created = aliases.upsert_instance_alias(instance, alias)
            logger.info(f"    {'Created' if alias_created else 'Updated'} alias: {alias_obj.name}")

    return hub


def create_hub_from_partner(
    partner: models.KommunityPartner,
    organization: karakter_models.Organization,
    license_signature: str | None = None,
) -> models.Hub | None:
    """Create a hub from a partner's preconfigured hub, honouring its pre-auth hook."""
    manifest = partner.preconfigured_hub_as_model
    if not manifest:
        raise ValueError(f"Partner '{partner.identifier}' has no preconfigured hub")

    logger.info(f"Creating hub from partner '{partner.identifier}' for org '{organization.slug}' ")

    hub = create_hub_from_manifest(
        manifest=manifest,
        organization=organization,
    )

    try:
        run_partner_pre_authorize_hook(
            partner=partner,
            organization=organization,
            hub=hub,
            hub_config=partner.preconfigured_hub,
            license_signature=license_signature,
        )
    except PartnerPreAuthorizationError:
        logger.exception(
            "Partner pre-authorization rejected hub '%s' for organization '%s'; deleting hub.",
            hub.identifier,
            organization.slug,
        )
        hub.delete()
        raise

    return hub


def auto_configure_kommunity_partners(
    organization: karakter_models.Organization,
) -> list[str]:
    """Apply every auto-configure kommunity partner that matches the organization's owner."""
    applied_partners = []

    auto_configure_partners = models.KommunityPartner.objects.filter(auto_configure=True)
    user = organization.owner

    for partner in auto_configure_partners:
        if not partner.applies_to_user(organization.owner):
            logger.info(f"Partner '{partner.identifier}' does not apply to user '{user}'")
            continue

        if not partner.preconfigured_hub:
            logger.warning(f"Partner '{partner.identifier}' has no preconfigured hub")
            continue

        logger.info(f"Applying partner '{partner.identifier}' to organization '{organization.slug}'")

        try:
            create_hub_from_partner(
                partner=partner,
                organization=organization,
            )
        except PartnerPreAuthorizationError:
            logger.warning(
                "Skipping auto-configured partner '%s' for organization '%s' because the pre-authorization hook rejected it.",
                partner.identifier,
                organization.slug,
            )
            continue

        applied_partners.append(partner.identifier)

    return applied_partners


def create_hub_auth_key(user: karakter_models.User, hub: models.Hub, ephemeral: bool = False, tags: list[str] = None) -> models.IonscaleAuthKey:
    # The mesh is a per-organization singleton, provisioned on explicit opt-in.
    # Read-only here: a hub uses the org's mesh if it has one, but does not
    # silently create a tailnet.
    layer = get_org_mesh(hub.organization)

    if not layer:
        raise Exception(
            "This organization has no mesh. Enable the ionscale mesh for the "
            "organization (or bring your own), or configure ionscale on this deployment."
        )

    tags = ["tag:hub-" + str(hub.pk)] if tags is None else tags

    key = get_ionscale_repo().create_auth_key(tailnet=layer.tailnet_name, ephemeral=ephemeral, pre_authorized=True, tags=tags)
    key = models.IonscaleAuthKey.objects.create(layer=layer, key=key, creator=user, ephemeral=ephemeral, tags=tags)
    return key


def create_mesh_auth_key(user: karakter_models.User, organization: karakter_models.Organization, ephemeral: bool = False, tags: list[str] = None) -> models.IonscaleAuthKey:
    """Mint a single-use pre-authorized key for an organization's mesh.

    Organization-scoped counterpart to ``create_hub_auth_key``: used by the mesh
    device-code flow to let a standalone machine join the org's tailnet. Read-only on the
    mesh — a machine uses the org's mesh if it has one, but does not silently create a
    tailnet.
    """
    layer = get_org_mesh(organization)

    if not layer:
        raise Exception(
            "This organization has no mesh. Enable the ionscale mesh for the "
            "organization (or bring your own), or configure ionscale on this deployment."
        )

    tags = ["tag:mesh-" + str(organization.pk)] if tags is None else tags

    key = get_ionscale_repo().create_auth_key(tailnet=layer.tailnet_name, ephemeral=ephemeral, pre_authorized=True, tags=tags)
    key = models.IonscaleAuthKey.objects.create(layer=layer, key=key, creator=user, ephemeral=ephemeral, tags=tags)
    return key

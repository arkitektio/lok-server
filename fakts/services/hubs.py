"""Hub lifecycle: building hubs from manifests/partners.

Includes the partner pre-authorization webhook and the auto-configuration of
kommunity partners for an organization.
"""

import logging
import secrets

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from fakts import base_models, models
from fakts.base_models import HubManifest
from fakts.services import aliases
from fakts.services.tokens import create_api_token  # noqa: F401  (kept for shim parity)
from ionscale.repo import get_ionscale_repo
from ionscale.acl import schedule_acl_apply
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

    counts = {"instances": 0, "roles": 0, "scopes": 0, "aliases": 0}

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

        counts["instances"] += 1
        logger.debug("%s instance %s", "Created" if inst_created else "Updated", instance.token)

        if service_manifest.roles:
            for role_config in service_manifest.roles:
                role, role_created = karakter_models.Role.objects.get_or_create(organization=organization, identifier=role_config.key, defaults={"description": role_config.description, "creating_instance": instance})
                role.used_by.add(instance)
                counts["roles"] += 1
                logger.debug("%s role %s", "Created" if role_created else "Updated", role.identifier)

        if service_manifest.scopes:
            for scope_config in service_manifest.scopes:
                scope, scope_created = karakter_models.Scope.objects.get_or_create(organization=organization, identifier=scope_config.key, defaults={"description": scope_config.description, "creating_instance": instance})
                scope.used_by.add(instance)
                counts["scopes"] += 1
                logger.debug("%s scope %s", "Created" if scope_created else "Updated", scope.identifier)

        for alias in instance_request.aliases:
            alias_obj, alias_created = aliases.upsert_instance_alias(instance, alias)
            counts["aliases"] += 1
            logger.debug("%s alias %s", "Created" if alias_created else "Updated", alias_obj.name)

    logger.info(
        "%s hub '%s' for org '%s' (%s)",
        "Created" if created else "Updated",
        hub.name,
        organization.slug,
        ", ".join(f"{n} {kind}" for kind, n in counts.items()),
    )
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

    logger.debug("Creating hub from partner '%s' for org '%s'", partner.identifier, organization.slug)

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
            logger.debug("Partner '%s' does not apply to user '%s'", partner.identifier, user)
            continue

        if not partner.preconfigured_hub:
            logger.warning(f"Partner '{partner.identifier}' has no preconfigured hub")
            continue

        logger.debug("Applying partner '%s' to organization '%s'", partner.identifier, organization.slug)

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


def create_mesh_auth_key(user: karakter_models.User, organization: karakter_models.Organization, ephemeral: bool = False, tags: list[str] = None) -> models.IonscaleAuthKey:
    """Mint a single-use pre-authorized key for an organization's mesh.

    Organization-scoped counterpart to ``enroll_hub_on_mesh``: used by the mesh
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


APP_MESH_KEY_EXPIRY_SECONDS = 15 * 60
"""An app uses its key once, right after the grant, to register its node. ionscale
keys are reusable until they expire, so keep the window short; the node itself
outlives the key (tagged nodes have key expiry disabled)."""


def enroll_app_on_mesh(
    user: karakter_models.User,
    organization: karakter_models.Organization,
    membership: karakter_models.Membership,
    app: models.App,
    device: models.Device | None,
) -> models.IonscaleAuthKey | None:
    """Mint a mesh key for an app installation without accumulating keys or machines.

    Returns ``None`` when the organization has no mesh. Re-grants of the same
    (membership, app, device) reuse one ``AppMeshEnrollment``:

    - the new key is minted first, so a failed mint leaves the old one in place;
    - the previous key is then revoked in ionscale;
    - the enrollment's stale nodes are pruned: every node tagged with the
      enrollment that is offline *and* not the newest. ionscale ids are
      time-ordered, and the newest node is the one the installation is most likely
      to come back as (an app re-granting is typically offline at that moment,
      and re-registering with its machine key updates that node in place). A node
      orphaned by lost tailscale state therefore survives one grant and is pruned
      on the next.

    Pruning needs a device: without one, two installations on different machines
    share the enrollment and would prune each other.
    """
    layer = get_org_mesh(organization)
    if not layer:
        return None

    enrollment, _ = models.AppMeshEnrollment.objects.get_or_create(
        membership=membership,
        app=app,
        device=device,
        defaults={"organization": organization},
    )

    repo = get_ionscale_repo()
    # Only the sidecar tag: `tag:mesh-<org>` is what member-reachable standalone
    # machines carry, and the ACL (ionscale.acl) keeps sidecars out of it.
    tags = [enrollment.tag]
    value = repo.create_auth_key(
        tailnet=layer.tailnet_name,
        ephemeral=False,
        pre_authorized=True,
        tags=tags,
        expiry_seconds=APP_MESH_KEY_EXPIRY_SECONDS,
    )
    key = models.IonscaleAuthKey.objects.create(layer=layer, key=value, creator=user, ephemeral=False, tags=tags)

    previous = enrollment.auth_key
    enrollment.auth_key = key
    enrollment.save(update_fields=["auth_key"])

    if previous is not None:
        try:
            repo.delete_auth_key(previous.layer.tailnet_name, previous.key)
            previous.delete()
        except Exception:
            logger.warning("Could not revoke the previous mesh key of %s", enrollment, exc_info=True)

    if device is not None:
        try:
            _prune_stale_machines(repo, layer.tailnet_name, enrollment.tag)
        except Exception:
            logger.warning("Could not prune stale mesh nodes of %s", enrollment, exc_info=True)

    schedule_acl_apply(organization.pk)
    return key


def enroll_hub_on_mesh(user: karakter_models.User, hub: models.Hub) -> models.IonscaleAuthKey | None:
    """Mint a mesh key for a hub, the way ``enroll_app_on_mesh`` does for an app.

    The hub row is its own enrollment (one per organization and identifier), so
    a re-authorization of the same hub rotates its key: mint first, then revoke
    the previous key and prune the hub's stale nodes (offline and not the
    newest). Returns ``None`` when the organization has no mesh.
    """
    layer = get_org_mesh(hub.organization)
    if not layer:
        return None

    repo = get_ionscale_repo()
    tags = [hub.mesh_tag]  # sidecar tag only, see enroll_app_on_mesh
    value = repo.create_auth_key(
        tailnet=layer.tailnet_name,
        ephemeral=False,
        pre_authorized=True,
        tags=tags,
        expiry_seconds=APP_MESH_KEY_EXPIRY_SECONDS,
    )
    key = models.IonscaleAuthKey.objects.create(hub=hub, layer=layer, key=value, creator=user, ephemeral=False, tags=tags)

    previous = hub.auth_key
    hub.auth_key = key
    hub.save(update_fields=["auth_key"])

    if previous is not None:
        try:
            repo.delete_auth_key(previous.layer.tailnet_name, previous.key)
            previous.delete()
        except Exception:
            logger.warning("Could not revoke the previous mesh key of hub %s", hub, exc_info=True)

    try:
        _prune_stale_machines(repo, layer.tailnet_name, hub.mesh_tag)
    except Exception:
        logger.warning("Could not prune stale mesh nodes of hub %s", hub, exc_info=True)

    schedule_acl_apply(hub.organization_id)
    return key


def _prune_stale_machines(repo, tailnet: str, tag: str) -> None:
    """Delete the nodes carrying ``tag`` that are offline and not the newest."""
    tagged = [m for m in repo.list_machines(tailnet) if tag in m.tags]
    if len(tagged) < 2:
        return
    newest = max(tagged, key=lambda m: int(m.id))
    for machine in tagged:
        if machine is newest or machine.connected:
            continue
        logger.info("Pruning stale mesh node %s (%s) tagged %s", machine.id, machine.name, tag)
        repo.delete_machine(machine.id)


def report_hub_health(hub: models.Hub, report: base_models.HubHealthReport) -> models.Hub:
    """Record a hub's health callback: liveness, version and mesh state on the hub,
    plus a snapshot (only the latest ``settings.HUB_HEALTH_RETENTION`` are kept).

    Instances the hub reports that are not its own are dropped from the snapshot.
    The reported mesh hostname is what ``kind: mesh`` aliases resolve to while
    the hub keeps calling in (see ``fakts.services.mesh``).
    """
    own = {str(pk) for pk in hub.instances.values_list("id", flat=True)} | set(
        hub.instances.values_list("token", flat=True)
    )
    payload = report.model_dump()
    payload["instances"] = {key: value for key, value in payload["instances"].items() if key in own}

    hub.last_seen_at = timezone.now()
    hub.last_healthy = report.healthy
    hub.version = report.version or ""
    fields = ["last_seen_at", "last_healthy", "version"]
    if report.mesh is not None:
        hub.mesh_connected = report.mesh.connected
        hub.mesh_host = (report.mesh.hostname or report.mesh.ipv4 or "") if report.mesh.connected else ""
        fields += ["mesh_connected", "mesh_host"]
    hub.save(update_fields=fields)

    models.HubHealthSnapshot.objects.create(hub=hub, healthy=report.healthy, payload=payload)
    retention = getattr(settings, "HUB_HEALTH_RETENTION", 20)
    keep = list(models.HubHealthSnapshot.objects.filter(hub=hub).values_list("id", flat=True)[:retention])
    models.HubHealthSnapshot.objects.filter(hub=hub).exclude(id__in=keep).delete()
    return hub

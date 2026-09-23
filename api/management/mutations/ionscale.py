from kante import Info
import strawberry
from django.db import transaction
from api.management import types
import kante
from fakts import models as fakts_models
from ionscale.repo import get_ionscale_repo
from ionscale.manager import ensure_org_mesh, apply_dns_config
from ionscale.sync import schedule_teardown
from api.management.authz import DENIED, assert_owner_or_admin, get_or_denied
from graphql import GraphQLError
from ionscale.acl import reserved_tags


@kante.input
class CreateIonscaleLayerInput:
    """Input for enabling the ionscale mesh for an organization"""

    organization_id: strawberry.ID = strawberry.field(description="The ID of the organization to enable the mesh for.")
    name: str | None = strawberry.field(description="Deprecated — the mesh is a per-organization singleton; ignored.")


def create_ionscale_layer(info: Info, input: CreateIonscaleLayerInput) -> types.ManagementLayer:
    """Enable (opt in to) the organization's ionscale mesh.

    The mesh is a per-organization singleton: if one already exists it is
    returned unchanged; otherwise it is provisioned. See `ensure_org_mesh`.
    """
    organization = get_or_denied(fakts_models.Organization.objects, id=input.organization_id)

    assert_owner_or_admin(info, organization)

    layer = ensure_org_mesh(organization)
    if layer is None:
        raise GraphQLError(
            "Could not enable the mesh: ionscale is not configured on this deployment."
        )
    return layer


@kante.input
class UpdateIonscaleLayerInput:
    """Input for updating an organization's mesh (ionscale layer)."""
    id: strawberry.ID = strawberry.field(description="The ID of the Ionscale layer to update.")
    name: str | None = strawberry.field(default=None, description="The name of the tailnet layer.")
    description: str | None = strawberry.field(default=None, description="The description of the tailnet layer.")
    magic_dns: bool | None = strawberry.field(default=None, description="Enable or disable MagicDNS for this mesh.")
    https_certs: bool | None = strawberry.field(default=None, description="Enable or disable HTTPS certificates for this mesh. Requires MagicDNS.")


def update_ionscale_layer(info: Info, input: UpdateIonscaleLayerInput) -> types.ManagementLayer:
    """Update an organization's mesh: naming and/or DNS (MagicDNS/HTTPS).

    Only the organization's owner or admins may reconfigure the mesh (it decides
    how its machines resolve names). Access itself is not configured here: every
    member of the organization is a member of its mesh.
    """

    layer = get_or_denied(fakts_models.IonscaleLayer.objects, id=input.id)

    assert_owner_or_admin(info, layer.organization)

    # Validate the DNS combination up front, before anything is mutated.
    if input.magic_dns is not None or input.https_certs is not None:
        magic_dns = input.magic_dns if input.magic_dns is not None else layer.magic_dns_enabled
        https_certs = input.https_certs if input.https_certs is not None else layer.https_enabled
        # HTTPS certs require MagicDNS (the cert domain *is* the MagicDNS name).
        if https_certs and not magic_dns:
            raise GraphQLError("HTTPS certificates require MagicDNS to be enabled.")

    if input.name is not None or input.description is not None:
        if input.name is not None:
            layer.name = input.name
        if input.description is not None:
            layer.description = input.description
        layer.save()

    if input.magic_dns is not None or input.https_certs is not None:
        if input.magic_dns is not None:
            layer.magic_dns_enabled = input.magic_dns
        if input.https_certs is not None:
            layer.https_enabled = input.https_certs
        # Save + push atomically: if ionscale rejects the change the model save is
        # rolled back, so the stored "desired state" never drifts ahead of what
        # ionscale actually has. Explicit user action, so the failure propagates
        # (and the UI shows an error) instead of silently reporting success.
        with transaction.atomic():
            layer.save()
            apply_dns_config(layer, raise_on_error=True)

    return layer



@kante.input
class DeleteIonscaleLayerInput:
    """Input for disabling (deleting) an organization's mesh layer."""

    id: strawberry.ID = strawberry.field(
        description="The mesh to disable. Irreversible: the tailnet and every machine "
        "enrolled in it are deleted on ionscale as well."
    )


def delete_ionscale_layer(info: Info, input: DeleteIonscaleLayerInput) -> strawberry.ID:
    """Disable (delete) an organization's mesh layer and tear its tailnet down.

    The tailnet is deleted on ionscale (forced, so enrolled machines go with
    it) once the layer's deletion has committed; a control-plane failure is
    logged and left to `manage.py reconcile_meshes`, which reports orphaned
    tailnets.
    """
    layer = get_or_denied(fakts_models.IonscaleLayer.objects, id=input.id)

    assert_owner_or_admin(info, layer.organization)

    tailnet_name = layer.tailnet_name
    with transaction.atomic():
        layer.delete()
        schedule_teardown(tailnet_name)

    return input.id


@kante.input
class CreateIonscaleAuthKeyInput:
    """Input for creating an auth key for an Ionscale layer"""
    layer_id: strawberry.ID = strawberry.field(description="The ID of the Ionscale layer to create the key for.")
    ephemeral: bool = strawberry.field(default=True, description="When enabled, machines authenticated by this key will be automatically removed after going offline.")
    tags: list[str] | None = strawberry.field(default=None, description="Machines authenticated by this key will be automatically tagged with these tags.")


def create_ionscale_auth_key(info: Info, input: CreateIonscaleAuthKeyInput) -> types.ManagementIonscaleAuthKey:
    """Mint a pre-authorized auth key for an organization's mesh. The key lets any
    machine join the mesh, so only the organization's owner or admins may mint one."""
    layer = get_or_denied(fakts_models.IonscaleLayer.objects, id=input.layer_id)

    assert_owner_or_admin(info, layer.organization)
    _reject_reserved_tags(input.tags)

    key = get_ionscale_repo().create_auth_key(
        tailnet=layer.tailnet_name,
        ephemeral=input.ephemeral,
        pre_authorized=True,
        tags=input.tags
    )

    key = fakts_models.IonscaleAuthKey.objects.create(
        layer=layer,
        key=key,
        creator=info.context.request.user,
        ephemeral=input.ephemeral,
        tags=input.tags or []
    )
    
    return key


@kante.input
class TailnetLockInput:
    """Input for changing a mesh's tailnet-lock capability."""

    layer_id: strawberry.ID = strawberry.field(description="The ID of the Ionscale layer (mesh) to change.")


def enable_tailnet_lock(info: Info, input: TailnetLockInput) -> types.ManagementLayer:
    """Grant the mesh's machines the tailnet-lock capability.

    This does NOT lock the network. It only permits `tailscale lock init`, which
    an admin then runs on a machine to create the key authority -- the private
    key never reaches the control plane, which is the entire point of tailnet
    lock. Only the organization's owner or admins may grant it: it changes how
    every machine on the mesh authenticates its peers.
    """
    layer = get_or_denied(fakts_models.IonscaleLayer.objects, id=input.layer_id)

    assert_owner_or_admin(info, layer.organization)

    try:
        get_ionscale_repo().enable_tailnet_lock(layer.tailnet_name)
    except Exception as exc:
        raise GraphQLError(f"Could not enable tailnet lock: {exc}")

    return layer


def disable_tailnet_lock(info: Info, input: TailnetLockInput) -> types.ManagementLayer:
    """Revoke the mesh's tailnet-lock capability.

    ionscale refuses this while a key authority is still active -- the authority
    has to be shut down from a client first (`tailscale lock disable` with a
    disablement secret), otherwise revoking the capability would partition the
    mesh. That refusal surfaces here as an error.
    """
    layer = get_or_denied(fakts_models.IonscaleLayer.objects, id=input.layer_id)

    assert_owner_or_admin(info, layer.organization)

    try:
        get_ionscale_repo().disable_tailnet_lock(layer.tailnet_name)
    except Exception as exc:
        raise GraphQLError(f"Could not disable tailnet lock: {exc}")

    return layer


def _reject_reserved_tags(tags) -> None:
    """`tag:app-*` / `tag:hub-*` mark app and hub sidecars; only lok mints them."""
    reserved = reserved_tags(tags)
    if reserved:
        raise GraphQLError(
            f"Tags {', '.join(reserved)} are reserved for app and hub sidecars and cannot be assigned by hand."
        )

from typing import Optional
from fakts.logic import find_instance_for_requirement_and_hub
import strawberry
import strawberry_django
from kante.types import Info
from .types import ManagementUser
import api.management.mutations as mutations
import api.management.types as types
import kante
from karakter import models as karakter_models
from karakter.hashers import hash_device_id
from fakts import models as fakts_models
from .datalayer import DatalayerExtension
from .extensions import RequireAuthenticationExtension
from .authz import DENIED, assert_member, get_or_denied, get_scoped_lookup, is_owner_or_admin
from .device_code_authz import resolve_device_code_with_proof
from allauth.socialaccount import models as smodels
from strawberry.schema.config import StrawberryConfig
from fakts.scalars import scalar_map as fakts_scalar_map
from .scalars import scalar_map as management_scalar_map
from graphql import GraphQLError


@strawberry.type
class Query:
    organizations: list[types.ManagementOrganization] = kante.django_field()
    kommunity_partners: list[types.ManagementKommunityPartner] = kante.django_field()
    friends: list[types.ManagementUser] = kante.django_field()
    apps: list[types.ManagementApp] = kante.django_field()
    releases: list[types.ManagementRelease] = kante.django_field()
    services: list[types.ManagementService] = kante.django_field()
    service_instances: list[types.ManagementServiceInstance] = kante.django_field()
    service_instance_mappings: list[types.ManagementServiceInstanceMapping] = kante.django_field()
    clients: list[types.ManagementClient] = kante.django_field()
    devices: list[types.ManagementDevice] = kante.django_field()
    redeem_tokens: list[types.ManagementRedeemToken] = kante.django_field()
    layers: list[types.ManagementLayer] = kante.django_field()
    device_groups: list[types.ManagementDeviceGroup] = kante.django_field()
    used_aliases: list[types.ManagementUsedAlias] = kante.django_field()
    reports: list[types.ManagementReport] = kante.django_field()
    service_releases: list[types.ManagementServiceRelease] = kante.django_field()
    instance_aliases: list[types.ManagementInstanceAlias] = kante.django_field()
    social_accounts: list[types.ManagementSocialAccount] = kante.django_field()
    memberships: list[types.ManagementMembership] = kante.django_field()
    role_requests: list[types.ManagementRoleRequest] = kante.django_field()
    scopes: list[types.ManagementScope] = kante.django_field()
    roles: list[types.ManagementRole] = kante.django_field()
    hubs: list[types.ManagementHub] = kante.django_field()
    ionscale_auth_keys: list[types.ManagementIonscaleAuthKey] = kante.django_field()

    @kante.django_field()
    def social_account(self, info: Info, id: strawberry.ID) -> types.ManagementSocialAccount:
        return get_scoped_lookup(types.ManagementSocialAccount, smodels.SocialAccount.objects, info, id=id)
    
    @kante.django_field()
    def kommunity_partner(self, info: Info, id: strawberry.ID) -> types.ManagementKommunityPartner:
        # Partners are a global catalog, deliberately not tenant-scoped.
        return get_or_denied(fakts_models.KommunityPartner.objects, id=id)

    @kante.django_field()
    def me(self, info: Info) -> ManagementUser:
        return info.context.request.user

    @kante.django_field()
    def role(self, info: Info, id: strawberry.ID) -> types.ManagementRole:
        return get_scoped_lookup(types.ManagementRole, karakter_models.Role.objects, info, id=id)


    @kante.django_field()
    def membership(self, info: Info, id: strawberry.ID) -> types.ManagementMembership:
        return get_scoped_lookup(types.ManagementMembership, karakter_models.Membership.objects, info, id=id)

    @kante.django_field()
    def scope(self, info: Info, id: strawberry.ID) -> types.ManagementScope:
        return get_scoped_lookup(types.ManagementScope, karakter_models.Scope.objects, info, id=id)

    @kante.django_field()
    def organization(self, info: Info, id: strawberry.ID) -> types.ManagementOrganization:
        return get_scoped_lookup(types.ManagementOrganization, karakter_models.Organization.objects, info, id=id)

    @kante.django_field()
    def used_alias(self, info: Info, id: strawberry.ID) -> types.ManagementUsedAlias:
        return get_scoped_lookup(types.ManagementUsedAlias, fakts_models.UsedAlias.objects, info, id=id)

    @kante.django_field()
    def instance_alias(self, info: Info, id: strawberry.ID) -> types.ManagementInstanceAlias:
        return get_scoped_lookup(types.ManagementInstanceAlias, fakts_models.InstanceAlias.objects, info, id=id)

    @kante.django_field(name="service")
    def _service(self, info: Info, id: strawberry.ID) -> types.ManagementService:
        return get_scoped_lookup(types.ManagementService, fakts_models.Service.objects, info, id=id)

    @kante.django_field()
    def app(self, info: Info, id: strawberry.ID) -> types.ManagementApp:
        return get_scoped_lookup(types.ManagementApp, fakts_models.App.objects, info, id=id)

    @kante.django_field()
    def service_instance(self, info: Info, id: strawberry.ID) -> types.ManagementServiceInstance:
        return get_scoped_lookup(types.ManagementServiceInstance, fakts_models.ServiceInstance.objects, info, id=id)

    @kante.django_field()
    def hub(self, info: Info, id: strawberry.ID) -> types.ManagementHub:
        return get_scoped_lookup(types.ManagementHub, fakts_models.Hub.objects, info, id=id)

    @kante.django_field()
    def oauth2_client_by_client_id(self, info: Info, client_id: str) -> types.ManagementOAuth2Client:
        # A miss is reported as the generic not-found so the endpoint cannot be
        # used to enumerate registered client ids.
        return get_or_denied(fakts_models.Client.objects, client_id=client_id)

    @kante.django_field()
    def validate_device_code(
        self,
        info: Info,
        device_code: strawberry.ID,
        hub: strawberry.ID,
        code: str,
    ) -> types.ValidationResult:
        """Dry-run an app device-code acceptance against one of the caller's hubs.

        ``code`` is the user code the device displayed (proof of possession): it
        is what stops a caller who guessed a sequential id from reading another
        tenant's pending manifest. The hub must belong to an organization the
        caller is a member of — the same bar as ``acceptDeviceCode``.
        """
        hub_obj = get_or_denied(fakts_models.Hub.objects, id=hub)
        assert_member(info, hub_obj.organization)
        device_code_obj = resolve_device_code_with_proof(
            fakts_models.DeviceCode, device_code_id=device_code, code=code, kind="app"
        )
        user = info.context.request.user
        try:
            manifest = device_code_obj.manifest_as_model
        except Exception:
            raise GraphQLError("The staged manifest of this device code is malformed.")
        errors: list[str] = []

        mappings: list[types.PotentialMapping] = []

        # Devices are keyed by (organization, hashed node_id). Surface the existing device
        # (if any) so the client knows whether accepting will create a new device.
        existing_device = None
        if manifest.node_id:
            existing_device = fakts_models.Device.objects.filter(
                organization=hub_obj.organization,
                node_id=hash_device_id(manifest.node_id, hub_obj.organization),
            ).first()

        if not manifest.requirements:
            return types.ValidationResult(
                valid=True,
                mappings=[],
                reason="Manifest has no requirements",
                existing_device=existing_device,
            )

        for req in manifest.requirements:
            try:
                instance = find_instance_for_requirement_and_hub(req, user, hub=hub_obj)
                if instance:
                    mappings.append(
                        types.PotentialMapping(
                            service_instance=instance,
                            key=req.key,
                            reason=None,
                        )
                    )
                else:
                    mappings.append(
                        types.PotentialMapping(
                            service_instance=None,
                            key=req.key,
                            reason=f"No suitable instance found for service {req.service}.",
                        )
                    )
                    if not req.optional:
                        errors.append(f"No suitable instance found for service {req.service}.")
            except Exception as e:
                mappings.append(
                    types.PotentialMapping(
                        service_instance=None,
                        key=req.key,
                        reason=str(e),
                    )
                )

        return types.ValidationResult(
            valid=len(errors) == 0,
            mappings=mappings,
            reason="\n".join(errors) if errors else "All requirements satisfied.",
            existing_device=existing_device,
        )

    @kante.django_field()
    def device_code_by_code(self, info: Info, device_code: str) -> types.ManagementDeviceCode:
        # The code itself is the capability (it is what the device displayed).
        return get_or_denied(fakts_models.DeviceCode.objects, code=device_code, kind="app")

    @kante.django_field()
    def invite_by_code(self, info: Info, invite_code: str) -> types.ManagementInvite:
        # `Invite.token` is a UUIDField: a non-UUID code used to raise a
        # ValidationError (a 500) instead of a clean not-found.
        invite = get_or_denied(karakter_models.Invite.objects, token=invite_code)
        # Public invites can be previewed by anyone with the link before signing in;
        # private invites reveal nothing until the visitor is authenticated.
        try:
            authenticated = bool(info.context.request.user.is_authenticated)
        except Exception:
            authenticated = False
        if not authenticated and not invite.public:
            raise GraphQLError("Please sign in to view this invitation")
        return invite

    @kante.django_field()
    def invite(self, info: Info, id: strawberry.ID) -> types.ManagementInvite:
        invite = get_or_denied(karakter_models.Invite.objects, id=id)
        if not is_owner_or_admin(info.context.request.user, invite.created_for):
            raise GraphQLError(DENIED)
        return invite

    @kante.django_field()
    def release(self, info: Info, id: strawberry.ID) -> types.ManagementRelease:
        return get_scoped_lookup(types.ManagementRelease, fakts_models.Release.objects, info, id=id)

    @kante.django_field()
    def layer(self, info: Info, id: strawberry.ID) -> types.ManagementLayer:
        return get_scoped_lookup(types.ManagementLayer, fakts_models.IonscaleLayer.objects, info, id=id)

    @kante.django_field()
    def ionscale_auth_key(self, info: Info, id: strawberry.ID) -> types.ManagementIonscaleAuthKey:
        return get_scoped_lookup(types.ManagementIonscaleAuthKey, fakts_models.IonscaleAuthKey.objects, info, id=id)


    @kante.django_field()
    def machine(self, info: Info, id: strawberry.ID) -> Optional[types.ManagementMachine]:
        from ionscale.repo import get_ionscale_repo
        from ionscale.base_models import MachineDetail

        repo = get_ionscale_repo()
        machine_id = str(id)

        # Find the machine by scanning only the layers the caller can see. This reuses the
        # reliable `list_machines` parser (the one the working mesh list page uses) instead of
        # the fragile single-machine `get_machine` parse, and gives us the owning layer for free.
        # Restricting to the caller's layers is also the authorization check — no machine from
        # another org is ever reachable.
        layers = fakts_models.IonscaleLayer.objects.filter(
            organization__memberships__user=info.context.request.user,
            tailnet_name__isnull=False,
        ).distinct()

        for layer in layers:
            # One subprocess per layer; short-circuit on the first match. Fine for the handful
            # of meshes a user typically belongs to.
            match = next((m for m in repo.list_machines(layer.tailnet_name) if str(m.id) == machine_id), None)
            if match is None:
                continue

            # Best-effort enrichment with the detail view for os/key_expiry/authorized/is_external/
            # fqdn. The detail parser is fragile (and hardcodes connected=False), so the list
            # `Machine` always wins for connected/ipv4/ipv6/name/tags; we only overlay the
            # detail-only attributes when the call succeeds.
            instance = match
            try:
                detail = repo.get_machine(machine_id)
                instance = MachineDetail(
                    id=match.id,
                    name=match.name,
                    tailnet=layer.tailnet_name,
                    ipv4=match.ipv4,
                    ipv6=match.ipv6,
                    ephemeral=match.ephemeral,
                    connected=match.connected,
                    last_seen=match.last_seen,
                    tags=match.tags,
                    # `authorized` is reliable from the list; keep it (fall back to detail).
                    authorized=match.authorized if match.authorized is not None else detail.authorized,
                    os=detail.os,
                    key_expiry=detail.key_expiry,
                    is_external=detail.is_external,
                    fqdn=detail.fqdn,
                )
            except Exception:
                pass

            return types.ManagementMachine(
                instance=instance,
                tailnet=layer.tailnet_name,
                layer_id=layer.id,
                magic_dns_enabled=layer.magic_dns_enabled,
            )

        # Not found in any of the caller's layers -> null (the frontend renders "not found").
        return None

    @kante.django_field()
    def client(self, info: Info, id: strawberry.ID) -> types.ManagementClient:
        return get_scoped_lookup(types.ManagementClient, fakts_models.Client.objects, info, id=id)

    @kante.django_field()
    def report(self, info: Info, id: strawberry.ID) -> types.ManagementReport:
        return get_scoped_lookup(types.ManagementReport, fakts_models.Report.objects, info, id=id)

    @kante.django_field()
    def service_instance_mapping(self, info: Info, id: strawberry.ID) -> types.ManagementServiceInstanceMapping:
        return get_scoped_lookup(types.ManagementServiceInstanceMapping, fakts_models.ServiceInstanceMapping.objects, info, id=id)

    @kante.django_field()
    def device(self, info: Info, id: strawberry.ID) -> types.ManagementDevice:
        return get_scoped_lookup(types.ManagementDevice, fakts_models.Device.objects, info, id=id)

    @kante.django_field()
    def service_release(self, info: Info, id: strawberry.ID) -> types.ManagementServiceRelease:
        return get_scoped_lookup(types.ManagementServiceRelease, fakts_models.ServiceRelease.objects, info, id=id)

    @kante.django_field()
    def device_group(self, info: Info, id: strawberry.ID) -> types.ManagementDeviceGroup:
        return get_scoped_lookup(types.ManagementDeviceGroup, fakts_models.DeviceGroup.objects, info, id=id)

    @kante.django_field()
    def device_code(self, info: Info, id: strawberry.ID) -> types.ManagementDeviceCode:
        # By id only codes already accepted into one of the caller's organizations
        # are visible; a pending code is reached through `deviceCodeByCode`.
        return get_scoped_lookup(types.ManagementDeviceCode, fakts_models.DeviceCode.objects, info, id=id, kind="app")

    @kante.django_field()
    def hub_device_code(self, info: Info, id: strawberry.ID) -> types.ManagementHubDeviceCode:
        return get_scoped_lookup(types.ManagementHubDeviceCode, fakts_models.DeviceCode.objects, info, id=id, kind="hub")

    @kante.django_field()
    def hub_device_code_by_code(self, info: Info, code: str) -> types.ManagementHubDeviceCode:
        return get_or_denied(fakts_models.DeviceCode.objects, code=code, kind="hub")

    @kante.django_field()
    def mesh_device_code(self, info: Info, id: strawberry.ID) -> types.ManagementMeshDeviceCode:
        return get_scoped_lookup(types.ManagementMeshDeviceCode, fakts_models.MeshDeviceCode.objects, info, id=id)

    @kante.django_field()
    def mesh_device_code_by_code(self, info: Info, code: str) -> types.ManagementMeshDeviceCode:
        return get_or_denied(fakts_models.MeshDeviceCode.objects, code=code)


@strawberry.type
class Mutation:
    create_organization = strawberry_django.mutation(
        resolver=mutations.create_organization,
    )
    connect_kommunity_partner = strawberry_django.mutation(
        resolver=mutations.connect_kommunity_partner,
    )
    update_organization = strawberry_django.mutation(
        resolver=mutations.update_organization,
    )
    delete_organization = strawberry_django.mutation(
        resolver=mutations.delete_organization,
    )

    create_invite = strawberry_django.mutation(
        resolver=mutations.create_invite,
    )
    accept_invite = strawberry_django.mutation(
        resolver=mutations.accept_invite,
    )
    decline_invite = strawberry_django.mutation(
        resolver=mutations.decline_invite,
        description="Decline an invite to join an organization.",
    )
    cancel_invite = strawberry_django.mutation(
        resolver=mutations.cancel_invite,
    )

    create_redeem_token = strawberry_django.mutation(
        resolver=mutations.create_redeem_token,
    )

    # device Mutations
    create_device = strawberry_django.mutation(
        resolver=mutations.create_device,
    )
    update_device = strawberry_django.mutation(
        resolver=mutations.update_device,
    )
    delete_device = strawberry_django.mutation(
        resolver=mutations.delete_device,
    )

    update_membership = strawberry_django.mutation(
        resolver=mutations.update_membership,
    )
    delete_membership = strawberry_django.mutation(
        resolver=mutations.delete_membership,
    )
    set_membership_brand_hue = strawberry_django.mutation(
        resolver=mutations.set_membership_brand_hue,
    )
    set_membership_notifications = strawberry_django.mutation(
        resolver=mutations.set_membership_notifications,
    )
    notify_member = strawberry_django.mutation(
        resolver=mutations.notify_member,
    )

    request_role = strawberry_django.mutation(
        resolver=mutations.request_role,
    )
    approve_role_request = strawberry_django.mutation(
        resolver=mutations.approve_role_request,
    )
    decline_role_request = strawberry_django.mutation(
        resolver=mutations.decline_role_request,
    )
    cancel_role_request = strawberry_django.mutation(
        resolver=mutations.cancel_role_request,
    )

    # Client Report Mutations
    resolve_report = strawberry_django.mutation(
        resolver=mutations.resolve_report,
    )
    unresolve_report = strawberry_django.mutation(
        resolver=mutations.unresolve_report,
    )
    request_client_report = strawberry_django.mutation(
        resolver=mutations.request_client_report,
    )

    # Hub Device Code Mutations
    accept_hub_device_code = strawberry_django.mutation(
        resolver=mutations.accept_hub_device_code,
    )
    decline_hub_device_code = strawberry_django.mutation(
        resolver=mutations.decline_hub_device_code,
    )
    # Mesh Device Code Mutations
    accept_mesh_device_code = strawberry_django.mutation(
        resolver=mutations.accept_mesh_device_code,
    )
    decline_mesh_device_code = strawberry_django.mutation(
        resolver=mutations.decline_mesh_device_code,
    )

    # Hub
    update_hub = strawberry_django.mutation(
        resolver=mutations.update_hub,
    )
    delete_hub = strawberry_django.mutation(
        resolver=mutations.delete_hub,
    )
    # Device Code Mutations
    accept_device_code = strawberry_django.mutation(
        resolver=mutations.accept_device_code,
    )
    decline_device_code = strawberry_django.mutation(
        resolver=mutations.decline_device_code,
    )
    # Session revocation (the operator side of RFC 7009 /o/revoke/)
    revoke_client_sessions = strawberry_django.mutation(
        resolver=mutations.revoke_client_sessions,
    )
    revoke_organization_sessions = strawberry_django.mutation(
        resolver=mutations.revoke_organization_sessions,
    )
    change_organization_owner = strawberry_django.mutation(
        resolver=mutations.change_organization_owner,
    )

    create_alias = strawberry_django.mutation(
        resolver=mutations.create_alias,
    )
    delete_alias = strawberry_django.mutation(
        resolver=mutations.delete_alias,
    )
    update_alias = strawberry_django.mutation(
        resolver=mutations.update_alias,
    )

    update_profile = strawberry_django.mutation(
        resolver=mutations.update_profile,
    )
    create_profile = strawberry_django.mutation(
        resolver=mutations.create_profile,
    )
    delete_profile = strawberry_django.mutation(
        resolver=mutations.delete_profile,
    )

    update_organization_profile = strawberry_django.mutation(
        resolver=mutations.update_organization_profile,
    )
    create_organization_profile = strawberry_django.mutation(
        resolver=mutations.create_organization_profile,
    )
    delete_organization_profile = strawberry_django.mutation(
        resolver=mutations.delete_organization_profile,
    )

    delete_device_group = strawberry_django.mutation(
        resolver=mutations.delete_device_group,
    )
    create_device_group = strawberry_django.mutation(
        resolver=mutations.create_device_group,
    )
    add_device_to_group = strawberry_django.mutation(
        resolver=mutations.add_device_to_group,
    )
    remove_device_from_group = strawberry_django.mutation(
        resolver=mutations.remove_device_from_group,
    )
    create_role_set = strawberry_django.mutation(
        resolver=mutations.create_role_set,
    )
    update_role_set = strawberry_django.mutation(
        resolver=mutations.update_role_set,
    )
    delete_role_set = strawberry_django.mutation(
        resolver=mutations.delete_role_set,
    )

    request_media_upload = strawberry_django.mutation(
        resolver=mutations.request_media_upload,
    )

    create_ionscale_layer = strawberry_django.mutation(
        resolver=mutations.create_ionscale_layer,
    )
    delete_ionscale_layer = strawberry_django.mutation(
        resolver=mutations.delete_ionscale_layer,
    )
    update_ionscale_layer = strawberry_django.mutation(
        resolver=mutations.update_ionscale_layer,
    )

    create_ionscale_auth_key = strawberry_django.mutation(
        resolver=mutations.create_ionscale_auth_key,
    )

    enable_tailnet_lock = strawberry_django.mutation(
        resolver=mutations.enable_tailnet_lock,
    )

    disable_tailnet_lock = strawberry_django.mutation(
        resolver=mutations.disable_tailnet_lock,
    )


schema = kante.Schema(
    query=Query,
    mutation=Mutation,
    types=[types.ManagementGithubAccount, types.ManagementGenericAccount, types.ManagementGoogleAccount, types.ManagementOrcidAccount],
    extensions=[
        RequireAuthenticationExtension,
        DatalayerExtension,
    ],
    config=StrawberryConfig(scalar_map={**fakts_scalar_map, **management_scalar_map}),
)

from kante import Info
import strawberry
from django.db import IntegrityError, transaction
from pydantic import ValidationError as PydanticValidationError
from api.management import types
from karakter import models
from karakter.hashers import hash_device_id
from fakts import models as fakts_models
from api.management.authz import DENIED, HUB_ADMIN_REQUIRED, assert_member, get_or_denied, is_owner_or_admin
from graphql import GraphQLError
from fakts import logic, builders, base_models, enums
from fakts.services import aliases as alias_services
import kante
from api.management.device_code_authz import resolve_device_code_with_proof


@kante.input
class AcceptHubDeviceCodeInput:
    """Input for accepting a pending hub device code into an organization."""

    device_code: strawberry.ID
    code: str = strawberry.field(
        description="The user-facing code the device displayed — proof that the approver actually saw the enrolment request"
    )
    organization: strawberry.ID
    allow_ionscale: bool = True


def accept_hub_device_code(info: Info, input: AcceptHubDeviceCodeInput) -> types.ManagementHub:
    """
    Accept a pending hub device code: provision the hub described by its staged
    manifest (service instances, roles, scopes, aliases, clients) inside the
    organization and bind the hub's identity client to the caller's membership.

    Requires the user code the device displayed (proof of possession) and
    ownership of — or the `admin` role in — the target organization.
    """
    user = info.context.request.user
    device_code = resolve_device_code_with_proof(
        fakts_models.DeviceCode, device_code_id=input.device_code, code=input.code, kind="hub"
    )
    organization = get_or_denied(models.Organization.objects, id=input.organization)

    # Creates a hub (plus service instances and auth keys) inside `organization`.
    # Membership first, so a non-member still only learns the generic denial;
    # a member who is not an owner/admin gets told to ask one.
    assert_member(info, organization)
    if not is_owner_or_admin(user, organization):
        raise GraphQLError(HUB_ADMIN_REQUIRED)

    try:
        manifest = device_code.hub_manifest_as_model
    except (PydanticValidationError, TypeError):
        raise GraphQLError("The staged hub manifest of this device code is malformed.")

    if fakts_models.Hub.objects.filter(organization=organization, identifier=manifest.identifier).exists():
        raise GraphQLError(
            f"A hub with identifier '{manifest.identifier}' already exists in this organization."
        )

    try:
        with transaction.atomic():
            return _provision_hub(info, input, device_code, organization, manifest, user)
    except IntegrityError:
        # The pre-check above closes the common case; this closes the race (or a
        # duplicate further down the manifest) without leaking a half-built hub.
        if fakts_models.Hub.objects.filter(organization=organization, identifier=manifest.identifier).exists():
            raise GraphQLError(
                f"A hub with identifier '{manifest.identifier}' already exists in this organization."
            )
        raise GraphQLError("Could not provision the hub: the manifest conflicts with existing objects in this organization.")


def _provision_hub(info: Info, input: AcceptHubDeviceCodeInput, device_code, organization, manifest, user) -> fakts_models.Hub:
    hub = fakts_models.Hub.objects.create(
        name=manifest.identifier,
        identifier=manifest.identifier,
        description=manifest.description or "",
        organization=organization,
        creator=user,
    )

    for servicer in manifest.instances:
        service_manifest = servicer.manifest
        device_id = service_manifest.node_id
        if device_id:
            device, _ = fakts_models.Device.objects.get_or_create(organization=organization, node_id=hash_device_id(device_id, organization))
        else:
            device = None
        instance = fakts_models.ServiceInstance.objects.filter(
            release__service__identifier=service_manifest.identifier,
            release__version=service_manifest.version,
            device=device,
            steward=user,
            hub=hub,
            organization=organization,
            instance_id=service_manifest.instance_id,
        ).first()

        if not instance:
            token = logic.create_api_token()
            service, _ = fakts_models.Service.objects.get_or_create(identifier=service_manifest.identifier, organization=organization, defaults={"description": service_manifest.description or ""})
            release, _ = fakts_models.ServiceRelease.objects.get_or_create(
                service=service,
                version=service_manifest.version,
            )
            instance = fakts_models.ServiceInstance.objects.create(
                release=release,
                device=device,
                steward=user,
                token=token,
                hub=hub,
                instance_id=service_manifest.instance_id,
                organization=organization,
            )

        for role in service_manifest.roles or []:
            r, _ = models.Role.objects.get_or_create(
                identifier=role.key,
                organization=organization,
                defaults={
                    "description": role.description or "",
                    "creating_instance": instance,
                },
            )

            r.used_by.add(instance)

        for scope in service_manifest.scopes or []:
            sc, _ = models.Scope.objects.get_or_create(
                identifier=scope.key,
                organization=organization,
                defaults={
                    "description": scope.description or "",
                    "creating_instance": instance,
                },
            )

            sc.used_by.add(instance)

        for alias in servicer.aliases:
            alias_services.upsert_instance_alias(instance, alias)

    accepting_user = info.context.request.user
    membership = get_or_denied(models.Membership.objects, user=accepting_user, organization=organization)

    for clr in manifest.clients:
        client_manifest = clr.manifest

        client = builders.create_public_client(
            kind=enums.ClientKindVanilla.DEVELOPMENT.value,
        )
        builders.bind_client(
            client,
            client_manifest,
            membership,
            hub=hub,
        )

    if input.allow_ionscale and manifest.request_auth_key:
        hub.auth_key = logic.create_hub_auth_key(user=info.context.request.user, hub=hub)
        hub.save()

    # Bind the staged (registered-at-start) client to the hub and the approving
    # user's membership: the hub server polls the token endpoint as this client
    # and receives its config in the token response envelope.
    staged = device_code.client
    staged.membership = membership
    staged.organization = organization
    staged.scope = "openid"
    staged.name = manifest.identifier
    staged.save()
    hub.client = staged
    hub.save(update_fields=["client"])

    device_code.organization = organization
    device_code.granted_scope = "openid"
    device_code.save()

    return hub


@kante.input
class DeclineHubDeviceCodeInput:
    """Input for declining a pending device code."""

    device_code: strawberry.ID
    code: str | None = strawberry.field(
        default=None,
        description="The code the device displayed. Proves the caller was actually "
        "shown this enrolment; without it, a guessed id is enough to deny "
        "someone else's. Optional only until clients are updated to send it.",
    )


def decline_hub_device_code(info: Info, input: DeclineHubDeviceCodeInput) -> types.ManagementHubDeviceCode:
    """
    Decline a pending hub device code.

    Marks the device code as denied; the polling hub receives `access_denied`.
    """
    device_code = resolve_device_code_with_proof(
        fakts_models.DeviceCode, device_code_id=input.device_code, code=input.code, kind="hub"
    )

    device_code.denied = True
    device_code.save()

    return device_code

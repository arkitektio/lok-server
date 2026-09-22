import logging

from kante import Info
import strawberry
from api.management import types
from karakter import models
from fakts import models as fakts_models
from api.management.authz import DENIED, assert_member, get_or_denied
from graphql import GraphQLError
from fakts import logic
import kante
from api.management.device_code_authz import resolve_device_code_with_proof

logger = logging.getLogger(__name__)


@kante.input
class AcceptDeviceCodeInput:
    """Input for accepting a pending app device code into one of your hubs."""

    device_code: strawberry.ID
    code: str = strawberry.field(
        description="The user-facing code the device displayed — proof that the approver actually saw the enrolment request"
    )
    hub: strawberry.ID
    device_name: str | None = strawberry.field(default=None, description="Name to give a newly created device (ignored if the device already exists).")
    declined_requirements: list[str] = strawberry.field(default_factory=list)
    """Requirement keys the user has explicitly declined (optional requirements only)."""
    allow_ionscale: bool = strawberry.field(
        default=True,
        description="Hand the app a pre-authorized key for the organization's mesh, if it asked for one (`requestAuthKey`).",
    )


def accept_device_code(info: Info, input: AcceptDeviceCodeInput) -> types.ManagementClient:
    """
    Accept a pending app device code: bind the staged client to the caller's
    membership in the hub's organization, map its requirements onto the hub's
    service instances and mint its credentials.

    Requires the user code the device displayed (proof of possession) and
    membership in the hub's organization.
    """
    user = info.context.request.user
    device_code = resolve_device_code_with_proof(
        fakts_models.DeviceCode, device_code_id=input.device_code, code=input.code, kind="app"
    )

    # A code that can never yield a token must not be "accepted": the grant side
    # rejects expired and denied codes, but `bind_client` still ran, and its
    # identity rotation *deletes* the existing client for the same
    # (release, membership, node, hub) tuple — so accepting a stale or already
    # declined code knocked out a working client.
    if device_code.is_expired():
        raise GraphQLError("This device code has expired.")
    if device_code.denied:
        raise GraphQLError("This device code was already denied.")
    hub = get_or_denied(fakts_models.Hub.objects, id=input.hub)

    organization = hub.organization
    # Accepting mints a client and an API token inside `organization`, so the
    # caller must belong to it -- otherwise any authenticated user could name
    # another tenant's hub and provision credentials there.
    assert_member(info, organization)

    validate_device_code = logic.validate_device_code(
        device_code=device_code,
        user=user,
        organization=organization,
        hub=hub,
        device_name=input.device_name,
        declined_requirements=input.declined_requirements,
    )

    if input.allow_ionscale and device_code.request_auth_key:
        _grant_mesh_key(device_code, user, organization)

    return validate_device_code.client


def _grant_mesh_key(device_code: fakts_models.DeviceCode, user, organization) -> None:
    """Mint the app's mesh key onto the code; the token response hands it out once.

    Best-effort: without a mesh, or with ionscale failing, the app is still
    authorized and falls back to the interactive mesh login.
    """
    client = fakts_models.Client.objects.select_related("membership", "release__app", "node").get(pk=device_code.client_id)
    try:
        key = logic.enroll_app_on_mesh(
            user=user,
            organization=organization,
            membership=client.membership,
            app=client.release.app,
            device=client.node,
        )
    except Exception:
        logger.warning("Could not mint a mesh key for client %s", client.client_id, exc_info=True)
        return
    if key is not None:
        device_code.auth_key = key
        device_code.save(update_fields=["auth_key"])


@kante.input
class DeclineDeviceCodeInput:
    """Input for declining a pending device code."""

    device_code: strawberry.ID
    code: str | None = strawberry.field(
        default=None,
        description="The code the device displayed. Proves the caller was actually "
        "shown this enrolment; without it, a guessed id is enough to deny "
        "someone else's. Optional only until clients are updated to send it.",
    )


def decline_device_code(info: Info, input: DeclineDeviceCodeInput) -> types.ManagementDeviceCode:
    """
    Decline a pending app device code.

    Marks the device code as denied; the polling device receives `access_denied`.
    """
    device_code = resolve_device_code_with_proof(
        fakts_models.DeviceCode, device_code_id=input.device_code, code=input.code, kind="app")

    device_code.denied = True
    device_code.save()

    return device_code

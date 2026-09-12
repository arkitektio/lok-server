from kante import Info
import strawberry
from api.management import types
from karakter import models
from fakts import models as fakts_models
from api.management.authz import assert_owner_or_admin, get_or_denied
from api.management.device_code_authz import resolve_device_code_with_proof
from fakts import logic
import kante


@kante.input
class AcceptMeshDeviceCodeInput:
    """Input for authorizing a machine to join an organization's mesh."""

    device_code: strawberry.ID
    code: str = strawberry.field(
        description="The user-facing code the device displayed — proof that the approver actually saw the enrolment request"
    )
    organization: strawberry.ID
    machine_name: str | None = None
    ephemeral: bool = False
    tags: list[str] | None = None


def accept_mesh_device_code(info: Info, input: AcceptMeshDeviceCodeInput) -> types.ManagementMeshDeviceCode:
    """
    Authorize a machine's request to join an organization's mesh.

    Mints a single-use pre-authorized key for the organization's mesh and links it to the
    device code, so the polling machine receives it (along with the coordination URL and
    the authorized machine name). Minting a mesh key is a privileged operation, so
    only the organization's owner or admins may authorize, and they must present the
    code the machine displayed (proof of possession).
    """
    user = info.context.request.user
    device_code = resolve_device_code_with_proof(
        fakts_models.MeshDeviceCode, device_code_id=input.device_code, code=input.code
    )
    organization = get_or_denied(models.Organization.objects, id=input.organization)

    assert_owner_or_admin(info, organization)

    key = logic.create_mesh_auth_key(
        user=user,
        organization=organization,
        ephemeral=input.ephemeral,
        tags=input.tags,
    )

    device_code.auth_key = key
    device_code.machine_name = input.machine_name or device_code.requested_machine_name
    device_code.save()

    return device_code


@kante.input
class DeclineMeshDeviceCodeInput:
    """Input for declining a machine's mesh join request."""

    device_code: strawberry.ID
    code: str | None = strawberry.field(
        default=None,
        description="The code the machine displayed. Proves the caller was actually "
        "shown this join request; without it, a guessed id is enough to deny "
        "someone else's. Optional only until clients are updated to send it.",
    )


def decline_mesh_device_code(info: Info, input: DeclineMeshDeviceCodeInput) -> types.ManagementMeshDeviceCode:
    """Decline a machine's request to join the mesh, marking the device code as denied."""
    device_code = resolve_device_code_with_proof(
        fakts_models.MeshDeviceCode, device_code_id=input.device_code, code=input.code
    )
    device_code.denied = True
    device_code.save()

    return device_code

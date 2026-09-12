from kante import Info
import strawberry
from api.management import types
from karakter import models
from karakter.hashers import hash_device_id
import kante
from api.management.authz import assert_member, get_or_denied
from fakts import models as fakts_models


@kante.input
class CreateDeviceInput:
    """Input for registering a device (compute node) in an organization."""

    organization: strawberry.ID
    device_id: strawberry.ID
    name: str


def create_device(info: Info, input: CreateDeviceInput) -> types.ManagementDevice:
    """Register (or rename) a device in an organization, keyed by its hashed device id."""
    organization = get_or_denied(models.Organization.objects, id=input.organization)

    assert_member(info, organization)

    c, _ = fakts_models.Device.objects.update_or_create(organization=organization, node_id=hash_device_id(input.device_id, organization), defaults=dict(name=input.name))

    return c


@kante.input
class UpdateDeviceInput:
    """Input for renaming a device."""

    id: strawberry.ID
    name: str


def update_device(info: Info, input: UpdateDeviceInput) -> types.ManagementDevice:
    """Rename a device."""

    device = get_or_denied(fakts_models.Device.objects, id=input.id)

    assert_member(info, device.organization)

    device.name = input.name
    device.save()

    return device


@kante.input
class DeleteDeviceInput:
    """Input for deleting a device."""

    id: strawberry.ID


def delete_device(info: Info, input: DeleteDeviceInput) -> strawberry.ID:
    """Delete a device, returning the deleted id."""
    device = get_or_denied(fakts_models.Device.objects, id=input.id)

    assert_member(info, device.organization)

    device.delete()
    return input.id

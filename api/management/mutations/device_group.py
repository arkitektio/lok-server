from kante import Info
import strawberry
from api.management import types
from karakter import models
import kante
from api.management.authz import DENIED, assert_member, get_or_denied
from fakts import models as fakts_models
from graphql import GraphQLError


@kante.input
class CreateDeviceGroupInput:
    """Input for creating a device group in an organization."""

    name: str
    organization: strawberry.ID


def create_device_group(info: Info, input: CreateDeviceGroupInput) -> types.ManagementDeviceGroup:
    """Create a device group in an organization."""

    organization = get_or_denied(models.Organization.objects, id=input.organization)

    assert_member(info, organization)

    dg = fakts_models.DeviceGroup.objects.create(
        name=input.name,
        organization=organization,
    )

    return dg


@kante.input
class DeleteDeviceGroupInput:
    """Input for deleting a device group."""

    id: strawberry.ID


def delete_device_group(info: Info, input: DeleteDeviceGroupInput) -> strawberry.ID:
    """Delete a device group, returning the deleted id. The devices themselves are kept."""
    dg = get_or_denied(fakts_models.DeviceGroup.objects, id=input.id)

    assert_member(info, dg.organization)

    dg.delete()
    return input.id


@kante.input
class AddDeviceToGroupInput:
    """Input for adding a device to a device group"""

    device: strawberry.ID
    device_group: strawberry.ID


def add_device_to_group(info: Info, input: AddDeviceToGroupInput) -> types.ManagementDevice:
    """Add a device to a device group of the same organization."""

    dg = get_or_denied(fakts_models.DeviceGroup.objects, id=input.device_group)
    device = get_or_denied(fakts_models.Device.objects, id=input.device)

    # Both sides must belong to an org the caller is in, and to the *same* org --
    # otherwise membership in one tenant would let you move another tenant's device.
    assert_member(info, dg.organization)
    assert_member(info, device.organization)
    if dg.organization_id != device.organization_id:
        raise GraphQLError(DENIED)

    device.device_groups.add(dg)
    device.save()

    return device


@kante.input
class RemoveDeviceFromGroupInput:
    """Input for removing a device from a device group"""

    device: strawberry.ID
    device_group: strawberry.ID


def remove_device_from_group(info: Info, input: RemoveDeviceFromGroupInput) -> types.ManagementDevice:
    """Remove a device from a device group."""

    dg = get_or_denied(fakts_models.DeviceGroup.objects, id=input.device_group)
    device = get_or_denied(fakts_models.Device.objects, id=input.device)

    # Both sides must belong to an org the caller is in, and to the *same* org --
    # otherwise membership in one tenant would let you move another tenant's device.
    assert_member(info, dg.organization)
    assert_member(info, device.organization)
    if dg.organization_id != device.organization_id:
        raise GraphQLError(DENIED)

    device.device_groups.remove(dg)
    device.save()

    return device

"""Device-group mutations on the management schema.

``addDeviceToGroup`` / ``removeDeviceFromGroup`` each check that the caller is a
member of the group's organization *and* of the device's organization — which a
user who belongs to both tenants passes. The only thing that stops such a user
from moving tenant A's device into tenant B's group is the explicit
``organization_id`` equality check; nothing exercised it. These tests pin that
guard, the plain-member bar on create/delete, and that deleting a group keeps
its devices.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from fakts import models as fakts_models
from tests import factories
from tests.conftest import build_auth_context

CREATE = 'mutation ($org: ID!) { createDeviceGroup(input: { organization: $org, name: "lab" }) { id name } }'
DELETE = "mutation ($id: ID!) { deleteDeviceGroup(input: { id: $id }) }"
ADD = "mutation ($d: ID!, $g: ID!) { addDeviceToGroup(input: { device: $d, deviceGroup: $g }) { id deviceGroups { id } } }"
REMOVE = "mutation ($d: ID!, $g: ID!) { removeDeviceFromGroup(input: { device: $d, deviceGroup: $g }) { id deviceGroups { id } } }"


def _context(membership):
    return build_auth_context(membership.user, membership.organization, factories.make_client(membership=membership))


def _org_with_member():
    membership = factories.make_membership()
    membership.roles.clear()
    return membership.organization, membership, _context(membership)


def _group(org, name="lab"):
    return fakts_models.DeviceGroup.objects.create(name=name, organization=org)


def _device(org, node_id="node-1"):
    return fakts_models.Device.objects.create(organization=org, node_id=node_id, name=node_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_can_create_and_delete_a_group():
    org, _membership, context = await sync_to_async(_org_with_member)()

    created = await management_schema.execute(CREATE, context_value=context, variable_values={"org": str(org.id)})
    assert not created.errors, created.errors
    group_id = created.data["createDeviceGroup"]["id"]

    deleted = await management_schema.execute(DELETE, context_value=context, variable_values={"id": group_id})
    assert not deleted.errors, deleted.errors
    assert deleted.data["deleteDeviceGroup"] == group_id
    assert not await sync_to_async(fakts_models.DeviceGroup.objects.filter(pk=group_id).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_non_member_cannot_create_or_delete_in_another_tenant():
    org, _membership, _ = await sync_to_async(_org_with_member)()
    group = await sync_to_async(_group)(org)
    outsider = await sync_to_async(lambda: _context(factories.make_membership()))()

    created = await management_schema.execute(CREATE, context_value=outsider, variable_values={"org": str(org.id)})
    deleted = await management_schema.execute(DELETE, context_value=outsider, variable_values={"id": str(group.id)})

    assert created.errors and created.errors[0].message == DENIED
    assert deleted.errors and deleted.errors[0].message == DENIED
    assert await sync_to_async(fakts_models.DeviceGroup.objects.filter(pk=group.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_group_keeps_its_devices():
    org, _membership, context = await sync_to_async(_org_with_member)()

    def seed():
        group = _group(org)
        device = _device(org)
        device.device_groups.add(group)
        return group, device

    group, device = await sync_to_async(seed)()

    result = await management_schema.execute(DELETE, context_value=context, variable_values={"id": str(group.id)})

    assert not result.errors, result.errors
    assert await sync_to_async(fakts_models.Device.objects.filter(pk=device.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_add_then_remove_round_trips_the_membership():
    org, _membership, context = await sync_to_async(_org_with_member)()
    group = await sync_to_async(_group)(org)
    device = await sync_to_async(_device)(org)
    variables = {"d": str(device.id), "g": str(group.id)}

    added = await management_schema.execute(ADD, context_value=context, variable_values=variables)
    assert not added.errors, added.errors
    assert [g["id"] for g in added.data["addDeviceToGroup"]["deviceGroups"]] == [str(group.id)]

    removed = await management_schema.execute(REMOVE, context_value=context, variable_values=variables)
    assert not removed.errors, removed.errors
    assert removed.data["removeDeviceFromGroup"]["deviceGroups"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_of_both_tenants_cannot_move_a_device_across_them():
    """Both ``assert_member`` checks pass for a dual member; only the same-org
    guard stops the cross-tenant move."""
    org_a, membership, _ = await sync_to_async(_org_with_member)()

    def seed():
        org_b = factories.make_organization()
        factories.make_membership(user=membership.user, organization=org_b)
        return org_b, _device(org_a), _group(org_b), _context(membership)

    _org_b, device, group_b, context = await sync_to_async(seed)()
    variables = {"d": str(device.id), "g": str(group_b.id)}

    added = await management_schema.execute(ADD, context_value=context, variable_values=variables)
    assert added.errors, f"device moved across tenants: {added.data}"
    assert added.errors[0].message == DENIED
    assert await sync_to_async(lambda: device.device_groups.count())() == 0

    removed = await management_schema.execute(REMOVE, context_value=context, variable_values=variables)
    assert removed.errors and removed.errors[0].message == DENIED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_outsider_cannot_add_to_a_group_they_are_not_in():
    org, _membership, _ = await sync_to_async(_org_with_member)()
    group = await sync_to_async(_group)(org)
    device = await sync_to_async(_device)(org)
    outsider = await sync_to_async(lambda: _context(factories.make_membership()))()

    result = await management_schema.execute(
        ADD, context_value=outsider, variable_values={"d": str(device.id), "g": str(group.id)}
    )

    assert result.errors and result.errors[0].message == DENIED

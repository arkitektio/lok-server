"""Role sets are the one management surface (besides ``deleteOrganization``) that
is **owner-only** rather than owner-or-admin. Nothing exercised the three
mutations, so an accidental relaxation to the usual ``assert_owner_or_admin``
would have been invisible. These pin the bar, that foreign role ids are dropped,
the duplicate-name rules, and that deleting a set never touches the roles.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from karakter import models
from karakter.managers import create_role
from tests import factories
from tests.test_management_tenant_isolation import _org_with_admin_and_member

OWNER_ONLY = "You must own the organization to manage role sets"

CREATE = """
mutation ($org: ID!, $name: String!, $roles: [ID!]) {
  createRoleSet(input: { organization: $org, name: $name, roles: $roles }) {
    id name roles { identifier }
  }
}
"""
UPDATE = """
mutation ($id: ID!, $name: String, $roles: [ID!]) {
  updateRoleSet(input: { id: $id, name: $name, roles: $roles }) { id name roles { identifier } }
}
"""
DELETE = "mutation ($id: ID!) { deleteRoleSet(input: { id: $id }) }"


def _setup():
    s = _org_with_admin_and_member()
    s["editor"] = create_role(s["org"], "editor")
    s["viewer"] = create_role(s["org"], "viewer")
    return s


async def _create(context, org, name, roles=None):
    return await management_schema.execute(
        CREATE, context_value=context, variable_values={"org": str(org.id), "name": name, "roles": roles}
    )


def _make_set(org, name, roles=()):
    role_set = models.RoleSet.objects.create(name=name, organization=org)
    role_set.roles.set(roles)
    return role_set


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_create_a_role_set_with_roles():
    s = await sync_to_async(_setup)()

    result = await _create(s["owner"], s["org"], "editors", [str(s["editor"].id)])

    assert not result.errors, result.errors
    assert result.data["createRoleSet"]["name"] == "editors"
    assert [r["identifier"] for r in result.data["createRoleSet"]["roles"]] == ["editor"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_is_not_enough_to_manage_role_sets():
    """Admins may do almost everything else; role sets are deliberately owner-only."""
    s = await sync_to_async(_setup)()

    result = await _create(s["admin"], s["org"], "editors", [str(s["editor"].id)])

    assert result.errors, f"an admin created a role set: {result.data}"
    assert result.errors[0].message == OWNER_ONLY
    assert not await sync_to_async(models.RoleSet.objects.filter(name="editors").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_is_rejected_too():
    s = await sync_to_async(_setup)()

    result = await _create(s["member"], s["org"], "editors")

    assert result.errors
    assert result.errors[0].message == OWNER_ONLY


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_foreign_role_ids_are_ignored():
    s = await sync_to_async(_setup)()
    foreign = await sync_to_async(lambda: create_role(factories.make_organization(), "editor"))()

    result = await _create(s["owner"], s["org"], "mixed", [str(foreign.id)])

    assert not result.errors, result.errors
    assert result.data["createRoleSet"]["roles"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_duplicate_name_is_refused():
    s = await sync_to_async(_setup)()
    await sync_to_async(_make_set)(s["org"], "editors")

    result = await _create(s["owner"], s["org"], "editors")

    assert result.errors
    assert "already exists" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_allows_renaming_to_own_name_and_replaces_roles():
    s = await sync_to_async(_setup)()
    role_set = await sync_to_async(_make_set)(s["org"], "editors", [s["editor"]])

    result = await management_schema.execute(
        UPDATE,
        context_value=s["owner"],
        variable_values={"id": str(role_set.id), "name": "editors", "roles": [str(s["viewer"].id)]},
    )

    assert not result.errors, result.errors
    assert result.data["updateRoleSet"]["name"] == "editors"
    assert [r["identifier"] for r in result.data["updateRoleSet"]["roles"]] == ["viewer"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_refuses_renaming_onto_another_set():
    s = await sync_to_async(_setup)()
    await sync_to_async(_make_set)(s["org"], "editors")
    other = await sync_to_async(_make_set)(s["org"], "viewers")

    result = await management_schema.execute(
        UPDATE, context_value=s["owner"], variable_values={"id": str(other.id), "name": "editors", "roles": None}
    )

    assert result.errors
    assert "already exists" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_cannot_update_or_delete():
    s = await sync_to_async(_setup)()
    role_set = await sync_to_async(_make_set)(s["org"], "editors", [s["editor"]])

    updated = await management_schema.execute(
        UPDATE, context_value=s["admin"], variable_values={"id": str(role_set.id), "name": "x", "roles": None}
    )
    deleted = await management_schema.execute(DELETE, context_value=s["admin"], variable_values={"id": str(role_set.id)})

    assert updated.errors and updated.errors[0].message == OWNER_ONLY
    assert deleted.errors and deleted.errors[0].message == OWNER_ONLY
    assert await sync_to_async(models.RoleSet.objects.filter(pk=role_set.pk, name="editors").exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_keeps_the_underlying_roles():
    s = await sync_to_async(_setup)()
    role_set = await sync_to_async(_make_set)(s["org"], "editors", [s["editor"]])

    result = await management_schema.execute(DELETE, context_value=s["owner"], variable_values={"id": str(role_set.id)})

    assert not result.errors, result.errors
    assert result.data["deleteRoleSet"] == str(role_set.id)
    assert not await sync_to_async(models.RoleSet.objects.filter(pk=role_set.pk).exists)()
    assert await sync_to_async(models.Role.objects.filter(pk=s["editor"].pk).exists)()

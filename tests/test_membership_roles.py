"""``updateMembership`` is the privilege-escalation surface of the management API.

It sets a member's roles, and until now nothing exercised it: a regression that
let a plain member grant themselves ``admin``, or attach a role from another
tenant, would have gone unnoticed. These tests pin the owner-or-admin bar, that
roles are *replaced* (not appended), that foreign role ids are silently dropped,
and the ``roles: []`` no-op asymmetry the resolver currently has.

Setup runs through ``sync_to_async`` (matching ``test_management_tenant_isolation``)
because the ORM is not usable from the event loop the async schema executes on.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from karakter.managers import create_role
from tests import factories
from tests.conftest import build_auth_context
from tests.test_management_tenant_isolation import _org_with_admin_and_member

UPDATE_MEMBERSHIP = """
mutation ($id: ID!, $roles: [ID!]) {
  updateMembership(input: { id: $id, roles: $roles }) {
    id
    roles { identifier }
  }
}
"""


def _setup():
    s = _org_with_admin_and_member()
    org = s["org"]
    s["editor"] = create_role(org, "editor")
    s["viewer"] = create_role(org, "viewer")
    s["admin_role"] = create_role(org, "admin")
    # Start the plain member off holding exactly one role so "replace" is observable.
    s["member_membership"].roles.set([s["viewer"]])
    return s


async def _update(context, membership, roles):
    return await management_schema.execute(
        UPDATE_MEMBERSHIP,
        context_value=context,
        variable_values={"id": str(membership.id), "roles": roles},
    )


async def _held(membership):
    return await sync_to_async(
        lambda: sorted(membership.roles.values_list("identifier", flat=True))
    )()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_cannot_grant_themselves_admin():
    s = await sync_to_async(_setup)()

    result = await _update(s["member"], s["member_membership"], [str(s["admin_role"].id)])

    assert result.errors, f"a plain member escalated to admin: {result.data}"
    assert "not allowed to manage memberships" in result.errors[0].message
    assert await _held(s["member_membership"]) == ["viewer"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_replaces_roles_rather_than_appending():
    s = await sync_to_async(_setup)()

    result = await _update(s["owner"], s["member_membership"], [str(s["editor"].id)])

    assert not result.errors, result.errors
    assert [r["identifier"] for r in result.data["updateMembership"]["roles"]] == ["editor"]
    # `roles.set` is a replacement: the viewer role the member held before is gone.
    assert await _held(s["member_membership"]) == ["editor"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_who_is_not_owner_may_also_set_roles():
    s = await sync_to_async(_setup)()

    result = await _update(s["admin"], s["member_membership"], [str(s["editor"].id)])

    assert not result.errors, result.errors
    assert await _held(s["member_membership"]) == ["editor"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_role_from_another_organization_is_dropped():
    s = await sync_to_async(_setup)()
    foreign_role = await sync_to_async(lambda: create_role(factories.make_organization(), "editor"))()

    result = await _update(
        s["owner"], s["member_membership"], [str(s["editor"].id), str(foreign_role.id)]
    )

    assert not result.errors, result.errors
    # Only the role that belongs to the membership's own organization lands.
    assert await _held(s["member_membership"]) == ["editor"]
    attached_ids = await sync_to_async(
        lambda: list(s["member_membership"].roles.values_list("id", flat=True))
    )()
    assert foreign_role.id not in attached_ids


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_empty_role_list_leaves_roles_untouched():
    """The resolver guards on ``if input.roles:``, so an empty list is a no-op rather
    than a clear. Pinned so a future "clear all roles" change is deliberate."""
    s = await sync_to_async(_setup)()

    result = await _update(s["owner"], s["member_membership"], [])

    assert not result.errors, result.errors
    assert await _held(s["member_membership"]) == ["viewer"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_outsider_gets_the_uniform_denial():
    s = await sync_to_async(_setup)()
    outsider = await sync_to_async(factories.make_membership)()
    outsider_context = await sync_to_async(
        lambda: build_auth_context(
            outsider.user, outsider.organization, factories.make_client(membership=outsider)
        )
    )()

    result = await _update(outsider_context, s["member_membership"], [str(s["editor"].id)])

    assert result.errors
    assert "not allowed to manage memberships" in result.errors[0].message
    assert await _held(s["member_membership"]) == ["viewer"]

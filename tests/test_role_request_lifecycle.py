"""The member-side half of role requests: ``requestRole`` and ``cancelRoleRequest``.

``tests/test_role_requests.py`` pins who may *list and approve* a request; the
mutations that create and withdraw one had no test at all. These pin the three
refusals in ``request_role`` (foreign role, role already held, duplicate pending
request), that a declined request unblocks a new one (the unique constraint is
partial on ``status="pending"``), that a non-member gets the uniform denial, and
that only the requester may cancel — not even the owner on their behalf.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from karakter import models
from karakter.managers import create_role
from tests import factories
from tests.test_role_requests import _context_for, _org_with_request

REQUEST = """
mutation ($org: ID!, $role: ID!) {
  requestRole(input: { organization: $org, role: $role, reason: "please" }) { id status }
}
"""
CANCEL = "mutation ($id: ID!) { cancelRoleRequest(input: { id: $id }) }"


async def _request(context, org, role):
    return await management_schema.execute(
        REQUEST, context_value=context, variable_values={"org": str(org.id), "role": str(role.id)}
    )


def _fresh_member_and_role():
    """An org with a plain member who holds nothing, and a requestable role."""
    org = factories.make_organization()
    member = factories.make_membership(organization=org)
    member.roles.clear()
    return org, member, create_role(org, "editor")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_can_request_a_role_of_their_organization():
    org, member, role = await sync_to_async(_fresh_member_and_role)()
    context = await sync_to_async(_context_for)(member)

    result = await _request(context, org, role)

    assert not result.errors, result.errors
    assert result.data["requestRole"]["status"] == "pending"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_role_of_another_organization_is_refused():
    org, member, _role = await sync_to_async(_fresh_member_and_role)()
    foreign = await sync_to_async(lambda: create_role(factories.make_organization(), "editor"))()
    context = await sync_to_async(_context_for)(member)

    result = await _request(context, org, foreign)

    assert result.errors
    assert result.errors[0].message == "That role does not belong to this organization."


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_already_held_role_is_refused():
    org, member, role = await sync_to_async(_fresh_member_and_role)()
    await sync_to_async(member.roles.add)(role)
    context = await sync_to_async(_context_for)(member)

    result = await _request(context, org, role)

    assert result.errors
    assert result.errors[0].message == "You already have this role."


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_second_pending_request_is_refused_until_the_first_is_resolved():
    org, member, role = await sync_to_async(_fresh_member_and_role)()
    context = await sync_to_async(_context_for)(member)

    first = await _request(context, org, role)
    assert not first.errors, first.errors

    second = await _request(context, org, role)
    assert second.errors
    assert second.errors[0].message == "You already have a pending request for this role."

    # Declining resolves the pending one; the partial unique constraint only
    # covers `status="pending"`, so asking again is allowed.
    def decline():
        models.RoleRequest.objects.get(pk=first.data["requestRole"]["id"]).decline(org.owner)

    await sync_to_async(decline)()
    third = await _request(context, org, role)
    assert not third.errors, third.errors
    count = await sync_to_async(models.RoleRequest.objects.filter(membership=member, role=role).count)()
    assert count == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_non_member_gets_the_uniform_denial():
    org, _member, role = await sync_to_async(_fresh_member_and_role)()
    outsider = await sync_to_async(factories.make_membership)()
    context = await sync_to_async(_context_for)(outsider)

    result = await _request(context, org, role)

    assert result.errors
    assert result.errors[0].message == DENIED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_requester_can_cancel_their_own_request():
    _org, request, requester, _admin, _bystander = await sync_to_async(_org_with_request)()
    context = await sync_to_async(_context_for)(requester)

    result = await management_schema.execute(CANCEL, context_value=context, variable_values={"id": str(request.id)})

    assert not result.errors, result.errors
    assert result.data["cancelRoleRequest"] == str(request.id)
    assert not await sync_to_async(models.RoleRequest.objects.filter(pk=request.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_and_owner_cannot_cancel_on_the_members_behalf():
    org, request, _requester, admin, _bystander = await sync_to_async(_org_with_request)()
    owner_membership = await sync_to_async(factories.make_membership)(user=org.owner, organization=org)

    for membership in (admin, owner_membership):
        context = await sync_to_async(_context_for)(membership)
        result = await management_schema.execute(CANCEL, context_value=context, variable_values={"id": str(request.id)})
        assert result.errors, f"{membership} cancelled someone else's request"
        assert result.errors[0].message == "You can only cancel your own role requests."

    assert await sync_to_async(models.RoleRequest.objects.filter(pk=request.pk).exists)()

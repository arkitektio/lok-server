"""Who may see and resolve a role request.

``approve_role_request``/``decline_role_request`` have always enforced
``is_owner_or_admin``, but ``ManagementRoleRequest.get_queryset`` used to scope
reads to *owners* only. An admin could therefore act on a request they had no way
to list — which is exactly what the admin inbox in kontrol needs to do. These
tests pin the two halves to the same bar, and pin the bar itself: an ordinary
member still sees only their own requests.

Setup runs through ``sync_to_async`` (matching ``test_management_tenant_isolation``)
because the ORM is not usable from the event loop the async schema executes on.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from karakter import models
from karakter.managers import create_role
from tests import factories
from tests.conftest import build_auth_context

ROLE_REQUESTS = """
query ($org: ID!) {
  roleRequests(filters: { organization: $org, status: "pending" }) {
    id
    role { identifier }
    membership { user { username } }
  }
}
"""


def _org_with_request():
    """An org whose owner is a *third* party, so "owner" and "admin" stay distinct.

    Returns the org, the pending request, the requesting member, an admin who
    does not own the org, and an unrelated plain member.
    """
    owner = factories.make_user()
    org = factories.make_organization(owner=owner)

    editor_role = create_role(org, "editor")
    admin_role = create_role(org, "admin")

    requester = factories.make_membership(organization=org)
    admin = factories.make_membership(organization=org)
    admin.roles.add(admin_role)
    bystander = factories.make_membership(organization=org)

    request = models.RoleRequest.objects.create(
        membership=requester, role=editor_role, reason="I want to edit"
    )
    return org, request, requester, admin, bystander


def _context_for(membership):
    client = factories.make_client(membership=membership)
    return build_auth_context(membership.user, membership.organization, client)


async def _list(context, org):
    return await management_schema.execute(
        ROLE_REQUESTS, context_value=context, variable_values={"org": str(org.id)}
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_can_list_the_orgs_role_requests():
    org, request, _requester, admin, _bystander = await sync_to_async(_org_with_request)()
    context = await sync_to_async(_context_for)(admin)

    result = await _list(context, org)

    assert not result.errors, result.errors
    assert [r["id"] for r in result.data["roleRequests"]] == [str(request.id)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_list_the_orgs_role_requests():
    org, request, _requester, _admin, _bystander = await sync_to_async(_org_with_request)()
    owner_membership = await sync_to_async(factories.make_membership)(
        user=org.owner, organization=org
    )
    context = await sync_to_async(_context_for)(owner_membership)

    result = await _list(context, org)

    assert not result.errors, result.errors
    assert [r["id"] for r in result.data["roleRequests"]] == [str(request.id)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_sees_no_one_elses_role_requests():
    org, _request, _requester, _admin, bystander = await sync_to_async(_org_with_request)()
    context = await sync_to_async(_context_for)(bystander)

    result = await _list(context, org)

    assert not result.errors, result.errors
    assert result.data["roleRequests"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_requester_still_sees_their_own_request():
    org, request, requester, _admin, _bystander = await sync_to_async(_org_with_request)()
    context = await sync_to_async(_context_for)(requester)

    result = await _list(context, org)

    assert not result.errors, result.errors
    assert [r["id"] for r in result.data["roleRequests"]] == [str(request.id)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_can_approve_and_the_role_lands_on_the_membership():
    org, request, requester, admin, _bystander = await sync_to_async(_org_with_request)()
    context = await sync_to_async(_context_for)(admin)

    result = await management_schema.execute(
        """
        mutation ($id: ID!) {
          approveRoleRequest(input: { id: $id }) { id status }
        }
        """,
        context_value=context,
        variable_values={"id": str(request.id)},
    )

    assert not result.errors, result.errors
    assert result.data["approveRoleRequest"]["status"] == "approved"
    held = await sync_to_async(
        lambda: list(requester.roles.values_list("identifier", flat=True))
    )()
    assert "editor" in held


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_cannot_approve():
    _org, request, _requester, _admin, bystander = await sync_to_async(_org_with_request)()
    context = await sync_to_async(_context_for)(bystander)

    result = await management_schema.execute(
        "mutation ($id: ID!) { approveRoleRequest(input: { id: $id }) { id status } }",
        context_value=context,
        variable_values={"id": str(request.id)},
    )

    assert result.errors, f"expected a denial, got: {result.data}"

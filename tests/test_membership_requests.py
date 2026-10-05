"""Asking to join an organization from outside it, on the main schema.

`requestMembership` is the one mutation that reaches past the organization in
the caller's token, so it must not turn into an existence oracle: every call
answers the same, whatever the organization id names.
"""

import pytest
from asgiref.sync import sync_to_async

from karakter import models
from karakter.graphql.mutations import membership_request as module
from karakter.managers import create_role
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

REQUEST = """
    mutation ($input: RequestMembershipInput!) { requestMembership(input: $input) }
"""

APPROVE = """
    mutation ($input: ApproveMembershipRequestInput!) {
        approveMembershipRequest(input: $input) { id user { id } roles { identifier } }
    }
"""

DECLINE = """
    mutation ($input: DeclineMembershipRequestInput!) {
        declineMembershipRequest(input: $input) { id status }
    }
"""

REQUESTS = """
    query ($id: ID!) { organization(id: $id) { membershipRequests { id status reason user { id } } } }
"""

MEMBER_OF = """
    query ($organization: ID!) { mycontext { memberOf(organization: $organization) } }
"""


@pytest.fixture(autouse=True)
def quiet_notifications(monkeypatch):
    """No pushes from tests; record who would have been told instead."""
    sent = []
    monkeypatch.setattr(module, "_notify", lambda recipients, title, message: sent.append((list(recipients), title)))
    return sent


def _context(membership):
    return build_auth_context(membership.user, membership.organization, factories.make_client(membership=membership))


def _setup():
    """An outsider signed into their own organization, and a target organization
    with its owner, an admin and a plain member."""
    outsider = factories.make_membership()
    target = factories.make_organization()
    create_role(organization=target, identifier="guest")
    owner = factories.make_membership(user=target.owner, organization=target)
    admin = factories.make_membership(organization=target)
    admin.roles.add(create_role(organization=target, identifier="admin"))
    member = factories.make_membership(organization=target)
    return {
        "outsider": outsider,
        "target": target,
        "outsider_context": _context(outsider),
        "owner_context": _context(owner),
        "admin_context": _context(admin),
        "member_context": _context(member),
        "member": member,
    }


async def _ask(context, organization_id, reason=None):
    return await schema.execute(
        REQUEST, context_value=context, variable_values={"input": {"organization": str(organization_id), "reason": reason}}
    )


def _rows(**lookup):
    return list(models.MembershipRequest.objects.filter(**lookup))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_request_is_stored_and_approval_creates_the_membership(quiet_notifications):
    s = await sync_to_async(_setup)()

    result = await _ask(s["outsider_context"], s["target"].id, "I was sent a link")
    assert not result.errors, result.errors
    assert result.data == {"requestMembership": True}

    rows = await sync_to_async(_rows)(organization=s["target"])
    assert [(r.user_id, r.status, r.reason) for r in rows] == [(s["outsider"].user_id, "pending", "I was sent a link")]
    # The owner and the admin are told, the plain member is not.
    assert len(quiet_notifications) == 1 and len(quiet_notifications[0][0]) == 2

    listed = await schema.execute(REQUESTS, context_value=s["admin_context"], variable_values={"id": str(s["target"].id)})
    assert not listed.errors, listed.errors
    assert [r["id"] for r in listed.data["organization"]["membershipRequests"]] == [str(rows[0].id)]

    approved = await schema.execute(
        APPROVE, context_value=s["admin_context"], variable_values={"input": {"id": str(rows[0].id)}}
    )
    assert not approved.errors, approved.errors
    assert approved.data["approveMembershipRequest"]["user"]["id"] == str(s["outsider"].user_id)
    assert [r["identifier"] for r in approved.data["approveMembershipRequest"]["roles"]] == ["guest"]

    fresh = await sync_to_async(lambda: models.MembershipRequest.objects.get(pk=rows[0].pk))()
    assert fresh.status == "approved" and fresh.created_membership_id is not None

    asked = await schema.execute(
        MEMBER_OF, context_value=s["outsider_context"], variable_values={"organization": str(s["target"].id)}
    )
    assert asked.data["mycontext"]["memberOf"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_approval_assigns_the_named_roles():
    s = await sync_to_async(_setup)()
    await _ask(s["outsider_context"], s["target"].id)
    row = (await sync_to_async(_rows)(organization=s["target"]))[0]

    approved = await schema.execute(
        APPROVE,
        context_value=s["owner_context"],
        variable_values={"input": {"id": str(row.id), "roles": ["admin", "notarole"]}},
    )
    assert not approved.errors, approved.errors
    assert [r["identifier"] for r in approved.data["approveMembershipRequest"]["roles"]] == ["admin"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_request_answers_the_same():
    """Real, missing, malformed, already a member, asked twice: one answer."""
    s = await sync_to_async(_setup)()
    context = s["outsider_context"]

    answers = [
        await _ask(context, s["target"].id),
        await _ask(context, s["target"].id),  # duplicate
        await _ask(context, 999999),  # no such organization
        await _ask(context, "not-an-id"),
        await _ask(context, s["outsider"].organization_id),  # already a member
    ]
    for answer in answers:
        assert not answer.errors, answer.errors
        assert answer.data == {"requestMembership": True}

    rows = await sync_to_async(_rows)()
    assert [(r.user_id, r.organization_id) for r in rows] == [(s["outsider"].user_id, s["target"].id)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_declined_request_is_not_repeated_right_away(quiet_notifications):
    s = await sync_to_async(_setup)()
    await _ask(s["outsider_context"], s["target"].id)
    row = (await sync_to_async(_rows)(organization=s["target"]))[0]

    declined = await schema.execute(DECLINE, context_value=s["admin_context"], variable_values={"input": {"id": str(row.id)}})
    assert not declined.errors, declined.errors
    assert declined.data["declineMembershipRequest"]["status"] == "declined"

    again = await _ask(s["outsider_context"], s["target"].id)
    assert again.data == {"requestMembership": True}
    assert len(await sync_to_async(_rows)(organization=s["target"])) == 1
    assert len(quiet_notifications) == 1

    # Answering twice is refused.
    twice = await schema.execute(APPROVE, context_value=s["admin_context"], variable_values={"input": {"id": str(row.id)}})
    assert twice.errors and "already been declined" in twice.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_owner_and_admins_see_and_answer_requests():
    s = await sync_to_async(_setup)()
    await _ask(s["outsider_context"], s["target"].id)
    row = (await sync_to_async(_rows)(organization=s["target"]))[0]

    # A plain member of the organization sees none and may not answer.
    listed = await schema.execute(REQUESTS, context_value=s["member_context"], variable_values={"id": str(s["target"].id)})
    assert not listed.errors, listed.errors
    assert listed.data["organization"]["membershipRequests"] == []

    for context in (s["member_context"], s["outsider_context"]):
        for document in (APPROVE, DECLINE):
            result = await schema.execute(document, context_value=context, variable_values={"input": {"id": str(row.id)}})
            assert result.errors and "not authorized" in result.errors[0].message

    fresh = await sync_to_async(lambda: models.MembershipRequest.objects.get(pk=row.pk))()
    assert fresh.status == "pending"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_of_says_nothing_about_other_organizations():
    s = await sync_to_async(_setup)()

    async def member_of(organization):
        result = await schema.execute(
            MEMBER_OF, context_value=s["outsider_context"], variable_values={"organization": str(organization)}
        )
        assert not result.errors, result.errors
        return result.data["mycontext"]["memberOf"]

    assert await member_of(s["outsider"].organization_id) is True
    assert await member_of(s["target"].id) is False
    assert await member_of(999999) is False
    assert await member_of("not-an-id") is False

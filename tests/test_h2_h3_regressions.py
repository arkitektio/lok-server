"""Regressions for two High findings of the 2026-08-21 security review.

H2 — invite tokens are bearer credentials; an ordinary member must not read them.
H3 — a comment's `parent` must be scoped, or it becomes a cross-tenant read.
"""

import pytest
from asgiref.sync import sync_to_async

from karakter.models import Invite, Role
from komment.models import Comment
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


LIST_INVITE_TOKENS = "query { invites { id token } }"

CREATE_COMMENT = """
    mutation ($input: CreateCommentInput!) {
        createComment(input: $input) { id }
    }
"""

READ_PARENT = """
    query ($id: ID!) {
        comment(id: $id) { id parent { id object user { username email } } }
    }
"""


# --------------------------------------------------------------------------- #
# H2
# --------------------------------------------------------------------------- #


def _member_and_admin_invite():
    """A plain member of an org that has a pending admin-bearing invite."""
    owner_membership = factories.make_membership()
    org = owner_membership.organization

    plain = factories.make_membership(organization=org)
    plain_client = factories.make_client(membership=plain)
    plain_context = build_auth_context(plain.user, org, plain_client, roles=("guest",))

    admin_role, _ = Role.objects.get_or_create(identifier="admin", organization=org)
    invite = Invite.objects.create(created_by=org.owner, created_for=org)
    invite.roles.set([admin_role])

    return plain_context, invite, org


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_cannot_read_invite_tokens_of_own_org():
    """The finding: scoping to the active organization alone let any member —
    `guest` included — list the pending tokens of their own tenant and redeem
    one carrying `admin`.
    """
    context, invite, _org = await sync_to_async(_member_and_admin_invite)()

    result = await schema.execute(LIST_INVITE_TOKENS, context_value=context)

    assert not result.errors, result.errors
    returned = {row["id"] for row in result.data["invites"]}
    assert str(invite.id) not in returned, "an ordinary member read an admin invite token"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_still_read_invite_tokens():
    """The fix must not break the people who legitimately manage invites."""

    def _setup():
        owner_membership = factories.make_membership()
        org = owner_membership.organization
        # `make_membership` does not necessarily create the owner's row.
        owner_m = factories.make_membership(user=org.owner, organization=org)
        client = factories.make_client(membership=owner_m)
        context = build_auth_context(org.owner, org, client)
        invite = Invite.objects.create(created_by=org.owner, created_for=org)
        return context, invite

    context, invite = await sync_to_async(_setup)()

    result = await schema.execute(LIST_INVITE_TOKENS, context_value=context)

    assert not result.errors, result.errors
    returned = {row["id"] for row in result.data["invites"]}
    assert str(invite.id) in returned, "the org owner lost access to their own invites"


# --------------------------------------------------------------------------- #
# H3
# --------------------------------------------------------------------------- #


def _two_tenants_with_a_victim_comment():
    mine = factories.make_membership()
    my_client = factories.make_client(membership=mine)
    my_context = build_auth_context(mine.user, mine.organization, my_client)

    theirs = factories.make_membership()
    victim_comment = Comment.objects.create(
        identifier="@service/thing",
        object="1",
        user=theirs.user,
        text="",
        descendants=[],
    )
    return my_context, victim_comment


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_thread_a_comment_onto_another_tenants_comment():
    """The finding: `parent_id=input.parent` was written straight to the FK, and
    the `parent` hop is a forward FK that does not re-apply `get_queryset` — so
    replying to a guessed pk read back that comment and its author's email.
    """
    context, victim_comment = await sync_to_async(_two_tenants_with_a_victim_comment)()

    result = await schema.execute(
        CREATE_COMMENT,
        context_value=context,
        variable_values={
            "input": {
                "identifier": "@service/thing",
                "object": "1",
                "descendants": [{"kind": "PARAGRAPH", "children": []}],
                "parent": str(victim_comment.id),
            }
        },
    )

    assert result.errors, "attaching to another tenant's comment was allowed"

    still_childless = await sync_to_async(
        Comment.objects.filter(parent=victim_comment).exists
    )()
    assert not still_childless, "a foreign reply was persisted onto the victim's thread"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_can_still_reply_to_own_comment():
    def _setup():
        mine = factories.make_membership()
        my_client = factories.make_client(membership=mine)
        context = build_auth_context(mine.user, mine.organization, my_client)
        own = Comment.objects.create(
            identifier="@service/thing", object="1", user=mine.user, text="", descendants=[]
        )
        return context, own

    context, own = await sync_to_async(_setup)()

    result = await schema.execute(
        CREATE_COMMENT,
        context_value=context,
        variable_values={
            "input": {
                "identifier": "@service/thing",
                "object": "1",
                "descendants": [{"kind": "PARAGRAPH", "children": []}],
                "parent": str(own.id),
            }
        },
    )

    assert not result.errors, result.errors

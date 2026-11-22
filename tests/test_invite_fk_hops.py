"""An invite reached through a foreign key (`Membership.createdThrough`,
`CollaborationGrant.grantedThrough`) is shown by the same policy as the invite
list: its organization's owner and admins, and whoever created it. A collaboration
invite's token stays redeemable until `max_uses`, so every member reading it off a
membership could hand the link to an outsider."""

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from api.management.schema import schema as management_schema
from karakter import models
from tests import factories
from tests.conftest import build_auth_context

HOPS = """
query {
  memberships {
    createdThrough { token }
    collaborationGrants { grantedThrough { token } }
  }
}
"""


def _setup(caller_is_owner: bool) -> tuple[HttpContext, str]:
    organization = factories.make_organization()
    factories.make_membership(user=organization.owner, organization=organization)
    instance = factories.make_service_instance(
        hub=factories.make_hub(organization=organization)
    )
    invite = models.Invite.objects.create(
        created_by=organization.owner,
        created_for=organization,
        shared_collaborations=[
            {"instance": instance.pk, "collaboration": "12", "roles": []}
        ],
    )
    guest = factories.make_membership(organization=organization, created_through=invite)
    models.CollaborationGrant.objects.create(
        membership=guest,
        instance=instance,
        collaboration_id="12",
        granted_by=organization.owner,
        granted_through=invite,
    )

    if caller_is_owner:
        caller = organization.owner
    else:
        caller = factories.make_membership(organization=organization).user
    membership = organization.memberships.get(user=caller)
    context = build_auth_context(
        caller, organization, factories.make_client(membership=membership)
    )
    return context, str(invite.token)


def _tokens(data: dict[str, Any] | None) -> set[str]:
    assert data is not None
    found: set[str] = set()
    for membership in data["memberships"]:
        if membership["createdThrough"]:
            found.add(membership["createdThrough"]["token"])
        for grant in membership["collaborationGrants"]:
            if grant["grantedThrough"]:
                found.add(grant["grantedThrough"]["token"])
    return found


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_reads_no_invite_token_through_a_membership() -> None:
    context, token = await sync_to_async(_setup)(caller_is_owner=False)

    result = await management_schema.execute(HOPS, context_value=context)

    assert result.errors is None, result.errors
    assert token not in _tokens(result.data)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_owner_still_sees_which_invite_created_a_membership() -> None:
    context, token = await sync_to_async(_setup)(caller_is_owner=True)

    result = await management_schema.execute(HOPS, context_value=context)

    assert result.errors is None, result.errors
    assert token in _tokens(result.data)

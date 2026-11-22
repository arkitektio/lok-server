"""A guest (a member only granted some collaborations) sees on the main schema what
the management schema shows them: themselves, their own membership and the hubs
their collaborations run on. Not the organization's member directory, devices or
clients — a guest's app token used to list all of those."""

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from karakter import models
from lok_server.schema import schema as main_schema
from tests import factories
from tests.conftest import build_auth_context

DIRECTORY = """
query {
  users { id email }
  organizations { users { id } memberships { user { id } } }
  hubs { id }
  devices { id }
  clients { id }
}
"""


def _setup(guest: bool) -> tuple[HttpContext, dict[str, str]]:
    organization = factories.make_organization()
    factories.make_membership(user=organization.owner, organization=organization)
    granted_hub = factories.make_hub(organization=organization)
    other_hub = factories.make_hub(organization=organization)
    instance = factories.make_service_instance(hub=granted_hub)
    colleague = factories.make_membership(organization=organization)
    colleague_client = factories.make_client(membership=colleague)

    caller = factories.make_membership(organization=organization)
    if guest:
        models.CollaborationGrant.objects.create(
            membership=caller,
            instance=instance,
            collaboration_id="12",
            granted_by=organization.owner,
        )
    context = build_auth_context(
        caller.user,
        organization,
        factories.make_client(membership=caller),
        roles=(),
        scope="openid lok_all",
    )
    return context, {
        "self": str(caller.user.id),
        "colleague": str(colleague.user.id),
        "granted_hub": str(granted_hub.id),
        "other_hub": str(other_hub.id),
        "colleague_client": str(colleague_client.id),
    }


async def _run(guest: bool) -> tuple[dict[str, Any], dict[str, str]]:
    context, ids = await sync_to_async(_setup)(guest)
    result = await main_schema.execute(DIRECTORY, context_value=context)
    assert result.errors is None, result.errors
    assert result.data is not None
    return result.data, ids


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_sees_themselves_and_their_hubs_only() -> None:
    data, ids = await _run(guest=True)

    assert [u["id"] for u in data["users"]] == [ids["self"]]
    (org,) = data["organizations"]
    assert [u["id"] for u in org["users"]] == [ids["self"]]
    assert [m["user"]["id"] for m in org["memberships"]] == [ids["self"]]
    assert [h["id"] for h in data["hubs"]] == [ids["granted_hub"]]
    assert data["devices"] == []
    assert data["clients"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_full_member_still_sees_the_organization() -> None:
    data, ids = await _run(guest=False)

    user_ids = {u["id"] for u in data["users"]}
    assert {ids["self"], ids["colleague"]} <= user_ids
    (org,) = data["organizations"]
    assert ids["colleague"] in {m["user"]["id"] for m in org["memberships"]}
    assert {ids["granted_hub"], ids["other_hub"]} <= {h["id"] for h in data["hubs"]}
    assert ids["colleague_client"] in {c["id"] for c in data["clients"]}

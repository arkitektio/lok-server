"""A guest (a member only granted some collaborations) sees on the main schema what
the management schema shows them: themselves, their own membership and the
organization's hubs (a collaboration spans all of its services). Not the
organization's member directory, devices or clients — a guest's app token used to
list all of those."""

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
    hub = factories.make_hub(organization=organization)
    other_hub = factories.make_hub(organization=organization)
    foreign_hub = factories.make_hub()  # another organization's
    colleague = factories.make_membership(organization=organization)
    colleague_client = factories.make_client(membership=colleague)

    caller = factories.make_membership(organization=organization)
    if guest:
        models.CollaborationGrant.objects.create(
            membership=caller,
            collaboration=models.Collaboration.objects.create(
                organization=organization, name="Plates"
            ),
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
        "hub": str(hub.id),
        "other_hub": str(other_hub.id),
        "foreign_hub": str(foreign_hub.id),
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
async def test_a_guest_sees_themselves_and_the_organizations_hubs_only() -> None:
    data, ids = await _run(guest=True)

    assert [u["id"] for u in data["users"]] == [ids["self"]]
    (org,) = data["organizations"]
    assert [u["id"] for u in org["users"]] == [ids["self"]]
    assert [m["user"]["id"] for m in org["memberships"]] == [ids["self"]]
    assert sorted(h["id"] for h in data["hubs"]) == sorted(
        [ids["hub"], ids["other_hub"]]
    )
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
    assert {ids["hub"], ids["other_hub"]} <= {h["id"] for h in data["hubs"]}
    assert ids["foreign_hub"] not in {h["id"] for h in data["hubs"]}
    assert ids["colleague_client"] in {c["id"] for c in data["clients"]}

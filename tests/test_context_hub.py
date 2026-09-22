"""``mycontext.hub`` on the main schema.

An app that is linked to several hubs needs to know which one it was approved
into so it can hand the id back as ``?hub=`` on a later configure link. The
context query already carries user, organization, roles and scope; this pins
that it also names the client's hub, and stays null for a client without one.
"""

import pytest
from asgiref.sync import sync_to_async

from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

QUERY = "query { mycontext { hub { id name identifier organization { id } } organization { id } } }"


async def _run(context):
    return await schema.execute(QUERY, context_value=context)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_context_names_the_app_clients_hub():
    def setup():
        membership = factories.make_membership()
        hub = factories.make_hub(organization=membership.organization)
        client = factories.make_client(membership=membership, hub=hub)
        return build_auth_context(membership.user, membership.organization, client), hub

    context, hub = await sync_to_async(setup)()

    result = await _run(context)

    assert not result.errors, result.errors
    got = result.data["mycontext"]["hub"]
    assert got["id"] == str(hub.id)
    assert got["identifier"] == hub.identifier
    assert got["organization"]["id"] == str(hub.organization_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_context_hub_is_null_for_a_client_without_one():
    def setup():
        membership = factories.make_membership()
        client = factories.make_client(membership=membership)
        return build_auth_context(membership.user, membership.organization, client)

    context = await sync_to_async(setup)()

    result = await _run(context)

    assert not result.errors, result.errors
    assert result.data["mycontext"]["hub"] is None

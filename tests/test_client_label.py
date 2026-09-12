"""The fakts (non-management) schema exposes a comprehensive `Client.name` label.

Querying a client through `lok_server.schema` should fold the app identifier,
release version, operating user and device into a single human-readable string
(e.g. `com.example.app:v0.1.1 by Johannes on my-laptop`) rather than the raw
stored client name.
"""

import pytest
from asgiref.sync import sync_to_async

from lok_server.schema import schema
from fakts import models as fmodels
from tests import factories
from tests.conftest import build_auth_context

CLIENTS_QUERY = "query { clients { id name } }"


def _setup():
    """Sync DB setup (must not run inside the async event loop)."""
    user = factories.make_user(username="jhnnsrs", first_name="Johannes", last_name="")
    membership = factories.make_membership(user=user)
    app = factories.make_app(identifier="com.example.app")
    release = factories.make_release(app=app, version="0.1.1")
    device = fmodels.Device.objects.create(
        node_id="node-1", name="my-laptop", organization=membership.organization
    )
    client = factories.make_client(membership=membership, release=release, node=device)
    return user, membership.organization, client


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_name_is_comprehensive_label():
    user, organization, client = await sync_to_async(_setup)()

    result = await schema.execute(
        CLIENTS_QUERY,
        context_value=build_auth_context(user, organization, client),
    )

    assert result.errors is None, result.errors
    names = {row["id"]: row["name"] for row in result.data["clients"]}
    assert names[str(client.id)] == "com.example.app:v0.1.1 by Johannes on my-laptop"


def _setup_no_device():
    user = factories.make_user(username="alice", first_name="Alice", last_name="Smith")
    membership = factories.make_membership(user=user)
    app = factories.make_app(identifier="com.example.tool")
    release = factories.make_release(app=app, version="2.3.0")
    client = factories.make_client(membership=membership, release=release)  # node stays null
    return user, membership.organization, client


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_name_omits_device_when_absent():
    """Without a device the label drops the `on <device>` suffix, and a full name
    (first + last) is used for the operator."""
    user, organization, client = await sync_to_async(_setup_no_device)()

    result = await schema.execute(
        CLIENTS_QUERY,
        context_value=build_auth_context(user, organization, client),
    )

    assert result.errors is None, result.errors
    names = {row["id"]: row["name"] for row in result.data["clients"]}
    assert names[str(client.id)] == "com.example.tool:v2.3.0 by Alice Smith"

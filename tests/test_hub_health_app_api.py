"""A hub's last reported health on the main (app-facing) schema.

An app reads the health of the hub it was approved into via
``mycontext { hub { ... } }``, or of any hub of its organization via
``hub(id:)`` / ``hubs``. Other organizations' hubs stay invisible.
"""

import pytest
from asgiref.sync import sync_to_async

from fakts.base_models import HubHealthReport
from fakts.services.hubs import report_hub_health
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

HEALTH = """
  online lastSeenAt lastHealthy version meshConnected meshHost
  latestHealth { healthy createdAt instances { instance { id } healthy reason } }
"""


def _setup(report: bool = True):
    membership = factories.make_membership()
    hub = factories.make_hub(organization=membership.organization)
    instance = factories.make_service_instance(hub=hub)
    client = factories.make_client(membership=membership, hub=hub)
    if report:
        report_hub_health(
            hub,
            HubHealthReport(
                healthy=False,
                version="1.2.3",
                instances={instance.token: {"healthy": False, "reason": "db down"}, "not-mine": {"healthy": True}},
                mesh={"connected": True, "hostname": "hub.org.mesh.example"},
            ),
        )
    return build_auth_context(membership.user, membership.organization, client), hub, instance


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_app_reads_its_hubs_last_reported_health():
    context, hub, instance = await sync_to_async(_setup)()

    result = await schema.execute(f"query {{ mycontext {{ hub {{ {HEALTH} }} }} }}", context_value=context)

    assert not result.errors, result.errors
    got = result.data["mycontext"]["hub"]
    assert got["online"] is True
    assert got["lastHealthy"] is False
    assert got["version"] == "1.2.3"
    assert got["meshConnected"] is True and got["meshHost"] == "hub.org.mesh.example"
    assert got["latestHealth"]["healthy"] is False
    # Keyed back to the instance object; entries for foreign instances are dropped.
    assert got["latestHealth"]["instances"] == [{"instance": {"id": str(instance.id)}, "healthy": False, "reason": "db down"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_hub_by_id_and_list_are_org_scoped():
    context, hub, _ = await sync_to_async(_setup)()
    foreign = await sync_to_async(factories.make_hub)()

    by_id = await schema.execute('query($id: ID!) { hub(id: $id) { id online } }', variable_values={"id": str(hub.id)}, context_value=context)
    assert not by_id.errors, by_id.errors
    assert by_id.data["hub"] == {"id": str(hub.id), "online": True}

    denied = await schema.execute('query($id: ID!) { hub(id: $id) { id } }', variable_values={"id": str(foreign.id)}, context_value=context)
    assert denied.errors

    listed = await schema.execute("query { hubs { id } }", context_value=context)
    assert not listed.errors, listed.errors
    assert [h["id"] for h in listed.data["hubs"]] == [str(hub.id)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_hub_that_never_reported():
    context, _, _ = await sync_to_async(_setup)(report=False)

    result = await schema.execute(f"query {{ mycontext {{ hub {{ {HEALTH} }} }} }}", context_value=context)

    assert not result.errors, result.errors
    got = result.data["mycontext"]["hub"]
    assert got["online"] is False and got["lastSeenAt"] is None and got["lastHealthy"] is None
    assert got["latestHealth"] is None

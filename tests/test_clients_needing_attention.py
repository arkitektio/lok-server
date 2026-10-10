"""``clients(filters: {needsAttention: true})`` — the dashboard's action list.

A client only needs attention when a service it was granted (a
``ServiceInstanceMapping`` exists, so the service is on the platform and the
client is entitled to it) is not reachable for it. A client that reports itself
non-functional because it asks for something nobody deployed is ordinary.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from fakts import models
from tests import factories
from tests.conftest import build_auth_context

CLIENTS = """
    query ($filters: ManagementClientFilter) { clients(filters: $filters) { id } }
"""


def _setup():
    organization = factories.make_organization()
    owner = factories.make_membership(user=organization.owner, organization=organization)
    hub = factories.make_hub(organization=organization)
    instance = factories.make_service_instance(hub=hub)

    def client(functional, mapped, reports):
        c = factories.make_client(membership=owner)
        c.functional = functional
        c.save(update_fields=["functional"])
        for key in mapped:
            models.ServiceInstanceMapping.objects.create(client=c, instance=instance, key=key)
        for key, valid in reports.items():
            models.UsedAlias.objects.create(client=c, key=key, valid=valid)
        return c

    clients = {
        # Granted, on the platform, and unreachable: the one real fault.
        "unreachable": client(False, ["mikro"], {"mikro": False}),
        # Asks for a service nobody mapped: says it is broken, but that is normal.
        "ungranted": client(False, [], {"kraph": False}),
        # One granted service is fine, the unreachable one was never granted.
        "mixed": client(False, ["mikro"], {"mikro": True, "kraph": False}),
        "healthy": client(True, ["mikro"], {"mikro": True}),
        "silent": client(True, ["mikro"], {}),
    }
    context = build_auth_context(organization.owner, organization, factories.make_client(membership=owner))
    return organization, clients, context


async def _ids(context, filters):
    result = await management_schema.execute(CLIENTS, variable_values={"filters": filters}, context_value=context)
    assert not result.errors, result.errors
    return {row["id"] for row in result.data["clients"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_a_granted_and_unreachable_service_needs_attention():
    organization, clients, context = await sync_to_async(_setup)()
    scope = {"organization": str(organization.id)}

    needing = await _ids(context, {**scope, "needsAttention": True})
    assert needing == {str(clients["unreachable"].id)}

    fine = await _ids(context, {**scope, "needsAttention": False})
    assert {str(clients[k].id) for k in ("ungranted", "mixed", "healthy", "silent")} <= fine
    assert str(clients["unreachable"].id) not in fine

    # The old signal was broader: every client that calls itself non-functional.
    broken = await _ids(context, {**scope, "functional": False})
    assert {str(clients[k].id) for k in ("unreachable", "ungranted", "mixed")} <= broken


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_triaged_client_leaves_the_action_list():
    organization, clients, context = await sync_to_async(_setup)()
    filters = {"organization": str(organization.id), "needsAttention": True, "latestReportResolved": False}

    assert await _ids(context, filters) == {str(clients["unreachable"].id)}

    await sync_to_async(
        lambda: models.Client.objects.filter(pk=clients["unreachable"].pk).update(latest_report_resolved=True)
    )()
    assert await _ids(context, filters) == set()

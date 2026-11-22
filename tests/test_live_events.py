"""Subscriptions that replace the client's refresh-on-focus and polling:
`myMessages`, `membershipRequests` and `hubHealth`.

Each subscriber gets its own consumer on the in-memory channel layer, with a
pump feeding the layer's messages into it, as the websocket handler would.
"""

import asyncio
import contextlib

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from kante.context import TemporalResponse, UniversalRequest, WsContext

from fakts.base_models import HubHealthReport
from fakts.services.hubs import report_hub_health
from karakter import models
from karakter.managers import create_role
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@pytest_asyncio.fixture
async def ws_context():
    """``ws_context(membership)`` → a websocket context authenticated as that member."""
    from channels.layers import get_channel_layer
    from strawberry.channels import ChannelsConsumer

    layer = get_channel_layer()
    await layer.flush()
    pumps = []

    async def make(membership) -> WsContext:
        def authenticate():
            return build_auth_context(membership.user, membership.organization, factories.make_client(membership=membership))

        http = await sync_to_async(authenticate)()
        token = http.headers["Authorization"].removeprefix("Bearer ")
        consumer = ChannelsConsumer()
        consumer.channel_layer = layer
        consumer.channel_name = await layer.new_channel()

        async def pump():
            while True:
                await consumer.dispatch(await layer.receive(consumer.channel_name))

        pumps.append(asyncio.create_task(pump()))
        return WsContext(
            request=UniversalRequest(_extensions={"token": token}),
            response=TemporalResponse(),
            connection_params={"token": token},
            consumer=consumer,
        )

    yield make

    for task in pumps:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class Listener:
    """Drains a subscription in the background, so the resolver joins its group
    right away (a subscription's resolver only runs once someone iterates it)."""

    def __init__(self, generator):
        self.generator = generator
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        async for result in self.generator:
            await self.queue.put(result)

    async def next(self, timeout: float = 5.0):
        result = await asyncio.wait_for(self.queue.get(), timeout)
        assert not result.errors, result.errors
        return result.data

    async def nothing(self, timeout: float = 0.5) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self.queue.get(), timeout)

    async def close(self) -> None:
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


async def _subscribe(context, document, **variables) -> Listener:
    generator = await schema.subscribe(document, context_value=context, variable_values=variables)
    listener = Listener(generator)
    # Let the resolver join its group before anything is broadcast.
    await asyncio.sleep(0.3)
    return listener


MY_MESSAGES = "subscription { myMessages { create { id title } update { id } delete } }"


async def test_my_messages_tells_the_owner_and_nobody_else(ws_context):
    me = await sync_to_async(factories.make_membership)()
    someone = await sync_to_async(factories.make_membership)(organization=me.organization)
    mine = await _subscribe(await ws_context(me), MY_MESSAGES)
    theirs = await _subscribe(await ws_context(someone), MY_MESSAGES)

    message = await models.SystemMessage.objects.acreate(user=me.user, title="Hub down", message="", action="")
    assert (await mine.next())["myMessages"] == {"create": {"id": str(message.pk), "title": "Hub down"}, "update": None, "delete": None}

    message.acknowledged = True
    await message.asave()
    assert (await mine.next())["myMessages"]["update"] == {"id": str(message.pk)}

    message_id = message.pk
    await message.adelete()
    assert (await mine.next())["myMessages"]["delete"] == str(message_id)

    await theirs.nothing()
    await mine.close()
    await theirs.close()


MEMBERSHIP_REQUESTS = "subscription { membershipRequests { create { id status } update { id status } } }"


async def test_membership_requests_reach_the_admins_only(ws_context):
    def setup():
        owner = factories.make_user()
        organization = factories.make_organization(owner=owner)
        create_role(organization=organization, identifier="guest")
        return factories.make_membership(user=owner, organization=organization), factories.make_membership(organization=organization)

    owner, member = await sync_to_async(setup)()
    admins = await _subscribe(await ws_context(owner), MEMBERSHIP_REQUESTS)
    members = await _subscribe(await ws_context(member), MEMBERSHIP_REQUESTS)

    outsider = await sync_to_async(factories.make_user)()
    request = await models.MembershipRequest.objects.acreate(user=outsider, organization=owner.organization, reason="please")
    assert (await admins.next())["membershipRequests"]["create"] == {"id": str(request.pk), "status": "pending"}

    await sync_to_async(request.decline)(owner.user)
    assert (await admins.next())["membershipRequests"]["update"] == {"id": str(request.pk), "status": "declined"}

    await members.nothing()
    await admins.close()
    await members.close()


HUB_HEALTH = "subscription ($hub: ID) { hubHealth(hub: $hub) { id lastHealthy online offlineAt } }"


async def test_hub_health_reports_the_organizations_hubs(ws_context):
    def setup():
        membership = factories.make_membership()
        watched = factories.make_hub(organization=membership.organization)
        other = factories.make_hub(organization=membership.organization)
        foreign = factories.make_hub()
        return membership, watched, other, foreign

    membership, watched, other, foreign = await sync_to_async(setup)()
    every = await _subscribe(await ws_context(membership), HUB_HEALTH)
    one = await _subscribe(await ws_context(membership), HUB_HEALTH, hub=str(watched.pk))

    await sync_to_async(report_hub_health)(foreign, HubHealthReport(healthy=True))
    await sync_to_async(report_hub_health)(other, HubHealthReport(healthy=True))
    await sync_to_async(report_hub_health)(watched, HubHealthReport(healthy=False))

    first = (await every.next())["hubHealth"]
    assert first["id"] == str(other.pk) and first["online"] is True and first["offlineAt"]
    assert (await every.next())["hubHealth"]["id"] == str(watched.pk)
    assert (await one.next())["hubHealth"] == {**(await _hub_row(watched)), "id": str(watched.pk)}
    await every.nothing()
    await one.nothing()
    await every.close()
    await one.close()


async def _hub_row(hub):
    await hub.arefresh_from_db()
    interval = getattr(django_settings, "HUB_HEALTH_INTERVAL", 60)
    import datetime

    offline_at = hub.last_seen_at + datetime.timedelta(seconds=3 * interval)
    return {"lastHealthy": False, "online": True, "offlineAt": offline_at.isoformat()}

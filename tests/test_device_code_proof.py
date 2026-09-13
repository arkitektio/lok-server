"""Proof-of-possession on the device-code mutations.

Device-code primary keys are sequential, so without the displayed ``code`` any
authenticated account could walk the ids and accept or deny every pending
enrolment. ``resolve_device_code_with_proof`` closes that, but nothing exercised
the decline mutations or its edge cases. These pin: an omitted code (the input
still declares it nullable) is denied, a wrong code / unknown id / malformed id
all yield the *same* denial (no existence oracle), ``kind`` narrowing between the
app and hub variants, and the expired/already-denied guards on ``acceptDeviceCode``
that stop a stale acceptance from knocking out a working client.
"""

from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from fakts import models as fakts_models
from tests import factories
from tests.conftest import build_auth_context
from tests.test_management_tenant_isolation import _staged_hub_code

DECLINE = "mutation ($input: DeclineDeviceCodeInput!) { declineDeviceCode(input: $input) { id denied } }"
DECLINE_HUB = "mutation ($input: DeclineHubDeviceCodeInput!) { declineHubDeviceCode(input: $input) { id denied } }"
ACCEPT = "mutation ($input: AcceptDeviceCodeInput!) { acceptDeviceCode(input: $input) { id } }"


def _member_context():
    membership = factories.make_membership()
    return membership, build_auth_context(
        membership.user, membership.organization, factories.make_client(membership=membership)
    )


async def _decline(context, query=DECLINE, **input):
    return await management_schema.execute(query, context_value=context, variable_values={"input": input})


async def _denied_flag(device_code):
    return await sync_to_async(lambda: fakts_models.DeviceCode.objects.get(pk=device_code.pk).denied)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_holder_of_the_code_can_decline():
    _membership, context = await sync_to_async(_member_context)()
    device_code = await sync_to_async(factories.make_device_code)()

    result = await _decline(context, deviceCode=str(device_code.id), code=device_code.code)

    assert not result.errors, result.errors
    assert result.data["declineDeviceCode"]["denied"] is True
    assert await _denied_flag(device_code) is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_omitting_the_code_is_denied_even_though_the_schema_allows_it():
    _membership, context = await sync_to_async(_member_context)()
    device_code = await sync_to_async(factories.make_device_code)()

    result = await _decline(context, deviceCode=str(device_code.id))

    assert result.errors, f"declined without proof: {result.data}"
    assert result.errors[0].message == DENIED
    assert await _denied_flag(device_code) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_wrong_code_unknown_id_and_malformed_id_are_indistinguishable():
    _membership, context = await sync_to_async(_member_context)()
    device_code = await sync_to_async(factories.make_device_code)()

    wrong = await _decline(context, deviceCode=str(device_code.id), code="not-the-code")
    unknown = await _decline(context, deviceCode="999999999", code=device_code.code)
    malformed = await _decline(context, deviceCode="not-an-id", code=device_code.code)

    messages = {r.errors[0].message for r in (wrong, unknown, malformed) if r.errors}
    assert messages == {DENIED}, (wrong.errors, unknown.errors, malformed.errors)
    assert await _denied_flag(device_code) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_hub_decline_does_not_reach_an_app_code_and_vice_versa():
    _membership, context = await sync_to_async(_member_context)()
    app_code = await sync_to_async(factories.make_device_code)()
    hub_code = await sync_to_async(_staged_hub_code)("proof-hub")

    crossed_hub = await _decline(context, DECLINE_HUB, deviceCode=str(app_code.id), code=app_code.code)
    crossed_app = await _decline(context, DECLINE, deviceCode=str(hub_code.id), code=hub_code.code)

    assert crossed_hub.errors and crossed_hub.errors[0].message == DENIED
    assert crossed_app.errors and crossed_app.errors[0].message == DENIED
    assert await _denied_flag(app_code) is False
    assert await _denied_flag(hub_code) is False

    straight = await _decline(context, DECLINE_HUB, deviceCode=str(hub_code.id), code=hub_code.code)
    assert not straight.errors, straight.errors
    assert await _denied_flag(hub_code) is True


async def _accept(context, device_code, hub):
    return await management_schema.execute(
        ACCEPT,
        context_value=context,
        variable_values={"input": {"deviceCode": str(device_code.id), "code": device_code.code, "hub": str(hub.id)}},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accepting_an_expired_code_is_refused():
    membership, context = await sync_to_async(_member_context)()
    hub = await sync_to_async(factories.make_hub)(organization=membership.organization)
    device_code = await sync_to_async(factories.make_device_code)(expires_at=timezone.now() - timedelta(seconds=1))

    result = await _accept(context, device_code, hub)

    assert result.errors
    assert result.errors[0].message == "This device code has expired."


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accepting_an_already_declined_code_is_refused():
    membership, context = await sync_to_async(_member_context)()
    hub = await sync_to_async(factories.make_hub)(organization=membership.organization)
    device_code = await sync_to_async(factories.make_device_code)(denied=True)

    result = await _accept(context, device_code, hub)

    assert result.errors
    assert result.errors[0].message == "This device code was already denied."


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accept_requires_the_code_and_membership_in_the_hubs_organization():
    membership, context = await sync_to_async(_member_context)()
    their_hub = await sync_to_async(factories.make_hub)()
    device_code = await sync_to_async(factories.make_device_code)()

    foreign = await _accept(context, device_code, their_hub)
    assert foreign.errors and foreign.errors[0].message == DENIED

    my_hub = await sync_to_async(factories.make_hub)(organization=membership.organization)
    wrong_code = await management_schema.execute(
        ACCEPT,
        context_value=context,
        variable_values={"input": {"deviceCode": str(device_code.id), "code": "nope", "hub": str(my_hub.id)}},
    )
    assert wrong_code.errors and wrong_code.errors[0].message == DENIED

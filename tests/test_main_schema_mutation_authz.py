"""Authorization regressions for main-schema mutations that had none at all.

Each of these fetched its target by a caller-supplied global pk and never
consulted `info`, so any principal holding a valid token could mutate any
tenant's (or any user's) objects. Their `api/management/` twins already had the
checks; these are the copies that did not.

Denials assert on the shared "Not found, or you are not authorized" text so the
error cannot be used as an existence oracle.
"""

import pytest
from asgiref.sync import sync_to_async

from karakter.models import Profile, SystemMessage
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _two_principals():
    """Two authenticated principals in unrelated organizations."""
    mine = factories.make_membership()
    my_client = factories.make_client(membership=mine)
    my_context = build_auth_context(mine.user, mine.organization, my_client)

    theirs = factories.make_membership()
    their_client = factories.make_client(membership=theirs)
    their_context = build_auth_context(
        theirs.user, theirs.organization, their_client
    )
    return my_context, mine, their_context, theirs


def _assert_denied(result):
    assert result.errors, f"expected a denial, got data: {result.data}"
    assert "not authorized" in result.errors[0].message, result.errors[0].message


UPDATE_DEVICE = """
    mutation ($input: UpdateDeviceInput!) {
        updateDevice(input: $input) { id name }
    }
"""

ACK_MESSAGE = """
    mutation ($input: AcknowledgeMessageInput!) {
        acknowledgeMessage(input: $input) { id title }
    }
"""

UPDATE_PROFILE = """
    mutation ($input: UpdateProfileInput!) {
        updateProfile(input: $input) { id name }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_rename_another_tenants_device():
    my_context, _mine, _their_context, theirs = await sync_to_async(_two_principals)()

    def _their_device():
        from fakts.models import Device

        return Device.objects.create(organization=theirs.organization, node_id="node-x", name="theirs")

    device = await sync_to_async(_their_device)()

    result = await schema.execute(
        UPDATE_DEVICE,
        context_value=my_context,
        variable_values={"input": {"id": str(device.id), "name": "pwned"}},
    )

    _assert_denied(result)
    fresh = await sync_to_async(lambda: type(device).objects.get(pk=device.pk))()
    assert fresh.name == "theirs"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_acknowledge_another_users_message():
    my_context, _mine, _their_context, theirs = await sync_to_async(_two_principals)()

    message = await sync_to_async(SystemMessage.objects.create)(
        user=theirs.user, title="t", message="m", action="noop"
    )

    result = await schema.execute(
        ACK_MESSAGE,
        context_value=my_context,
        variable_values={"input": {"id": str(message.id), "acknowledged": True}},
    )

    _assert_denied(result)
    fresh = await sync_to_async(SystemMessage.objects.get)(pk=message.pk)
    assert fresh.acknowledged is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_cannot_overwrite_another_users_profile():
    my_context, _mine, _their_context, theirs = await sync_to_async(_two_principals)()

    def _their_profile():
        profile, _ = Profile.objects.get_or_create(user=theirs.user, defaults={"name": "theirs"})
        profile.name = "theirs"
        profile.save()
        return profile

    profile = await sync_to_async(_their_profile)()

    result = await schema.execute(
        UPDATE_PROFILE,
        context_value=my_context,
        variable_values={"input": {"id": str(profile.id), "name": "pwned", "avatar": "1"}},
    )

    _assert_denied(result)
    fresh = await sync_to_async(Profile.objects.get)(pk=profile.pk)
    assert fresh.name == "theirs"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_upload_key_is_namespaced_under_the_caller():
    """A presigned POST grants write to exactly one key, so it must not be
    attacker-chosen — `MEDIA_BUCKET` is served publicly by the gateway."""
    import re

    from karakter.graphql.mutations.upload import scoped_key

    _my_context, mine, _their_context, _theirs = await sync_to_async(_two_principals)()

    # The client's filename is not used at all, so it cannot pick or escape the prefix.
    key = scoped_key(mine.user, "image/png")
    assert re.fullmatch(rf"users/{mine.user.id}/[0-9a-f]{{32}}\.png", key), key
    assert scoped_key(mine.user, "image/png") != key

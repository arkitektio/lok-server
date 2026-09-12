"""Per-organization notification consent, and who may send through it.

Registering a device in the companion app is the *global* consent; the
``Membership.allow_notifications`` flag is the per-organization mute layered on
top. The risk this file guards is that the flag becomes decorative — a settings
switch that flips a column while ``notifyMember`` keeps pushing regardless — so
every send-path test asserts on ``ComChannel.publish`` itself, not just on the
GraphQL error.

Setup runs through ``sync_to_async`` (matching ``test_role_requests``) because the
ORM is not usable from the event loop the async schema executes on.
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from karakter import models
from karakter.managers import create_role
from tests import factories
from tests.conftest import build_auth_context

SET_NOTIFICATIONS = """
mutation ($input: SetMembershipNotificationsInput!) {
  setMembershipNotifications(input: $input) { id allowNotifications }
}
"""

NOTIFY_MEMBER = """
mutation ($input: NotifyMemberInput!) {
  notifyMember(input: $input) { delivered attempted membership { id } }
}
"""

READ_MEMBERSHIP = """
query ($id: ID!) {
  membership(id: $id) { allowNotifications hasNotificationChannel }
}
"""


def _org_with_member():
    """An org whose owner is a third party, so "owner" and "admin" stay distinct.

    Returns the org, an admin who does not own it, a plain member with one
    registered device, and an unrelated member with none.
    """
    owner = factories.make_user()
    org = factories.make_organization(owner=owner)

    admin_role = create_role(org, "admin")
    admin = factories.make_membership(organization=org)
    admin.roles.add(admin_role)

    member = factories.make_membership(organization=org)
    models.ComChannel.objects.create(user=member.user, token=f"ExponentPushToken[{member.pk}]")

    bystander = factories.make_membership(organization=org)
    return org, admin, member, bystander


def _context_for(membership):
    client = factories.make_client(membership=membership)
    return build_auth_context(membership.user, membership.organization, client)


async def _run(query, context, variables):
    return await management_schema.execute(query, context_value=context, variable_values=variables)


def _reload(membership):
    return models.Membership.objects.get(pk=membership.pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_new_membership_starts_opted_in():
    """The flag is a mute, not a second consent — registering the device was the yes."""
    _org, _admin, member, _bystander = await sync_to_async(_org_with_member)()
    assert member.allow_notifications is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_can_opt_out_and_back_in():
    org, _admin, member, _bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(member)

    result = await _run(
        SET_NOTIFICATIONS, context, {"input": {"organization": str(org.id), "allow": False}}
    )
    assert not result.errors, result.errors
    assert result.data["setMembershipNotifications"]["allowNotifications"] is False
    assert (await sync_to_async(_reload)(member)).allow_notifications is False

    result = await _run(
        SET_NOTIFICATIONS, context, {"input": {"organization": str(org.id), "allow": True}}
    )
    assert not result.errors, result.errors
    assert (await sync_to_async(_reload)(member)).allow_notifications is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_cannot_flip_someone_elses_consent():
    """The lookup pins `user` to the caller, so there is no membership to hit."""
    org, _admin, member, bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(bystander)

    result = await _run(
        SET_NOTIFICATIONS, context, {"input": {"organization": str(org.id), "allow": False}}
    )

    # The bystander only ever changes their own row; the member's stays put.
    assert not result.errors, result.errors
    assert (await sync_to_async(_reload)(member)).allow_notifications is True
    assert (await sync_to_async(_reload)(bystander)).allow_notifications is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_can_notify_an_opted_in_member():
    _org, admin, member, _bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(admin)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER,
            context,
            {"input": {"membership": str(member.pk), "title": "Heads up", "message": "Standup at 10"}},
        )

    assert not result.errors, result.errors
    assert result.data["notifyMember"] == {
        "delivered": 1,
        "attempted": 1,
        "membership": {"id": str(member.pk)},
    }
    publish.assert_called_once_with("Heads up", "Standup at 10")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_opted_out_member_is_never_published_to():
    """The regression this whole feature hinges on: the mute must reach the wire."""
    _org, admin, member, _bystander = await sync_to_async(_org_with_member)()
    await sync_to_async(models.Membership.objects.filter(pk=member.pk).update)(
        allow_notifications=False
    )
    context = await sync_to_async(_context_for)(admin)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert result.errors
    assert "turned off notifications" in result.errors[0].message
    publish.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_may_notify_a_peer():
    """Sending is member-level, not admin-level: the recipient's mute is the gate."""
    _org, _admin, member, bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(bystander)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert not result.errors, result.errors
    assert result.data["notifyMember"]["delivered"] == 1
    publish.assert_called_once()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_peers_send_still_respects_the_recipients_mute():
    """Loosening *who* may send must not loosen whether the mute is honoured."""
    _org, _admin, member, bystander = await sync_to_async(_org_with_member)()
    await sync_to_async(models.Membership.objects.filter(pk=member.pk).update)(
        allow_notifications=False
    )
    context = await sync_to_async(_context_for)(bystander)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert result.errors
    assert "turned off notifications" in result.errors[0].message
    publish.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_outsider_may_not_notify_across_organizations():
    """The membership lookup is unscoped, so `assert_member` is the only thing
    standing between a stranger and someone else's tenant."""
    _org, _admin, member, _bystander = await sync_to_async(_org_with_member)()
    outsider = await sync_to_async(factories.make_membership)()
    context = await sync_to_async(_context_for)(outsider)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert result.errors
    # The uniform denial: a stranger cannot tell a real membership id from a fake one.
    assert result.errors[0].message == DENIED
    publish.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_notifying_a_member_with_no_device_is_an_error_not_a_silent_success():
    """`User.notify` returns [] with no channels — returning True there would lie."""
    _org, admin, _member, bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(admin)

    result = await _run(
        NOTIFY_MEMBER, context, {"input": {"membership": str(bystander.pk), "message": "hi"}}
    )

    assert result.errors
    assert "no device registered" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_empty_message_is_rejected():
    _org, admin, member, _bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(admin)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "   "}}
        )

    assert result.errors
    assert "must not be empty" in result.errors[0].message
    publish.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_title_defaults_to_the_organization_name():
    """The send form leaves the title optional, so the push still needs a sender."""
    org, admin, member, _bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(admin)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert not result.errors, result.errors
    title, _message = publish.call_args.args
    # `make_organization` gives name and slug different values, so this pins the
    # name branch specifically rather than passing on either.
    assert org.name and org.name != org.slug
    assert title == org.name


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_title_falls_back_to_the_slug_for_an_unnamed_organization():
    """`Organization.name` is nullable, and a push with an empty title reads as spam."""
    org, admin, member, _bystander = await sync_to_async(_org_with_member)()
    await sync_to_async(models.Organization.objects.filter(pk=org.pk).update)(name=None)
    context = await sync_to_async(_context_for)(admin)

    with patch.object(models.ComChannel, "publish", return_value="ok") as publish:
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert not result.errors, result.errors
    title, _message = publish.call_args.args
    assert title == org.slug


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_has_notification_channel_reflects_registered_devices():
    """The send form disables itself on this, so it must track the real channels."""
    _org, admin, member, bystander = await sync_to_async(_org_with_member)()
    context = await sync_to_async(_context_for)(admin)

    result = await _run(READ_MEMBERSHIP, context, {"id": str(member.pk)})
    assert not result.errors, result.errors
    assert result.data["membership"]["hasNotificationChannel"] is True

    result = await _run(READ_MEMBERSHIP, context, {"id": str(bystander.pk)})
    assert not result.errors, result.errors
    assert result.data["membership"]["hasNotificationChannel"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_rejecting_device_counts_as_attempted_but_not_delivered():
    """Expo answers per-device; the UI reports the split, so it must be truthful."""
    _org, admin, member, _bystander = await sync_to_async(_org_with_member)()
    await sync_to_async(models.ComChannel.objects.create)(
        user=member.user, token=f"ExponentPushToken[{member.pk}-second]"
    )
    context = await sync_to_async(_context_for)(admin)

    with patch.object(models.ComChannel, "publish", side_effect=["ok", "Error"]):
        result = await _run(
            NOTIFY_MEMBER, context, {"input": {"membership": str(member.pk), "message": "hi"}}
        )

    assert not result.errors, result.errors
    assert result.data["notifyMember"]["attempted"] == 2
    assert result.data["notifyMember"]["delivered"] == 1

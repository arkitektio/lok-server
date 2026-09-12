"""Per-organization notification consent, and sending to a member's devices.

Two halves of the same feature:

- ``set_membership_notifications`` — the member's own opt-in/opt-out for one of
  their organizations. Registering a device in the companion app is the global
  consent; this flag is the per-organization mute.
- ``notify_member`` — one member of an organization pushes a notification to
  another, through whatever channels that member registered.

The consent check does not live here: ``Membership.notify`` enforces it, so a
muted membership stays muted regardless of who calls it.
"""

import logging

import strawberry
from graphql import GraphQLError
from kante.types import Info

from api.management import types
from api.management.authz import assert_member, get_or_denied
from karakter import models

logger = logging.getLogger(__name__)


@strawberry.input(description="Turn this organization's notifications on or off for yourself.")
class SetMembershipNotificationsInput:
    organization: strawberry.ID
    allow: bool


def set_membership_notifications(
    info: Info, input: SetMembershipNotificationsInput
) -> types.ManagementMembership:
    """Opt the requesting user in or out of notifications from one organization.

    Scoped to the caller's *own* membership — the lookup pins `user` to the
    caller, so a member can only ever change their own consent, and "no such
    organization" and "not your membership" are the same denial.
    """
    membership = get_or_denied(
        models.Membership.objects,
        user=info.context.request.user,
        organization_id=input.organization,
    )
    membership.allow_notifications = input.allow
    membership.save()
    return membership


@strawberry.input(description="Send a notification to one member of your organization.")
class NotifyMemberInput:
    membership: strawberry.ID
    message: str
    title: str | None = None


def notify_member(info: Info, input: NotifyMemberInput) -> types.ManagementNotificationResult:
    """Push a notification to a member's registered devices.

    Any member of the organization may notify any other member of it — the
    recipient's own ``allow_notifications`` is the gate that matters, and it is
    theirs alone to set. The membership lookup is deliberately unscoped and
    ``assert_member`` does the gating, so a non-member gets the uniform denial
    and cannot use this as an existence oracle for membership ids.

    Raises:
        GraphQLError: when the caller does not belong to the target's
            organization, the message is empty, the member has muted the
            organization, or the member has no device registered. The last two
            are reported plainly rather than returning a success the sender
            would misread.
    """
    membership = get_or_denied(models.Membership.objects, pk=input.membership)
    organization = membership.organization
    user = info.context.request.user

    assert_member(info, organization)

    message = (input.message or "").strip()
    if not message:
        raise GraphQLError("Notification message must not be empty")

    title = (input.title or "").strip() or (organization.name or organization.slug or "Notification")

    try:
        results = membership.notify(title, message)
    except models.NotificationsMuted as exc:
        raise GraphQLError(str(exc)) from exc
    except Exception as exc:  # broad catch to wrap transport/ORM errors
        logger.exception("Failed to notify membership=%s", membership.pk)
        raise GraphQLError("Failed to send notification") from exc

    if not results:
        raise GraphQLError(
            "This member has no device registered for notifications. "
            "They need to sign in to the companion app first."
        )

    delivered = sum(1 for _channel_id, status in results if status != "Error")
    logger.info(
        "Notified membership=%s (%s/%s devices) by user=%s",
        membership.pk,
        delivered,
        len(results),
        user.id,
    )
    return types.ManagementNotificationResult(
        delivered=delivered,
        attempted=len(results),
        membership=membership,
    )

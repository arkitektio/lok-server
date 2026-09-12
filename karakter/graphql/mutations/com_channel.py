"""
karakter.graphql.mutations.com_channel

GraphQL mutations for client communication channels and notifications.

This module provides:
- register_com_channel: stores/updates a user's communication channel
  token used to deliver messages to the user.
- notify_user: server-side mutation to send an in-app or external
  notification to a user.

The implementations perform authentication and input validation and
log and wrap lower-level errors to provide clearer failure reasons to
API callers.
"""

import logging
from typing import Optional, cast

import strawberry
from graphql import GraphQLError
from kante.types import Info

from karakter import models, types
from karakter.authz import get_or_denied, get_user

logger = logging.getLogger(__name__)


@strawberry.input
class RegisterComChannelInput:
    """Input for registering/updating a communication channel.

    Attributes:
        token: platform-specific device/token string used to address
            notifications for the authenticated user.
    """

    token: str


def register_com_channel(info: Info, input: RegisterComChannelInput) -> types.ComChannel:
    """Register or update the current user's communication channel.

    Validates that the caller is authenticated and that a non-empty
    token is provided. Database errors are logged and re-raised as a
    RuntimeError to avoid leaking internal exception details to API
    consumers.

    Args:
        info: resolver info containing the request context.
        input: RegisterComChannelInput with the channel token.

    Returns:
        The created or updated ``ComChannel`` instance.

    Raises:
        GraphQLError: when the caller is not authenticated, the token is empty,
            or a database error occurs. Always a `GraphQLError` so the failure
            is a clean GraphQL error rather than a 500.
    """
    user = get_user(info)

    token = (input.token or "").strip()
    if not token:
        raise GraphQLError("Token must not be empty")

    try:
        channel, _created = models.ComChannel.objects.update_or_create(
            user=user,
            defaults={"token": token},
        )
    except Exception as exc:  # broad catch to wrap DB/ORM errors
        logger.exception("Failed to register communication channel for user=%s", getattr(user, "id", None))
        raise GraphQLError("Failed to register communication channel") from exc

    return cast(types.ComChannel, channel)


@strawberry.input
class NotifyUserInput:
    """Input for sending a notification to a user.

    Attributes:
        user: the target user's ID (strawberry.ID, typically a string).
        message: the notification body (required).
        title: short notification title (optional but encouraged).
    """

    user: strawberry.ID
    message: str
    title: Optional[str] = ""


def notify_user(info: Info, input: NotifyUserInput) -> bool:
    """Send a notification to a user.

    The caller must be authenticated. Non-staff callers may only send
    notifications to themselves; staff users may notify any user.

    Args:
        info: resolver info containing request context.
        input: NotifyUserInput describing the target and message.

    Returns:
        True when the notification was successfully queued/sent.

    Raises:
        GraphQLError: when the caller is not authenticated, is not allowed to
            notify the target, the target does not exist, the message is empty,
            or sending fails. Denials use the uniform "not found / not
            authorized" text so the target id cannot be used as an existence
            oracle.
    """
    caller = get_user(info)

    # Resolve target user. Only self-notification is allowed, so fold the
    # caller into the lookup: "no such user" and "not you" are the same denial.
    target_user = get_or_denied(models.User.objects, id=input.user, pk=caller.pk)

    message = (input.message or "").strip()
    if not message:
        raise GraphQLError("Notification message must not be empty")

    title = (input.title or "").strip()

    try:
        target_user.notify(title, message)
    except Exception as exc:
        logger.exception("Failed to send notification to user=%s", getattr(target_user, "id", None))
        raise GraphQLError("Failed to send notification") from exc

    return True

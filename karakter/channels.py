"""Realtime channels of lok's subscriptions.

Each signal carries ids only; the subscriber re-fetches the row through the
scoped queryset, which is where tenancy is enforced (the room name only keeps
unrelated traffic away). Group names use dots: kante's ``Channel.org_group``
builds ``:``-separated names, which are not valid channel-layer group names.
"""

from kante.channel import build_channel
from pydantic import BaseModel, Field


class SystemMessageSignal(BaseModel):
    """A system message of one user was created, changed or deleted (exactly one is set)."""

    create: int | None = None
    update: int | None = None
    delete: int | None = None


class MembershipRequestSignal(BaseModel):
    """A membership request into one organization was made or answered (exactly one is set)."""

    create: int | None = None
    update: int | None = None


class HubHealthSignal(BaseModel):
    """A hub reported its health."""

    hub_id: int = Field(description="The hub that reported.")


system_message_channel = build_channel(SystemMessageSignal, name="lok_system_messages")
membership_request_channel = build_channel(
    MembershipRequestSignal, name="lok_membership_requests"
)
hub_health_channel = build_channel(HubHealthSignal, name="lok_hub_health")


def user_messages_group(user_id: int) -> str:
    """The room of one user's system messages."""
    return f"lok_system_messages.user.{user_id}"


def membership_requests_group(organization_id: int) -> str:
    """The room of the membership requests into one organization."""
    return f"lok_membership_requests.org.{organization_id}"


def hub_health_group(organization_id: int) -> str:
    """The room of the health reports of one organization's hubs."""
    return f"lok_hub_health.org.{organization_id}"

"""Membership requests into the active organization, live, for its owner and admins."""

from typing import AsyncGenerator

from channels.db import database_sync_to_async
from django.db.models import Q
from kante.types import Info

from karakter import models, types
from karakter.authz import get_organization, get_user
from karakter.channels import membership_request_channel, membership_requests_group
from karakter.roles import owner_or_admin_q

__all__ = ["membership_requests"]


async def membership_requests(self, info: Info) -> AsyncGenerator[types.MembershipRequestEvent, None]:
    """An event whenever someone asks to join the active organization, or a request is answered.

    Only its owner and admins see any, as on the `membershipRequests` query: every
    event is re-read through the same rule, so someone who stops being an admin
    stops hearing about requests.
    """
    user = await database_sync_to_async(get_user)(info)
    organization = await database_sync_to_async(get_organization)(info)

    def visible(request_id: int) -> models.MembershipRequest | None:
        return (
            models.MembershipRequest.objects.filter(Q(id=request_id, organization=organization), owner_or_admin_q(user, "organization"))
            .distinct()
            .first()
        )

    async for signal in membership_request_channel.listen(info.context, [membership_requests_group(organization.pk)]):
        request_id = signal.create if signal.create is not None else signal.update
        if request_id is None:
            continue
        row = await database_sync_to_async(visible)(request_id)
        if row is None:
            continue
        if signal.create is not None:
            yield types.MembershipRequestEvent(create=row)
        else:
            yield types.MembershipRequestEvent(update=row)

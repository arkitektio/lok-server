"""Hub health reports of the active organization, live."""

from typing import Annotated, AsyncGenerator

import strawberry
from channels.db import database_sync_to_async
from kante.types import Info

from karakter.authz import get_organization
from karakter.channels import hub_health_channel, hub_health_group

__all__ = ["hub_health"]


async def hub_health(
    self, info: Info, hub: strawberry.ID | None = None
) -> AsyncGenerator[Annotated["Hub", strawberry.lazy("fakts.types")], None]:
    """The hub, each time one of the organization's hubs reports its health (only `hub`, when given).

    A hub that goes silent sends nothing: its `offlineAt` says when it stops
    counting as online, so a client sets a timer for that instead of polling.
    """
    from fakts import models as fakts_models

    organization = await database_sync_to_async(get_organization)(info)

    def reported(hub_id: int) -> fakts_models.Hub | None:
        return fakts_models.Hub.objects.filter(id=hub_id, organization=organization).first()

    async for signal in hub_health_channel.listen(info.context, [hub_health_group(organization.pk)]):
        if hub is not None and str(signal.hub_id) != str(hub):
            continue
        row = await database_sync_to_async(reported)(signal.hub_id)
        if row is not None:
            yield row

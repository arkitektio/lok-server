"""The caller's own system messages, live."""

from typing import AsyncGenerator

import strawberry
from channels.db import database_sync_to_async
from kante.types import Info

from karakter import models, types
from karakter.authz import get_user
from karakter.channels import system_message_channel, user_messages_group

__all__ = ["my_messages"]


async def my_messages(self, info: Info) -> AsyncGenerator[types.SystemMessageEvent, None]:
    """An event whenever one of the caller's system messages is created, changed or deleted."""
    user = await database_sync_to_async(get_user)(info)

    def own(message_id: int) -> models.SystemMessage | None:
        return models.SystemMessage.objects.filter(id=message_id, user=user).first()

    async for signal in system_message_channel.listen(info.context, [user_messages_group(user.pk)]):
        if signal.delete is not None:
            yield types.SystemMessageEvent(delete=strawberry.ID(str(signal.delete)))
            continue
        message_id = signal.create if signal.create is not None else signal.update
        if message_id is None:
            continue
        message = await database_sync_to_async(own)(message_id)
        if message is None:
            continue
        if signal.create is not None:
            yield types.SystemMessageEvent(create=message)
        else:
            yield types.SystemMessageEvent(update=message)

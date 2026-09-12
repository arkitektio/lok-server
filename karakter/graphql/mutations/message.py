import logging

import strawberry
from kante.types import Info
from karakter import models, types
from karakter.authz import get_or_denied, get_user

logger = logging.getLogger(__name__)


@strawberry.input
class AcknowledgeMessageInput:
    id: strawberry.ID
    acknowledged: bool


def acknowledge_message(
    info: Info, input: AcknowledgeMessageInput
) -> types.SystemMessage:
    """Acknowledge one of the caller's own system messages.

    `SystemMessage` is per-user (`karakter.models.SystemMessage.user`), so this is
    scoped to the caller rather than to an organization. It previously fetched by
    bare pk, letting any principal acknowledge anyone's messages.
    """
    message = get_or_denied(models.SystemMessage.objects, id=input.id, user=get_user(info))
    message.acknowledged = input.acknowledged
    message.save()
    return message

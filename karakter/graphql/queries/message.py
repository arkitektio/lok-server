
import strawberry
from kante.types import Info
from karakter import models, types
from karakter.authz import get_or_denied, get_user


def message(info: Info, id: strawberry.ID) -> types.SystemMessage:
    """One of the caller's own system messages (they are per-user)."""
    return get_or_denied(models.SystemMessage.objects, id=id, user=get_user(info))


def my_active_messages(info: Info) -> list[types.SystemMessage]:
    return models.SystemMessage.objects.filter(user=get_user(info), acknowledged=False)

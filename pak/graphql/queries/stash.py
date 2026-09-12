from kante.types import Info
import strawberry
from django.db.models import Q
from karakter.authz import get_or_denied, get_user
from pak import types, models
import logging

logger = logging.getLogger(__name__)


def stash(info: Info, id: strawberry.ID) -> types.Stash:
    """One of the caller's stashes (own or shared with them).

    `Stash.owner` is the ownership field — this used to filter on a `user`
    field that does not exist and raised `FieldError` on every call.
    """
    user = get_user(info)
    return get_or_denied(models.Stash.objects.distinct(), Q(owner=user) | Q(shared_with=user), id=id)


def my_stashes(info: Info) -> list[types.Stash]:
    user = get_user(info)
    return models.Stash.objects.filter(owner=user)


def stash_item(info: Info, id: strawberry.ID) -> types.StashItem:
    """A single stash item, reachable only through a stash the caller can read."""
    user = get_user(info)
    return get_or_denied(
        models.StashItem.objects.distinct(),
        Q(stash__owner=user) | Q(stash__shared_with=user),
        id=id,
    )

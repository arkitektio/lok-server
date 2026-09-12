
import strawberry
from django.db.models import Q
from kante.types import Info
from karakter.authz import get_or_denied, get_user
from komment import models, scalars, types


def _visible_to(user) -> Q:
    """A comment is visible to its author and to the users it mentions."""
    return Q(user=user) | Q(mentions=user)


def comment(info: Info, id: strawberry.ID) -> types.Comment:
    user = get_user(info)
    return get_or_denied(models.Comment.objects.distinct(), _visible_to(user), id=id)


def comments_for(
    info: Info, identifier: scalars.Identifier, object: strawberry.ID
) -> list[types.Comment]:
    user = get_user(info)
    return models.Comment.objects.filter(_visible_to(user), identifier=identifier, object=object).distinct()


def my_mentions(info: Info) -> list[types.Comment]:
    return models.Comment.objects.filter(mentions=get_user(info))

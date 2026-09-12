
import strawberry
from kante.types import Info
from karakter import models, types
from karakter.authz import get_or_denied, get_user


def group(info: Info, id: strawberry.ID) -> types.Group:
    """A group, visible only if the caller is a member of it.

    `Group` is Django's auth group and has no organization relation, so
    membership is the only tenant-safe boundary (matches `Group.get_queryset`).
    """
    # `User.groups` is declared with `related_query_name="karakter_user"`.
    return get_or_denied(models.Group.objects, id=id, karakter_user=get_user(info))


def mygroups(info: Info) -> list[types.Group]:
    return get_user(info).groups.all()

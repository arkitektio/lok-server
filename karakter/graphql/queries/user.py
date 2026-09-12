
import strawberry
from kante.types import Info
from karakter import models, types
from karakter.authz import get_scoped_or_denied, get_user


def user(info: Info, id: strawberry.ID) -> types.User:
    """A user, visible only if they share the caller's active organization."""
    return get_scoped_or_denied(
        models.User.objects.distinct(), info, field="memberships__organization", id=id
    )


def me(info: Info) -> types.User:
    return get_user(info)

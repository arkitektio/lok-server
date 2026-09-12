import strawberry_django
from pak import models, filters
import strawberry
import datetime
from karakter.types import User
from karakter.authz import get_user
from django.db.models import Q
from kante.types import Info


@strawberry_django.type(
    models.StashItem,
    ordering=filters.StashItemOrdering,
    filters=filters.StashItemFilter,
    pagination=True,
    description="""
A stashed item
""",
)
class StashItem:
    id: strawberry.ID
    identifier: str
    object: str
    added_at: datetime.datetime
    updated_at: datetime.datetime

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Only items in stashes the caller owns (or that are shared with them)."""
        user = get_user(info)
        return queryset.filter(Q(stash__owner=user) | Q(stash__shared_with=user)).distinct()


@strawberry_django.type(
    models.Stash,
    ordering=filters.StashOrdering,
    filters=filters.StashFilter,
    pagination=True,
    description="""
A Stash
""",
)
class Stash:
    id: strawberry.ID
    name: str
    description: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    is_active: bool

    items: list["StashItem"]

    @strawberry.field(description="The owner of the stash")
    def owner(self, info: Info) -> User:
        return self.owner

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Stashes are per-user: the caller's own, plus ones shared with them.

        Without this the root `stashes` list returned every user's stashes.
        """
        user = get_user(info)
        return queryset.filter(Q(owner=user) | Q(shared_with=user)).distinct()

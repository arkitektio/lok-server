import strawberry
from pak import models
from typing import Optional
from strawberry_django.filters import FilterLookup
import strawberry_django
from django.db.models import Q


@strawberry_django.filter_type(models.StashItem)
class StashItemFilter:
    # StashItem has no `username`; its searchable columns are `identifier` and `object`.
    identifier: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}identifier__icontains": value}) | Q(**{f"{prefix}object__icontains": value})

    @strawberry_django.filter_field
    def stashes(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}stash__in": value})


@strawberry_django.filter_type(models.Stash, description="__doc__")
class StashFilter:
    """A Filterset to Filter Groups"""

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.order_type(models.StashItem)
class StashItemOrdering:
    id: strawberry.auto
    updated_at: strawberry.auto


@strawberry_django.order_type(models.Stash)
class StashOrdering:
    id: strawberry.auto
    name: strawberry.auto
    created_at: strawberry.auto
    updated_at: strawberry.auto

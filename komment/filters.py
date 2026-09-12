import strawberry
from komment import models
from typing import Optional
from strawberry_django.filters import FilterLookup
import strawberry_django
from django.db.models import Q


@strawberry_django.filter_type(models.Comment)
class CommentFilter:
    # Comment has no `name`; `text` is its searchable column.
    text: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}text__icontains": value})


@strawberry_django.order_type(models.Comment)
class CommentOrdering:
    id: strawberry.auto
    created_at: strawberry.auto

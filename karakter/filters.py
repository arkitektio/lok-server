import strawberry
from karakter import models
from typing import Optional
from strawberry_django.filters import FilterLookup
import strawberry_django
from django.db.models import Q
from allauth.socialaccount import models as smodels


@strawberry_django.filter_type(models.User)
class UserFilter:
    username: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}username__contains": value})


@strawberry_django.filter_type(models.Group, description="__doc__")
class GroupFilter:
    """A Filterset to Filter Groups"""

    name: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(models.Role, description="__doc__")
class RoleFilter:
    """A Filterset to Filter Roles"""

    # `Role` has no `name` column; its human-facing key is `identifier`.
    identifier: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}identifier__icontains": value})


@strawberry_django.filter_type(models.ComChannel, description="__doc__")
class ComChannelFilter:
    """A Filterset to Filter Communication Channels"""

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(models.Organization, description="__doc__")
class OrganizationFilter:
    """A Filterset to Filter Groups"""

    name: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(models.Membership, description="__doc__")
class MembershipFilter:
    """A Filterset to Filter Memberships"""

    # `Membership` has no `name` column; `search` matches the member's username.

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}user__username__icontains": value})


@strawberry_django.filter_type(models.Profile)
class ProfileFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}bio__contains": value})


@strawberry_django.filter_type(models.OrganizationProfile)
class OrganizationProfileFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}bio__contains": value})


@strawberry_django.filter_type(models.GroupProfile)
class GroupProfileFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}bio__contains": value})


@strawberry_django.filter_type(models.SystemMessage)
class SystemMessageFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}title__icontains": value}) | Q(**{f"{prefix}message__icontains": value})


@strawberry_django.filter_type(models.Invite)
class InviteFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}email__icontains": value})

    @strawberry_django.filter_field
    def status(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}status": value})


@strawberry_django.filter_type(smodels.SocialAccount)
class SocialAccountFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}uid__contains": value})

    @strawberry_django.filter_field
    def provider(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}provider": value})


@strawberry_django.order_type(models.User)
class UserOrdering:
    id: strawberry.auto


@strawberry_django.order_type(models.Group)
class GroupOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(models.Role)
class RoleOrdering:
    id: strawberry.auto


@strawberry_django.order_type(models.Organization)
class OrganizationOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(models.ComChannel)
class ComChannelOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(models.Membership)
class MembershipOrdering:
    id: strawberry.auto


@strawberry_django.order_type(models.Invite)
class InviteOrdering:
    id: strawberry.auto
    created_at: strawberry.auto

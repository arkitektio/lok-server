import datetime

import strawberry
import strawberry_django
from allauth.socialaccount import models as smodels
from django.db.models import Q, QuerySet
from django.db.models.expressions import OrderBy
from django.utils import timezone
from strawberry import Info
from strawberry_django.filters import FilterLookup

from karakter import models
from karakter.enums import MembershipRequestStatus


@strawberry_django.filter_type(models.User)
class UserFilter:
    username: FilterLookup[str] | None | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}username__contains": value})


@strawberry_django.filter_type(models.Group, description="__doc__")
class GroupFilter:
    """A Filterset to Filter Groups"""

    name: FilterLookup[str] | None | None

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
    identifier: FilterLookup[str] | None | None

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

    name: FilterLookup[str] | None | None

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

    @strawberry_django.filter_field(
        description="Only the memberships holding this role."
    )
    def role(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}roles__id": value})

    @strawberry_django.filter_field(
        description="Only the organization's admins (true) or only the others (false)."
    )
    def is_admin(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}is_admin": value})

    @strawberry_django.filter_field(
        description="Only the memberships made at or after this time. A membership whose creation time is unknown (older than the record) never matches."
    )
    def created_after(self, value: datetime.datetime, prefix: str) -> Q:
        return Q(**{f"{prefix}created_at__gte": value})

    @strawberry_django.filter_field(
        description="Only the memberships made before this time. A membership whose creation time is unknown never matches."
    )
    def created_before(self, value: datetime.datetime, prefix: str) -> Q:
        return Q(**{f"{prefix}created_at__lt": value})


@strawberry_django.filter_type(models.MembershipRequest, description="__doc__")
class MembershipRequestFilter:
    """A Filterset to Filter Membership Requests"""

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field(
        description="Matches the requester's username or their reason."
    )
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}user__username__icontains": value}) | Q(
            **{f"{prefix}reason__icontains": value}
        )

    @strawberry_django.filter_field(
        description="Only the requests in one of these states."
    )
    def status(self, value: list[MembershipRequestStatus], prefix: str) -> Q:
        return Q(**{f"{prefix}status__in": [v.value for v in value]})


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
        return Q(**{f"{prefix}title__icontains": value}) | Q(
            **{f"{prefix}message__icontains": value}
        )


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

    @strawberry_django.filter_field(
        description="Only the invites granting this collaboration (its key)."
    )
    def collaboration(self, value: strawberry.ID, prefix: str) -> Q:
        # Entries are stored as `CollaborationRef.as_entry()`: the collaboration's
        # key, plus the roles, which containment ignores.
        entry = {"collaboration": str(value)}
        return Q(**{f"{prefix}shared_collaborations__contains": [entry]})

    @strawberry_django.filter_field(
        description="Only the links that grant collaborations (true), or only the invites to become a member (false)."
    )
    def grants_collaborations(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}shared_collaborations__isnull": not value})

    @strawberry_django.filter_field(
        description="Only the invites that can still be accepted (true: pending and not expired), or only the others (false)."
    )
    def valid(self, value: bool, prefix: str) -> Q:
        live = Q(**{f"{prefix}status": models.Invite.Status.PENDING}) & (
            Q(**{f"{prefix}expires_at__isnull": True})
            | Q(**{f"{prefix}expires_at__gte": timezone.now()})
        )
        return live if value else ~live


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
    created_at: strawberry.auto

    @strawberry_django.order_field(description="By the member's username.")
    def username(
        self,
        queryset: QuerySet,
        info: Info,
        value: strawberry_django.Ordering,
        prefix: str,
    ) -> tuple[QuerySet, list[OrderBy]]:
        return queryset, [value.resolve(f"{prefix}user__username")]


@strawberry_django.order_type(models.MembershipRequest)
class MembershipRequestOrdering:
    id: strawberry.auto
    created_at: strawberry.auto
    responded_at: strawberry.auto


@strawberry_django.order_type(models.Invite)
class InviteOrdering:
    id: strawberry.auto
    created_at: strawberry.auto

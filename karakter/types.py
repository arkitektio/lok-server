import datetime
from typing import List, Optional, cast
from karakter.datalayer import get_current_datalayer
import strawberry
import strawberry_django
from kante.types import Info
from karakter import filters, models, scalars
from allauth.socialaccount import models as smodels
import kante
from .type_gen import create_stats_type
from django.db.models import Q
from karakter.authz import build_prescoped_queryset, get_organization, get_user


def build_prescoper(field="organization"):
    def prescoper(queryset, info):
        return build_prescoped_queryset(info, queryset, field=field)

    return prescoper


@strawberry_django.type(
    models.Group,
    ordering=filters.GroupOrdering,
    filters=filters.GroupFilter,
    pagination=True,
    description="""
A Group is the base unit of Role Based Access Control. A Group can have many users and many permissions. A user can have many groups. A user with a group that has a permission can perform the action that the permission allows.
Groups are propagated to the respecting subservices. Permissions are not. Each subservice has to define its own permissions and mappings to groups.
""",
)
class Group:
    id: strawberry.ID
    name: str
    profile: Optional["GroupProfile"]

    @strawberry_django.field(description="The users that are in the group")
    def users(self, info: Info) -> List["User"]:
        return models.User.objects.filter(groups=self)

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Restrict groups to the ones the caller belongs to.

        `Group` is Django's auth group and carries no organization relation, so
        the only tenant-safe boundary available is membership: you can see the
        groups you are in. Without this the root `groups` list (and the group
        profiles hanging off it) enumerated every group in the deployment.
        """
        # `User.groups` is declared with `related_query_name="karakter_user"`.
        return queryset.filter(karakter_user=get_user(info)).distinct()


@strawberry_django.type(models.MediaStore)
class MediaStore:
    id: strawberry.ID
    path: str | None
    bucket: str
    key: str

    @strawberry_django.field()
    def presigned_url(self, info: Info, host: str | None = None) -> str:
        datalayer = get_current_datalayer()
        return cast(models.MediaStore, self).get_presigned_url(info, datalayer=datalayer, host=host)


@strawberry_django.type(
    models.User,
    ordering=filters.UserOrdering,
    filters=filters.UserFilter,
    pagination=True,
    description="""
A User is a person that can log in to the system. They are uniquely identified by their username.
And can have an email address associated with them (but don't have to).

A user can be assigned to groups and has a profile that can be used to display information about them.
Detail information about a user can be found in the profile.

All users can have social accounts associated with them. These are used to authenticate the user with external services,
such as ORCID or GitHub.

""",
)
class User:
    id: strawberry.ID
    username: str
    first_name: str | None
    last_name: str | None
    email: str | None
    groups: list[Group]
    memberships: list["Membership"] = strawberry_django.field(description="The memberships of the user in organizations")
    avatar: str | None
    profile: "Profile"
    com_channels: list["ComChannel"] = strawberry_django.field(description="The communication channels that the user has")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return build_prescoped_queryset(info, queryset, field="memberships__organization")


UserStats, UserStatsResolver = create_stats_type(
    model=models.User,
    filters=filters.UserFilter,
    allowed_fields={
        "created_at": "created_at",
    },
    allowed_datetime_fields={"created_at": "created_at"},
    prescope=build_prescoper(field="memberships__organization"),
)


@strawberry_django.type(
    models.Profile,
    filters=filters.ProfileFilter,
    pagination=True,
    description="""
A Profile of a User. A Profile can be used to display personalised information about a user,
such as a display name, a short bio and an avatar.
""",
)
class Profile:
    id: strawberry.ID
    bio: str | None = strawberry.field(description="A short bio of the user")
    name: str | None = strawberry.field(description="The name of the user")
    avatar: MediaStore | None = strawberry.field(description="The avatar of the user")


@strawberry_django.type(
    models.OrganizationProfile,
    filters=filters.OrganizationProfileFilter,
    pagination=True,
    description="""
A Profile of an Organization. An OrganizationProfile can be used to display public information
about an organization, such as a display name, a short bio and an avatar (logo).
""",
)
class OrganizationProfile:
    id: strawberry.ID
    bio: str | None = strawberry.field(description="A short bio of the organization")
    name: str | None = strawberry.field(description="The display name of the organization")
    avatar: MediaStore | None = strawberry.field(description="The avatar (logo) of the organization")


@strawberry_django.type(
    models.GroupProfile,
    filters=filters.GroupProfileFilter,
    pagination=True,
    description="""
A Profile of a Group. A GroupProfile can be used to display information about a group,
such as a display name, a short bio and an avatar.
""",
)
class GroupProfile:
    id: strawberry.ID
    bio: str | None = strawberry.field(description="A short bio of the group")
    name: str | None = strawberry.field(description="The name of the group")
    avatar: MediaStore | None = strawberry.field(description="The avatar of the group")


@strawberry_django.interface(
    smodels.SocialAccount,
    description="""
A Social Account is an account that is associated with a user. It can be used to authenticate the user with external services. It
can be used to store extra data about the user that is specific to the provider. We provide typed access to the extra data for
some providers. For others we provide a generic json field that can be used to store arbitrary data. Generic accounts are
always available, but typed accounts are only available for some providers.
""",
)
class SocialAccount:
    provider: str = strawberry.field(
        description="The provider of the account. This can be used to determine the type of the account. "
        "For ordinary providers this is the provider id ('google', 'orcid'); for sub-providers "
        "(SAML, OpenID Connect) it is the configured `provider_id` of the specific app, e.g. 'saml:acme'."
    )
    uid: str = strawberry.field(description="The unique identifier of the account. This is unique for the provider.")
    extra_data: scalars.ExtraData = strawberry.field(description="Extra data that is specific to the provider. This is a json field and can be used to store arbitrary data.")


@strawberry.type(description="""The ORCID Identifier of a user. This is a unique identifier that is used to identify a user on the ORCID service. It is composed of a uri, a path and a host.""")
class OrcidIdentifier:
    uri: str = strawberry.field(description="The uri of the identifier")
    path: str = strawberry.field(description="The path of the identifier")
    host: str = strawberry.field(description="The host of the identifier")


@strawberry.type(description="""The ORCID Preferences of a user. This is a set of preferences that are specific to the ORCID service. Currently only the locale is supported.""")
class OrcidPreferences:
    locale: str = strawberry.field(description="The locale of the user. This is used to determine the language of the ORCID service.")


@strawberry.type(description="""Assoiated OridReseracher Result""")
class OrcidResearcherURLS:
    path: str
    urls: list[str]


@strawberry.type()
class OrcidAddresses:
    path: str
    addresses: list[str]


@strawberry.type()
class OrcidPerson:
    researcher_urls: list[str]
    addresses: list[str]


@strawberry.type()
class OrcidActivities:
    educations: list[str]


@strawberry_django.type(
    smodels.SocialAccount,
    filters=filters.SocialAccountFilter,
    pagination=True,
    description="""
An ORCID Account is a Social Account that maps to an ORCID Account. It provides information about the
user that is specific to the ORCID service. This includes the ORCID Identifier, the ORCID Preferences and
the ORCID Person. The ORCID Person contains information about the user that is specific to the ORCID service.
This includes the ORCID Activities, the ORCID Researcher URLs and the ORCID Addresses.

""",
)
class OrcidAccount(SocialAccount):
    @strawberry_django.field(description="The ORCID Identifier of the user. The UID of the account is the same as the path of the identifier. Null if the provider did not return one.")
    def identifier(self) -> Optional[OrcidIdentifier]:
        data = (self.extra_data or {}).get("orcid-identifier")
        if not isinstance(data, dict):
            return None
        try:
            return OrcidIdentifier(uri=data["uri"], path=data["path"], host=data["host"])
        except KeyError:
            return None

    @strawberry_django.field(description="Information about the person that is specific to the ORCID service.")
    def person(self) -> Optional[OrcidPerson]:
        person = self.extra_data.get("person", None)
        if not person:
            return None

        researcher_urls = (self.extra_data.get("researcher-urls") or {}).get("researcher-urls") or []
        addresses = (self.extra_data.get("addresses") or {}).get("addresses") or []

        return OrcidPerson(researcher_urls=researcher_urls, addresses=addresses)

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider == "orcid"


@strawberry_django.type(
    smodels.SocialAccount,
    filters=filters.SocialAccountFilter,
    pagination=True,
    description="""
The Github Account is a Social Account that maps to a Github Account. It provides information about the
user that is specific to the Github service. This includes the Github Identifier.

""",
)
class GithubAccount(SocialAccount):
    @strawberry_django.field(description="The GitHub login of the account, if the provider returned one.")
    def identifier(self) -> str | None:
        login = (self.extra_data or {}).get("login")
        return str(login) if login else None

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider == "github"


@strawberry_django.type(
    smodels.SocialAccount,
    filters=filters.SocialAccountFilter,
    pagination=True,
    description="""
The Generic Account is a Social Account that maps to a generic account. It provides information about the
user that is specific to the provider. This includes untyped extra data.

""",
)
class GenericAccount(SocialAccount):
    extra_data: scalars.ExtraData

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider != "orcid"


@strawberry.type(description="""A Communication""")
class Communication:
    channel: strawberry.ID


@strawberry_django.type(
    models.SystemMessage,
    filters=filters.SystemMessageFilter,
    pagination=True,
    description="""
A System Message is a message that is sent to a user. 
It can be used to notify the user of important events or to request their attention.
System messages can use Rekuest Hooks as actions to allow the user to interact with the message.


""",
)
class SystemMessage:
    id: strawberry.ID
    title: str | None
    message: str | None
    action: str
    user: User


@strawberry_django.type(models.Role, filters=filters.RoleFilter, pagination=True, description="""A Role is a set of permissions that can be assigned to a user. It is used to define what a user can do in the system.""", ordering=filters.RoleOrdering)
class Role:
    id: strawberry.ID
    identifier: str
    organization: "Organization"

    @kante.django_field()
    def description(self, info: Info) -> "str":
        return self.description or self.identifier

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return build_prescoped_queryset(info, queryset, field="organization")


@strawberry_django.type(
    models.Membership,
    ordering=filters.MembershipOrdering,
    filters=filters.MembershipFilter,
    pagination=True,
    description="""
A Membership is a relation between a User and an Organization. It can have multiple Roles assigned to it.
""",
)
class Membership:
    id: strawberry.ID
    user: User
    organization: "Organization"
    roles: List["Role"] = strawberry.field(description="The roles that the user has in the organization")
    brand_hue: Optional[float] = strawberry.field(
        description="The member's personal brand hue (0–360) for this organization, if set. "
        "Null means they have not overridden the organization's default — fall back to "
        "`organization.brandHue`."
    )
    brand_chroma: Optional[float] = strawberry.field(
        description="The member's personal brand chroma (0–1) for this organization, if set. "
        "Null means they have not overridden the organization's default — fall back to "
        "`organization.brandChroma`."
    )

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return build_prescoped_queryset(info, queryset, field="organization")


@strawberry_django.type(models.Organization, filters=filters.OrganizationFilter, pagination=True, description="""An Organization is a group of users that can work together on a project.""", ordering=filters.OrganizationOrdering)
class Organization:
    id: strawberry.ID
    slug: str
    description: str | None = strawberry.field(description="A short description of the organization")
    brand_hue: Optional[float] = strawberry.field(description="The organization's default brand hue (0–360), if set. Members can override it per-membership.")
    brand_chroma: Optional[float] = strawberry.field(description="The organization's default brand chroma (0–1), if set. Members can override it per-membership.")
    avatar: MediaStore | None = strawberry.field(description="The logo of the organization")
    active_users: List[User] = strawberry.field(description="The users that are currently active in the organization")
    profile: "OrganizationProfile"
    memberships: List["Membership"] = strawberry_django.field(description="the memberships of people")
    invites: List["Invite"] = strawberry_django.field(description="the invites for this organization")

    @strawberry_django.field(description="The roles that are available in the organization")
    def roles(self) -> List["Role"]:
        return self.roles.all()

    @strawberry_django.field(description="The users that are part of the organization")
    def users(self) -> List[User]:
        return models.User.objects.filter(memberships__organization=self).distinct()

    @strawberry_django.field(description="The name of this organization")
    def name(self) -> str:
        return self.name or self.slug or f"organization-{self.id}"

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Only the caller's active organization is visible on this schema."""
        return queryset.filter(id=get_organization(info).id)


@strawberry_django.type(models.ComChannel, filters=filters.ComChannelFilter, pagination=True, description="""A communication channel through which a user can be notified (e.g. a push token).""", ordering=filters.ComChannelOrdering)
class ComChannel:
    id: strawberry.ID
    user: User


@strawberry_django.type(models.Invite, filters=filters.InviteFilter, pagination=True, description="""A single-use magic invite link that allows one person to join an organization.""", ordering=filters.InviteOrdering)
class Invite:
    id: strawberry.ID
    token: str
    email: str | None
    created_by: User
    created_for: Organization
    created_at: datetime.datetime
    expires_at: datetime.datetime | None
    status: str
    accepted_by: User | None
    declined_by: User | None
    responded_at: datetime.datetime | None
    roles: list["Role"]
    created_memberships: list["Membership"]

    @strawberry_django.field(description="Check if the invite is still valid and pending")
    def valid(self) -> bool:
        """Check if the invite is still valid"""
        return self.is_valid()

    @strawberry_django.field(description="Get the full URL for accepting this invite")
    def invite_url(self, info: Info) -> str:
        """Generate the full URL for accepting this invite on the kontrol SPA."""
        from django.conf import settings

        return f"{settings.KONTROL_FRONTEND_URL}/invite/{self.token}"

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Restrict invites to ones the caller owns or administers.

        This type exposes `token` — and `invite_url`, which embeds it — and an
        invite token is a bearer credential: whoever holds it can redeem it and
        receive whatever roles the invite carries.

        Scoping to the active organization alone was not enough. Every member of
        that organization, `guest` included, could list the pending tokens of
        their own tenant and redeem one carrying `admin` (from a second account,
        or after leaving and rejoining — `accept_invite` never checks that the
        caller is the intended recipient). This now matches the bar the
        management twin already enforces, `api.management.types.ManagementInvite`,
        whose docstring describes exactly that attack.
        """
        user = get_user(info)
        organization = get_organization(info)
        return queryset.filter(
            Q(created_for=organization),
            Q(created_for__owner=user)
            | Q(
                created_for__memberships__user=user,
                created_for__memberships__roles__identifier="admin",
            ),
        ).distinct()


@strawberry.type
class Context:
    """The context of this app. It is used to provide information about the current request and user."""

    user: User = strawberry.field(description="The user that is associated with this app")
    organization: Organization = strawberry.field(description="The organization that is associated with this app")
    roles: List[str] = strawberry.field(description="The roles that the user has in the organization")
    scope: List[str] = strawberry.field(description="The scope of the app within in the organization")

    @strawberry_django.field(description="Are we acting in the active organization of the user?")
    def fits_active_organization(self) -> bool:
        """Check if the context is for the active organization of the user"""
        if not self.user or not self.organization:
            return False
        return self.user.active_organization == self.organization

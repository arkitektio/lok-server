import datetime
from typing import List, Optional, cast
from django.conf import settings
from django.db.models import Q
from karakter.datalayer import get_current_datalayer
import strawberry
from django.contrib.auth import get_user_model
import strawberry_django
from kante.types import Info
from karakter import enums, models, scalars
from allauth.socialaccount import models as smodels
import kante
from fakts import models as fakts_models
from fakts import filters as fakts_filters
from fakts import scalars as fakts_scalars
from fakts import base_models
from fakts import enums as fakts_enums
from api.management import filters, enums, scalars
from api.management.authz import is_owner_or_admin, owner_or_admin_q
from pydantic import ValidationError as PydanticValidationError
from karakter import filters as karakter_filters
from strawberry.experimental import pydantic
from ionscale.base_models import Machine
from ionscale.repo import get_ionscale_repo


def build_prescoper(field="organization"):
    """Deliberately absent: use `karakter.types.build_prescoper`.

    A no-op copy used to live here under the same name as the real scoping
    helper, returning the queryset unchanged. Nothing called it, but any future
    `create_stats_type(..., prescope=build_prescoper(...))` in this module would
    have aggregated across every tenant while reading as if it were scoped.
    """
    raise NotImplementedError(
        "api.management.types.build_prescoper was a no-op. Import "
        "karakter.types.build_prescoper (or write real scoping) instead."
    )


def _caller(info: Info):
    """The authenticated caller, or ``None``.

    Field resolvers on types reachable from the public ``inviteByCode`` root run
    for anonymous visitors too, and reading ``request.user`` raises on kante's
    ``UniversalRequest`` when no principal was set — so this never raises and
    fails closed (``None``) instead.
    """
    try:
        user = info.context.request.user
    except Exception:
        return None
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _can_see_user_details(info: Info, target) -> bool:
    """Whether the caller may read ``target``'s personal details.

    Mirrors the ``friends`` scoping in ``ManagementUser.get_queryset``: the user
    themselves, or anyone who shares an organization with them. ``get_queryset``
    only runs for list fields — a forward FK hop (``createdBy { email }``,
    ``membership { user { email } }``) reaches the type unscoped, so the
    per-field gate is what actually protects the data.
    """
    caller = _caller(info)
    if caller is None:
        return False
    if caller.id == target.id:
        return True
    request = getattr(info.context, "request", None)
    cache = getattr(request, "_lok_visible_user_ids", None)
    if cache is None:
        cache = {}
        try:
            setattr(request, "_lok_visible_user_ids", cache)
        except Exception:
            pass
    key = (caller.id, target.id)
    if key not in cache:
        cache[key] = models.User.objects.filter(
            id=target.id, memberships__organization__memberships__user=caller
        ).exists()
    return cache[key]


def _membership_scoped(queryset, info: Info, path: str):
    """Filter ``queryset`` to rows whose organization (reached via ``path``) the
    caller belongs to. ``path`` is the ORM lookup from the row to its
    organization, e.g. ``"organization"`` or ``"instance__organization"``."""
    return queryset.filter(**{f"{path}__memberships__user": info.context.request.user}).distinct()


@strawberry_django.type(
    models.Group,
    filters=karakter_filters.GroupFilter,
    pagination=True,
    description="""
A Group is the base unit of Role Based Access Control. A Group can have many users and many permissions. A user can have many groups. A user with a group that has a permission can perform the action that the permission allows.
Groups are propagated to the respecting subservices. Permissions are not. Each subservice has to define its own permissions and mappings to groups.
""",
)
class ManagementGroup:
    id: strawberry.ID
    name: str
    profile: Optional["ManagementGroupProfile"]

    @strawberry_django.field(description="The users that are in the group")
    def users(self, info: Info) -> List["ManagementUser"]:
        return models.User.objects.filter(groups=self)


@strawberry_django.type(models.MediaStore)
class ManagementMediaStore:
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
    ordering=filters.ManagementUserOrdering,
    filters=karakter_filters.UserFilter,
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
class ManagementUser:
    id: strawberry.ID
    username: str
    groups: list[ManagementGroup]
    memberships: list["ManagementMembership"] = strawberry_django.field(description="The memberships of the user in organizations")
    avatar: str | None
    profile: "ManagementProfile"
    com_channels: list["ManagementComChannel"] = strawberry_django.field(description="The communication channels that the user has")

    @strawberry_django.field(
        description="The user's email address. Only returned to the user themselves and to people who share an organization with them.",
        only=["email"],
    )
    def email(self, info: Info) -> str | None:
        """Gate personal details on the `friends` relationship.

        `get_queryset` below scopes *listings*, but it does not run on FK
        traversal — and `inviteByCode` is a deliberately public root field, so an
        anonymous visitor holding a public invite code could read the inviter's
        email through `createdBy { email }`. The kontrol invite page renders
        `username` and the avatar, never the email, so this costs the SPA nothing.
        """
        if not _can_see_user_details(info, self):
            return None
        return self.email

    @strawberry_django.field(
        description="The user's first name. Only returned to the user themselves and to people who share an organization with them.",
        only=["first_name"],
    )
    def first_name(self, info: Info) -> str | None:
        if not _can_see_user_details(info, self):
            return None
        return self.first_name

    @strawberry_django.field(
        description="The user's last name. Only returned to the user themselves and to people who share an organization with them.",
        only=["last_name"],
    )
    def last_name(self, info: Info) -> str | None:
        if not _can_see_user_details(info, self):
            return None
        return self.last_name

    @strawberry_django.field(description="The social (external login) accounts linked to this user. Only the user themselves can see their own; empty for everyone else.")
    def social_accounts(self, info: Info) -> List["ManagementSocialAccount"]:
        caller = _caller(info)
        if caller is None or caller.id != self.id:
            return []
        return smodels.SocialAccount.objects.filter(user_id=self.id)

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Restrict user listings to people the caller shares an organization with.

        This type exposes `email`, real names, `memberships` and `com_channels`,
        and backs the root `friends` field — which, unscoped, was a directory dump
        of every user in the deployment to anyone who registered an account
        (signup is open and `ACCOUNT_EMAIL_VERIFICATION` defaults to "none").

        Scoped the same way as `ManagementOrganization`: any organization the
        caller is a member of, not just their active one, so a user legitimately
        in several organizations still sees all their colleagues.
        """
        user = info.context.request.user
        return queryset.filter(
            memberships__organization__memberships__user=user
        ).distinct()


@strawberry_django.type(
    models.Profile,
    filters=karakter_filters.ProfileFilter,
    pagination=True,
    description="""
A Profile of a User. A Profile can be used to display personalied information about a user.

""",
)
class ManagementProfile:
    id: strawberry.ID
    bio: str | None = strawberry.field(description="A short bio of the user")
    name: str | None = strawberry.field(description="The name of the user")
    avatar: ManagementMediaStore | None = strawberry.field(description="The avatar of the user")
    banner: ManagementMediaStore | None = strawberry.field(description="The banner of the user")


@strawberry_django.type(
    models.OrganizationProfile,
    filters=karakter_filters.OrganizationFilter,
    pagination=True,
    description="""
A Profile of an Organization. A Profile can be used to display personalised information about an organization.

""",
)
class ManagementOrganizationProfile:
    id: strawberry.ID
    organization: "ManagementOrganization"
    bio: str | None = strawberry.field(description="A short bio of the organization")
    name: str | None = strawberry.field(description="The display name of the organization")
    avatar: ManagementMediaStore | None = strawberry.field(description="The avatar of the organization")
    banner: ManagementMediaStore | None = strawberry.field(description="The banner of the organization")


@strawberry_django.type(
    models.GroupProfile,
    filters=karakter_filters.GroupProfileFilter,
    pagination=True,
    description="""
A Profile of a Group. A Profile can be used to display personalised information about a group.

""",
)
class ManagementGroupProfile:
    id: strawberry.ID
    bio: str | None = strawberry.field(description="A short bio of the group")
    name: str | None = strawberry.field(description="The name of the group")
    avatar: ManagementMediaStore | None = strawberry.field(description="The avatar of the group")


@strawberry_django.interface(
    smodels.SocialAccount,
    description="""
A Social Account is an account that is associated with a user. It can be used to authenticate the user with external services. It
can be used to store extra data about the user that is specific to the provider. We provide typed access to the extra data for
some providers. For others we provide a generic json field that can be used to store arbitrary data. Generic accounts are
always available, but typed accounts are only available for some providers.
""",
)
class ManagementSocialAccount:
    id: strawberry.ID
    provider: str = strawberry.field(description="The provider of the account. This can be used to determine the type of the account.")
    uid: str = strawberry.field(description="The unique identifier of the account. This is unique for the provider.")
    extra_data: scalars.ExtraData = strawberry.field(description="Extra data that is specific to the provider. This is a json field and can be used to store arbitrary data.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(user=info.context.request.user)


@strawberry.type(description="""The ORCID Identifier of a user. This is a unique identifier that is used to identify a user on the ORCID service. It is composed of a uri, a path and a host.""")
class ManagementOrcidIdentifier:
    uri: str = strawberry.field(description="The uri of the identifier")
    path: str = strawberry.field(description="The path of the identifier")
    host: str = strawberry.field(description="The host of the identifier")


@strawberry.type(description="""The ORCID Preferences of a user. This is a set of preferences that are specific to the ORCID service. Currently only the locale is supported.""")
class ManagementOrcidPreferences:
    locale: str = strawberry.field(description="The locale of the user. This is used to determine the language of the ORCID service.")


@strawberry.type(description="""Assoiated OridReseracher Result""")
class ManagementOrcidResearcherURLS:
    path: str
    urls: list[str]


@strawberry.type()
class ManagementOrcidAddresses:
    path: str
    addresses: list[str]


@strawberry.type()
class ManagementOrcidPerson:
    researcher_urls: list[str]
    addresses: list[str]


@strawberry.type()
class ManagementOrcidActivities:
    educations: list[str]


@strawberry_django.type(
    smodels.SocialAccount,
    filters=karakter_filters.SocialAccountFilter,
    pagination=True,
    description="""
An ORCID Account is a Social Account that maps to an ORCID Account. It provides information about the
user that is specific to the ORCID service. This includes the ORCID Identifier, the ORCID Preferences and
the ORCID Person. The ORCID Person contains information about the user that is specific to the ORCID service.
This includes the ORCID Activities, the ORCID Researcher URLs and the ORCID Addresses.

""",
)
class ManagementOrcidAccount(ManagementSocialAccount):
    @strawberry_django.field(description="The ORCID Identifier of the user. The UID of the account is the same as the path of the identifier.")
    def identifier(self) -> Optional[ManagementOrcidIdentifier]:
        data = (self.extra_data or {}).get("orcid-identifier")
        if not isinstance(data, dict):
            return None
        try:
            return ManagementOrcidIdentifier(uri=data["uri"], path=data["path"], host=data["host"])
        except (KeyError, TypeError):
            return None

    @strawberry_django.field(description="Information about the person that is specific to the ORCID service.")
    def person(self) -> Optional[ManagementOrcidPerson]:
        person = self.extra_data.get("person", None)
        if not person:
            return None

        researcher_urls = self.extra_data.get("researcher-urls", {}).get("researcher-urls", [])
        addresses = self.extra_data.get("addresses", {}).get("addresses", [])

        return ManagementOrcidPerson(researcher_urls=researcher_urls, addresses=addresses)

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider == "orcid"


@strawberry_django.type(
    smodels.SocialAccount,
    filters=karakter_filters.SocialAccountFilter,
    pagination=True,
    description="""
The Github Account is a Social Account that maps to a Github Account. It provides information about the
user that is specific to the Github service. This includes the Github Identifier.

""",
)
class ManagementGithubAccount(ManagementSocialAccount):
    @strawberry_django.field(description="Not available for GitHub accounts; always null.")
    def identifier(self) -> str | None:
        return None

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider == "github"


@strawberry_django.type(
    smodels.SocialAccount,
    filters=karakter_filters.SocialAccountFilter,
    pagination=True,
    description="""
The Generic Account is a Social Account that maps to a generic account. It provides information about the
user that is specific to the provider. This includes untyped extra data.

""",
)
class ManagementGenericAccount(ManagementSocialAccount):
    extra_data: scalars.ExtraData

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider != "orcid"


@strawberry_django.type(
    smodels.SocialAccount,
    pagination=True,
    description="""
The Google Account is a Social Account that maps to a Google Account. It provides information about the
user that is specific to the Google service.

""",
)
class ManagementGoogleAccount(ManagementSocialAccount):
    extra_data: scalars.ExtraData

    @staticmethod
    def is_type_of(ob, info):
        return ob.provider == "google"


@strawberry.type(description="""A Communication""")
class ManagementCommunication:
    channel: strawberry.ID


@strawberry_django.type(
    models.SystemMessage,
    filters=filters.ManagementSystemMessageFilter,
    pagination=True,
    description="""
A System Message is a message that is sent to a user. 
It can be used to notify the user of important events or to request their attention.
System messages can use Rekuest Hooks as actions to allow the user to interact with the message.


""",
)
class ManagementSystemMessage:
    id: strawberry.ID
    title: str
    message: str
    action: str
    user: ManagementUser


@strawberry_django.type(models.Role, filters=filters.ManagementRoleFilter, ordering=filters.ManagementRoleOrdering, pagination=True, description="""A Role is a set of permissions that can be assigned to a user. It is used to define what a user can do in the system.""")
class ManagementRole:
    id: strawberry.ID
    identifier: str
    organization: "ManagementOrganization"
    creating_instance: Optional["ManagementServiceInstance"]
    is_builtin: bool = strawberry.field(description="If this role is a built-in role that cannot be deleted (admin)")
    memberships: List["ManagementMembership"] = strawberry_django.field(description="The memberships that have this role")
    used_by: List["ManagementServiceInstance"] = strawberry_django.field(description="The service instances that use this role")

    @kante.django_field()
    def description(self, info: Info) -> "str":
        return self.description or self.identifier

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(models.RoleSet, pagination=True, description="""A RoleSet is a named bundle of roles within an organization that can be applied together — to seed an invite or to grant to a member in one action.""")
class ManagementRoleSet:
    id: strawberry.ID
    name: str
    organization: "ManagementOrganization"
    roles: List["ManagementRole"] = strawberry_django.field(description="The roles bundled in this set")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(models.Scope, filters=filters.ManagementScopeFilter, ordering=filters.ManagementScopeOrdering, pagination=True, description="""A Scope represents a permission or capability that can be granted to clients and users. It is used to define what access level a user or client has in the system.""")
class ManagementScope:
    id: strawberry.ID
    identifier: str
    organization: "ManagementOrganization"
    creating_instance: Optional["ManagementServiceInstance"]
    is_builtin: bool = strawberry.field(description="If this scope is a built-in scope that cannot be deleted (admin)")
    used_by: List["ManagementServiceInstance"] = strawberry_django.field(description="The service instances that use this scope")

    @kante.django_field()
    def description(self, info: Info) -> "str":
        return self.description or self.identifier

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(
    models.Membership,
    filters=filters.ManagementMembershipFilter,
    ordering=filters.ManagementMembershipOrdering,
    pagination=True,
    description="""
A Membership is a relation between a User and an Organization. It can have multiple Roles assigned to it.
""",
)
class ManagementMembership:
    id: strawberry.ID
    user: ManagementUser
    organization: "ManagementOrganization"
    roles: List["ManagementRole"] = strawberry.field(description="The roles that the user has in the organization")
    created_through: Optional["ManagementInvite"] = strawberry.field(description="The invite that created this membership")
    brand_hue: Optional[float] = strawberry.field(description="The member's personal brand hue (0–360) for this organization, if set.")
    brand_chroma: Optional[float] = strawberry.field(description="The member's personal brand chroma (0–1) for this organization, if set.")
    allow_notifications: bool = strawberry.field(description="Whether this organization is allowed to push notifications to the member's registered devices.")
    role_requests: List["ManagementRoleRequest"] = strawberry_django.field(description="The role requests this member has made in the organization")

    @strawberry_django.field(
        description="Whether the member has at least one device registered for notifications (e.g. through the companion app). Opting in does nothing until they do."
    )
    def has_notification_channel(self) -> bool:
        return models.ComChannel.objects.filter(user_id=self.user_id).exists()

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        # A caller may only see memberships of organizations they themselves belong to.
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(
    models.RoleRequest,
    filters=filters.ManagementRoleRequestFilter,
    ordering=filters.ManagementRoleRequestOrdering,
    pagination=True,
    description="""A member's request to be granted an additional role in their organization. The organization owner approves or declines it.""",
)
class ManagementRoleRequest:
    id: strawberry.ID
    membership: "ManagementMembership"
    role: "ManagementRole"
    reason: Optional[str] = strawberry.field(description="An optional note from the member explaining the request.")
    status: str = strawberry.field(description="The status of the request: pending, approved, or declined.")
    created_at: datetime.datetime
    resolved_by: Optional[ManagementUser] = strawberry.field(description="The owner who approved or declined the request.")
    responded_at: Optional[datetime.datetime]

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        # A caller may see a role request only if it is their own, or if they own
        # *or administer* the organization it targets (so the admin inbox can list
        # what its holder is already allowed to approve). This deliberately mirrors
        # `is_owner_or_admin`, the bar `approve_role_request`/`decline_role_request`
        # enforce — an admin who could approve a request but not read it could only
        # ever act on ids they guessed.
        user = info.context.request.user
        return queryset.filter(
            Q(membership__user=user) | owner_or_admin_q(user, "membership__organization")
        ).distinct()


@strawberry_django.type(models.Organization, filters=karakter_filters.OrganizationFilter, pagination=True, description="""An Organization is a group of users that can work together on a project.""", ordering=filters.ManagementOrganizationOrdering)
class ManagementOrganization:
    id: strawberry.ID
    slug: str
    name: str | None = strawberry.field(description="The name of this organization")
    description: str | None = strawberry.field(description="A short description of the organization")
    brand_hue: Optional[float] = strawberry.field(description="The organization's default brand hue (0–360), if set. Members can override it per-membership.")
    brand_chroma: Optional[float] = strawberry.field(description="The organization's default brand chroma (0–1), if set. Members can override it per-membership.")
    require_device_auth: Optional[bool] = strawberry.field(description="Whether clients created in this organization must present a device node_id. None/False means device auth is not required.")
    access_token_lifetime: Optional[int] = strawberry.field(description="Access-token lifetime in seconds for this organization's clients. Null means the server default (one hour). Clamped into the server's allowed range when tokens are issued.")
    active_users: List[ManagementUser] = strawberry.field(description="The users that are currently active in the organization")
    profile: Optional["ManagementOrganizationProfile"] = strawberry.field(description="The profile of the organization")
    memberships: List["ManagementMembership"] = strawberry_django.field(description="the memberships of people")
    invites: List["ManagementInvite"] = strawberry_django.field(description="the invites for this organization")
    clients: List["ManagementClient"] = strawberry_django.field(description="The clients that belong to this organization")
    service_instances: List["ManagementServiceInstance"] = strawberry_django.field(description="The service instances that belong to this organization")

    @strawberry_django.field(description="The users that are part of the organization")
    def users(self) -> List[ManagementUser]:
        return get_user_model().objects.filter(memberships__organization=self).distinct()

    @strawberry_django.field(description="The roles that are available in the organization")
    def roles(self) -> List["ManagementRole"]:
        return self.roles.all()

    @strawberry_django.field(description="The role sets (named bundles of roles) defined in the organization")
    def role_sets(self) -> List["ManagementRoleSet"]:
        return self.role_sets.all()

    @strawberry_django.field(description="Whether the currently authenticated user is the owner of this organization.")
    def am_i_owner(self, info: Info) -> bool:
        user = info.context.request.user
        if not user.is_authenticated:
            return False
        return self.owner_id == user.id

    @strawberry_django.field(description="Whether the currently authenticated user owns this organization or holds its `admin` role — the bar for privileged operations such as adding a hub.")
    def am_i_admin(self, info: Info) -> bool:
        user = info.context.request.user
        if not user.is_authenticated:
            return False
        return is_owner_or_admin(user, self)

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(memberships__user=info.context.request.user).distinct()


@strawberry_django.type(models.ComChannel, filters=filters.ManagementComChannelFilter, pagination=True, description="""A communication channel (e.g. a push-notification endpoint) through which a user can be notified.""")
class ManagementComChannel:
    id: strawberry.ID
    user: ManagementUser


@strawberry.type(description="""The outcome of sending a notification to a member. `delivered` counts the devices the push actually reached, so a send to a member with no registered device is visibly a no-op rather than a silent success.""")
class ManagementNotificationResult:
    delivered: int = strawberry.field(description="How many of the member's devices accepted the notification.")
    attempted: int = strawberry.field(description="How many registered devices were tried.")
    membership: "ManagementMembership" = strawberry.field(description="The member the notification was addressed to.")


@strawberry_django.type(models.Invite, filters=filters.ManagementInviteFilter, pagination=True, description="""A single-use magic invite link that allows one person to join an organization.""")
class ManagementInvite:
    id: strawberry.ID
    token: str
    created_by: ManagementUser
    created_for: ManagementOrganization
    created_at: datetime.datetime
    expires_at: datetime.datetime | None
    public: bool
    status: str
    accepted_by: ManagementUser | None
    declined_by: ManagementUser | None
    responded_at: datetime.datetime | None
    roles: list["ManagementRole"]
    created_memberships: list["ManagementMembership"]

    @strawberry_django.field(
        description="The e-mail address this invite was addressed to, if any. Only visible to the organization's owner and admins.",
        only=["email", "created_for"],
        select_related=["created_for"],
    )
    def email(self, info: Info) -> str | None:
        """`inviteByCode` is public, so an anonymous visitor with a public invite
        link must not learn who it was addressed to."""
        caller = _caller(info)
        if caller is None or not is_owner_or_admin(caller, self.created_for):
            return None
        return self.email

    @strawberry_django.field(description="Check if the invite is still valid and pending")
    def valid(self) -> bool:
        """Check if the invite is still valid"""
        return self.is_valid()

    @strawberry_django.field(description="Get the full URL for accepting this invite")
    def invite_url(self, info: Info) -> str:
        """Generate the full URL for accepting this invite on the kontrol SPA."""
        return f"{settings.KONTROL_FRONTEND_URL}/invite/{self.token}"

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Only an organization's owner or admins may list its invites.

        This type exposes `token` (and `invite_url`, which embeds it), and an invite
        token is a bearer credential: whoever holds it can redeem it and receive
        whatever roles the invite carries. Without this scoping the type had none,
        so any member — including a `guest` — could reach it through
        `ManagementOrganization.invites` and read pending tokens for their own
        organization, redeeming one that grants `admin`.

        The bar matches the single-invite `invite(id)` query and the privileged bar
        in `authz.assert_owner_or_admin`. `inviteByCode` is unaffected: it returns a
        model instance directly, and its caller already holds the token.
        """
        user = info.context.request.user
        return queryset.filter(
            Q(created_for__owner=user)
            | Q(
                created_for__memberships__user=user,
                created_for__memberships__roles__identifier="admin",
            )
        ).distinct()


@pydantic.type(base_models.Requirement)
class ManagementStagingRequirement:
    service: str
    key: str
    description: str | None = None
    optional: bool = False


@pydantic.type(base_models.PublicSource)
class ManagementStagingPublicSource:
    kind: str
    url: str


@pydantic.type(base_models.Manifest)
class ManagementStagingManifest:
    version: str
    identifier: str
    title: str | None = None
    description: str | None = None
    logo: str | None = None
    scopes: list[str]
    authors: list[str]
    keywords: list[str]
    license: str | None = None
    homepage: str | None = None
    repo_url: str | None = None
    public_sources: list[ManagementStagingPublicSource] | None = strawberry.field(description="Public sources for this staging service")
    requirements: list[ManagementStagingRequirement]
    # The raw device node id is a secret-ish identifier (it is hashed per organization
    # before it is ever persisted on a Device). It is deliberately NOT exposed; the UI only
    # needs to know whether the manifest is device-bound.
    node_id: strawberry.Private[str | None] = None

    @strawberry.field(description="Whether this manifest is bound to a device (carries a node id). The id itself is never exposed.")
    def has_node_id(self) -> bool:
        return bool(self.node_id)


@pydantic.type(base_models.Role)
class StagingRole:
    key: str
    description: str | None = None


@pydantic.type(base_models.Scope)
class StagingScope:
    key: str
    description: str | None = None


@pydantic.type(base_models.StagingAlias)
class StagingAlias:
    id: strawberry.ID
    kind: str
    name: Optional[str]
    host: Optional[str]
    port: Optional[int]
    ssl: bool = True
    path: Optional[str] = None
    challenge: Optional[str] = None
    scope: str = "local"
    public: bool = False

@pydantic.type(base_models.ServiceManifest)
class ManagementStagingServiceManifest:
    version: str
    identifier: str
    description: str | None = None
    logo: str | None = None
    scopes: list[StagingScope] | None = None
    roles: list[StagingRole] | None = None
    instance_id: str | None = None
    public_sources: list[ManagementStagingPublicSource] | None = None


@pydantic.type(base_models.InstanceRequest)
class ManagementStagingInstanceRequest:
    manifest: ManagementStagingServiceManifest
    aliases: list[StagingAlias] | None = None
    identifier: str
    description: Optional[str] = None


@pydantic.type(base_models.ClientRequest)
class ManagementStagingClientRequest:
    manifest: ManagementStagingManifest
    identifier: str
    description: Optional[str] = None


@pydantic.type(base_models.HubManifest)
class ManagementHubManifest:
    identifier: str
    instances: list[ManagementStagingInstanceRequest]
    clients: list[ManagementStagingClientRequest]


@strawberry_django.type(
    fakts_models.Service,
    ordering=filters.ManagementServiceOrdering,
    description="A Service is a Webservice that a Client might want to access. It is not the configured instance of the service, but the service itself.",
    pagination=True,
    filters=fakts_filters.ServiceFilter,
)
class ManagementService:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the service")
    identifier: fakts_scalars.ServiceIdentifier = strawberry.field(description="The identifier of the service. This should be a globally unique string that identifies the service. We encourage you to use the reverse domain name notation. E.g. `com.example.myservice`")
    description: str | None = strawberry.field(description="The description of the service. This should be a human readable description of the service.")
    releases: list["ManagementServiceRelease"] = strawberry_django.field(
        description="The releases of the service. A service release is a configured instance of a service. It will be configured by a configuration backend and will be used to send to the client as a configuration. It should never contain sensitive information."
    )
    logo: ManagementMediaStore | None = strawberry.field(description="The logo of the service. This should be a url to a logo that can be used to represent the service.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "organization")


@strawberry_django.type(
    fakts_models.KommunityPartner,
    description="A KommunityPartner represents a pre-configured partner that can provide hubs and services to organizations. Partners can be auto-configured to automatically create hubs for new organizations.",
    pagination=True,
    filters=filters.ManagementKommunityPartnerFilter,
    ordering=filters.ManagementKommunityPartnerOrdering,
)
class ManagementKommunityPartner:
    id: strawberry.ID
    auth_url: str | None = strawberry.field(description="The authentication URL of the partner.")
    logo_url: str | None = strawberry.field(description="The logo URL of the partner.")
    image_url: str | None = strawberry.field(description="A larger marketing image URL for the partner.")
    description: str | None = strawberry.field(description="The description of the partner.")
    short_description: str | None = strawberry.field(description="A short description of the partner for cards and previews.")
    license_agreement: str | None = strawberry.field(description="Optional license agreement text that must be signed before connecting this partner.")
    name: str = strawberry.field(description="The name of the partner.")
    identifier: str = strawberry.field(description="The unique identifier of the partner.")
    website_url: str | None = strawberry.field(description="The website URL of the partner.")
    partner_kind: str = strawberry.field(description="The kind of partner (e.g., 'preauthorized', 'oauth2').")
    kommunity_kind: str = strawberry.field(description="The kind of kommunity (e.g., 'open', 'restricted', 'private').")
    auto_configure: bool = strawberry.field(description="Whether this partner should automatically create hubs for new organizations.")
    oauth_client: Optional["ManagementOAuth2Client"] = strawberry.field(description="The OAuth2 client associated with this partner, if any.")

    @strawberry.field(description="Check if this partner applies to the current user based on filter conditions.")
    def applies_to_me(self, info: Info) -> bool:
        user = info.context.request.user
        if not user.is_authenticated:
            return False
        return self.applies_to_user(user)




@strawberry.type(description="Result of validating a device code against an organization")
class PotentialMapping:
    service_instance: Optional["ManagementServiceInstance"]
    key: str
    reason: str | None


@strawberry.type
class ValidationResult:
    valid: bool
    reason: str | None
    mappings: list[PotentialMapping]
    existing_device: Optional["ManagementDevice"] = strawberry.field(
        default=None,
        description=(
            "The device that already exists for this node in the selected hub's "
            "organization. If null, accepting will create a new device."
        ),
    )


@strawberry_django.type(
    fakts_models.ServiceRelease,
    ordering=filters.ManagementServiceReleaseOrdering,
    description="A ServiceRelease is a specific version of a Service. Service instances are always instances of one release.",
    pagination=True,
    filters=filters.ManagementServiceReleaseFilter,
)
class ManagementServiceRelease:
    id: strawberry.ID
    service: ManagementService = strawberry_django.field(description="The service that this release belongs to.")
    version: str = strawberry.field(description="The version of the service release.")
    instances: list["ManagementServiceInstance"] = strawberry_django.field(
        description="The instances of the service release. A service instance is a configured instance of a service. It will be configured by a configuration backend and will be used to send to the client as a configuration. It should never contain sensitive information."
    )

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "service__organization")


@strawberry_django.type(
    fakts_models.ServiceInstance,
    description="A ServiceInstance is a configured instance of a Service. It will be configured by a configuration backend and will be used to send to the client as a configuration. It should never contain sensitive information.",
    pagination=True,
    filters=filters.ManagementServiceInstanceFilter,
    ordering=filters.ManagementServiceInstanceOrdering,
)
class ManagementServiceInstance:
    id: strawberry.ID
    release: ManagementServiceRelease = strawberry_django.field(description="The service that this instance belongs to.")
    organization: "ManagementOrganization" = strawberry_django.field(description="The organization that owns this instance.")
    device: Optional["ManagementDevice"] = strawberry.field(description="The device that this instance is associated with, if any.")
    instance_id: str = strawberry.field(description="The identifier of the instance. This is a unique string that identifies the instance. It is used to identify the instance in the code and in the database.")
    allowed_users: list[ManagementUser] = strawberry_django.field(description="The users that are allowed to use this instance.")
    denied_users: list[ManagementUser] = strawberry_django.field(description="The users that are denied to use this instance.")
    allowed_groups: list[ManagementGroup] = strawberry_django.field(description="The groups that are allowed to use this instance.")
    denied_groups: list[ManagementGroup] = strawberry_django.field(description="The groups that are denied to use this instance.")
    mappings: list["ManagementServiceInstanceMapping"] = strawberry_django.field(description="The mappings of the hub. A mapping is a mapping of a service to a service instance. This is used to configure the hub.")
    logo: ManagementMediaStore | None = strawberry.field(description="The logo of the app. This should be a url to a logo that can be used to represent the app.")
    aliases: list["ManagementInstanceAlias"] = strawberry_django.field(
        description="The aliases of the instance. An alias is a way to reach the instance. Clients can use these aliases to check if they can reach the instance. An alias can be an absolute alias (e.g. 'example.com') or a relative alias (e.g. 'example.com/path'). If the alias is relative, it will be relative to the layer's domain, port and path."
    )
    roles: list["ManagementRole"] = strawberry_django.field(description="The roles that are associated with this instance. These roles will be assigned to users that are allowed to use this instance.")
    scopes: list["ManagementScope"] = strawberry_django.field(description="The scopes that are associated with this instance. These scopes will be assigned to users that are allowed to use this instance.")

    @strawberry_django.field(description="A human-readable identifier of the instance: `instance_id @ device @ organization`.")
    def identifier(self) -> str:
        return f"{self.instance_id} @ {self.device.name if self.device else 'no-device'} @ {self.organization.slug}"

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "organization")


@strawberry_django.type(
    fakts_models.Hub,
    description="A Hub is a collection of service instances and clients that work together. It represents a deployable configuration for an organization.",
    pagination=True,
    filters=filters.ManagementHubFilter,
    ordering=filters.ManagementHubOrdering,
)
class ManagementHub:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the hub")
    description: str | None = strawberry.field(description="The description of the hub. This should be a human readable description of the hub.")
    creator: ManagementUser = strawberry_django.field(description="The user who created this hub")
    organization: "ManagementOrganization" = strawberry_django.field(description="The organization that owns this hub.")
    instances: list["ManagementServiceInstance"] = strawberry_django.field(
        description="The instances of the hub. A service instance is a configured instance of a service."
    )
    clients: list["ManagementClient"] = strawberry_django.field(description="The clients that are part of this hub. A client is an application that uses the services in the hub.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(
    fakts_models.InstanceAlias,
    description="An alias for a service instance. This is used to provide a more user-friendly name for the instance.",
    filters=filters.ManagementInstanceAliasFilter,
    ordering=filters.ManagementInstanceAliasOrdering,
    pagination=True,
)
class ManagementInstanceAlias:
    id: strawberry.ID
    layer: Optional["ManagementLayer"] = strawberry.field(description="The layer that this alias belongs to.")
    instance: ManagementServiceInstance = strawberry_django.field(description="The instance that this alias belongs to.")
    name: Optional[str] = strawberry.field(description="The name of the alias.")
    kind: str = strawberry.field(description="The kind of alias (relative or absolute).")
    host: Optional[str] = strawberry.field(description="The host of the alias, if its a ABSOLUTE alias (e.g. 'example.com'). If not set, the alias is relative to the layer's domain.")
    port: Optional[int] = strawberry.field(description="The port of the alias, if its a ABSOLUTE alias (e.g. 'example.com:8080'). If not set, the alias is relative to the layer's port.")
    path: Optional[str] = strawberry.field(description="The path of the alias, if its a ABSOLUTE alias (e.g. 'example.com/path'). If not set, the alias is relative to the layer's path.")
    ssl: bool = strawberry.field(description="Is this alias using SSL? If true, the alias will be accessed via https:// instead of http://.")
    challenge: str = strawberry.field(description="The challenge of the alias. This is used to verify that the alias is reachable.")
    usages: list["ManagementUsedAlias"] = strawberry_django.field(description="The usages of this alias by clients.")
    scope: str = strawberry.field(description="The scope of the alias. E.g 'local' means that the alias can only be used within the local network.")
    public: bool = strawberry.field(description="Is this alias publicly reachable? If true, the coordination server can also check the alias's health directly, enabling health checks from the kontrol interface.")

    @strawberry_django.field(description="The organization that owns this alias (via the instance).")
    def organization(self) -> "ManagementOrganization":
        return self.instance.organization

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "instance__organization")


@strawberry_django.type(
    fakts_models.ServiceInstanceMapping,
    ordering=filters.ManagementServiceInstanceMappingOrdering,
    filters=filters.ServiceInstanceMappingFilter,
    pagination=True,
    description="A ServiceInstanceMapping binds one of a client's requirement keys to the service instance that fulfils it.",
)
class ManagementServiceInstanceMapping:
    id: strawberry.ID
    instance: ManagementServiceInstance = strawberry_django.field(description="The service instance this mapping points at.")
    client: "ManagementClient" = strawberry_django.field(description="The client this mapping belongs to.")
    key: str = strawberry.field(description="The requirement key of the client that this mapping fulfils.")
    optional: bool = strawberry.field(description="Is this mapping optional? If a mapping is optional, you can configure the client without this mapping.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "instance__organization")


@strawberry_django.type(
    fakts_models.App,
    ordering=filters.ManagementAppOrdering,
    filters=fakts_filters.AppFilter,
    description="An App is the Arkitekt equivalent of a Software Application. It is a collection of `Releases` that can be all part of the same application. E.g the App `Napari` could have the releases `0.1.0` and `0.2.0`.",
    pagination=True,
)
class ManagementApp:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the app")
    identifier: fakts_scalars.AppIdentifier = strawberry.field(description="The identifier of the app. This should be a globally unique string that identifies the app. We encourage you to use the reverse domain name notation. E.g. `com.example.myapp`")

    releases: list["ManagementRelease"] = strawberry_django.field(description="The releases of the app. A release is a version of the app that can be installed by a user.")

    logo: ManagementMediaStore | None = strawberry.field(description="The logo of the app. This should be a url to a logo that can be used to represent the app.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "organization")


@strawberry.type
class ManagementMachine:
    instance: strawberry.Private[Machine]
    tailnet: strawberry.Private[str]
    layer_id: strawberry.Private[strawberry.ID]
    magic_dns_enabled: strawberry.Private[bool]

    def __init__(self, instance: Machine, tailnet: str, layer_id: strawberry.ID, magic_dns_enabled: bool = False):
         self.instance = instance
         self.tailnet = tailnet
         self.layer_id = layer_id
         # Whether the owning layer has MagicDNS on. Threaded in at construction so the
         # `magic_dns_name` field doesn't hit the DB per machine (avoids N+1 on the list page).
         self.magic_dns_enabled = magic_dns_enabled

    @strawberry.field
    def id(self) -> strawberry.ID:
        return strawberry.ID(self.instance.id)

    @strawberry.field
    def local_id(self) -> str:
        return self.instance.id

    @strawberry.field
    def name(self) -> str:
        return self.instance.name

    @strawberry.field
    def ipv4(self) -> Optional[str]:
        return self.instance.ipv4

    @strawberry.field
    def ipv6(self) -> Optional[str]:
        return self.instance.ipv6

    @strawberry.field
    def ephemeral(self) -> bool:
        return self.instance.ephemeral

    @strawberry.field
    def connected(self) -> bool:
        return self.instance.connected

    @strawberry.field
    def last_seen(self) -> Optional[datetime.datetime]:
        return self.instance.last_seen

    @strawberry.field
    def tags(self) -> List[str]:
        return self.instance.tags

    @strawberry.field(description="The operating system reported by the machine, if known.")
    def os(self) -> Optional[str]:
        return getattr(self.instance, "os", None)

    @strawberry.field(description="When the machine's node key expires, if key expiry is enabled.")
    def key_expiry(self) -> Optional[datetime.datetime]:
        return getattr(self.instance, "key_expiry", None)

    @strawberry.field(description="Whether the machine is authorized on the tailnet, or null when unknown.")
    def authorized(self) -> Optional[bool]:
        return getattr(self.instance, "authorized", None)

    @strawberry.field(description="Whether the machine is an external node (belongs to another tailnet), or null when unknown.")
    def is_external(self) -> Optional[bool]:
        return getattr(self.instance, "is_external", None)

    @strawberry.field(
        description="The machine's MagicDNS name (e.g. `myhost.mytailnet.mesh.example.com`), or null "
        "when MagicDNS is disabled for the mesh or no suffix is configured."
    )
    def magic_dns_name(self) -> Optional[str]:
        # MagicDNS off for this mesh -> the name would not resolve, so surface nothing.
        if not self.magic_dns_enabled:
            return None
        # Prefer the real FQDN reported by ionscale over deriving it (avoids guessing the format).
        fqdn = getattr(self.instance, "fqdn", None)
        if fqdn:
            return fqdn
        # Derive the MagicDNS name. ionscale namespaces machines under their tailnet, so the
        # form is `<name>.<tailnet>.<suffix>` (e.g. gpu-01.myorg.mesh.arkitekt.live).
        suffix = getattr(settings, "IONSCALE_MAGIC_DNS_SUFFIX", None)
        if suffix and self.instance.name:
            parts = [self.instance.name]
            if self.tailnet:
                parts.append(self.tailnet)
            parts.append(suffix)
            return ".".join(parts)
        return None



@strawberry.type(description="One machine's standing under tailnet lock.")
class ManagementTailnetLockNode:
    machine_id: str = strawberry.field(description="The ionscale machine id.")
    name: str = strawberry.field(description="The machine's name.")
    signed: bool = strawberry.field(
        description="Whether the machine's node key is signed by the key authority. An unsigned machine is registered but unreachable by locked peers until an existing signing node signs it."
    )


@strawberry.type(
    description="The state of tailnet lock for a mesh. Tailnet lock has two independent halves: the control plane grants the capability, but the key authority itself is created by running `tailscale lock init` on a machine. A mesh can sit with the capability granted and no authority indefinitely."
)
class ManagementTailnetLockStatus:
    capability_enabled: bool = strawberry.field(
        description="Whether the control plane grants machines the tailnet-lock capability. Required before `tailscale lock init` will work."
    )
    authority_active: bool = strawberry.field(
        description="Whether a key authority actually exists and is enforcing signatures. Only a client can bring this about."
    )
    authority_disabled: bool = strawberry.field(
        description="Whether an authority existed and was shut down with a disablement secret. Distinct from never having had one."
    )
    head: str = strawberry.field(description="The head of the tailnet key authority chain, empty when there is no authority.")
    nodes: List[ManagementTailnetLockNode] = strawberry.field(description="Every machine on the mesh and whether its key is signed.")


@strawberry_django.type(
    fakts_models.IonscaleLayer,
    description="A Layer is a transport layer that needs to be used to reach an alias. E.g a VPN layer or a Tor layer.",
    pagination=True,
    filters=filters.ManagementLayerFilter,
    ordering=filters.ManagementLayerOrdering,
)
class ManagementLayer:
    id: strawberry.ID
    organization: "ManagementOrganization" = strawberry_django.field(description="The organization that owns this alias.")
    kind: enums.LayerKind = strawberry.field(description="The kind of the layer. E.g. `VPN` or `TOR`")
    name: str = strawberry.field(description="The name of the layer")
    description: str | None = strawberry.field(description="The description of the layer. This should be a human readable description of the layer.")
    logo: ManagementMediaStore | None = strawberry.field(description="The logo of the layer. This should be a url to a logo that can be used to represent the layer.")
    aliases: list["ManagementInstanceAlias"] = strawberry_django.field(
        description="The aliases that are reachable through this layer. An alias is a way to reach a service instance over this transport layer."
    )
    tailnet_name: str = strawberry.field(description="The tailnet name of the layer. This is only set for Ionscale layers.")
    magic_dns_enabled: bool = strawberry.field(description="Whether MagicDNS is enabled for this mesh.")
    https_enabled: bool = strawberry.field(description="Whether HTTPS certificates are enabled for this mesh. Requires MagicDNS.")
    auth_keys: list["ManagementIonscaleAuthKey"] = strawberry_django.field(description="The auth keys that are associated with this layer.")
    
    
    
    @strawberry.field(
        description="Tailnet lock status for this mesh (only works for IonscaleLayers). Null when the layer has no tailnet or ionscale is unreachable."
    )
    def tailnet_lock(self, info: Info) -> Optional[ManagementTailnetLockStatus]:
        if not self.tailnet_name:
            return None
        try:
            status = get_ionscale_repo().get_tailnet_lock_status(self.tailnet_name)
        except Exception:
            # Same degradation as `machine` above: a mesh page should still
            # render when ionscale is down, rather than failing the whole query.
            return None
        return ManagementTailnetLockStatus(
            capability_enabled=status.capability_enabled,
            authority_active=status.authority_active,
            authority_disabled=status.authority_disabled,
            head=status.head,
            nodes=[
                ManagementTailnetLockNode(machine_id=n.machine_id, name=n.name, signed=n.signed)
                for n in status.nodes
            ],
        )

    @strawberry.field(description="The machines associated with this layer (only works for IonscaleLayers)")
    def machines(self, info: Info) -> List[ManagementMachine]:
        if self.tailnet_name:
             machines = get_ionscale_repo().list_machines(self.tailnet_name)
             return [ManagementMachine(instance=m, tailnet=self.tailnet_name, layer_id=self.id, magic_dns_enabled=self.magic_dns_enabled) for m in machines]
        return []

    @strawberry.field(description="A specific machine associated with this layer (only works for IonscaleLayers)")
    def machine(self, info: Info, id: str) -> Optional[ManagementMachine]:
        """Look one machine up *within this layer's tailnet*.

        `ionscale machines get --machine-id` is a global lookup that takes no
        tailnet argument, so fetching by raw id and trusting the result returned
        another organization's machine — hostname, OS, tailnet IPs, ACL tags,
        key expiry — labelled as if it were on the caller's own tailnet. Resolve
        through `list_machines(self.tailnet_name)` instead, which is the same
        pattern the root `machine` resolver uses and documents as its
        authorization check.
        """
        if not self.tailnet_name:
            return None
        try:
            machines = get_ionscale_repo().list_machines(self.tailnet_name)
        except Exception:
            return None
        match = next((m for m in machines if str(m.id) == str(id)), None)
        if match is None:
            return None
        return ManagementMachine(instance=match, tailnet=self.tailnet_name, layer_id=self.id, magic_dns_enabled=self.magic_dns_enabled)

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(
    fakts_models.IonscaleAuthKey,
    filters=filters.ManagementIonscaleAuthKeyFilter,
    ordering=filters.ManagementIonscaleAuthKeyOrdering,
    pagination=True,
)
class ManagementIonscaleAuthKey:
    id: strawberry.ID
    created_at: datetime.datetime
    ephemeral: bool
    tags: list[str]
    creator: "ManagementUser"
    layer: ManagementLayer

    @strawberry_django.field(
        description="The pre-authorized mesh key. A live mesh-join credential: only returned to the key's creator and to the organization's owner/admins, null for everyone else.",
        only=["key", "creator", "layer"],
        select_related=["layer"],
    )
    def key(self, info: Info) -> str | None:
        caller = _caller(info)
        if caller is None:
            return None
        if self.creator_id == caller.id or is_owner_or_admin(caller, self.layer.organization):
            return self.key
        return None

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(layer__organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(
    fakts_models.Release,
    ordering=filters.ManagementReleaseOrdering,
    description="A Release is a version of an app. Releases might change over time. E.g. a release might be updated to fix a bug, and the release might be updated to add a new feature. This is why they are the home for `scopes` and `requirements`, which might change over the release cycle.",
)
class ManagementRelease:
    id: strawberry.ID
    app: ManagementApp = strawberry_django.field(description="The app that this release belongs to.")
    version: fakts_scalars.Version = strawberry.field(description="The version of the release. This should be a string that identifies the version of the release. We enforce semantic versioning notation. E.g. `0.1.0`. The version is unique per app.")
    name: str = strawberry.field(description="The name of the release. This should be a string that identifies the release beyond the version number. E.g. `canary`.")
    logo: ManagementMediaStore | None = strawberry.field(description="The logo of the release. This should be a url to a logo that can be used to represent the release.")
    scopes: list[str] = strawberry.field(description="The scopes of the release. Scopes are used to limit the access of a client to a user's data. They represent app-level permissions.")
    clients: list["ManagementClient"] = strawberry_django.field(description="The clients of the release")

    @strawberry_django.field(description="The requirements of the release: the services a client of this release needs mapped before it can run.")
    def requirements(self, info: Info) -> list[ManagementStagingRequirement]:
        raw = self.requirements
        if isinstance(raw, dict):
            raw = list(raw.values())
        if not isinstance(raw, list):
            return []
        out: list[ManagementStagingRequirement] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(ManagementStagingRequirement.from_pydantic(base_models.Requirement(**entry)))
            except (PydanticValidationError, TypeError):
                continue
        return out

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "app__organization")


@strawberry_django.type(
    fakts_models.DeviceGroup,
    description="A DeviceGroup is a group of compute nodes that can be used to run clients. DeviceGroups can be used to group compute nodes by location, hardware type, or any other criteria.",
    pagination=True,
    filters=filters.ManagementDeviceGroupFilter,
    ordering=filters.ManagementDeviceGroupOrdering,
)
class ManagementDeviceGroup:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the device group.")

    @strawberry_django.field(description="The number of devices in this device group.")
    def devices(self, info: Info) -> list["ManagementDevice"]:
        return self.devices.all()

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(fakts_models.Device, filters=filters.ManagementDeviceFilter, ordering=filters.ManagementDeviceOrdering, pagination=True)
class ManagementDevice:
    id: strawberry.ID
    name: str | None
    node_id: strawberry.ID
    clients: list["ManagementClient"]
    organization: "ManagementOrganization" = strawberry_django.field(description="The organization that owns this compute node.")
    service_instances: list[ManagementServiceInstance] = strawberry_django.field(description="The service instances that are associated with this compute node.")
    device_groups: list[ManagementDeviceGroup] = strawberry_django.field(description="The device groups that belong to this compute node.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        # Devices are scoped to every organization the caller is a member of (matching
        # ManagementDeviceGroup / ManagementClient). Callers narrow to a single org via
        # the `organization` filter (e.g. the /organization/:orgId/devices page); scoping
        # to only the active org would hide devices of orgs the user is legitimately in.
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry.type(description="A Public Source is a source of information about a client that is publicly available. E.g. a GitHub repository or a website.")
class ManagementPublicSource:
    kind: str = strawberry.field(description="The kind of the public source. E.g. `github` or `website`.")
    url: str = strawberry.field(description="The url of the public source.")


@strawberry_django.type(fakts_models.UsedAlias, pagination=True, ordering=filters.ManagementUsedAliasOrdering)
class ManagementUsedAlias:
    id: strawberry.ID
    key: str
    alias: Optional[ManagementInstanceAlias] = strawberry.field(description="The alias that is used.")
    client: "ManagementClient" = strawberry_django.field(description="The client that is using the alias.")
    valid: bool = strawberry.field(description="Is the alias valid for the client?")
    reason: Optional[str] = strawberry.field(description="If the alias is not valid, the reason why it is not valid.")

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _membership_scoped(queryset, info, "client__organization")


@strawberry.type(description="One per-requirement entry in a client report snapshot: which alias was resolved for a requirement key and whether it was reachable.")
class ManagementReportEntry:
    key: str = strawberry.field(description="The requirement key this entry reports on.")
    valid: bool = strawberry.field(description="Was the resolved alias reachable when the client reported?")
    reason: Optional[str] = strawberry.field(description="If the alias was not reachable, the reason the client gave.")
    alias: Optional[ManagementInstanceAlias] = strawberry.field(description="The alias the client resolved this requirement to (resolved live; may be null if it no longer exists or none was reported).")


@strawberry_django.type(
    fakts_models.Report,
    description="A point-in-time snapshot of a client's self-report (functional flag + per-requirement alias reports). Only the latest few per client are retained.",
    filters=filters.ManagementReportFilter,
    ordering=filters.ManagementReportOrdering,
    pagination=True,
)
class ManagementReport:
    id: strawberry.ID
    client: "ManagementClient" = strawberry_django.field(description="The client this report belongs to.")
    functional: bool = strawberry_django.field(description="Did the client report itself as functional at report time?")
    created_at: datetime.datetime = strawberry_django.field(description="When the client submitted this report.")
    resolved_at: Optional[datetime.datetime] = strawberry_django.field(description="When an operator acknowledged this report; null while it still needs attention.")
    resolved_by: Optional["ManagementUser"] = strawberry_django.field(description="The member who acknowledged this report.")
    resolution_note: Optional[str] = strawberry_django.field(description="Optional note the operator left when acknowledging this report.")

    @strawberry_django.field(description="Has this report been acknowledged? Acknowledging does not change what the client reported — it only takes the client off the dashboard's action list until its next report.")
    def is_resolved(self, info: Info) -> bool:
        return self.resolved_at is not None

    @strawberry_django.field(description="The per-requirement alias reports captured in this snapshot.")
    def entries(self, info: Info) -> list[ManagementReportEntry]:
        entries: list[ManagementReportEntry] = []
        for key, report in (self.alias_reports or {}).items():
            alias_id = report.get("alias_id")
            alias = fakts_models.InstanceAlias.objects.filter(id=alias_id).first() if alias_id else None
            entries.append(
                ManagementReportEntry(
                    key=key,
                    valid=report.get("valid", False),
                    reason=report.get("reason"),
                    alias=alias,
                )
            )
        return entries

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(client__organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(
    fakts_models.Client,
    description="""A client is a way of authenticating users with a release.
 The strategy of authentication is defined by the kind of client. And allows for different authentication flow. 
 E.g a client can be a DESKTOP app, that might be used by multiple users, or a WEBSITE that wants to connect to a user's account, 
 but also a DEVELOPMENT client that is used by a developer to test the app. The client model thinly wraps the oauth2 client model, which is used to authenticate users.""",
    filters=filters.ManagementClientFilter,
    ordering=filters.ManagementClientOrdering,
    pagination=True,
)
class ManagementClient:
    id: strawberry.ID
    functional: bool = strawberry_django.field(description="Is this client functional? A non-functional client cannot be used to authenticate users.")
    release: ManagementRelease | None = strawberry_django.field(description="The release that this client belongs to. Null for clients that are not bound to an app release: hub identities, relying parties, and registrations that are still awaiting approval.")
    public: bool = strawberry_django.field(description="Is this client public? A public client has no client secret and authenticates through a user-facing flow (device code / PKCE) instead.")

    @strawberry_django.field(
        description="The kind of the client. The kind defines the authentication flow that is used to authenticate users with this client.",
        only=["kind"],
    )
    def kind(self, info: Info) -> fakts_enums.ClientKind:
        # The strawberry enum members wrap `enum_value(...)` definitions, so map
        # by member name (the model stores the lowercase value).
        try:
            return fakts_enums.ClientKind[str(self.kind or "").upper()]
        except KeyError:
            return fakts_enums.ClientKind.DEVELOPMENT

    @strawberry_django.field(
        description="The operational role of the client: INTERFACE (a human interface operated by a user) vs AGENT (an autonomous client authorized once that then runs unattended, receiving tasks).",
        only=["role"],
    )
    def role(self, info: Info) -> fakts_enums.ClientRole:
        try:
            return fakts_enums.ClientRole[str(self.role or "").upper()]
        except KeyError:
            return fakts_enums.ClientRole.INTERFACE
    @strawberry_django.field(
        description="The user this client acts for (derived from its membership).",
        only=["membership"],
        select_related=["membership__user"],
    )
    def user(self, info: Info) -> ManagementUser | None:
        return self.membership.user if self.membership_id else None

    organization: ManagementOrganization | None = strawberry_django.field(description="The organization this client is bound to. Null until a registration is approved, and for global relying-party clients.")
    logo: ManagementMediaStore | None = strawberry_django.field(description="The logo of the release. This should be a url to a logo that can be used to represent the release.")
    name: str = strawberry_django.field(description="The name of the client. This is a human readable name of the client.")
    mappings: list["ManagementServiceInstanceMapping"] = strawberry_django.field(description="The mappings of the client. A mapping is a mapping of a service to a service instance. This is used to configure the hub.")
    used_aliases: list[ManagementUsedAlias] = strawberry_django.field(description="The aliases that are used by this client.")
    last_reported_at: datetime.datetime | None = strawberry_django.field(description="The last time the client reported in. This is used to determine if the client is active or not.")
    scopes: list["ManagementScope"] = strawberry_django.field(description="The scopes that are granted to this client.")
    reports: list["ManagementReport"] = strawberry_django.field(description="The retained self-reports of this client, most recent first.")
    last_healthy_report: Optional["ManagementReport"] = strawberry_django.field(description="The most recent report where the client was functional; null if it has never reported healthy.")
    latest_report_resolved: bool = strawberry_django.field(description="Has an operator acknowledged this client's most recent report? Reset by every incoming report.")
    report_requested_at: Optional[datetime.datetime] = strawberry_django.field(description="When an operator asked this client to re-report its configuration; null when nothing is pending. While set, the client's token responses carry `please_report`.")
    report_requested_by: Optional[ManagementUser] = strawberry_django.field(description="The operator who asked this client to re-report.")

    @strawberry_django.field(description="Is this client being asked to re-report its configuration? True until the client's next report arrives.", only=["report_requested_at"])
    def please_report(self, info: Info) -> bool:
        return self.report_requested_at is not None

    @strawberry_django.field(description="The app manifest this client was registered with, if it parses.")
    def manifest(self, info: Info) -> Optional[ManagementStagingManifest]:
        if not self.manifest:
            return None
        try:
            return ManagementStagingManifest.from_pydantic(base_models.Manifest(**self.manifest))
        except (PydanticValidationError, TypeError):
            return None

    @strawberry_django.field(description="The issue url of the client. This is the url where users can report issues and get more information about the client.")
    def issue_url(self, info: Info) -> str | None:
        for source in self.public_sources:
            if source.get("kind", "").lower() == "github":
                return source.get("url") + "/issues/new"

        return None

    @strawberry_django.field(description="The public sources of the client. These are the public sources where users can find more information about the client.")
    def public_sources(self, info: Info) -> list[ManagementPublicSource]:
        sources = []
        for source in self.public_sources:
            sources.append(
                ManagementPublicSource(
                    kind=source.get("kind"),
                    url=source.get("url"),
                )
            )
        return sources

    @strawberry_django.field(description="The device (compute node) this client runs on, if it registered one.")
    def device(self, info: Info) -> Optional["ManagementDevice"]:
        return self.node

    @strawberry_django.field(description="The client's most recent report; null if it has never reported.")
    def latest_report(self, info: Info) -> Optional["ManagementReport"]:
        return self.reports.order_by("-created_at", "-id").first()

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(organization__memberships__user=info.context.request.user).distinct()


@strawberry_django.type(fakts_models.Client, filters=filters.ManagementOAuth2ClientFilter, pagination=True, description="""The OAuth2 identity view of a (unified) client — what the consent page needs to display.""")
class ManagementOAuth2Client:
    id: strawberry.ID
    name: str
    client_id: str
    kind: str


@strawberry_django.type(fakts_models.DeviceCode, filters=filters.ManagementDeviceCodeFilter, pagination=True, description="""A DeviceCode is used for the device code flow for client authentication.""")
class ManagementDeviceCode:
    id: strawberry.ID
    created_at: datetime.datetime
    expires_at: datetime.datetime
    code: str
    denied: bool

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        """By-id access is scoped to codes accepted into one of the caller's
        organizations. A *pending* code has no organization yet and is therefore
        not reachable by id at all — the `...ByCode` queries are the capability
        path for it (the code is what the device displayed)."""
        return _membership_scoped(queryset, info, "organization")

    @strawberry_django.field(
        description="The (bound) client this code was accepted into. Null while pending — the staged registration exists but is not approved yet.",
        only=["client"],
    )
    def client(self, info: Info) -> Optional[ManagementClient]:
        return self.client if self.client.membership_id else None

    @strawberry_django.field(description="The requested client kind (written onto the staged client at registration)", only=["client"])
    def staging_kind(self, info: Info) -> str:
        return self.client.kind

    @strawberry_django.field(description="Whether this device code is for a public client", only=["client"])
    def staging_public(self, info: Info) -> bool:
        return self.client.public

    @strawberry_django.field(description="The staging manifest for this device code")
    def staging_manifest(self, info: Info) -> Optional[ManagementStagingManifest]:
        if not self.staging_manifest:
            return None
        try:
            return ManagementStagingManifest.from_pydantic(base_models.Manifest(**self.staging_manifest))
        except (PydanticValidationError, TypeError):
            return None


@strawberry_django.type(fakts_models.DeviceCode, filters=filters.ManagementHubDeviceCodeFilter, pagination=True, description="""A HubDeviceCode is a hub-kind staged authorization (unified DeviceCode model).""")
class ManagementHubDeviceCode:
    id: strawberry.ID
    created_at: datetime.datetime
    expires_at: datetime.datetime
    code: str
    denied: bool

    @strawberry_django.field(
        description="The hub this code was accepted into. Null while pending, and null unless the caller is a member of the hub's organization.",
        only=["client"],
    )
    def hub(self, info: Info) -> Optional[ManagementHub]:
        try:
            hub = self.client.hub_identity
        except fakts_models.Hub.DoesNotExist:
            return None
        if hub is None:
            return None
        caller = _caller(info)
        if caller is None:
            return None
        if not hub.organization.memberships.filter(user=caller).exists():
            return None
        return hub

    @strawberry_django.field(description="The hub manifest for this device code")
    def manifest(self, info: Info) -> Optional[ManagementHubManifest]:
        if not self.staging_manifest:
            return None
        try:
            return ManagementHubManifest.from_pydantic(base_models.HubManifest(**self.staging_manifest))
        except (PydanticValidationError, TypeError):
            return None

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        """See ``ManagementDeviceCode.get_queryset``: pending hub codes are reachable
        only through ``hubDeviceCodeByCode``."""
        return _membership_scoped(queryset, info, "organization")


@strawberry_django.type(fakts_models.MeshDeviceCode, filters=filters.ManagementMeshDeviceCodeFilter, pagination=True, description="""A MeshDeviceCode is used for the device-code flow that lets a machine join an organization's mesh.""")
class ManagementMeshDeviceCode:
    id: strawberry.ID
    user: Optional[ManagementUser]
    created_at: datetime.datetime
    expires_at: datetime.datetime
    code: str
    requested_machine_name: Optional[str]
    machine_name: Optional[str]
    description: Optional[str]
    denied: bool

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        """A mesh device code carries no organization of its own; it is tied to a
        tenant only once accepted (through the minted key's layer) or to the user
        who requested it. Pending codes are reachable only via ``meshDeviceCodeByCode``."""
        user = info.context.request.user
        return queryset.filter(
            Q(user=user) | Q(auth_key__layer__organization__memberships__user=user)
        ).distinct()
    # NB: the minted pre-auth key is deliberately NOT exposed here. The by-code lookup is
    # unauthenticated-preview-friendly, so surfacing the secret would leak a live mesh-join
    # credential to anyone who learns the code. The machine receives the key only via the
    # REST /f/meshchallenge/ poll (gated on the secret challenge_code).


@strawberry_django.type(fakts_models.RedeemToken, filters=filters.ManagementRedeemTokenFilter, pagination=True, ordering=filters.ManagementRedeemTokenOrdering)
class ManagementRedeemToken:
    id: strawberry.ID
    created_at: datetime.datetime
    expires_at: datetime.datetime | None
    hub: ManagementHub = strawberry_django.field(description="The hub that this redeem token grants access to.")
    client: ManagementClient | None = strawberry.field(description="The client that this redeem token belongs to.")
    user: ManagementUser = strawberry_django.field(description="The user that this redeem token belongs to.")

    @strawberry_django.field(
        description="The redeem token. A bearer credential: only returned to the user who issued it and to the hub organization's owner/admins, null for everyone else.",
        only=["token", "user", "hub"],
        select_related=["hub"],
    )
    def token(self, info: Info) -> str | None:
        caller = _caller(info)
        if caller is None:
            return None
        if self.user_id == caller.id or is_owner_or_admin(caller, self.hub.organization):
            return self.token
        return None

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return queryset.filter(hub__organization__memberships__user=info.context.request.user).distinct()

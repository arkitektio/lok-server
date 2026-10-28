import logging
import secrets
from typing import TYPE_CHECKING, Any, Optional, List, Tuple

import requests
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.utils import timezone
import uuid
from karakter import fields, datalayer
from karakter.deeplinks import default_deeplink_apps

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager

logger = logging.getLogger(__name__)


# A presigned media URL is handed out unchanged for this long, and is valid for twice as long.
PRESIGNED_KEPT_SECONDS = 3600
PRESIGNED_VALID_SECONDS = 2 * PRESIGNED_KEPT_SECONDS


class S3Store(models.Model):
    """Base model for objects stored in S3.

    Attributes mirror essential S3 metadata used elsewhere in the
    codebase.
    """

    path = fields.S3Field(null=True, blank=True, help_text="The stodre of the image", unique=True)
    key = models.CharField(max_length=1000)
    bucket = models.CharField(max_length=1000)
    populated = models.BooleanField(default=False)


class MediaStore(S3Store):
    """Small helper around S3-backed stored objects.

    Provides convenience helpers for generating presigned URLs and
    uploading content.
    """

    def get_presigned_url(self, info, datalayer: datalayer.Datalayer, host: Optional[str] = None) -> str:
        """Return a presigned URL for the stored S3 object.

        The same URL for an hour: a fresh signature on every resolve made every answer carry a
        new URL for every avatar and logo, so a client re-rendered and the browser downloaded
        each image again on each refetch. Minted valid for two hours and kept for one (in
        Django's cache: per process by default, shared when a deployment configures one), so a
        URL handed out is always good for at least an hour, and it asks the store to let the
        browser keep the image for that hour.

        Args:
            info: GraphQL resolver info (passed through to datalayer if used).
            datalayer: object exposing ``s3`` boto3 session/client.
            host: optional host to replace the endpoint with when returning.
        """
        cache_key = f"lok:presigned:{self.bucket}:{self.key}"
        url = cache.get(cache_key)
        if url is None:
            url = datalayer.s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": self.key,
                    "ResponseCacheControl": f"private, max-age={PRESIGNED_KEPT_SECONDS}",
                },
                ExpiresIn=PRESIGNED_VALID_SECONDS,
            )
            cache.set(cache_key, url, PRESIGNED_KEPT_SECONDS)
        return url.replace(getattr(settings, "AWS_S3_ENDPOINT_URL", ""), host or "")

    def fill_info(self) -> None:
        """Populate or refresh derived metadata for the stored object."""
        pass

    def put_file(self, datalayer: datalayer.Datalayer, file) -> None:
        """Upload a file-like object to the object's S3 location and save model."""
        s3 = datalayer.s3
        s3.upload_fileobj(file, self.bucket, self.key)
        self.save()


def generate_device_salt() -> str:
    """Random per-organization salt used to hash device ids (64 hex chars)."""
    return secrets.token_hex(32)


class NotificationsMuted(Exception):
    """Raised when a member has opted an organization out of notifying them."""


class Organization(models.Model):
    """An Organization in the System

    An Organization is a group of users that can be used to manage access to resources.
    Each organization has a unique name and can have multiple users associated with it.
    """

    if TYPE_CHECKING:
        # What Django adds, said for the type checker (it does not run django-stubs' plugin).
        @property
        def owner_id(self) -> int:
            """The owner's primary key."""
            ...

        @property
        def memberships(self) -> "RelatedManager[Membership]":
            """Who belongs to the organization: its members and its guests."""
            ...

    slug = models.CharField(max_length=1000, null=True, blank=True, unique=True)
    name = models.CharField(max_length=1000, null=True, blank=True)
    description = models.CharField(max_length=4000, null=True, blank=True)
    # Deprecated: the canonical organization logo is `OrganizationProfile.avatar`.
    # Writes go there; this column is only read as a fallback for old rows.
    avatar = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    owner = models.ForeignKey("User", on_delete=models.CASCADE, related_name="owned_organizations")
    brand_hue = models.FloatField(
        null=True,
        blank=True,
        help_text="The organization's default brand hue (0–360). Members can override "
        "it with their own membership brand hue.",
    )
    brand_chroma = models.FloatField(
        null=True,
        blank=True,
        help_text="The organization's default brand chroma (0–1). Members can override "
        "it with their own membership brand chroma.",
    )
    require_device_auth = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="When set, clients created in this organization must present a device "
        "device_id (device authentication). None/False means device auth is not required.",
    )
    access_token_lifetime = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        help_text="Access-token lifetime in seconds for tokens issued to this organization's "
        "clients. None means the server default (authapp.server.ACCESS_TOKEN_EXPIRES_IN, one "
        "hour). Clamped into [MIN_ACCESS_TOKEN_EXPIRES_IN, MAX_ACCESS_TOKEN_EXPIRES_IN] at "
        "token generation, so a stale or oversized value can never outlive the cap.",
    )
    deeplink_apps = models.JSONField(
        default=default_deeplink_apps,
        blank=True,
        help_text="The apps kontrol may forward this organization's deep links to: a list of "
        "{protocol, name, install_url, mobile}. The first entry is the default (on a mobile "
        "device, the first with `mobile`). Empty switches forwarding off. See "
        "karakter.deeplinks for the rules.",
    )
    public_link_preview = models.BooleanField(
        default=False,
        help_text="Opt-in: show this organization's name, description and logo on kontrol's "
        "link pages to visitors who are not members (or not signed in). Off, a link reveals "
        "nothing about the organization it belongs to.",
    )
    # Server-only secret. Combined with SECRET_KEY to hash device ids so the same
    # device hashes differently across organizations and is never stored in the clear.
    device_salt = models.CharField(max_length=64, default=generate_device_salt, editable=False)

    def __str__(self):
        return self.name or self.slug or "Unnamed Organization"


class Role(models.Model):
    if TYPE_CHECKING:
        @property
        def memberships(self) -> "RelatedManager[Membership]":
            """The memberships that hold this role."""
            ...

    identifier = models.CharField(max_length=1000, null=True, blank=True)
    description = models.CharField(max_length=4000, null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="roles")
    creating_instance = models.ForeignKey("fakts.ServiceInstance", on_delete=models.CASCADE, null=True, blank=True)
    is_builtin = models.BooleanField(default=False, help_text="If this role is a built-in role that cannot be deleted (admin)")
    is_admin_role = models.BooleanField(
        default=False,
        db_default=False,
        help_text="The role that lets a member do everything in the service declaring it. Lok gives it to the organization's owner and admins (karakter.service_roles).",
    )
    is_default_role = models.BooleanField(
        default=False,
        db_default=False,
        help_text="The role every full member holds in the service declaring it, unless an admin took it away (karakter.service_roles).",
    )
    used_by = models.ManyToManyField("fakts.ServiceInstance", related_name="roles", blank=True)

    class Meta:
        unique_together = ("identifier", "organization")


class RoleSet(models.Model):
    """A named bundle of Roles within an organization.

    Lets an owner group several roles together so they can be applied at once —
    either to seed an invite (the invitee receives every role in the set) or to
    grant to an existing member in a single action.
    """

    name = models.CharField(max_length=1000)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="role_sets")
    roles = models.ManyToManyField(Role, related_name="role_sets", blank=True)

    class Meta:
        unique_together = ("name", "organization")

    def __str__(self):
        return f"{self.name} ({self.organization})"


class Scope(models.Model):
    identifier = models.CharField(max_length=1000, null=True, blank=True)
    description = models.CharField(max_length=4000, null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="scopes")
    creating_instance = models.ForeignKey("fakts.ServiceInstance", on_delete=models.CASCADE, null=True, blank=True)
    is_builtin = models.BooleanField(default=False, help_text="If this scope is a built-in scope that cannot be deleted (admin)")
    is_all_scope = models.BooleanField(
        default=False,
        db_default=False,
        help_text="The scope that grants an app everything the service declaring it offers.",
    )
    used_by = models.ManyToManyField("fakts.ServiceInstance", related_name="scopes", blank=True)

    class Meta:
        unique_together = ("identifier", "organization")


class Membership(models.Model):
    """A Membership of a User in an Organization with a Role"""

    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    roles = models.ManyToManyField(Role, related_name="memberships", blank=True)
    is_admin = models.BooleanField(
        default=False,
        db_default=False,
        editable=False,
        help_text="Whether the member holds the organization's admin role: recomputed from `roles` "
        "whenever they change (karakter.roles), never set by hand.",
    )
    created_through = models.ForeignKey("Invite", on_delete=models.SET_NULL, null=True, related_name="created_memberships")
    automatic_roles = models.ManyToManyField(
        Role,
        related_name="automatically_held_by",
        blank=True,
        help_text="The roles of `roles` lok gave by itself (a service's admin or default role): the ones it takes back when they stop applying (karakter.service_roles).",
    )
    withheld_roles = models.ManyToManyField(
        Role,
        related_name="withheld_from",
        blank=True,
        help_text="Default roles an admin took away from this member: lok does not give them back.",
    )
    brand_hue = models.FloatField(
        null=True,
        blank=True,
        help_text="Personal brand hue (0–360) this member picked for the organization. "
        "Tints the UI while this organization is active.",
    )
    brand_chroma = models.FloatField(
        null=True,
        blank=True,
        help_text="Personal brand chroma (0–1) this member picked for the organization. "
        "Sets how saturated the tint is while this organization is active.",
    )
    allow_notifications = models.BooleanField(
        default=True,
        help_text="Whether this organization may push notifications to the member's "
        "registered devices. Registering a device (in the companion app) is the "
        "global consent; this flag is the per-organization mute.",
    )

    class Meta:
        unique_together = ("user", "organization")

    @property
    def is_scoped(self) -> bool:
        """Whether this membership only reaches the collaborations granted to it (a guest).

        There is no flag for it: a membership with collaboration grants (live
        or expired) is scoped, one without is a full member.
        ``karakter.collaboration_grants`` keeps the two from mixing: a scoped
        membership never holds roles, and removing its last grant removes the
        membership instead of leaving a full member behind.
        """
        return self.collaboration_grants.exists()

    def get_user_id(self):
        return str(self.user.pk)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the membership, never its ``is_admin``: that is recomputed from the roles
        (``karakter.roles``), and a membership loaded before its roles changed would write the
        old value back."""
        if self.pk is not None and not kwargs.get("force_insert") and kwargs.get("update_fields") is None:
            kwargs["update_fields"] = [field.name for field in self._meta.concrete_fields if not field.primary_key and field.name != "is_admin"]
        super().save(*args, **kwargs)

    def notify(self, title: str, message: str) -> List[Tuple[Optional[int], str]]:
        """Send an organization notification to this member's devices.

        The per-organization opt-in is enforced here rather than at the call
        site, so no future caller can route around it: a muted membership is
        never a delivery, whatever the sender believes.

        Raises:
            NotificationsMuted: when the member has opted this organization out.
        """
        if not self.allow_notifications:
            raise NotificationsMuted(
                "This member has turned off notifications from this organization."
            )
        return self.user.notify(title, message)


class RoleRequest(models.Model):
    """A member's request to be granted an additional Role in their Organization.

    A request only makes sense for an existing membership, so it hangs off the
    Membership (which pins the user and organization) plus the Role being asked
    for. The organization's owner or one of its admins approves or declines it;
    approval adds the role to the membership.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="role_requests")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="requests")
    reason = models.CharField(
        max_length=2000, null=True, blank=True, help_text="Optional note from the member explaining the request."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_role_requests"
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # At most one *pending* request per (membership, role); resolved
            # requests don't block asking again later.
            models.UniqueConstraint(
                fields=["membership", "role"],
                condition=models.Q(status="pending"),
                name="unique_pending_role_request",
            )
        ]

    def approve(self, user):
        self.membership.roles.add(self.role)
        self.status = self.Status.APPROVED
        self.resolved_by = user
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "responded_at"])

    def decline(self, user):
        self.status = self.Status.DECLINED
        self.resolved_by = user
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "responded_at"])


class MembershipRequest(models.Model):
    """A User's request to become a member of an Organization they are not in.

    The counterpart of an Invite, started from the other side: someone who was
    shared a link into an organization asks to be let in, and the organization's
    owner or one of its admins approves or declines. Approval creates the
    Membership with the roles the approver picked.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="membership_requests")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="membership_requests")
    reason = models.CharField(
        max_length=2000, null=True, blank=True, help_text="Optional note from the user explaining the request."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_membership_requests"
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    created_membership = models.ForeignKey(
        Membership, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_through_request"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one *pending* request per (user, organization); resolved
            # requests don't block asking again later.
            models.UniqueConstraint(
                fields=["user", "organization"],
                condition=models.Q(status="pending"),
                name="unique_pending_membership_request",
            )
        ]

    def approve(self, user, roles=None):
        """Let the requester in. `roles` are Role rows of this organization;
        without any the member gets `guest`, as an invite without roles does."""
        membership, _ = Membership.objects.get_or_create(user=self.user, organization=self.organization)
        if membership.is_scoped:
            from karakter.collaboration_grants import upgrade_to_full

            # They were a guest of some collaborations; now they are in.
            upgrade_to_full(membership)
        if not roles:
            roles = list(Role.objects.filter(identifier="guest", organization=self.organization))
        membership.roles.add(*roles)
        self.status = self.Status.APPROVED
        self.resolved_by = user
        self.responded_at = timezone.now()
        self.created_membership = membership
        self.save(update_fields=["status", "resolved_by", "responded_at", "created_membership"])
        return membership

    def decline(self, user):
        self.status = self.Status.DECLINED
        self.resolved_by = user
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "resolved_by", "responded_at"])


class User(AbstractUser):
    """A User of the System

    Lok Users are the main users of the system. They can be assigned to groups and have profiles, that can be used to display information about them.
    Each user is identifier by a unique username, and can have an email address associated with them.


    """

    email = models.EmailField(null=True, blank=True)
    active_organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="active_users",
        null=True,
        blank=True,
        help_text="The organization that the user is currently active in",
    )
    # authentikate (v2) ships its own concrete `User(AbstractUser)` model, which
    # also defines `groups`/`user_permissions` with the default `user_set` reverse
    # accessor. Override the related names here so the two models don't clash
    # (fields.E304). This is a Python-only change with no database effect.
    groups = models.ManyToManyField(
        Group,
        verbose_name="groups",
        blank=True,
        help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
        related_name="karakter_users",
        related_query_name="karakter_user",
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name="user permissions",
        blank=True,
        help_text="Specific permissions for this user.",
        related_name="karakter_users",
        related_query_name="karakter_user",
    )

    def get_user_id(self):
        return str(self.pk)

    @property
    def is_faktsadmin(self):
        return self.groups.filter(name="admin").exists()

    @property
    def avatar(self) -> "MediaStore | None":
        """The user's avatar, which lives on their `Profile`."""
        profile = Profile.objects.filter(user=self).select_related("avatar").first()
        return profile.avatar if profile else None

    def notify(self, title: str, message: str) -> List[Tuple[Optional[int], str]]:
        """Send a notification to all registered communication channels.

        Logs publish failures but attempts all channels. Returns a list of
        (channel_id, status) tuples so callers can inspect individual
        delivery results.
        """
        results: List[Tuple[Optional[int], str]] = []
        for channel in self.com_channels.all():
            try:
                status = channel.publish(title, message)
            except Exception:
                logger.exception("Error publishing to channel=%s for user=%s", getattr(channel, "id", None), getattr(self, "id", None))
                status = "Error"
            results.append((getattr(channel, "id", None), status))

        return results


class Profile(models.Model):
    """A Profile of a User"""

    name = models.CharField(max_length=1000, null=True, blank=True)
    bio = models.CharField(max_length=4000, null=True, blank=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    banner = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True, related_name="profile_banners")
    public_link_preview = models.BooleanField(
        default=False,
        help_text="Opt-in: show this user's name and picture on kontrol's link pages when a "
        "link they shared names them (`?user_id=`), to visitors who may not be signed in. "
        "Only ever shown together with an organization that opted in as well.",
    )


class OrganizationProfile(models.Model):
    """A Profile of a User"""

    name = models.CharField(max_length=1000, null=True, blank=True)
    bio = models.CharField(max_length=4000, null=True, blank=True)
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    banner = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True, related_name="organization_banners")


class GroupProfile(models.Model):
    """A Profile of a Group"""

    name = models.CharField(max_length=1000, null=True, blank=True)
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="profile")
    bio = models.CharField(max_length=4000, null=True, blank=True)
    avatar = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)


class ComChannel(models.Model):
    """A Channel to send notifications to a user"""

    name = models.CharField(max_length=1000, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="com_channels")
    token = models.CharField(max_length=1000, null=True, blank=True, unique=True)

    def publish(self, title: str, message: str) -> str:
        """Publish a notification to the configured push endpoint.

        Returns a string status and logs HTTP/JSON errors instead of
        raising to make caller handling simpler.
        """
        try:
            resp = requests.post(
                "https://exp.host/--/api/v2/push/send",
                json={
                    "to": self.token,
                    "title": title,
                    "body": message,
                },
                timeout=5,
            )
            resp.raise_for_status()
        except requests.RequestException:
            logger.exception("HTTP error while publishing to token=%s", self.token)
            return "Error"

        try:
            data = resp.json()
            # Safely navigate nested JSON
            status = data.get("data", {}).get("status", "unknown")
            return status
        except ValueError:
            logger.exception("Invalid JSON response when publishing to token=%s", self.token)
            return "Error"


class SystemMessage(models.Model):
    """A System Message"""

    hook = models.CharField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="A hook that the ui can use to design the message",
    )
    title = models.CharField(max_length=1000, null=True, blank=True)
    message = models.CharField(max_length=4000, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(help_text="The action to take (e.g. the node)")
    acknowledged = models.BooleanField(default=False)
    unique = models.CharField(max_length=1000, null=True, blank=True)


class Invite(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    email = models.EmailField(null=True, blank=True)  # Optional, for reference only
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_invites")
    created_for = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invites")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    public = models.BooleanField(
        default=False,
        help_text="If true, anyone with the link can preview the invitation "
        "(organization, inviter, expiry) before signing in. Private invites "
        "require authentication before any details are shown.",
    )

    # Status tracking
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    accepted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="accepted_invites")
    declined_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="declined_invites")
    responded_at = models.DateTimeField(null=True, blank=True)

    # Roles to assign when invite is accepted
    roles = models.ManyToManyField("Role", related_name="invites", blank=True)

    # A collaboration invite grants these collaborations
    # (``karakter.collaboration_grants`` entries,
    # ``{"instance": "7", "collaboration": "12", "roles": ["editor"]}``) instead of
    # making its holder a member. Null for an ordinary invite.
    shared_collaborations = models.JSONField(
        null=True,
        blank=True,
        help_text="The collaborations this invite grants, as `{instance, collaboration, roles}` entries. "
        "Null for an invite that grants full membership.",
    )
    max_uses = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="How many people may accept a collaboration invite. Null: anyone with the link "
        "until it expires. Ordinary invites are always single-use.",
    )
    uses = models.PositiveIntegerField(default=0, db_default=0, help_text="How many people accepted it so far.")
    mesh = models.BooleanField(
        default=True,
        db_default=True,
        help_text="For a collaboration invite: whether its guests' apps may join the organization's mesh, "
        "reaching only the hubs their collaborations run on. Copied onto every grant it makes.",
    )

    @property
    def is_collaboration_invite(self) -> bool:
        return self.shared_collaborations is not None

    def is_valid(self):
        if self.status != self.Status.PENDING:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def accept(self, user):
        self.status = self.Status.ACCEPTED
        self.accepted_by = user
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "accepted_by", "responded_at"])

    def decline(self, user):
        if self.is_collaboration_invite:
            # A collaboration invite may be meant for many people: one of them
            # saying no must not close it for the rest.
            return
        self.status = self.Status.DECLINED
        self.declined_by = user
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "declined_by", "responded_at"])

    def cancel(self):
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status"])


class CollaborationGrant(models.Model):
    """One collaboration of a service instance granted to a scoped membership (a guest).

    A collaboration lives in a service (``collaboration_id`` is its id there) and
    holds that service's objects; lok knows nothing of what is in it. ``roles``
    are role identifiers of the organization that hold only inside the
    collaboration (none: the guest may only view it). lok neither checks that the
    collaboration exists nor what the roles allow: it lists the grants in the
    guest's tokens (``collabs``), and the service enforces them.
    """

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="collaboration_grants")
    instance = models.ForeignKey("fakts.ServiceInstance", on_delete=models.CASCADE, related_name="collaboration_grants", help_text="The service instance the collaboration lives in")
    collaboration_id = models.CharField(max_length=200, help_text="The collaboration's id in its service")
    roles = models.JSONField(default=list, blank=True, help_text="Role identifiers of the organization that hold inside the collaboration. Empty: view only.")
    granted_through = models.ForeignKey(Invite, on_delete=models.SET_NULL, null=True, blank=True, related_name="collaboration_grants")
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="collaboration_grants_given")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    mesh = models.BooleanField(
        default=False,
        db_default=False,
        help_text="Whether the guest's apps may join the organization's mesh to reach the hub this collaboration's "
        "instance runs on. Set from the invite it was granted through.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["membership", "instance", "collaboration_id"], name="one_grant_per_collaboration_and_membership"),
        ]

    def is_live(self) -> bool:
        return self.expires_at is None or self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"{self.instance_id}/{self.collaboration_id}"


from .signals import *

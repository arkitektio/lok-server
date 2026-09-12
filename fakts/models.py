import secrets as _secrets
from django.db import models
from django.utils import timezone
from typing import Dict, Any
from django.contrib.auth import get_user_model
from django_choices_field import TextChoicesField

# Create your models here.
from typing import List
import uuid
from typing import Optional
from authlib.oauth2.rfc6749 import ClientMixin
from authlib.oauth2.rfc6749.errors import InvalidClientError
from fakts import fields, enums
from django.db.models import Q  # noqa: F401  (re-exported as fakts.models.Q)
from django.contrib.auth.models import AbstractUser, Group  # noqa: F401  (AbstractUser re-exported as fakts.models.AbstractUser)
from karakter.models import MediaStore, Organization
from fakts import base_models, errors


def generate_client_id() -> str:
    """Generate a unique OAuth2 client id."""
    return str(uuid.uuid4())


def generate_client_secret() -> str:
    """Generate a confidential client secret (relying parties only — fakts
    clients are public and carry none)."""
    return _secrets.token_urlsafe(32)


def generate_device_secret() -> str:
    """Generate a staged authorization's full-entropy polling secret."""
    return _secrets.token_urlsafe(32)


class KommunityPartner(models.Model):
    name = models.CharField(max_length=1000)
    description = models.TextField(default="No description available", null=True, blank=True)
    short_description = models.CharField(max_length=280, null=True, blank=True)
    logo_url = models.CharField(max_length=1000, null=True, blank=True)
    image_url = models.CharField(max_length=1000, null=True, blank=True)
    website_url = models.CharField(max_length=1000, null=True, blank=True)
    identifier = fields.IdentifierField(unique=True)
    auth_url = models.CharField(max_length=1000, null=True, blank=True)
    license_agreement = models.TextField(null=True, blank=True, help_text="Optional license agreement text shown before connecting this partner.")
    pre_authorize_hook = models.CharField(max_length=1000, null=True, blank=True, help_text="Optional hook called after creating a partner hub. The response must explicitly approve the hub.")
    pre_authorize_token = models.CharField(max_length=1000, null=True, blank=True, help_text="Optional bearer token sent to the pre-authorize hook.")
    oauth_client = models.ForeignKey("Client", on_delete=models.CASCADE, null=True, related_name="kommunity_partners")
    partner_kind = models.CharField(
        max_length=50,
        choices=[(e.value, e.name) for e in enums.PartnerKind],
        help_text="The kind of partner",
    )
    kommunity_kind = models.CharField(
        max_length=50,
        choices=[(e.value, e.name) for e in enums.KommunityKind],
        help_text="The kind of kommunity",
    )
    auto_configure = models.BooleanField(default=False)
    preconfigured_hub = models.JSONField(help_text="A preconfigured hub that gets created when a user redeems a token from this partner.", null=True, blank=True)
    filter_config = models.JSONField(
        help_text="Filter conditions to determine which users/organizations this partner applies to. Example: {'email_domain_equals': ['example.com', 'test.org'], 'email_domain_ends_with': ['edu']}",
        null=True,
        blank=True,
        default=dict,
    )

    def __str__(self):
        return f"{self.identifier}"

    @property
    def preconfigured_hub_as_model(self) -> Optional[base_models.HubManifest]:
        if not self.preconfigured_hub:
            return None
        return base_models.HubManifest(**self.preconfigured_hub)

    def applies_to_user(self, user) -> bool:
        """
        Check if this partner's filter conditions apply to the given user.

        If no filter_config is set, the partner applies to everyone.
        If filter_config is set, all conditions must be satisfied.

        Supported filter conditions:
        - email_domain_equals: list of domains that the user's email must match exactly
        - email_domain_ends_with: list of domain suffixes that the user's email domain must end with
        - username_equals: list of usernames that must match exactly
        - username_contains: list of substrings that must be in the username
        """
        if not self.filter_config:
            return True

        user_email = getattr(user, "email", None) or ""
        user_email_domain = user_email.split("@")[-1].lower() if "@" in user_email else ""
        username = getattr(user, "username", "") or ""

        # Check email_domain_equals
        if "email_domain_equals" in self.filter_config:
            domains = self.filter_config["email_domain_equals"]
            if isinstance(domains, list) and domains:
                if user_email_domain.lower() not in [d.lower() for d in domains]:
                    return False

        # Check email_domain_ends_with
        if "email_domain_ends_with" in self.filter_config:
            suffixes = self.filter_config["email_domain_ends_with"]
            if isinstance(suffixes, list) and suffixes:
                if not any(user_email_domain.endswith(s.lower()) for s in suffixes):
                    return False

        # Check username_equals
        if "username_equals" in self.filter_config:
            usernames = self.filter_config["username_equals"]
            if isinstance(usernames, list) and usernames:
                if username not in usernames:
                    return False

        # Check username_contains
        if "username_contains" in self.filter_config:
            substrings = self.filter_config["username_contains"]
            if isinstance(substrings, list) and substrings:
                if not any(s in username for s in substrings):
                    return False

        return True


class Layer(models.Model):
    name = models.CharField(max_length=1000)
    identifier = fields.IdentifierField(unique=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="layers",
    )
    logo = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    description = models.TextField(default="No description available", null=True, blank=True)
    dns_probe = models.TextField(default="No probe available", null=True, blank=True)
    get_probe = models.TextField(default="No probe available", null=True, blank=True)
    kind = models.CharField(
        max_length=50,
        help_text="The kind of layer",
    )

    def __str__(self):
        return f"{self.identifier}"


class IonscaleLayer(Layer):
    tailnet_name = models.CharField(max_length=1000, unique=True)
    # Desired per-mesh DNS state. lok is the source of truth: these are pushed to
    # ionscale via `set-dns` (see ionscale.manager.apply_dns_config). HTTPS certs
    # require MagicDNS (the cert domain *is* the MagicDNS name), enforced at the
    # mutation layer. Default on for new meshes.
    magic_dns_enabled = models.BooleanField(default=True)
    https_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"Ionscale Layer: {self.identifier} ({self.tailnet_name})"


class IonscaleAuthKey(models.Model):
    hub = models.ForeignKey("Hub", on_delete=models.CASCADE, related_name="ionscale_auth_keys", null=True, blank=True)
    layer = models.ForeignKey(IonscaleLayer, on_delete=models.CASCADE, related_name="auth_keys")
    key = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="created_ionscale_auth_keys")
    ephemeral = models.BooleanField(default=False)
    tags = models.JSONField(default=list)

    def __str__(self):
        return f"Auth Key for {self.layer.tailnet_name} ({self.created_at})"


class Service(models.Model):
    name = models.CharField(max_length=1000)
    identifier = fields.IdentifierField()
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="services",
        help_text="The organization this service registration belongs to.",
    )
    logo = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    description = models.TextField(default="No description available", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "identifier"],
                name="Only one service identifier per organization",
            )
        ]

    def __str__(self):
        return f"{self.identifier}"

    def validate_instance(self, instance: Dict[str, Any]) -> List[str]:
        errors = []
        warnings = []

        if not instance.get("key"):
            errors.append("Instance does not contain a key")

        return errors + warnings


class ServiceRelease(models.Model):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="releases")
    version = fields.VersionField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service", "version"],
                name="Only one per service and version",
            )
        ]

    def __str__(self):
        return f"{self.service}:{self.version}"


class ServiceInstance(models.Model):
    hub = models.ForeignKey("Hub", on_delete=models.CASCADE, related_name="instances")
    release = models.ForeignKey(ServiceRelease, on_delete=models.CASCADE, related_name="instances")
    logo = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    instance_id = models.CharField(max_length=1000, default="default")
    private_key = models.TextField(help_text="The private key of the instance, used for signing claims.", null=True, blank=True)
    steward = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="stewarded_instances",
        help_text="The user who is responsible for this instance. If null the admin is stewared by admin user.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="service_instances",
        help_text="The organization that owns this instance. If null the instance is global.",
    )
    device = models.ForeignKey("Device", on_delete=models.CASCADE, null=True, blank=True, related_name="service_instances")
    template = models.TextField()
    denied_users = models.ManyToManyField(get_user_model(), related_name="denied_instances")
    denied_groups = models.ManyToManyField(Group, related_name="denied_instances")
    allowed_users = models.ManyToManyField(get_user_model(), related_name="allowed_instances")
    allowed_groups = models.ManyToManyField(Group, related_name="allowed_instances")
    allowed_organizations = models.ManyToManyField(Organization, related_name="allowed_instances")
    public_key = models.TextField(null=True, blank=True, help_text="The public key of the instance, if applicable.")
    token = models.CharField(max_length=1000, help_text="The token of the instance, used for authentication.")

    def __str__(self):
        return f"{self.release}:{self.instance_id}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "instance_id", "organization", "device", "hub"],
                name="Only one instance_id per release, organization and device and instance",
            ),
            models.UniqueConstraint(
                fields=["token", "hub"],
                name="Only one token per hub",
            ),
        ]

    def render(self, context: base_models.LinkingContext) -> base_models.InstanceClaim:
        """Render all aliases of the instance into a list of URLs."""
        urls = []
        for alias in self.aliases.all():
            try:
                url = alias.to_url(context)
                urls.append(url)
            except AssertionError as e:
                raise errors.InstanceAliasNotFound(f"Error rendering alias {alias}: {str(e)}")

        challenge_key = {"kind": "ed25519", "key": self.public_key} if self.public_key else None

        return base_models.InstanceClaim(
            service=self.release.service.identifier,
            identifier=str(self.id),
            aliases=urls,
            challenge_key=challenge_key,
        )


class InstancePermission(models.Model):
    kind = models.CharField(
        max_length=10,
        choices=[(e.value, e.name) for e in enums.InstancePermissionKind],
        help_text="Allow or deny access",
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="instance_permissions")
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="instance_permissions", null=True, blank=True)
    instance = models.ForeignKey(ServiceInstance, on_delete=models.CASCADE, related_name="permissions")


class InstanceAlias(models.Model):
    """An alias for a service instance. This is used to provide a more user-friendly name for the instance."""

    layer = models.ForeignKey(Layer, on_delete=models.CASCADE, related_name="aliases", null=True, blank=True)
    instance = models.ForeignKey(ServiceInstance, on_delete=models.CASCADE, related_name="aliases")
    name = models.CharField(max_length=1000, null=True, blank=True, help_text="The name of the alias")
    host = models.CharField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="The host of the alias, if its a ABSOLUTE alias (e.g. 'example.com'). If not set, the alias is relative to the layer's domain.",
    )
    port = models.IntegerField(
        null=True,
        blank=True,
        help_text="The port of the alias",
    )
    kind = TextChoicesField(
        choices_enum=enums.AliasKindChoices,
        default=enums.AliasKindChoices.RELATIVE.value,
        help_text="The kind of alias. If relative, the alias is relative to the layer's domain. If absolute, the alias is an absolute URL.",
    )
    ssl = models.BooleanField(
        default=True,
        help_text="If the alias is available over SSL or not. If not set, the alias is assumed to be available over SSL.",
    )
    public = models.BooleanField(
        default=False,
        help_text="If the alias is publicly reachable. If true, the coordination server can also check the alias's health directly (in addition to client-side reports), which allows checking its health from the kontrol interface.",
    )
    challenge = models.TextField(
        default="ht",
        help_text=""""A challenge URL to verify the alias on the client. If it returns a 200 OK, the alias is valid. It can additionally return a JSON object with a `challenge
        key that contains the challenge to be solved by the client.""",
    )
    path = models.CharField(max_length=1000, null=True, blank=True, help_text="The path of the alias,")
    scope = models.CharField(max_length=20, default="local", help_text="The scope of the alias. 'local' means that the alias is only available within the local network. 'network' means that the alias is available within the organization's network.")

    class Meta:
        """Meta class for InstanceAlias model."""

        constraints = [
            models.UniqueConstraint(
                fields=["instance", "host", "port", "ssl", "path", "kind"],
                name="Only one alias per instance host port ssl path kind",
            )
        ]

    def to_url(self, linking: base_models.LinkingContext) -> base_models.Alias:
        """Convert the alias to a URL based on the linking context."""
        if self.kind == enums.AliasKindChoices.RELATIVE.value:
            # Relative alias: resolved against the coordination server (the linking
            # request host), not any layer. The client reaches it and health-checks
            # the `challenge` directly — no layer indirection.
            return base_models.Alias(
                id=str(self.id),
                ssl=linking.request.is_secure,
                host=linking.request.host,
                port=self.port if self.port else linking.request.port,
                path=self.path,
                challenge=self.challenge,
                public=self.public,
            )
        else:
            return base_models.Alias(
                id=str(self.id),
                ssl=self.ssl,
                host=self.host,
                port=self.port,
                path=self.path,
                challenge=self.challenge,
                public=self.public,
            )

    def __str__(self) -> str:
        """String representation of the InstanceAlias model."""
        return f"{self.instance}@{self.layer}:{self.name}"


class RedeemToken(models.Model):
    """A redeem token is a token that can be used to redeem the rights to create
    a client. It is used to give the recipient the right to create a client.

    If the token is not redeemed within the expires_at time, it will be invalid.
    If the token has been redeemed, but the manifest has changed, the token will be invalid.


    """

    client = models.OneToOneField("Client", on_delete=models.CASCADE, related_name="redeemed_client", null=True)
    token = models.CharField(max_length=1000, unique=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True)
    manifest_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="SHA-256 hash of the manifest this token was first redeemed with. Used to detect manifest changes on re-redeem.",
    )
    allow_reredeem = models.BooleanField(
        default=False,
        help_text="If set, this token may be re-redeemed even when the manifest hash differs from the originally redeemed one.",
    )
    pinned_manifest = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The manifest this token was pre-authorized for, fixed at mint time. When set, a "
            "redeem must present the same identifier, version and node_id, and may request "
            "only a subset of the pinned scopes and requirements; anything else is refused "
            "before a client is provisioned. NULL means the token is unpinned and the "
            "manifest is fixed on first redeem (manifest_hash) instead."
        ),
    )
    max_redemptions = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "How many times this token may be redeemed. NULL means unlimited. "
            "Each redeem mints a fresh access+refresh pair, so an unlimited, "
            "never-expiring token is a permanent foothold for whoever holds it."
        ),
    )
    redemption_count = models.PositiveIntegerField(
        default=0,
        help_text="How many times this token has been redeemed so far.",
    )

    def redemptions_exhausted(self) -> bool:
        """Whether this token has been redeemed as many times as it is allowed."""
        if self.max_redemptions is None:
            return False
        return self.redemption_count >= self.max_redemptions
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="issued_tokens")
    hub = models.ForeignKey(
        "Hub",
        on_delete=models.CASCADE,
        related_name="issued_tokens",
    )


class Hub(models.Model):
    name = models.CharField(max_length=1000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="hubs",
    )
    identifier = fields.IdentifierField()
    description = models.TextField(default="No description available", null=True, blank=True)
    creator = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="created_hubs",
    )
    client = models.OneToOneField(
        "Client",
        on_delete=models.SET_NULL,
        related_name="hub_identity",
        null=True,
        blank=True,
        help_text="The hub server's identity (a public unified Client, bound at hub device-code "
        "accept). Hub servers poll the token endpoint as this client and receive their config "
        "in the token response envelope. Null for hubs provisioned outside the device-code "
        "flow (e.g. partner auto-configuration), which still use `token` + /f/claimhub/. "
        "Distinct from `Client.hub` — the hub an *app* client composes against.",
    )
    # Deprecated: only the partner-webhook /f/claimhub/ path still uses this.
    # Interactive hubs authenticate via their client identity instead.
    # Unique: it is a bearer secret looked up by value at /f/claimhub/, so it
    # must identify exactly one hub (and the lookup is an index hit, not a scan).
    token = models.CharField(max_length=1000, unique=True, default=uuid.uuid4)
    auth_key = models.ForeignKey(
        IonscaleAuthKey,
        on_delete=models.SET_NULL,
        related_name="hubs",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "identifier"],
                name="Only one hub identifier per organization",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.organization})"


class DeviceCode(models.Model):
    """A staged authorization on the canonical grant (RFC 8628 shaped), for
    apps and hubs alike.

    ``/o/app-authorization/`` / ``/o/hub-authorization/`` create it together
    with the dynamically registered *public* unified ``Client`` — the staged
    row IS the client, unbound until a human accepts in kontrol (which binds
    membership/organization and, for apps, release/hub/mappings; for hubs,
    creates the ``Hub`` and links ``Hub.client``). The device then exchanges
    ``secret`` at ``/o/token/`` (device-code grant) for tokens + its rendered
    config in one response, which burns the code.

    ``code`` is the short human user code (configure URL, decline proof);
    ``secret`` the full-entropy polling secret. Approval marker: the client's
    ``membership`` is set.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    kind = TextChoicesField(
        choices_enum=enums.DeviceCodeKindChoices,
        default=enums.DeviceCodeKindChoices.APP.value,
        help_text="What accepting this code produces: an app client or a whole hub.",
    )
    code = models.CharField(max_length=100, unique=True)
    secret = models.CharField(
        max_length=100,
        unique=True,
        default=generate_device_secret,
        help_text="The full-entropy device_code polled at the token endpoint. Distinct from "
        "`code`, the short human-transcribable user code shown in the configure URL — a "
        "shoulder-surfed user code must not be a polling secret.",
    )
    client = models.OneToOneField(
        "Client",
        on_delete=models.CASCADE,
        related_name="device_code",
        help_text="The unified client dynamically registered at start — the staged row itself. "
        "Approval binds it in place; the code is burned at token issuance.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="device_codes",
        help_text="The organization the code was accepted into. Null while pending.",
    )
    granted_scope = models.TextField(
        default="",
        help_text="Space-separated scope granted at accept; becomes the token request's scope.",
    )
    interval = models.IntegerField(default=5, help_text="Minimum polling interval in seconds (RFC 8628).")
    last_polled_at = models.DateTimeField(null=True, blank=True)
    staging_manifest = models.JSONField(default=dict, help_text="The app Manifest or HubManifest staged at start.")
    expires_at = models.DateTimeField()
    denied = models.BooleanField(default=False)

    @property
    def manifest_as_model(self) -> base_models.Manifest:
        return base_models.Manifest(**self.staging_manifest)

    @property
    def hub_manifest_as_model(self) -> base_models.HubManifest:
        return base_models.HubManifest(**self.staging_manifest)

    # --- authlib DeviceCredentialMixin contract (rfc8628) ---

    def get_client_id(self) -> str | None:
        return self.client.client_id if self.client_id else None

    def get_scope(self) -> str:
        return self.granted_scope

    def get_user_code(self) -> str:
        return self.code

    def get_expires_in(self) -> int:
        return int((self.expires_at - self.created_at).total_seconds())

    def is_expired(self) -> bool:
        from django.utils import timezone

        return timezone.now() > self.expires_at

    def get_nonce(self) -> None:
        return None

    def get_auth_time(self) -> None:
        return None


class MeshDeviceCode(models.Model):
    """A device-code flow for a machine that wants to join an organization's mesh.

    Mirrors ``ServiceDeviceCode``: ``code`` is the human-visible value that goes in the
    configure URL, ``challenge_code`` is the secret the machine polls with. On accept a
    per-machine pre-authorized ``IonscaleAuthKey`` is minted and linked as ``auth_key``,
    and ``machine_name`` is returned to the machine as a hint for ``tailscale up --hostname``.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    code = models.CharField(max_length=100, unique=True)
    challenge_code = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True)
    auth_key = models.ForeignKey(
        "IonscaleAuthKey",
        on_delete=models.SET_NULL,
        related_name="mesh_device_code",
        null=True,
        blank=True,
    )
    requested_machine_name = models.CharField(max_length=1000, null=True, blank=True)
    machine_name = models.CharField(max_length=1000, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    staging_ephemeral = models.BooleanField(default=False)
    staging_tags = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    denied = models.BooleanField(default=False)

    def __str__(self):
        return f"MeshDeviceCode {self.code} ({self.requested_machine_name})"


class App(models.Model):
    name = models.CharField(max_length=1000)
    identifier = fields.IdentifierField()
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="apps",
        help_text="The organization this app registration belongs to — the same identifier in two organizations is two registrations.",
    )
    logo = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "identifier"],
                name="Only one app identifier per organization",
            )
        ]

    def __str__(self):
        return f"{self.identifier}"


class Release(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="releases")
    version = fields.VersionField()
    is_latest = models.BooleanField(default=False)
    is_dev = models.BooleanField(default=False)
    name = models.CharField(max_length=1000)
    logo = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True)
    scopes = models.JSONField(default=list)
    requirements = models.JSONField(default=dict)

    # NOTE: no is_latest()/is_dev() methods here — defining methods with the
    # same names as the fields above silently shadowed the field descriptors.

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["app", "version"],
                name="Only one per app and version",
            )
        ]

    def __str__(self):
        return f"{self.app}:{self.version}"


class DeviceGroup(models.Model):
    name = models.CharField(max_length=1000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="device_groups",
    )

    def __str__(self):
        return f"{self.name} ({self.organization})"


class Device(models.Model):
    node_id = models.CharField(max_length=1000)
    name = models.CharField(max_length=1000, null=True, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    device_groups = models.ManyToManyField(DeviceGroup, related_name="devices", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["node_id", "organization"],
                name="Only one node_id per organization",
            ),
        ]


class Client(models.Model, ClientMixin):
    """The one client model: every OAuth2 principal is a row here.

    Kinds of rows and their lifecycle:

    - **App clients** (`development`/`website`/`desktop`/`mobile`): the row is created by
      dynamic registration at ``/o/app-authorization/`` with identity fields
      only; human approval *binds* it (membership, organization, release, hub,
      mappings, scope). ``membership`` null == not yet approved.
    - **Hub identities** (`hub`): same lifecycle via ``/o/hub-authorization/``;
      the created ``Hub`` links back via ``Hub.client`` (reverse:
      ``client.hub_identity``).
    - **Relying parties** (`relying_party`): confidential OIDC clients
      provisioned from config by ``ensureopenid``; global (no organization).

    Implements authlib's ``ClientMixin`` directly — there is no separate
    OAuth2 client table anymore.
    """

    # --- OAuth2 identity -------------------------------------------------
    client_id = models.CharField(max_length=48, unique=True, default=generate_client_id)
    client_secret = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Empty for public clients (every fakts-provisioned client). Only relying parties are confidential.",
    )
    redirect_uris = models.TextField(blank=True, default="")
    scope = models.TextField(
        blank=True,
        default="",
        help_text="Space-separated scope this client may request; written at accept from the granted org scopes.",
    )
    token_endpoint_auth_method = models.CharField(max_length=48, default="none")
    grant_types = models.TextField(default="")
    response_types = models.TextField(blank=True, default="")
    id_token_signed_response_alg = models.CharField(max_length=48, default="RS256")
    membership = models.ForeignKey(
        "karakter.Membership",
        on_delete=models.CASCADE,
        related_name="clients",
        null=True,
        blank=True,
        help_text="The (user, organization) this client acts for. Null means not yet approved (staged) or a global relying party.",
    )
    email_template = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="Template for the OIDC `email` claim rendered from membership variables "
        "(e.g. '{username}@corp.example'). When blank, the user's own email is used. "
        "See authapp.oidc_claims.resolve_email.",
    )
    require_nonce = models.BooleanField(
        default=False,
        help_text=(
            "Reject an authorization-code request from this client that carries no `nonce`. "
            "OIDC Core §3.1.2.1 makes `nonce` OPTIONAL for the code flow, and no discovery "
            "field can advertise a stricter rule, so this is a deliberate non-standard "
            "tightening that has to be agreed with the relying party out of band — hence "
            "per-client and off by default. Only meaningful for the handful of rows "
            "provisioned as OIDC relying parties from `openid_apps`: the clients minted "
            "dynamically by the device-code and redeem paths never run the code flow at all."
        ),
    )

    # --- App/deployment side ---------------------------------------------
    hub = models.ForeignKey(Hub, on_delete=models.CASCADE, related_name="clients", null=True, blank=True)
    functional = models.BooleanField(default=True)
    latest_report_resolved = models.BooleanField(
        default=False,
        help_text=(
            "Has an operator acknowledged the client's most recent report? Denormalised from"
            " Report.resolved_at (like `functional` itself) so the dashboard can list clients"
            " needing attention with a plain indexed filter. Cleared by every incoming report,"
            " so a client that is still broken comes back onto the list."
        ),
    )
    report_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When an operator asked this client to re-report its configuration; null when"
            " nothing is pending. While set, every token response for this client carries"
            " `please_report: true` (see authapp.fakts_grants.FaktsEnvelopeMixin), so the"
            " client re-reports on its next hourly refresh at the latest. Cleared by the"
            " incoming report (fakts.services.clients.report_client)."
        ),
    )
    report_requested_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_client_reports",
        help_text="The operator who asked for the report; kept for the dashboard's audit trail.",
    )
    name = models.CharField(max_length=1000, default="No name")
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="clients", null=True, blank=True)
    kind = TextChoicesField(
        choices_enum=enums.ClientKindChoices,
        default=enums.ClientKindChoices.DEVELOPMENT.value,
        help_text="What kind of principal this client is.",
    )
    role = TextChoicesField(
        choices_enum=enums.ClientRoleChoices,
        default=enums.ClientRoleChoices.INTERFACE.value,
        help_text="Operational role: human INTERFACE vs autonomous task-receiving AGENT.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="clients",
        null=True,
        blank=True,
        help_text="Denormalized from membership (carries constraints and tenant scoping). Null for staged rows and global relying parties.",
    )
    public = models.BooleanField(default=False)
    node = models.ForeignKey(Device, null=True, blank=True, related_name="clients", on_delete=models.SET_NULL)
    public_sources = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    requirements_hash = models.CharField(max_length=1000, unique=False, blank=True, default="")
    statuses = models.JSONField(default=dict, help_text="Per-requirement grant outcomes: {'key': 'granted'|'denied'|'unavailable'}.")
    logo = models.ForeignKey(MediaStore, on_delete=models.CASCADE, null=True, blank=True)
    last_reported_at = models.DateTimeField(auto_now=True)
    last_healthy_report = models.ForeignKey(
        "Report",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The most recent report where the client was functional; null if it has never reported healthy.",
    )
    manifest = models.JSONField(default=dict)
    scopes = models.ManyToManyField("karakter.Scope", related_name="clients", blank=True)

    class Meta:
        constraints = [
            # A client's identity is (release, membership, node, hub): the same app
            # approved by the same person on the same device is a *different*
            # client per hub. Matches the rotation key in ``bind_client``.
            models.UniqueConstraint(
                fields=["release", "membership", "node", "hub"],
                name="Only one client per release, membership, node and hub",
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} Client {self.client_id}"

    # --- Derived identity -------------------------------------------------

    @property
    def please_report(self) -> bool:
        """Whether an operator is waiting for this client to re-report itself."""
        return self.report_requested_at is not None

    @property
    def user(self):
        """The acting user, derived from the membership (there is no user FK)."""
        return self.membership.user if self.membership_id else None

    def resolve_membership(self):
        """The membership every issued token is scoped to. No fallback hops —
        an unbound client simply cannot get a token."""
        if self.membership_id:
            return self.membership
        raise InvalidClientError(description="Client is not attached to an organization membership.")

    @property
    def user_id(self):
        """authlib's save_token stores this as the token's subject (a Membership pk)."""
        return self.resolve_membership().id

    # --- authlib ClientMixin ----------------------------------------------

    def get_client_id(self):
        return self.client_id

    def get_default_redirect_uri(self):
        return self.redirect_uris.split()[0] if self.redirect_uris.split() else None

    def get_allowed_scope(self, scope):
        """Narrow a requested scope to what this client is registered for.

        An omitted scope resolves to the client's *own* registered scope rather
        than "". Returning "" made the stored `OAuth2Token.scope` disagree with
        the token actually issued: `authapp.token_generators.get_extra_claims`
        falls back to `client.scope` when the request carries none, and RFC 9068
        extra claims override the base claim — so the signed JWT advertised the
        client's entire allowed scope while the database row recorded none.

        That split had two edges: the DB-backed validator at `/o/user_info/`
        rejected a token whose own JWT claimed `profile`, and any resource server
        trusting the JWT granted more than lok had recorded as granted. Both
        halves now derive from the same value.
        """
        if not scope:
            return self.scope or ""
        allowed = set(self.scope.split())
        return " ".join([s for s in scope.split() if s in allowed])

    def check_redirect_uri(self, redirect_uri):
        """Exact-match against the registered, space-joined list. A substring
        test would match a registered URI appearing anywhere in an attacker's
        URL (e.g. as a query parameter)."""
        if not redirect_uri:
            return False
        return redirect_uri in self.redirect_uris.split()

    def check_client_secret(self, client_secret):
        """Constant-time comparison — `==` short-circuits at the first
        differing byte and leaks the secret's prefix through timing."""
        if not client_secret:
            return False
        return _secrets.compare_digest(str(self.client_secret), str(client_secret))

    def check_endpoint_auth_method(self, method, endpoint):
        """`none` is accepted at the token/revocation endpoints only for
        clients explicitly registered public (every fakts client); confidential
        relying parties use the secret methods."""
        if endpoint in ("token", "revocation"):
            if method == "none":
                return self.token_endpoint_auth_method == "none"
            return method in ("client_secret_basic", "client_secret_post")

        return self.token_endpoint_auth_method == method

    def check_response_type(self, response_type):
        allowed = self.response_types.split() or ["code"]
        return response_type in allowed

    def check_grant_type(self, grant_type):
        return grant_type in self.grant_types.split()


class ServiceInstanceMapping(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="mappings")
    instance = models.ForeignKey(ServiceInstance, on_delete=models.CASCADE, related_name="mappings")
    key = models.CharField(max_length=1000)
    description = models.TextField(max_length=1000, null=True, blank=True)
    optional = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "client"],
                name="Only one instance per key and hub",
            )
        ]

    def __str__(self):
        return f"{self.key}:{self.instance}@{self.client}"


class UsedAlias(models.Model):
    """A client's most recent self-report for one requirement key: which alias it
    resolved to and whether it was reachable."""

    valid = models.BooleanField(default=True)
    alias = models.ForeignKey(InstanceAlias, on_delete=models.CASCADE, related_name="usages", null=True, blank=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="used_aliases")
    key = models.CharField(max_length=1000)
    reason = models.TextField(null=True, blank=True)
    used_at = models.DateTimeField(auto_now=True, help_text="When the client last reported this usage.")

    def __str__(self):
        return f"{self.alias} used in {self.key} at {self.used_at}"


class Report(models.Model):
    """A point-in-time snapshot of a client's self-report (functional flag +
    the per-requirement alias_reports payload). Only the latest N per client
    are retained (N configurable via settings.CLIENT_REPORT_RETENTION)."""

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="reports")
    functional = models.BooleanField(default=True)
    alias_reports = models.JSONField(
        default=dict,
        help_text="Raw snapshot of the reported payload: {key: {alias_id, valid, reason}}.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an operator acknowledged this report; null while it still needs attention.",
    )
    resolved_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_reports",
        help_text="The member who acknowledged this report.",
    )
    resolution_note = models.TextField(
        null=True,
        blank=True,
        help_text="Optional note from the operator explaining how the report was dealt with.",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Report for {self.client} at {self.created_at}"

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def is_latest(self) -> bool:
        """Whether this is the client's most recent report."""
        latest = self.client.reports.order_by("-created_at", "-id").first()
        return latest is not None and latest.pk == self.pk

    def resolve(self, user, note: str | None = None):
        """Acknowledge this report.

        Acknowledgement deliberately does NOT touch `client.functional` — the
        client said it was broken and that stays on the record. It only means an
        operator has triaged it, which is what takes the client off the
        dashboard's action list. Resolving the client's *latest* report also
        flips `client.latest_report_resolved`; the next report to arrive clears
        that again (see fakts.services.clients.report_client), so a client that
        is still broken comes back.
        """
        self.resolved_at = timezone.now()
        self.resolved_by = user
        self.resolution_note = note
        self.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])
        if self.is_latest:
            Client.objects.filter(pk=self.client_id).update(latest_report_resolved=True)

    def unresolve(self):
        """Reopen an acknowledged report."""
        self.resolved_at = None
        self.resolved_by = None
        self.resolution_note = None
        self.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])
        if self.is_latest:
            Client.objects.filter(pk=self.client_id).update(latest_report_resolved=False)


class TailscaleInspector(models.Model):
    name = models.CharField(max_length=1000)
    api_key = models.CharField(max_length=1000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="tailscale_inspectors",
    )

    def __str__(self):
        return f"{self.name} ({self.organization})"


class LinkInspector(models.Model):
    name = models.CharField(max_length=1000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="link_inspectors",
    )
    link = models.CharField(max_length=1000)

    def __str__(self):
        return f"{self.name} ({self.organization})"

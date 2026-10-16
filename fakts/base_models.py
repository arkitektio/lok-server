from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
from typing import Dict, List, Optional, Literal, Union
from django.conf import settings
from fakts import enums


class Layer(BaseModel):
    identifier: str
    kind: Union[Literal["WEB"], Literal["TAILSCALE"]]
    dns_probe: str | None = None
    get_probe: str | None = None


class WellKnownFakts(BaseModel):
    name: str = settings.DEPLOYMENT_NAME
    version: str
    protocol_version: str = "2"
    description: str | None = None
    base_url: str
    frontend_url: str
    """DEPRECATED. The deployment's base domain. Kept for back-compat only —
    clients must use the explicit, always-absolute `configure` (and
    `mesh_configure` / `hub_configure`) templates instead of deriving links
    from this value. Will be removed in a future protocol version."""
    configure: str | None = None
    """Absolute URL template for the device-code configure page. The literal
    `{code}` placeholder is substituted by the client with the device code.
    Supersedes deriving the configure link from the (deprecated) `frontend_url`."""

    # --- OAuth 2.0 authorization server metadata (RFC 8414 vocabulary). The
    # same core is served at /.well-known/oauth-authorization-server and
    # /.well-known/openid-configuration; it is inlined here so a fakts client
    # needs exactly one discovery request. ---
    issuer: str | None = None
    """The OAuth issuer identifier — the `iss` of issued access tokens."""
    device_authorization_endpoint: str | None = None
    """Absolute URL of the app authorization endpoint (RFC 8628 device
    authorization). Fakts extension: it also performs dynamic client
    registration — the client POSTs its manifest and receives a fresh public
    `client_id` together with the `device_code`, so no client identity needs to
    exist in advance."""
    token_endpoint: str | None = None
    """Absolute URL of the OAuth2 token endpoint. The client polls it with
    grant_type urn:ietf:params:oauth:grant-type:device_code (or exchanges a
    redeem token via urn:fakts:grant-type:redeem) and receives the access token,
    refresh token and rendered instances in one response. Refreshing there
    re-renders the instances."""
    revocation_endpoint: str | None = None
    """Absolute URL of the RFC 7009 revocation endpoint. A client that logs out
    POSTs its refresh token here (`token`, `token_type_hint=refresh_token`,
    `client_id`): the refresh chain ends at once, and its mesh sidecar is reaped."""
    jwks_uri: str | None = None
    """Absolute URL of the JWKS used to verify issued access tokens."""
    grant_types_supported: List[str] = Field(default_factory=list)
    """Grant types the token endpoint accepts. Fakts clients use the device-code
    URN (interactive, then refresh_token for continuity) or
    urn:fakts:grant-type:mandate-key (headless: form fields `key` + JSON `manifest`;
    access tokens only, the client runs the grant again when one expires)."""
    token_endpoint_auth_methods_supported: List[str] = Field(default_factory=list)
    """Client authentication methods at the token endpoint. Fakts-provisioned
    clients are public and authenticate with `none` (client_id only)."""
    mesh_coord_url: str | None = None
    """Public coordination URL of the ionscale mesh coordination server that clients
    should point their tailnet at. `None` when this deployment has no mesh configured."""
    mesh_device_code_start: str | None = None
    """Absolute URL of the *mesh* device-code start endpoint — a machine POSTs here to
    request joining an organization's mesh and receives a `code` + `challenge`."""
    mesh_challenge_url: str | None = None
    """Absolute URL of the *mesh* device-code challenge endpoint — the machine polls it
    with its `challenge` code to receive the minted mesh pre-auth key once granted."""
    mesh_key_endpoint: str | None = None
    """Absolute URL an app or hub POSTs to, with its Bearer access token, for a fresh
    key to the mesh sidecar it was granted at approval (`{"mesh": {...}}`, the member
    the initial token response carried). For a client that lost its mesh state; it
    answers 403 when no sidecar was granted and 409 while the sidecar's node counts as
    online (retry with backoff: an uncleanly stopped node stays "online" for minutes)."""
    mesh_configure: str | None = None
    """Absolute URL template for the mesh configure page. The literal `{code}`
    placeholder is substituted by the machine with the mesh device code."""
    hub_authorization_endpoint: str | None = None
    """Absolute URL of the *hub* authorization endpoint — a hub server POSTs a
    hub manifest here to dynamically register a public OAuth2 client and stage
    a hub device code; it then polls `token_endpoint` with the device-code
    grant and receives tokens + its rendered hub config in one response."""
    hub_deletion_endpoint: str | None = None
    """Absolute URL a hub POSTs to, with its own Bearer access token, to remove
    itself from this server — the hub, its identity client and its mesh nodes."""
    hub_claim: str | None = None
    """DEPRECATED. Absolute URL of the *hub* claim endpoint — the holder of a
    hub token POSTs it here to receive the rendered server configuration. Only
    the partner-webhook path still uses it; interactive hubs receive their
    config through the token endpoint (`hub_authorization_endpoint` +
    `token_endpoint`). Will be removed in a future protocol version."""
    hub_configure: str | None = None
    """Absolute URL template for the hub configure page. The literal `{code}`
    placeholder is substituted by the client with the hub device code."""


class Requirement(BaseModel):
    key: str
    service: str
    """ The service is the service that will be used to fill the key, it will be used to find the correct instance. It needs to fullfill
    the reverse domain naming scheme"""
    optional: bool = False
    """ The optional flag indicates if the requirement is optional or not. Users should be able to use the client even if the requirement is not met. """
    description: Optional[str] = None
    """ The description is a human readable description of the requirement. Will be show to the user when asking for the requirement."""


class PublicSource(BaseModel):
    kind: Literal["github", "website"]
    url: str


class Manifest(BaseModel):
    """A Manifest is a description of a client. It contains all the information
    necessary to create a set of client, release and app objects in the database.
    """

    identifier: str
    """ The identifier is a unique string that identifies the client. """
    version: str
    """ The version is a string that identifies the version of the client. """
    title: Optional[str] = None
    """ A human readable display name for the app. Used as the name of the App and Release. """
    description: Optional[str] = None
    """ A human readable description of what the app does. """
    logo: Optional[str] = None
    """ The logo is a url to a logo that should be used for the client. """
    scopes: list[str] = Field(default_factory=list)
    """ The scopes are a list of scopes that the client can request. """
    requirements: list[Requirement] = Field(default_factory=list)
    """ The requirements are a list of requirements that the client needs to run on (e.g. needs GPU)"""
    device_id: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("device_id", "node_id")
    )
    """ The device the client runs on. ``node_id`` is the deprecated spelling; it is
    still accepted on input and stored manifests, and read back as ``device_id``. """
    authors: list[str] = Field(default_factory=list)
    """ The authors that created and maintain the app. """
    keywords: list[str] = Field(default_factory=list)
    """ Keywords/tags that describe the app and help with discoverability. """
    license: Optional[str] = None
    """ The license of the app (SPDX identifier or free text). """
    homepage: Optional[str] = None
    """ The homepage url of the app (repo_url already tracks the issue tracker). """
    repo_url: Optional[str] = None
    """ The repo_url is the url to track issues and get more information about the client. """
    public_sources: Optional[List[PublicSource]] = None
    """ The public_sources are a list of public sources where the client can be found. """


class MandatePinMismatch(ValueError):
    """A manifest is not covered by a mandate's pin."""


class RequirementPin(BaseModel):
    """A requirement as a mandate pins it: which key, served by which service."""

    key: str
    service: str


class MandatePin(BaseModel):
    """The ceiling a mandate puts on the apps that log in under it.

    Identity (``identifier``, and ``version`` and ``device_id`` when set) must match
    exactly; ``scopes`` and ``requirements`` are ceilings: a manifest may ask for a
    subset, never more. A field left ``None`` is open: an open identifier is fixed by
    the first app that logs in, open scopes are bounded by the organization's,
    open requirements by the hub's instances. Cosmetic manifest fields are never
    compared: a running app assembles them from its image.
    """

    identifier: str | None = None
    version: str | None = None
    scopes: list[str] | None = None
    requirements: list[RequirementPin] | None = None
    device_id: str | None = Field(default=None, validation_alias=AliasChoices("device_id", "node_id"))

    @classmethod
    def from_manifest(cls, manifest: "Manifest", *, version: bool = True, device: bool = False) -> "MandatePin":
        return cls(
            identifier=manifest.identifier,
            version=manifest.version if version else None,
            scopes=sorted(set(manifest.scopes or [])),
            requirements=[RequirementPin(key=r.key, service=r.service) for r in manifest.requirements or []],
            device_id=manifest.device_id if device else None,
        )

    @property
    def is_open(self) -> bool:
        return self.identifier is None

    def _requirement_pairs(self) -> set[tuple[str, str]] | None:
        if self.requirements is None:
            return None
        return {(r.key, r.service) for r in self.requirements}

    def check(self, manifest: "Manifest") -> None:
        """Raise :class:`MandatePinMismatch` unless the pin covers ``manifest``."""
        if self.identifier is not None and manifest.identifier != self.identifier:
            raise MandatePinMismatch(f"This mandate is pinned to app '{self.identifier}', not '{manifest.identifier}'.")
        if self.version is not None and manifest.version != self.version:
            raise MandatePinMismatch(
                f"This mandate is pinned to version '{self.version}' of '{manifest.identifier}', not '{manifest.version}'."
            )
        if self.device_id is not None and manifest.device_id != self.device_id:
            raise MandatePinMismatch("This mandate is pinned to a different device than the one the manifest names.")
        if self.scopes is not None:
            extra_scopes = sorted(set(manifest.scopes or []) - set(self.scopes))
            if extra_scopes:
                raise MandatePinMismatch(f"This mandate does not authorize the scope(s) {', '.join(extra_scopes)}.")
        pairs = self._requirement_pairs()
        if pairs is not None:
            extra = sorted(f"{r.key}={r.service}" for r in manifest.requirements or [] if (r.key, r.service) not in pairs)
            if extra:
                raise MandatePinMismatch(f"This mandate does not authorize the requirement(s) {', '.join(extra)}.")

    def assert_within(self, ceiling: "MandatePin") -> None:
        """Raise :class:`MandatePinMismatch` unless every app this pin admits is admitted by ``ceiling``."""
        for name in ("identifier", "version", "device_id"):
            fixed = getattr(ceiling, name)
            if fixed is not None and getattr(self, name) != fixed:
                raise MandatePinMismatch(f"The {name.replace('_', ' ')} must be '{fixed}', as the issuing mandate pins it.")
        if ceiling.scopes is not None:
            if self.scopes is None:
                raise MandatePinMismatch("Scopes must be pinned, as the issuing mandate pins them.")
            extra_scopes = sorted(set(self.scopes) - set(ceiling.scopes))
            if extra_scopes:
                raise MandatePinMismatch(f"The scope(s) {', '.join(extra_scopes)} exceed the issuer's.")
        ceiling_pairs = ceiling._requirement_pairs()
        if ceiling_pairs is not None:
            own = self._requirement_pairs()
            if own is None:
                raise MandatePinMismatch("Requirements must be pinned, as the issuing mandate pins them.")
            extra = sorted(f"{key}={service}" for key, service in own - ceiling_pairs)
            if extra:
                raise MandatePinMismatch(f"The requirement(s) {', '.join(extra)} exceed the issuer's.")

    def narrowed_to_device(self, device_id: str) -> "MandatePin":
        return self.model_copy(update={"device_id": device_id})

    def fixed_to(self, identifier: str) -> "MandatePin":
        return self.model_copy(update={"identifier": identifier})


class MandateKeyGrantRequest(BaseModel):
    """The form fields of the ``urn:fakts:grant-type:mandate-key`` grant."""

    key: SecretStr = Field(min_length=32, max_length=1000)
    manifest: Manifest
    requested_client_kind: enums.ClientKindVanilla = enums.ClientKindVanilla.DEVELOPMENT
    requested_client_role: enums.ClientRoleVanilla = enums.ClientRoleVanilla.INTERFACE
    request_auth_key: bool = False

    @field_validator("requested_client_kind")
    @classmethod
    def _headless_kind(cls, kind: enums.ClientKindVanilla) -> enums.ClientKindVanilla:
        allowed = (enums.ClientKindVanilla.DEVELOPMENT, enums.ClientKindVanilla.DESKTOP, enums.ClientKindVanilla.MOBILE)
        if kind not in allowed:
            raise ValueError(f"A mandate key logs in a {', '.join(k.value for k in allowed)} client, not a {kind.value} one.")
        return kind


class Role(BaseModel):
    key: str
    description: Optional[str] = None
    """ The description is a human readable description of the role. Will be show to the user when asking for the requirement."""


class Scope(BaseModel):
    key: str
    description: Optional[str] = None
    """ The description is a human readable description of the scope. Will be show to the user when asking for the requirement."""


class ServiceManifest(BaseModel):
    """A Manifest is a description of a client. It contains all the information
    necessary to create a set of client, release and app objects in the database.
    """

    identifier: str
    """ The identifier is a unique string that identifies the client. """
    version: str
    """ The version is a string that identifies the version of the client. """
    description: Optional[str] = None
    """ The description is a human readable description of the client. """
    logo: Optional[str] = None
    """ The logo is a url to a logo that should be used for the client. """
    roles: Optional[List[Role]] = Field(default_factory=list)
    """ The requirements are a list of requirements that the client needs to run on (e.g. needs GPU)"""
    scopes: Optional[List[Scope]] = Field(default_factory=list)
    """ The scopes are a list of scopes that the client can request. """
    device_id: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("device_id", "node_id")
    )
    """ The device the service runs on. ``node_id`` is the deprecated spelling. """
    instance_id: Optional[str] = "default"
    """ The instance_id is the id of the instance that the runs on """
    public_sources: Optional[List[PublicSource]] = None
    """ The public_sources are a list of public sources where the client can be found. """
    challenge_key: Optional[str] = None
    """ Base64-encoded raw Ed25519 public key (32 bytes). When set, the Fakts server stores it
    and includes it in claims so clients can verify signed alias challenges. """


class HubInputModel(BaseModel):
    """A hub is a Jinja2 YAML template that will be rendered
    with the LinkingContext as context. The result of the rendering
    will be used to send to the client as a configuration."""

    name: str
    template: str


class DeviceCodeStartRequest(BaseModel):
    """A DeviceCodeStartRequest is used to start the device code flow. It contains
    the manifest of the client that wants to start the flow and the redirect uris
    as well as the requested client kind."""

    manifest: Manifest
    expiration_time_seconds: int = 300
    redirect_uris: list[str] = Field(default_factory=list)
    requested_client_kind: enums.ClientKindVanilla = enums.ClientKindVanilla.DEVELOPMENT
    requested_client_role: enums.ClientRoleVanilla = enums.ClientRoleVanilla.INTERFACE
    request_public: bool = False
    supported_layers: List[str] = Field(default_factory=lambda: ["web"])
    request_auth_key: bool = False
    """Ask for a pre-authorized key to the organization's mesh. If the approver allows
    it, the device-code token response carries it once, as ``mesh.ionscale_auth_key``
    (+ ``mesh.ionscale_coord_url``); a client that later loses its mesh state asks
    the well-known ``mesh_key_endpoint`` for a new one. Lives on the request, not the manifest: it is a
    property of this grant, and the manifest is hashed for redeem-change detection."""


class StagingAlias(BaseModel):
    id: str
    name: Optional[str] = None
    ssl: bool = True
    host: Optional[str] = None
    """Required for every kind but ``mesh``. A mesh alias's host is filled in at
    render time with the hub node's MagicDNS name, so a declared one is ignored.
    A ``docker`` alias's host is a name on the hub's own docker network."""
    port: Optional[int] = None
    path: Optional[str] = None
    challenge: Optional[str] = None
    kind: str = "absolute"
    scope: Literal["local", "network", "public", "ionscale"] = "local"
    public: bool = False
    """If the alias is publicly reachable, the coordination server can also check its health directly (enabling health checks from the kontrol interface)."""

    @model_validator(mode="after")
    def _host_unless_mesh(self) -> "StagingAlias":
        if self.kind not in enums.AliasKindChoices.values:
            raise ValueError(f"Alias '{self.id}' has an unknown kind '{self.kind}' (one of: {', '.join(enums.AliasKindChoices.values)}).")
        if self.kind == enums.AliasKindChoices.MESH.value:
            self.host = None
        elif not self.host:
            raise ValueError(f"Alias '{self.id}' needs a host (only kind 'mesh' resolves its own).")
        if self.kind == enums.AliasKindChoices.DOCKER.value and self.public:
            raise ValueError(f"Alias '{self.id}' is of kind 'docker' and cannot be public: it is only reachable from inside the hub's own docker environment.")
        return self


class MeshDeviceCodeStartRequest(BaseModel):
    """A MeshDeviceCodeStartRequest is used to start the mesh device-code flow. A machine
    that wants to join an organization's mesh POSTs this to request a pre-authorized key.
    """

    requested_machine_name: str | None = None
    """The machine's suggested node name. A human authorizer sees it pre-filled on the
    configure page and may edit it; the final value is returned to the machine as a hint
    for `tailscale up --hostname=<machine_name>`."""
    description: str | None = None
    """A human readable description of the machine / why it wants to join, shown to the
    authorizing user on the configure page."""
    ephemeral: bool = False
    """Whether the minted node should be ephemeral (auto-removed when offline)."""
    tags: List[str] = Field(default_factory=list)
    """Optional ionscale ACL tags to request for the minted key."""
    expiration_time_seconds: int = 600


class InstanceRequest(BaseModel):
    """A ServiceRequest is used to request a service instance from the server.
    It contains the manifest of the service that is being requested.
    """

    identifier: str
    description: Optional[str] = None
    """A human readable description of the request."""
    manifest: ServiceManifest
    aliases: List[StagingAlias] = Field(default_factory=list)


class ClientRequest(BaseModel):
    """A ClientRequest is used to request a client from the server.
    It contains the manifest of the client that is being requested.
    """

    identifier: str
    """How the hub knows this client: unique within the hub. The hub's envelope
    answers under it (``client_requests``)."""
    description: Optional[str] = None
    """A human readable description of the request."""
    manifest: Manifest
    """What the client may be. Approving the hub issues a mandate pinned to the
    manifest's identifier, scopes, requirements and (when set) device; its version is
    left open, so the hub can run the next release of the app on the same key."""
    mandate_key: str = Field(min_length=32, max_length=1000)
    """The mandate key the hub proposes, already written into the client's
    environment. Lok keeps only its hash and never hands it back."""


class HubManifest(BaseModel):
    """A Hub Request allows to request seting up a hub of clients and services."""

    identifier: str = Field(..., description="A unique identifier for the hub WITHIN the organization.")
    description: Optional[str] = None
    """A human readable description of the hub."""
    logo: Optional[str] = None
    instances: List[InstanceRequest] = Field(default_factory=list)
    clients: List[ClientRequest] = Field(default_factory=list)
    request_auth_key: bool = False


class HubStartRequest(BaseModel):
    """A Hub Start Request allows to start the setup of a hub."""

    hub: HubManifest
    expiration_time_seconds: int = 600


class DeviceCodeChallengeRequest(BaseModel):
    """A DeviceCodeChallengeRequest is used to start the device code flow. It only
    contains the device code."""

    code: str


class ServerClaimRequest(BaseModel):
    token: str


class AliasReport(BaseModel):
    alias_id: str | None = None
    valid: bool
    reason: Optional[str] = None


class ReportRequest(BaseModel):
    """A client's self-report. The client is identified by its Bearer access
    token (the JWT's `client_id` claim), not by a payload token."""

    alias_reports: Dict[str, AliasReport] = Field(default_factory=dict)
    functional: bool = True


class LinkingRequest(BaseModel):
    host: str
    port: Optional[str] = None
    base_url: Optional[str] = None
    is_secure: bool = False
    jwks_url: Optional[str] = None
    """The issuer's JWKS endpoint (the `jwks_uri` openid-configuration advertises)."""
    hub_keys_base: Optional[str] = None
    """Where hubs' trust bundles live (issuer-anchored); a hub's is `<base><hub pk>`."""


class LinkingClient(BaseModel):
    """The client a config is rendered for. Public clients only — there is no
    secret to ship; auth happens via the OAuth2 token endpoint."""

    client_id: str
    name: str


class LinkingContext(BaseModel):
    deployment_name: str = Field(default=settings.DEPLOYMENT_NAME)
    request: LinkingRequest
    "Everything is a string"
    manifest: Manifest
    client: LinkingClient
    secure: bool = False


class ServerLinkingContext(BaseModel):
    deployment_name: str = Field(default=settings.DEPLOYMENT_NAME)
    request: LinkingRequest
    secure: bool = False


class Alias(BaseModel):
    id: str
    """The id is a unique string that identifies the alias."""
    ssl: bool = True
    """The ssl flag indicates if the alias is available over SSL or not."""
    host: str
    """The host is the host of the alias, it is used to create the URL."""
    port: Optional[int] = None
    """The port is the port of the alias, it is used to create the URL."""
    path: Optional[str] = None
    """The path is the path of the alias, it is used to create the URL."""
    challenge: str = Field(
        description="A challenge url to verify the alias on the client. If it returns a 200 OK, the alias is valid. It can additionally return a JSON object with a `challenge` key that contains the challenge to be solved by the client.",
    )
    public: bool = False
    """If the alias is publicly reachable, the coordination server can also check its health directly (enabling health checks from the kontrol interface)."""
    kind: Literal["absolute", "mesh", "docker"] = "absolute"
    """How the alias is reached. ``mesh`` aliases are only reachable over the
    organization's mesh: clients route them (and their challenge) through their
    mesh sidecar. ``docker`` aliases are only reachable from inside the hub's own
    docker environment: every client gets them, and only one running there passes
    the challenge. Clients that do not know the field ignore it."""


class InstanceClaim(BaseModel):
    """InstancesClaim is a claim that contains the instances that are available
    for the client. It is used to link the client to the server and to provide
    the client with the necessary information to connect to the server.
    """

    service: str
    """The service is the service that will be used to fill the key, it will be used to find the correct instance. It needs to fullfill"""
    identifier: str
    """The identifier is a unique string that identifies the instance."""
    aliases: List[Alias] = Field(default_factory=list)
    challenge_key: Optional[Dict] = None
    """Ed25519 public key for verifying signed alias challenges: {"kind": "ed25519", "key": "<base64 raw 32 bytes>"}. Absent when the instance has no registered key."""


class SelfClaim(BaseModel):
    deployment_name: str = Field(default=settings.DEPLOYMENT_NAME)
    alias: Alias
    jwks_url: Optional[str] = None
    """Where to fetch the keys that sign this deployment's access tokens."""
    hub_keys_url: Optional[str] = None
    """Where to fetch the hub's trust bundle: the public keys of its service instances (a JWKS;
    each key names its `service`). Services verify each other's signed requests against it."""
    sub: Optional[str] = None
    """The id of the user the client acts for: the access token's `sub` claim."""
    organization: Optional[str] = None
    """The organization pk: the access token's `org` claim."""
    hub: Optional[str] = None
    """The hub pk: for an app, the hub its client is bound to; for a hub, the hub
    itself. Null for an app client bound to no hub. Unlike the access token's
    `hub` claim (the hub identifier), this is stable, and matches the hub's mesh
    tag (`tag:hub-<pk>`). With `sub` and `organization`, what a client keys its
    persisted logins (e.g. its mesh state) by."""


class MeshClaim(BaseModel):
    """Mesh access, identical for apps and hubs: the ``mesh`` member of the
    *initial* device-code token response when a key was requested and granted,
    and of the ``mesh_key_endpoint`` answer. Never part of a (refresh-rendered)
    envelope; the identity it belongs to is
    in ``self``."""

    ionscale_auth_key: str
    ionscale_coord_url: str | None = None


class FaktsEnvelope(BaseModel):
    """The fakts members appended to a successful OAuth2 token response for a
    fakts client. Auth material (access_token, refresh_token, expires_in,
    scope, client_id) lives in the standard token-response fields next to
    these. The one exception is mesh access: an app (``request_auth_key``) or
    hub (``HubManifest.request_auth_key``) that was granted a key gets a
    ``mesh`` block (:class:`MeshClaim`) on the *initial* device-code
    response only — never on refresh.
    """

    self: SelfClaim
    instances: Dict[str, InstanceClaim] = Field(default_factory=dict)
    statuses: Dict[str, str] = Field(default_factory=dict)
    """Per-requirement grant outcomes keyed by manifest requirement key.
    Values: 'granted' | 'denied' | 'unavailable'. Omitted for registrations
    that predate this feature (clients should treat missing keys as 'unknown')."""


class HubInstanceClaim(BaseModel):
    """InstancesClaim is a claim that contains the instances that are available
    for the client. It is used to link the client to the server and to provide
    the client with the necessary information to connect to the server.
    """

    identifier: str
    private_key: str | None = None


class HubClientClaim(BaseModel):
    """A client belonging to a hub, identified by its OAuth2 client_id. Hub
    servers recognise the client by the `client_id` claim of its JWT — the old
    opaque client token credential no longer exists."""

    client_id: str | None = None


class HubClientRequestClaim(BaseModel):
    """A client the hub asked for when it was approved.

    The hub proposed the mandate key and hands it to the client as
    ``FAKTS_MANDATE_KEY``; lok answers with the key's SHA-256 only, so the hub can
    see which of its keys is approved without lok ever sending a secret back."""

    mandate: str
    key_sha256: str
    client_ids: List[str] = Field(default_factory=list)
    """The clients that have logged in with the key, one per device."""


class HubClaimAnswer(BaseModel):
    """A hub's envelope, appended to its token responses like an app's
    :class:`FaktsEnvelope` (and with the same one-shot ``auth`` rule: mesh keys
    are never rendered here, see :class:`MeshClaim`)."""

    self: SelfClaim
    instances: Dict[str, HubInstanceClaim] = Field(default_factory=dict)
    clients: Dict[str, HubClientClaim] = Field(default_factory=dict)
    client_requests: Dict[str, HubClientRequestClaim] = Field(default_factory=dict)
    """The clients the hub's manifest requested, by the request's identifier."""


class InstanceHealth(BaseModel):
    healthy: bool
    reason: Optional[str] = None


class MeshHealth(BaseModel):
    connected: bool
    hostname: Optional[str] = None
    """The hub node's MagicDNS name, as its tailscaled reports it."""
    ipv4: Optional[str] = None


class HubHealthReport(BaseModel):
    """What a hub posts to ``/f/hubhealth/``. The hub is identified by its
    Bearer access token (the JWT's `client_id` claim), like a client report."""

    healthy: bool
    version: Optional[str] = None
    instances: Dict[str, InstanceHealth] = Field(default_factory=dict)
    """Keyed by instance identifier (the keys of the hub envelope's `instances`)."""
    mesh: Optional[MeshHealth] = None

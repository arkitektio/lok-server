from pydantic import BaseModel, Field
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
    jwks_uri: str | None = None
    """Absolute URL of the JWKS used to verify issued access tokens."""
    grant_types_supported: List[str] = Field(default_factory=list)
    """Grant types the token endpoint accepts. Fakts clients use the device-code
    URN (interactive) and urn:fakts:grant-type:redeem (headless: form fields
    `redeem_token` + JSON `manifest`), then refresh_token for continuity."""
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
    mesh_configure: str | None = None
    """Absolute URL template for the mesh configure page. The literal `{code}`
    placeholder is substituted by the machine with the mesh device code."""
    hub_authorization_endpoint: str | None = None
    """Absolute URL of the *hub* authorization endpoint — a hub server POSTs a
    hub manifest here to dynamically register a public OAuth2 client and stage
    a hub device code; it then polls `token_endpoint` with the device-code
    grant and receives tokens + its rendered hub config in one response."""
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
    node_id: Optional[str] = None
    """ The node_id is the id of the node that the runs on """
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
    node_id: Optional[str] = None
    """ The node_id is the id of the node that the runs on """
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


class StagingAlias(BaseModel):
    id: str
    name: Optional[str] = None
    ssl: bool = True
    host: str
    port: Optional[int] = None
    path: Optional[str] = None
    challenge: Optional[str] = None
    kind: str = "absolute"
    scope: Literal["local", "network", "public", "ionscale"] = "local"
    public: bool = False
    """If the alias is publicly reachable, the coordination server can also check its health directly (enabling health checks from the kontrol interface)."""


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
    description: Optional[str] = None
    """A human readable description of the request."""
    manifest: Manifest


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


class FaktsEnvelope(BaseModel):
    """The fakts members appended to a successful OAuth2 token response for a
    fakts client. Auth material (access_token, refresh_token, expires_in,
    scope, client_id) lives in the standard token-response fields next to
    these; there is no separate auth block anymore.
    """

    self: SelfClaim
    instances: Dict[str, InstanceClaim] = Field(default_factory=dict)
    statuses: Dict[str, str] = Field(default_factory=dict)
    """Per-requirement grant outcomes keyed by manifest requirement key.
    Values: 'granted' | 'denied' | 'unavailable'. Omitted for registrations
    that predate this feature (clients should treat missing keys as 'unknown')."""


class HubAuthClaim(BaseModel):
    jwks_url: str
    ionscale_auth_key: str | None = None
    ionscale_coord_url: str | None = None


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


class HubClaimAnswer(BaseModel):
    """A ClaimAnswer is the answer to a claim request. It contains the
    linking context that should be used to link the client to the server.
    """

    self: SelfClaim
    auth: HubAuthClaim
    instances: Dict[str, HubInstanceClaim] = Field(default_factory=dict)
    clients: Dict[str, HubClientClaim] = Field(default_factory=dict)

from enum import Enum
import strawberry
from django.db.models import TextChoices


class LayerKindChoices(TextChoices):
    """Event Type for the Event Operator"""

    WEB = "public", "WEB (Value represent WEB)"
    TAILSCALE = "tailscale", "TAILSCALE (Value represent TAILSCALE)"
    VPN = "vpn", "VPN (Value represent VPN)"
    DOCKER = "docker", "DOCKER (Value represent DOCKER)"


class AliasKindChoices(TextChoices):
    """Event Type for the Event Operator"""

    ABSOLUTE = "absolute", "ABSOLUTE (Value represent ABSOLUTE)"
    MESH = "mesh", "MESH (Value represent MESH)"
    DOCKER = "docker", "DOCKER (only reachable from inside the hub's own docker environment)"


class ClientKindChoices(TextChoices):
    """What kind of principal a (unified) Client row is."""

    WEBSITE = "website", "WEBSITE (Value represent WEBSITE)"
    DEVELOPMENT = "development", "DEVELOPMENT (Value represent DEVELOPMENT)"
    DESKTOP = "desktop", "DESKTOP (Value represent DESKTOP)"
    MOBILE = "mobile", "MOBILE (Value represent MOBILE)"
    HUB = "hub", "HUB (a hub server's identity)"
    RELYING_PARTY = "relying_party", "RELYING_PARTY (a config-provisioned OIDC relying party)"


class ClientKindVanilla(str, Enum):
    WEBSITE = "website"
    DEVELOPMENT = "development"
    DESKTOP = "desktop"
    MOBILE = "mobile"
    HUB = "hub"
    RELYING_PARTY = "relying_party"


class DeviceCodeKindChoices(TextChoices):
    """What a staged device-code authorization produces on accept."""

    APP = "app", "APP (an app/client registration)"
    HUB = "hub", "HUB (a whole-hub provisioning)"


class ClientRoleChoices(TextChoices):
    """The operational role of a client, orthogonal to its kind (auth flow)."""

    INTERFACE = "interface", "INTERFACE (Value represent INTERFACE)"
    AGENT = "agent", "AGENT (Value represent AGENT)"


class ClientRoleVanilla(str, Enum):
    INTERFACE = "interface"
    AGENT = "agent"


class MandateClaimantChoices(TextChoices):
    """Who may use a mandate."""

    KEY = "key", "KEY (whoever holds the mandate key logs in as the grantor)"
    AGENT = "agent", "AGENT (an agent app may issue keyed child mandates under it)"


class MandateOriginChoices(TextChoices):
    """Who issued a mandate: the mandate dies with its issuer."""

    MEMBER = "member", "MEMBER (a member, through kontrol)"
    CLIENT = "client", "CLIENT (an app, as its own user)"
    AGENT = "agent", "AGENT (an agent, under a parent agent mandate)"
    HUB = "hub", "HUB (approving a hub that asks for the client)"
    CONFIG = "config", "CONFIG (the deployment config)"


class MandateRevocationChoices(TextChoices):
    """Why a mandate stopped working."""

    REVOKED = "revoked", "REVOKED (withdrawn by its grantor or an admin)"
    PARENT_REVOKED = "parent_revoked", "PARENT_REVOKED (the agent mandate it was issued under was withdrawn)"
    ISSUER_REVOKED = "issuer_revoked", "ISSUER_REVOKED (the app that issued it was rejected or revoked)"
    REMOVED_FROM_CONFIG = "removed_from_config", "REMOVED_FROM_CONFIG (the deployment config no longer names it)"
    SUPERSEDED = "superseded", "SUPERSEDED (replaced by a new key, or no longer asked for)"
    ORGANIZATION_REVOKED = "organization_revoked", "ORGANIZATION_REVOKED (every session of the organization was revoked)"


@strawberry.enum(description="Who may use a mandate.")
class MandateClaimant(str, Enum):
    KEY = strawberry.enum_value("key", description="Whoever holds the mandate key logs in as the grantor.")
    AGENT = strawberry.enum_value("agent", description="The agent app it names may issue keyed child mandates under it.")


@strawberry.enum(description="Who issued a mandate. A mandate dies with its issuer.")
class MandateOrigin(str, Enum):
    MEMBER = strawberry.enum_value("member", description="A member, through kontrol.")
    CLIENT = strawberry.enum_value("client", description="An app, as its own user.")
    AGENT = strawberry.enum_value("agent", description="An agent, under a parent agent mandate.")
    HUB = strawberry.enum_value("hub", description="Approving a hub that asks for the client.")
    CONFIG = strawberry.enum_value("config", description="The deployment config.")


@strawberry.enum(description="Why a mandate stopped working.")
class MandateRevocation(str, Enum):
    REVOKED = strawberry.enum_value("revoked", description="Withdrawn by its grantor or an admin.")
    PARENT_REVOKED = strawberry.enum_value("parent_revoked", description="The agent mandate it was issued under was withdrawn.")
    ISSUER_REVOKED = strawberry.enum_value("issuer_revoked", description="The app that issued it was rejected or revoked.")
    REMOVED_FROM_CONFIG = strawberry.enum_value("removed_from_config", description="The deployment config no longer names it.")
    SUPERSEDED = strawberry.enum_value("superseded", description="Replaced by a new key, or no longer asked for.")
    ORGANIZATION_REVOKED = strawberry.enum_value("organization_revoked", description="Every session of the organization was revoked.")


@strawberry.enum
class ClientRole(str, Enum):
    INTERFACE = strawberry.enum_value(
        "interface",
        description="""An interface client. Interface clients are human interfaces: a user actively
operates them (clicking through a UI, running a desktop app, browsing a website). They represent a
person interacting with the platform in real time.""",
    )
    AGENT = strawberry.enum_value(
        "agent",
        description="""An agent client. Agent clients are authorized once by a user and then run
unattended, receiving and processing tasks on that user's behalf (e.g. a Rekuest worker). They act
automatically rather than being driven by a human in real time.""",
    )


@strawberry.enum
class FaktValueType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"


@strawberry.enum
class ClientKind(str, Enum):
    DEVELOPMENT = strawberry.enum_value(
        "development",
        description="A development client. Development clients are clients that receive a client_id and client_secret, and are always linked to a user, that grants rights when creating the application. There is no active user authentication when the app gets started. They are used for development purposes.",
    )
    WEBSITE = strawberry.enum_value(
        "website",
        description="""A website clients. Website clients need to undergo an authentication flow, where the user is redirected to the website, before the client can be used. They are used for website applications, that want to access a user's data, and are hosted on non trusted domains.""",
    )
    DESKTOP = strawberry.enum_value(
        "desktop",
        description="""A desktop client. Desktop clients need to undergo an authentication flow, where the user is redirect back to the application. They use redirect but only on loopback adapters.""",
    )
    MOBILE = strawberry.enum_value(
        "mobile",
        description="""A mobile client. Mobile clients (iOS/Android apps) are public clients that need to undergo an authentication flow, where the user is redirected back to the application through a custom URL scheme or an app link, secured with PKCE.""",
    )
    HUB = strawberry.enum_value(
        "hub",
        description="A hub server's own identity: the client a hub authenticates as to claim its configuration and report. Never bound to an app release.",
    )
    RELYING_PARTY = strawberry.enum_value(
        "relying_party",
        description="A confidential OIDC relying party provisioned from config (ensureopenid). Global — belongs to no organization.",
    )


@strawberry.enum
class InstancePermissionKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@strawberry.enum
class KommunityKind(str, Enum):
    OPEN = "open"
    RESTRICTED = "restricted"
    PRIVATE = "private"
    
    
@strawberry.enum
class PartnerKind(str, Enum):
    PREAUTHORIZED = "preauthorized"
    OAUTH_FLOW = "oauth2"
    
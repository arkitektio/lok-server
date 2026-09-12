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
    RELATIVE = "relative", "RELATIVE (Value represent RELATIVE)"


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
    
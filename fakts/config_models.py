from typing import Literal

from pydantic import BaseModel, Field, model_validator

from fakts import enums
from fakts.base_models import HubManifest, Role, Scope, StagingAlias


class LayerModel(BaseModel):
    """Model representing a layer in the system."""

    identifier: str
    name: str | None = None
    kind: Literal[
        "loopback",
        "lan",
        "tailscale",
        "vpn",
        "public",
        "docker",
        "kubernetes",
        "tor",
        "zerotier",
        "manual",
        "proxy",
        "web",
    ]
    logo: str | None = None
    description: str | None = "No description available"
    get_probe: str | None = None


# Alias for backwards compatibility - use StagingAlias from base_models
AliasModel = StagingAlias


# Alias for backwards compatibility - use Role from base_models
RoleConfig = Role


# Alias for backwards compatibility - use Scope from base_models
ScopeConfig = Scope


class ServiceInstanceModel(BaseModel):
    """Model representing a service instance. Belongs to a service and has multiple aliases."""

    organization: str | None = None
    service: str
    version: str | None = "1.0.0"
    identifier: str
    roles: list[Role] = Field(default_factory=list)
    scopes: list[Scope] = Field(default_factory=list)
    aliases: list[StagingAlias] = Field(default_factory=list)


class ClientInstanceModel(BaseModel):
    organization: str | None = None
    client: str
    version: str | None = "1.0.0"
    identifier: str


class HubsConfigModel(BaseModel):
    instances: list[ServiceInstanceModel] = []
    clients: list[ClientInstanceModel] = []
    name: str
    description: str | None = None
    organization: str
    identifier: str | None = None  # Using identifier as slug


class YamlConfigModel(BaseModel):
    """Model representing the YAML configuration."""

    hubs: list[HubsConfigModel] = []

    @model_validator(mode="after")
    def validate_args(self):
        return self


class Oauth2ClientModel(BaseModel):
    """Model representing an OAuth2 client."""

    client_id: str
    client_secret: str
    redirect_uris: list[str] = []
    scopes: list[str] = []


class FilterConfigModel(BaseModel):
    """Model representing filter conditions for partner applicability.

    All conditions are optional. If multiple conditions are specified,
    ALL must be satisfied (AND logic).
    """

    email_domain_equals: list[str] | None = None
    email_domain_ends_with: list[str] | None = None
    username_equals: list[str] | None = None
    username_contains: list[str] | None = None


class KommunityPartnerModel(BaseModel):
    """Model representing a Kommunity partner."""

    name: str
    identifier: str = Field(
        ..., description="Unique identifier for the partner within the system"
    )
    auth_url: str | None = None
    website_url: str | None = None
    description: str | None = None
    short_description: str | None = None
    logo_url: str | None = None
    image_url: str | None = None
    license_agreement: str | None = None
    pre_authorize_hook: str | None = None
    pre_authorize_token: str | None = None
    oauth2: Oauth2ClientModel | None = None
    partner_kind: enums.PartnerKind = enums.PartnerKind.PREAUTHORIZED
    kommunity_kind: enums.KommunityKind = enums.KommunityKind.OPEN
    auto_configure: bool = False
    preconfigured_hub: HubManifest | None = None
    filter_config: FilterConfigModel | None = None


class KommunityPartnerConfigModel(BaseModel):
    """Model representing the Kommunity YAML configuration."""

    partners: list[KommunityPartnerModel] = []

    @model_validator(mode="after")
    def validate_args(self):
        return self

from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Literal
from fakts import enums
from fakts.base_models import HubManifest, StagingAlias, Role, Scope


class LayerModel(BaseModel):
    """Model representing a layer in the system."""

    identifier: str
    name: Optional[str] = None
    kind: Literal["loopback", "lan", "tailscale", "vpn", "public", "docker", "kubernetes", "tor", "zerotier", "manual", "proxy", "web"]
    logo: Optional[str] = None
    description: Optional[str] = "No description available"
    get_probe: Optional[str] = None


# Alias for backwards compatibility - use StagingAlias from base_models
AliasModel = StagingAlias


# Alias for backwards compatibility - use Role from base_models
RoleConfig = Role


# Alias for backwards compatibility - use Scope from base_models
ScopeConfig = Scope


class ServiceInstanceModel(BaseModel):
    """Model representing a service instance. Belongs to a service and has multiple aliases."""

    organization: Optional[str] = None
    service: str
    version: Optional[str] = "1.0.0"
    identifier: str
    roles: List[Role] = Field(default_factory=list)
    scopes: List[Scope] = Field(default_factory=list)
    aliases: List[StagingAlias] = Field(default_factory=list)


class ClientInstanceModel(BaseModel):
    organization: Optional[str] = None
    client: str
    version: Optional[str] = "1.0.0"
    identifier: str


class HubsConfigModel(BaseModel):
    instances: List[ServiceInstanceModel] = []
    clients: List[ClientInstanceModel] = []
    name: str
    description: Optional[str] = None
    organization: str
    identifier: Optional[str] = None  # Using identifier as slug


class YamlConfigModel(BaseModel):
    """Model representing the YAML configuration."""

    hubs: List[HubsConfigModel] = []

    @model_validator(mode="after")
    def validate_args(self):
        return self


class Oauth2ClientModel(BaseModel):
    """Model representing an OAuth2 client."""

    client_id: str
    client_secret: str
    redirect_uris: List[str] = []
    scopes: List[str] = []


class FilterConfigModel(BaseModel):
    """Model representing filter conditions for partner applicability.

    All conditions are optional. If multiple conditions are specified,
    ALL must be satisfied (AND logic).
    """

    email_domain_equals: Optional[List[str]] = None
    email_domain_ends_with: Optional[List[str]] = None
    username_equals: Optional[List[str]] = None
    username_contains: Optional[List[str]] = None


class KommunityPartnerModel(BaseModel):
    """Model representing a Kommunity partner."""

    name: str
    identifier: str = Field(..., description="Unique identifier for the partner within the system")
    auth_url: Optional[str] = None
    website_url: Optional[str] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    logo_url: Optional[str] = None
    image_url: Optional[str] = None
    license_agreement: Optional[str] = None
    pre_authorize_hook: Optional[str] = None
    pre_authorize_token: Optional[str] = None
    oauth2: Optional[Oauth2ClientModel] = None
    partner_kind: enums.PartnerKind = enums.PartnerKind.PREAUTHORIZED
    kommunity_kind: enums.KommunityKind = enums.KommunityKind.OPEN
    auto_configure: bool = False
    preconfigured_hub: Optional[HubManifest] = None
    filter_config: Optional[FilterConfigModel] = None


class RedeemTokenModel(BaseModel):
    """Model representing a redeem token configuration."""

    # Optional: an omitted token is *generated* with real entropy rather than
    # being whatever string an operator typed into a YAML file. A redeem token
    # is an unauthenticated bearer credential at the token endpoint — it is the
    # one secret in this flow whose entropy was a human's choice.
    token: Optional[str] = None
    user: str
    organization: str
    hub: str
    expires_in_days: Optional[int] = Field(
        default=90,
        description=(
            "Lifetime from provisioning. `null` means never expires, which is "
            "only appropriate for a token you rotate by other means."
        ),
    )
    max_redemptions: Optional[int] = Field(
        default=None,
        description=(
            "How many times this token may be redeemed. `null` means unlimited "
            "(the historical behaviour). Set to 1 for a genuine single-use token."
        ),
    )


class RedeemTokenConfigs(BaseModel):
    """The redeem tokens configuration model"""

    tokens: List[RedeemTokenModel] = Field(default_factory=list)


class KommunityPartnerConfigModel(BaseModel):
    """Model representing the Kommunity YAML configuration."""

    partners: List[KommunityPartnerModel] = []

    @model_validator(mode="after")
    def validate_args(self):
        return self

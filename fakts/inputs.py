import strawberry
from strawberry.experimental import pydantic
from fakts.base_models import Manifest, LinkingContext, LinkingRequest, PublicSource
from typing import Optional
from pydantic import BaseModel, Field
from fakts import enums
import enum


class RequirementModel(BaseModel):
    service: str
    optional: bool = False
    description: Optional[str] = None
    key: str


@pydantic.input(RequirementModel)
class RequirementInput:
    service: str
    optional: bool = False
    description: Optional[str] = None
    key: str


@strawberry.enum
class PublicSourceKind(str, enum.Enum):
    GITHUB = "github"
    WEBSITE = "website"


@pydantic.input(PublicSource)
class PublicSourceInput:
    kind: PublicSourceKind
    url: str


@pydantic.input(Manifest)
class ManifestInput:
    identifier: str
    version: str
    title: Optional[str] = None
    description: Optional[str] = None
    logo: Optional[str] = None
    scopes: list[str]
    device_id: Optional[str] = strawberry.field(
        default=None, description="The device the client runs on."
    )
    node_id: Optional[str] = strawberry.field(
        default=None, deprecation_reason="Use deviceId."
    )
    requirements: list[RequirementInput] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    license: Optional[str] = None
    homepage: Optional[str] = None
    repo_url: Optional[str] = None
    public_sources: list[PublicSourceInput] | None = None


class ScanBackendInputModel(BaseModel):
    backend: str | None


@pydantic.input(ScanBackendInputModel)
class ScanBackendInput:
    backend: str | None = None


@pydantic.input(LinkingRequest)
class LinkingRequestInput:
    host: str
    port: str
    is_secure: bool


@pydantic.input(LinkingContext)
class LinkingContextInput:
    request: LinkingRequestInput
    manifest: ManifestInput


class RenderInputModel(BaseModel):
    client: str
    hub: str | None = None
    request: LinkingRequest | None = None
    manifest: Manifest | None = None


@pydantic.input(RenderInputModel)
class RenderInput:
    client: strawberry.ID
    hub: strawberry.ID | None = None
    request: LinkingRequestInput | None = None
    manifest: ManifestInput | None = None


class KeyValueInputModel(BaseModel):
    key: str
    value: str
    as_type: enums.FaktValueType


class UserDefinedServiceInstanceInputModel(BaseModel):
    identifier: str
    values: list[KeyValueInputModel] = Field(default_factory=list)


@pydantic.input(KeyValueInputModel)
class KeyValueInput:
    key: str
    value: str
    as_type: enums.FaktValueType


@pydantic.input(UserDefinedServiceInstanceInputModel)
class UserDefinedServiceInstanceInput:
    identifier: str
    values: list[KeyValueInput] = strawberry.field(default_factory=list)


class UpdateServiceInstanceInputModel(BaseModel):
    id: str


@pydantic.input(UpdateServiceInstanceInputModel)
class UpdateServiceInstanceInput:
    id: strawberry.ID
    allowed_users: list[strawberry.ID] | None = None
    allowed_groups: list[strawberry.ID] | None = None
    denied_groups: list[strawberry.ID] | None = None
    denied_users: list[strawberry.ID] | None = None


class UpdateDeviceInputModel(BaseModel):
    id: str
    name: str | None


@pydantic.input(UpdateDeviceInputModel)
class UpdateDeviceInput:
    id: strawberry.ID
    name: str | None


class CreateServiceInstanceInputModel(BaseModel):
    identifier: str
    service: str
    allowed_users: list[str] | None = None
    allowed_groups: list[str] | None = None
    denied_groups: list[str] | None = None
    denied_users: list[str] | None = None


@pydantic.input(CreateServiceInstanceInputModel)
class CreateServiceInstanceInput:
    identifier: str
    service: strawberry.ID
    allowed_users: list[strawberry.ID] | None = None
    allowed_groups: list[strawberry.ID] | None = None
    denied_groups: list[strawberry.ID] | None = None
    denied_users: list[strawberry.ID] | None = None

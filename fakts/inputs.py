import enum

import strawberry
from pydantic import BaseModel, Field
from strawberry.experimental import pydantic

from fakts import enums
from fakts.base_models import LinkingContext, LinkingRequest, Manifest, PublicSource


class RequirementModel(BaseModel):
    service: str
    optional: bool = False
    description: str | None = None
    key: str
    version: str | None = None


@pydantic.input(RequirementModel)
class RequirementInput:
    service: str
    optional: bool = False
    description: str | None = None
    key: str
    version: str | None = strawberry.field(
        default=None,
        description="The service versions the client was written for, as a specifier (advisory).",
    )


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
    title: str | None = None
    description: str | None = None
    logo: str | None = None
    scopes: list[str]
    device_id: str | None = strawberry.field(
        default=None, description="The device the client runs on."
    )
    node_id: str | None = strawberry.field(
        default=None, deprecation_reason="Use deviceId."
    )
    requirements: list[RequirementInput] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    license: str | None = None
    homepage: str | None = None
    repo_url: str | None = None
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

import datetime

import strawberry_django
import strawberry
from typing import Optional
from karakter import types
from fakts import models, scalars, filters, enums
from authapp import types as atypes
from kante.types import Info
from strawberry.scalars import JSON

# `DENIED` is re-exported for `fakts.graphql.mutations.render`; the single source
# of truth (and of the tenant-scoping helpers) is `karakter.authz`.
from karakter.authz import DENIED, build_prescoped_queryset  # noqa: F401


@strawberry.type(description="Temporary Credentials for a file upload that can be used by a Client (e.g. in a python datalayer)")
class PresignedPostCredentials:
    """Temporary Credentials for a a file upload."""

    key: str
    x_amz_algorithm: str
    x_amz_credential: str
    x_amz_date: str
    x_amz_signature: str
    policy: str
    datalayer: str
    bucket: str
    store: str
    content_type: str = strawberry.field(description="The Content-Type the upload form must send; the policy pins it exactly.")


@strawberry.type(description="A scope that can be assigned to a client. Scopes are used to limit the access of a client to a user's data. They represent app-level permissions.")
class Scope:
    label: str = strawberry.field(description="The label of the scope. This is the human readable name of the scope.")
    description: str = strawberry.field(description="The description of the scope. This is a human readable description of the scope.")
    value: str = strawberry.field(description="The value of the scope. This is the value that is used in the OAuth2 flow.")


@strawberry_django.type(
    models.Layer,
    ordering=filters.LayerOrdering,
    description="A Layer is a network through which service instances can be reached (e.g. the public web, a tailnet, a VPN, or a docker network). Instance aliases are resolved relative to the layer they belong to.",
    pagination=True,
    filters=filters.LayerFilter,
)
class Layer:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the layer")
    identifier: scalars.ServiceIdentifier = strawberry.field(description="The identifier of the layer. This should be a globally unique string that identifies the layer. We encourage you to use the reverse domain name notation. E.g. `com.example.mylayer`")
    logo: types.MediaStore | None = strawberry.field(description="The logo of the layer. This should be a url to a logo that can be used to represent the layer.")
    description: str | None = strawberry.field(description="The description of the layer. This should be a human readable description of the layer.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(
    models.Hub,
    ordering=filters.HubOrdering,
    description="A Hub is a specific configuration of a Service. It contains the configuration for a particular version of the service.",
    pagination=True,
    filters=filters.HubFilter,
)
class Hub:
    id: strawberry.ID
    organization: types.Organization = strawberry.field(description="The organization that this hub belongs to.")
    identifier: scalars.ServiceIdentifier = strawberry.field(description="The identifier of the hub. This should be a globally unique string that identifies the hub. We encourage you to use the reverse domain name notation. E.g. `com.example.myhub`")
    description: str | None = strawberry.field(description="The description of the service. This should be a human readable description of the service.")
    name: str = strawberry.field(description="The name of the hub. This should be a human readable name of the hub.")
    last_seen_at: Optional[datetime.datetime] = strawberry.field(description="When the hub last reported its health. Null if it never has.")
    last_healthy: Optional[bool] = strawberry.field(description="Whether the hub's last health report said it was healthy. Null if it never reported.")
    version: str = strawberry.field(description="The hub software version, as last reported (empty if never reported).")
    mesh_connected: Optional[bool] = strawberry.field(description="Whether the hub's last health report said its node is on the mesh. Null if it never reported mesh state.")
    mesh_host: str = strawberry.field(description="The hub node's MagicDNS name (or mesh IP), as last reported by the hub. Empty when unknown or off the mesh.")

    @strawberry_django.field(description="Whether the hub reported its health within the last three reporting intervals.")
    def online(self) -> bool:
        return self.online

    @strawberry_django.field(description="The hub's most recent health report, or null if it never reported.")
    def latest_health(self) -> Optional["HubHealthReport"]:
        return self.health_snapshots.first()

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry.type(description="The health a hub reported for one of its service instances.")
class InstanceHealth:
    instance: "ServiceInstance" = strawberry.field(description="The service instance this entry is about.")
    healthy: bool = strawberry.field(description="Did the hub report the instance healthy?")
    reason: Optional[str] = strawberry.field(default=None, description="Why the instance is unhealthy, if the hub said.")


@strawberry_django.type(models.HubHealthSnapshot, description="One health report a hub posted to lok.")
class HubHealthReport:
    id: strawberry.ID
    healthy: bool = strawberry.field(description="Did the hub report itself healthy?")
    created_at: datetime.datetime = strawberry.field(description="When the hub reported.")

    @strawberry_django.field(description="The per-instance health in this report. Only instances of the hub are listed.")
    def instances(self) -> list[InstanceHealth]:
        reported = (self.payload or {}).get("instances") or {}
        by_token = {i.token: i for i in self.hub.instances.filter(token__in=list(reported))}
        return [
            InstanceHealth(instance=by_token[key], healthy=bool(value.get("healthy")), reason=value.get("reason"))
            for key, value in reported.items()
            if key in by_token
        ]


@strawberry_django.type(
    models.Service,
    ordering=filters.ServiceOrdering,
    description="A Service is a Webservice that a Client might want to access. It is not the configured instance of the service, but the service itself.",
    pagination=True,
    filters=filters.ServiceFilter,
)
class Service:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the service")
    identifier: scalars.ServiceIdentifier = strawberry.field(description="The identifier of the service. This should be a globally unique string that identifies the service. We encourage you to use the reverse domain name notation. E.g. `com.example.myservice`")
    description: str | None = strawberry.field(description="The description of the service. This should be a human readable description of the service.")
    releases: list["ServiceRelease"] = strawberry_django.field(
        description="The releases of the service. A service release is a specific version of a service. It will be configured by a configuration backend and will be used to send to the client as a configuration. It should never contain sensitive information."
    )
    logo: types.MediaStore | None = strawberry.field(description="The logo of the service. This should be a url to a logo that can be used to represent the service.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(
    models.ServiceRelease,
    ordering=filters.ServiceReleaseOrdering,
    description="A ServiceRelease is a specific release of a Service. It contains the configuration for a particular version of the service.",
    pagination=True,
    filters=filters.ServiceReleaseFilter,
)
class ServiceRelease:
    id: strawberry.ID
    version: str = strawberry.field(description="The version of the service. This should be a human readable version string.")
    service: Service = strawberry.field(description="The service that this release belongs to.")
    instances: list["ServiceInstance"] = strawberry_django.field(
        description="The instances of the service. A service instance is a configured instance of a service. It will be configured by a configuration backend and will be used to send to the client as a configuration. It should never contain sensitive information."
    )

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset, field="service__organization")


@strawberry_django.type(
    models.ServiceInstance,
    ordering=filters.ServiceInstanceOrdering,
    description="A ServiceInstance is a configured instance of a Service. It will be configured by a configuration backend and will be used to send to the client as a configuration. It should never contain sensitive information.",
    pagination=True,
    filters=filters.ServiceInstanceFilter,
)
class ServiceInstance:
    id: strawberry.ID
    release: ServiceRelease = strawberry.field(description="The service release that this instance belongs to.")
    instance_id: strawberry.ID = strawberry.field(description="The instance id of the instance. This is a unique string that identifies the instance. It is used to identify the instance in the code and in the database.")  
    allowed_users: list[types.User] = strawberry_django.field(description="The users that are allowed to use this instance.")

    @strawberry_django.field(description="A human readable name of the instance, derived from its service identifier and instance id.", select_related=["release__service"])
    def name(self, info: Info) -> str:
        return f"{self.release.service.identifier}:{self.instance_id}"

    denied_users: list[types.User] = strawberry_django.field(description="The users that are denied to use this instance.")
    allowed_groups: list[types.Group] = strawberry_django.field(description="The groups that are allowed to use this instance.")
    denied_groups: list[types.Group] = strawberry_django.field(description="The groups that are denied to use this instance.")
    mappings: list["ServiceInstanceMapping"] = strawberry_django.field(description="The mappings of the hub. A mapping is a mapping of a service to a service instance. This is used to configure the hub.")
    logo: types.MediaStore | None = strawberry.field(description="The logo of the app. This should be a url to a logo that can be used to represent the app.")
    aliases: list["InstanceAlias"] = strawberry_django.field(
        description="The aliases of the instance. An alias is a way to reach the instance. Clients can use these aliases to check if they can reach the instance. An alias can be an absolute alias (e.g. 'example.com') or a relative alias (e.g. 'example.com/path'). If the alias is relative, it will be relative to the layer's domain, port and path."
    )

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(
    models.InstanceAlias,
    ordering=filters.InstanceAliasOrdering,
    description="An alias for a service instance. This is used to provide a more user-friendly name for the instance.",
)
class InstanceAlias:
    id: strawberry.ID
    layer: Optional[Layer] = strawberry.field(description="The layer that this alias belongs to, if any.")
    instance: ServiceInstance = strawberry.field(description="The instance that this alias belongs to.")
    kind: str = strawberry.field(description="The kind of alias. If relative, the alias is resolved against the layer's domain/port/path; if absolute, it is a full URL.")
    host: Optional[str] = strawberry.field(description="The host of the alias, if its a ABSOLUTE alias (e.g. 'example.com'). If not set, the alias is relative to the layer's domain.")
    port: Optional[int] = strawberry.field(description="The port of the alias, if its a ABSOLUTE alias (e.g. 'example.com:8080'). If not set, the alias is relative to the layer's port.")
    path: Optional[str] = strawberry.field(description="The path of the alias, if its a ABSOLUTE alias (e.g. 'example.com/path'). If not set, the alias is relative to the layer's path.")
    ssl: bool = strawberry.field(description="Is this alias using SSL? If true, the alias will be accessed via https:// instead of http://. This is used to indicate that the alias is secure and should be accessed via SSL")
    challenge: str = strawberry.field(description="The challenge of the alias. This is used to verify that the alias is reachable. If set, the alias will be accessed via the challenge URL (e.g. 'example.com/.well-known/challenge'). If not set, the alias will be accessed via the instance's URL.")
    public: bool = strawberry.field(description="Is this alias publicly reachable? If true, the coordination server can also check the alias's health directly, enabling health checks from the kontrol interface.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset, field="instance__organization")


@strawberry_django.type(
    models.ServiceInstanceMapping,
    ordering=filters.ServiceInstanceMappingOrdering,
    description="A ServiceInstanceMapping binds one of a client's requirements (by key) to the ServiceInstance that fulfils it. The set of mappings of a client is its composed configuration.",
)
class ServiceInstanceMapping:
    id: strawberry.ID
    instance: ServiceInstance = strawberry.field(description="The service instance this requirement is mapped to.")
    client: "Client" = strawberry.field(description="The client whose requirement this mapping fulfils.")
    key: str = strawberry.field(description="The requirement key of the client that this mapping fulfils. Unique per client.")
    optional: bool = strawberry.field(description="Is this mapping optional? If a mapping is optional, you can configure the client without this mapping.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset, field="instance__organization")


@strawberry.type
class DefinedValue:
    key: str
    value: str
    as_type: enums.FaktValueType


@strawberry_django.type(
    models.App,
    ordering=filters.AppOrdering,
    filters=filters.AppFilter,
    description="An App is the Arkitekt equivalent of a Software Application. It is a collection of `Releases` that can be all part of the same application. E.g the App `Napari` could have the releases `0.1.0` and `0.2.0`.",
    pagination=True,
)
class App:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the app")
    identifier: scalars.AppIdentifier = strawberry.field(description="The identifier of the app. This should be a globally unique string that identifies the app. We encourage you to use the reverse domain name notation. E.g. `com.example.myapp`")

    releases: list["Release"] = strawberry.field(description="The releases of the app. A release is a version of the app that can be installed by a user.")

    logo: types.MediaStore | None = strawberry.field(description="The logo of the app. This should be a url to a logo that can be used to represent the app.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(
    models.Release,
    ordering=filters.ReleaseOrdering,
    description="A Release is a version of an app. Releases might change over time. E.g. a release might be updated to fix a bug, and the release might be updated to add a new feature. This is why they are the home for `scopes` and `requirements`, which might change over the release cycle.",
)
class Release:
    id: strawberry.ID
    app: App = strawberry.field(description="The app that this release belongs to.")
    version: scalars.Version = strawberry.field(description="The version of the release. This should be a string that identifies the version of the release. We enforce semantic versioning notation. E.g. `0.1.0`. The version is unique per app.")
    name: str = strawberry.field(description="The name of the release. This should be a string that identifies the release beyond the version number. E.g. `canary`.")
    logo: types.MediaStore | None = strawberry.field(description="The logo of the release. This should be a url to a logo that can be used to represent the release.")
    scopes: list[str] = strawberry.field(description="The scopes of the release. Scopes are used to limit the access of a client to a user's data. They represent app-level permissions.")
    clients: list["Client"] = strawberry.field(description="The clients of the release")

    @strawberry_django.field(
        description="The requirements of the release: the services (by key and service identifier) a client of this release needs composed against it. Each entry is a manifest `Requirement` object (`key`, `service`, `optional`, `description`).",
    )
    def requirements(self, info: Info) -> list[JSON]:
        # Stored as a JSONField whose *default* is a dict but which every write
        # path (fakts.services.clients.bind_client) fills with the manifest's
        # list of requirement objects. Tolerate both shapes.
        raw = self.requirements
        if isinstance(raw, dict):
            return [raw] if raw else []
        return list(raw or [])

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset, field="app__organization")


@strawberry.type
class PublicSource:
    kind: str = strawberry.field(description="The kind of the public source. E.g. 'github'")
    url: str = strawberry.field(description="The url of the public source")


# Database `kind` value -> GraphQL enum. Every `ClientKindChoices` member must
# appear here; unknown/legacy values fall back to DEVELOPMENT rather than None.
_CLIENT_KINDS = {
    enums.ClientKindChoices.WEBSITE.value: enums.ClientKind.WEBSITE,
    enums.ClientKindChoices.DESKTOP.value: enums.ClientKind.DESKTOP,
    enums.ClientKindChoices.MOBILE.value: enums.ClientKind.MOBILE,
    enums.ClientKindChoices.DEVELOPMENT.value: enums.ClientKind.DEVELOPMENT,
    enums.ClientKindChoices.HUB.value: enums.ClientKind.HUB,
    enums.ClientKindChoices.RELYING_PARTY.value: enums.ClientKind.RELYING_PARTY,
}


@strawberry_django.type(
    models.Client,
    ordering=filters.ClientOrdering,
    description="""A client is a way of authenticating users with a release.
 The strategy of authentication is defined by the kind of client. And allows for different authentication flow. 
 E.g a client can be a DESKTOP app, that might be used by multiple users, or a WEBSITE that wants to connect to a user's account, 
 but also a DEVELOPMENT client that is used by a developer to test the app. The client model thinly wraps the oauth2 client model, which is used to authenticate users.""",
    filters=filters.ClientFilter,
    pagination=True,
)
class Client:
    id: strawberry.ID
    functional: bool = strawberry_django.field(description="Is this client functional? A functional client is a client that is able to authenticate users. If a client is not functional, it will not be able to authenticate users.")
    release: Release | None = strawberry_django.field(description="The release that this client belongs to. Null for clients that are not bound to an app release (hub identities, relying parties, pending registrations).")
    client_id: str = strawberry_django.field(description="The OAuth2 client id this client authenticates as.")
    public: bool = strawberry_django.field(description="Is this client public? A public client cannot keep a secret (desktop apps, mobile apps, SPAs, hub identities) and authenticates without one, relying on PKCE / device-code flows instead.")

    @strawberry_django.field(
        description="The user this client acts for (derived from its membership).",
        only=["membership"],
        select_related=["membership__user"],
    )
    def user(self, info: Info) -> types.User | None:
        return self.membership.user if self.membership_id else None

    logo: types.MediaStore | None = strawberry_django.field(description="The logo of the release. This should be a url to a logo that can be used to represent the release.")
    node: Optional["Device"] = strawberry_django.field(description="The node this runs on")

    @strawberry_django.field(
        description="A human-readable label for the client that folds in the app, version, "
        "operator and device — e.g. `com.example.app:v0.1.1 by Johannes on my-laptop`.",
        select_related=["release__app", "membership__user", "node"],
    )
    def name(self, info: Info) -> str:
        release = self.release
        label = f"{release.app.identifier}:v{release.version}" if release else (self.name or "Unknown client")
        person = self.membership.user if self.membership_id else None
        if person:
            full = f"{person.first_name or ''} {person.last_name or ''}".strip()
            label += f" by {full or person.username}"
        if self.node and self.node.name:
            label += f" on {self.node.name}"
        return label
    mappings: list["ServiceInstanceMapping"] = strawberry_django.field(description="The mappings of the client. A mapping is a mapping of a service to a service instance. This is used to configure the hub.")






    @strawberry_django.field(description="What kind of principal this client is (its authentication strategy): DEVELOPMENT, WEBSITE, DESKTOP, MOBILE, HUB or RELYING_PARTY.")
    def kind(self, info: Info) -> enums.ClientKind:
        return _CLIENT_KINDS.get(self.kind, enums.ClientKind.DEVELOPMENT)

    @strawberry_django.field(description="The operational role of the client. INTERFACE clients are human interfaces operated by a user in real time. AGENT clients are authorized once and then run unattended, receiving and processing tasks on the user's behalf.")
    def role(self, info: Info) -> enums.ClientRole:
        if self.role == "agent":
            return enums.ClientRole.AGENT
        return enums.ClientRole.INTERFACE

    @strawberry_django.field(description="The issue url of the client. This is the url where users can report issues and get more information about the client.")
    def issue_url(self, info: Info) -> str | None:
        for source in self.public_sources or []:
            if not isinstance(source, dict):
                continue
            url = source.get("url")
            if (source.get("kind") or "").lower() == "github" and url:
                return f"{url.rstrip('/')}/issues/new"

        return None

    @strawberry_django.field(description="The public sources of the client. These are the public sources where users can find more information about the client.")
    def public_sources(self, info: Info) -> list[PublicSource]:
        sources = []
        for source in self.public_sources or []:
            if not isinstance(source, dict):
                continue
            sources.append(
                PublicSource(
                    kind=source.get("kind") or "",
                    url=source.get("url") or "",
                )
            )
        return sources

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Restrict clients to the caller's active organization.

        Client rows are tenant-private (instance mappings, manifests, health);
        without this the root `clients` list returned every tenant's.
        """
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(
    models.DeviceGroup,
    ordering=filters.DeviceGroupOrdering,
    description="A DeviceGroup is a group of compute nodes that can be used to run clients. DeviceGroups can be used to group compute nodes by location, hardware type, or any other criteria.",
    pagination=True,
    filters=filters.DeviceGroupFilter,
)
class DeviceGroup:
    id: strawberry.ID
    name: str = strawberry.field(description="The name of the device group.")
    devices: list["Device"] = strawberry_django.field(description="The devices that belong to this device group.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(models.Device, filters=filters.DeviceFilter, pagination=True, ordering=filters.DeviceOrdering)
class Device:
    id: strawberry.ID
    name: str | None
    node_id: strawberry.ID = strawberry_django.field(deprecation_reason="Use deviceId.")
    clients: list[Client]

    @strawberry_django.field(description="The (per-organization hashed) id of the device.")
    def device_id(self) -> strawberry.ID:
        return self.node_id
    device_groups: list[DeviceGroup] = strawberry_django.field(description="The device groups that belong to this device.")

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset)


@strawberry_django.type(models.RedeemToken, filters=filters.RedeemTokenFilter, pagination=True, ordering=filters.RedeemTokenOrdering)
class RedeemToken:
    id: strawberry.ID
    token: str = strawberry.field(description="The token of the redeem token")
    client: Client | None = strawberry.field(description="The client that this redeem token belongs to.")
    user: types.User = strawberry.field(description="The user that this redeem token belongs to.")
    expires_at: datetime.datetime | None = strawberry.field(description="When this token stops being redeemable. Null means never.")
    max_redemptions: int | None = strawberry.field(description="How many times this token may be redeemed. Null means unlimited.")
    redemption_count: int = strawberry.field(description="How many times this token has been redeemed so far.")
    pinned_manifest: JSON | None = strawberry.field(
        description=(
            "The manifest this token was pre-authorized for at mint time, or null for an "
            "unpinned token. A redeem must match its identifier, version and device_id exactly "
            "and may only request a subset of its scopes and requirements."
        )
    )

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        return build_prescoped_queryset(info, queryset, field="hub__organization").filter(
            user=info.context.request.user
        )

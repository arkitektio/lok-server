import strawberry
import strawberry_django
from django.db.models import Q
from strawberry_django.filters import FilterLookup

from fakts import enums as fakts_enums
from fakts import models as fakts_models
from fakts.scalars import AppIdentifier
from karakter import models


@strawberry_django.filter_type(models.User, description="Filter for User model.")
class UserFilter:
    """Filter for User model."""

    # `User` has no `name` column; filter on `username` instead.
    username: FilterLookup[str] | None | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}username__icontains": value})


@strawberry_django.filter_type(models.Group, description="Filter for Group model.")
class GroupFilter:
    name: FilterLookup[str] | None | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.filter_type(fakts_models.Client)
class ClientFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    @strawberry_django.filter_field
    def role(self, value: fakts_enums.ClientRole, prefix: str) -> Q:
        # `Client.role` is a TextChoicesField, which only accepts its enum
        # members in lookups — a bare string raised at query time.
        # `ClientRole` is a `str, Enum` built from `strawberry.enum_value(...)`,
        # so its `.value` is the *repr* of the wrapper, not the raw string. The
        # member *names* line up with the model's choices enum, so map by name.
        return Q(**{f"{prefix}role": fakts_enums.ClientRoleChoices[value.name]})


@strawberry_django.filter_type(fakts_models.App)
class AppFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value}) | Q(
            **{f"{prefix}identifier__icontains": value}
        )

    @strawberry_django.filter_field(
        description="Only the apps with one of these identifiers (exact match)."
    )
    def identifiers(self, value: list[AppIdentifier], prefix: str) -> Q:
        return Q(**{f"{prefix}identifier__in": [str(v) for v in value]})


@strawberry_django.filter_type(fakts_models.Mandate)
class MandateFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}label__icontains": value}) | Q(
            **{f"{prefix}subject_manifest__identifier__icontains": value}
        )

    @strawberry_django.filter_field
    def live(self, value: bool, prefix: str) -> Q:
        from django.utils import timezone

        live = Q(**{f"{prefix}revoked_at__isnull": True}) & (
            Q(**{f"{prefix}expires_at__isnull": True})
            | Q(**{f"{prefix}expires_at__gt": timezone.now()})
        )
        return live if value else ~live

    @strawberry_django.filter_field(
        description="Only the mandates this claimant may use: KEY (keyed) or AGENT (an agent app issues keys under it)."
    )
    def claimant(self, value: fakts_enums.MandateClaimant, prefix: str) -> Q:
        return Q(
            **{f"{prefix}claimant": fakts_enums.MandateClaimantChoices[value.name]}
        )

    @strawberry_django.filter_field(
        description="Only the mandates issued under a parent agent mandate (true), or only the top-level ones (false)."
    )
    def has_parent(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}parent__isnull": not value})

    @strawberry_django.filter_field(
        description="Only the agent mandates this app (by identifier) may issue keys under."
    )
    def agent_identifier(self, value: AppIdentifier, prefix: str) -> Q:
        return Q(**{f"{prefix}agent_identifier": str(value)})

    @strawberry_django.filter_field(
        description="Only the mandates admitting this app (by identifier, exact)."
    )
    def subject_identifier(self, value: AppIdentifier, prefix: str) -> Q:
        return Q(**{f"{prefix}subject_manifest__identifier": str(value)})

    @strawberry_django.filter_field(
        description="Only the mandates whose clients may (true) or may not (false) ask for a mesh key."
    )
    def allow_mesh(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}allow_mesh": value})


@strawberry_django.filter_type(fakts_models.Service)
class ServiceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.filter_type(fakts_models.Device)
class DeviceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    @strawberry_django.filter_field(
        description="Only the devices with one of these device ids: the ids the other services know a device by (`Device.deviceId`, a token's `client_device` claim), used as they are, never hashed again."
    )
    def device_ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}node_id__in": [str(v) for v in value]})

    @strawberry_django.filter_field(
        description="Only the devices in this device group."
    )
    def device_group(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}device_groups__id": value})


@strawberry_django.filter_type(fakts_models.DeviceGroup)
class DeviceGroupFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.filter_type(fakts_models.Layer)
class LayerFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.filter_type(fakts_models.ServiceInstance)
class ServiceInstanceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}instance_id__icontains": value})


@strawberry_django.filter_type(fakts_models.ServiceRelease)
class ServiceReleaseFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}version__icontains": value})


@strawberry_django.filter_type(fakts_models.Hub)
class HubFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.order_type(fakts_models.App)
class AppOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.Release)
class ReleaseOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.Release)
class ReleaseFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field(description="Only the releases of these apps.")
    def apps(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}app_id__in": value})

    @strawberry_django.filter_field(
        description="Releases whose version, name or app name or identifier contain this (any case)."
    )
    def search(self, value: str, prefix: str) -> Q:
        return (
            Q(**{f"{prefix}version__icontains": value})
            | Q(**{f"{prefix}name__icontains": value})
            | Q(**{f"{prefix}app__name__icontains": value})
            | Q(**{f"{prefix}app__identifier__icontains": value})
        )


@strawberry_django.order_type(fakts_models.Client)
class ClientOrdering:
    id: strawberry.auto
    name: strawberry.auto
    created_at: strawberry.auto
    last_reported_at: strawberry.auto


@strawberry_django.order_type(fakts_models.Service)
class ServiceOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.ServiceRelease)
class ServiceReleaseOrdering:
    id: strawberry.auto


@strawberry_django.order_type(fakts_models.Device)
class DeviceOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.DeviceGroup)
class DeviceGroupOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.Layer)
class LayerOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.ServiceInstance)
class ServiceInstanceOrdering:
    id: strawberry.auto


@strawberry_django.order_type(fakts_models.InstanceAlias)
class InstanceAliasOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.ServiceInstanceMapping)
class ServiceInstanceMappingOrdering:
    id: strawberry.auto


@strawberry_django.order_type(fakts_models.Hub)
class HubOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.Mandate)
class MandateOrdering:
    id: strawberry.auto
    created_at: strawberry.auto
    last_used_at: strawberry.auto


@strawberry_django.order_type(fakts_models.BugReport)
class BugReportOrdering:
    id: strawberry.auto
    created_at: strawberry.auto


@strawberry_django.filter_type(fakts_models.BugReport)
class BugReportFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}title__icontains": value}) | Q(
            **{f"{prefix}description__icontains": value}
        )

    @strawberry_django.filter_field(description="Reports filed from this client.")
    def client(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}client__id": value})

    @strawberry_django.filter_field(
        description="Reports filed from any client of this release."
    )
    def release(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}release__id": value})

    @strawberry_django.filter_field(
        description="Reports filed from any release of this app."
    )
    def app(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}release__app__id": value})

    @strawberry_django.filter_field(description="Reports this user filed.")
    def reporter(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}reporter__id": value})

    @strawberry_django.filter_field(
        description="True: only resolved reports. False: only the ones still open."
    )
    def resolved(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}resolved_at__isnull": not value})

    @strawberry_django.filter_field(
        description="True: only reports already passed on to an issue tracker. False: only those that were not."
    )
    def forwarded(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}forwarded_at__isnull": not value})

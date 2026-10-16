import strawberry
from karakter import models
from typing import Optional
from strawberry_django.filters import FilterLookup
import strawberry_django
from django.db.models import Q
from fakts import models as fakts_models
from fakts import enums as fakts_enums


@strawberry_django.filter_type(models.User, description="Filter for User model.")
class UserFilter:
    """Filter for User model."""

    # `User` has no `name` column; filter on `username` instead.
    username: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}username__contains": value})


@strawberry_django.filter_type(models.Group, description="Filter for Group model.")
class GroupFilter:
    name: Optional[FilterLookup[str]] | None

    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(fakts_models.Client)
class ClientFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})

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
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(fakts_models.Mandate)
class MandateFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}label__icontains": value}) | Q(**{f"{prefix}subject_manifest__identifier__icontains": value})

    @strawberry_django.filter_field
    def live(self, value: bool, prefix: str) -> Q:
        from django.utils import timezone

        live = Q(**{f"{prefix}revoked_at__isnull": True}) & (
            Q(**{f"{prefix}expires_at__isnull": True}) | Q(**{f"{prefix}expires_at__gt": timezone.now()})
        )
        return live if value else ~live


@strawberry_django.filter_type(fakts_models.Service)
class ServiceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(fakts_models.Device)
class DeviceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(fakts_models.DeviceGroup)
class DeviceGroupFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.filter_type(fakts_models.Layer)
class LayerFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


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

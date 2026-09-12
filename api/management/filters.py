import strawberry
import strawberry_django
from django.db.models import Q
from kante.types import Info
from fakts import models as fakts_models
from fakts import enums as fakts_enums
from karakter import models as karakter_models


@strawberry_django.order_type(fakts_models.KommunityPartner)
class ManagementKommunityPartnerOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.KommunityPartner)
class ManagementKommunityPartnerFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})

    @strawberry_django.filter_field
    def auto_configure(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}auto_configure": value})

    @strawberry_django.filter_field
    def has_preconfigured_hub(self, value: bool, queryset, prefix: str):
        if value:
            return (
                queryset.exclude(preconfigured_hub__isnull=True).exclude(
                    preconfigured_hub={}
                ),
                Q(),
            )
        return (
            queryset.filter(preconfigured_hub__isnull=True)
            | queryset.filter(preconfigured_hub={}),
            Q(),
        )

    @strawberry_django.filter_field
    def applicable_for_me(self, info: Info, value: bool, queryset, prefix: str):
        """Filter partners that apply to the current user based on their filter_config."""
        user = info.context.request.user
        if not user.is_authenticated:
            return (queryset.none() if value else queryset), Q()

        # We need to filter in Python since filter_config logic is complex
        applicable_ids = [
            partner.id for partner in queryset if partner.applies_to_user(user) == value
        ]
        return queryset.filter(id__in=applicable_ids), Q()


@strawberry_django.order_type(fakts_models.IonscaleLayer)
class ManagementLayerOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.IonscaleLayer)
class ManagementLayerFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.order_type(fakts_models.DeviceGroup)
class ManagementDeviceGroupOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.DeviceGroup)
class ManagementDeviceGroupFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.order_type(fakts_models.Hub)
class ManagementHubOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.Hub)
class ManagementHubFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.order_type(fakts_models.Device)
class ManagementDeviceOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.Device)
class ManagementDeviceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.order_type(karakter_models.Membership)
class ManagementMembershipOrdering:
    id: strawberry.auto


@strawberry_django.filter_type(karakter_models.Membership)
class ManagementMembershipFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}user__username__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.order_type(karakter_models.RoleRequest)
class ManagementRoleRequestOrdering:
    id: strawberry.auto
    created_at: strawberry.auto


@strawberry_django.filter_type(karakter_models.RoleRequest)
class ManagementRoleRequestFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}membership__organization__id": value})

    @strawberry_django.filter_field
    def membership(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}membership__id": value})

    @strawberry_django.filter_field
    def status(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}status": value})


@strawberry_django.order_type(fakts_models.Client)
class ManagementClientOrdering:
    id: strawberry.auto
    name: strawberry.auto
    created_at: strawberry.auto
    last_reported_at: strawberry.auto


@strawberry_django.filter_type(fakts_models.Client)
class ManagementClientFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})

    @strawberry_django.filter_field
    def functional(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}functional": value})

    @strawberry_django.filter_field
    def latest_report_resolved(self, value: bool, prefix: str) -> Q:
        """Whether the client's most recent report has been acknowledged.

        Combine with `functional: false` to list the clients that are broken AND
        not yet triaged — the dashboard's action list.
        """
        return Q(**{f"{prefix}latest_report_resolved": value})

    @strawberry_django.filter_field
    def role(self, value: fakts_enums.ClientRole, prefix: str) -> Q:
        return Q(**{f"{prefix}role": value.value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})

    @strawberry_django.filter_field
    def hub(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}hub__id": value})


@strawberry_django.order_type(fakts_models.Report)
class ManagementReportOrdering:
    id: strawberry.auto
    created_at: strawberry.auto


@strawberry_django.filter_type(fakts_models.Report)
class ManagementReportFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def client(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}client__id": value})


@strawberry_django.order_type(fakts_models.InstanceAlias)
class ManagementInstanceAliasOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.filter_type(fakts_models.InstanceAlias)
class ManagementInstanceAliasFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__contains": value})


@strawberry_django.order_type(fakts_models.ServiceInstanceMapping)
class ManagementServiceInstanceMappingOrdering:
    id: strawberry.auto


@strawberry_django.filter_type(fakts_models.ServiceInstanceMapping)
class ServiceInstanceMappingFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}key__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}instance__organization__id": value})


@strawberry_django.order_type(fakts_models.ServiceInstance)
class ManagementServiceInstanceOrdering:
    id: strawberry.auto


@strawberry_django.filter_type(fakts_models.ServiceInstance)
class ManagementServiceInstanceFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}instance_id__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})

    @strawberry_django.filter_field
    def hub(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}hub__id": value})


@strawberry_django.order_type(fakts_models.RedeemToken)
class ManagementRedeemTokenOrdering:
    id: strawberry.auto
    created_at: strawberry.auto


@strawberry_django.filter_type(fakts_models.RedeemToken)
class ManagementRedeemTokenFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        # Never search on the token itself: a substring match on a bearer
        # credential is a character-by-character oracle for it.
        return Q(**{f"{prefix}hub__name__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}hub__organization__id": value})

    @strawberry_django.filter_field
    def hub(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}hub__id": value})


@strawberry_django.order_type(karakter_models.Role)
class ManagementRoleOrdering:
    id: strawberry.auto


@strawberry_django.filter_type(karakter_models.Role)
class ManagementRoleFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}identifier__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})

    @strawberry_django.filter_field
    def creating_instance(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}creating_instance__id": value})


@strawberry_django.order_type(karakter_models.Scope)
class ManagementScopeOrdering:
    id: strawberry.auto


@strawberry_django.filter_type(karakter_models.Scope)
class ManagementScopeFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}identifier__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})

    @strawberry_django.filter_field
    def creating_instance(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}creating_instance__id": value})


@strawberry_django.order_type(fakts_models.IonscaleAuthKey)
class ManagementIonscaleAuthKeyOrdering:
    id: strawberry.auto
    created_at: strawberry.auto


@strawberry_django.filter_type(fakts_models.IonscaleAuthKey)
class ManagementIonscaleAuthKeyFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        # Never search on the key itself (a live mesh credential).
        return Q(**{f"{prefix}layer__name__icontains": value})

    @strawberry_django.filter_field
    def layer(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}layer__id": value})

    @strawberry_django.filter_field
    def ephemeral(self, value: bool, prefix: str) -> Q:
        return Q(**{f"{prefix}ephemeral": value})


@strawberry_django.order_type(karakter_models.Organization)
class ManagementOrganizationOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(karakter_models.User)
class ManagementUserOrdering:
    id: strawberry.auto


@strawberry_django.order_type(fakts_models.Service)
class ManagementServiceOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.ServiceRelease)
class ManagementServiceReleaseOrdering:
    id: strawberry.auto


@strawberry_django.order_type(fakts_models.App)
class ManagementAppOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.Release)
class ManagementReleaseOrdering:
    id: strawberry.auto
    name: strawberry.auto


@strawberry_django.order_type(fakts_models.UsedAlias)
class ManagementUsedAliasOrdering:
    id: strawberry.auto


# --- Minimal per-type filters ------------------------------------------------
#
# Several management types used to borrow a filter class declared for a
# *different* model (``karakter_filters.OrganizationFilter`` on invites, device
# codes, com channels ...; ``ProfileFilter`` on system messages). Their ``search``
# then filtered on a column the model does not have and every search request
# 500'd with a FieldError. Each type now gets its own filter over real columns.


@strawberry_django.filter_type(karakter_models.Invite)
class ManagementInviteFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}created_for__name__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}created_for__id": value})

    @strawberry_django.filter_field
    def status(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}status": value})


@strawberry_django.filter_type(fakts_models.DeviceCode)
class ManagementDeviceCodeFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}client__name__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.filter_type(fakts_models.DeviceCode)
class ManagementHubDeviceCodeFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}client__name__icontains": value})

    @strawberry_django.filter_field
    def organization(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}organization__id": value})


@strawberry_django.filter_type(fakts_models.MeshDeviceCode)
class ManagementMeshDeviceCodeFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}requested_machine_name__icontains": value}) | Q(
            **{f"{prefix}machine_name__icontains": value}
        )


@strawberry_django.filter_type(karakter_models.ComChannel)
class ManagementComChannelFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.filter_type(fakts_models.Client)
class ManagementOAuth2ClientFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}name__icontains": value})


@strawberry_django.filter_type(karakter_models.SystemMessage)
class ManagementSystemMessageFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}title__icontains": value})


@strawberry_django.filter_type(fakts_models.ServiceRelease)
class ManagementServiceReleaseFilter:
    @strawberry_django.filter_field
    def ids(self, value: list[strawberry.ID], prefix: str) -> Q:
        return Q(**{f"{prefix}id__in": value})

    @strawberry_django.filter_field
    def search(self, value: str, prefix: str) -> Q:
        return Q(**{f"{prefix}service__name__icontains": value}) | Q(
            **{f"{prefix}version__icontains": value}
        )

    @strawberry_django.filter_field
    def service(self, value: strawberry.ID, prefix: str) -> Q:
        return Q(**{f"{prefix}service__id": value})

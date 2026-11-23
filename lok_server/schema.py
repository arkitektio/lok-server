import kante
import strawberry
import strawberry_django
from arkitekt_service.graphql import TelemetryExtension
from authentikate.catch_alls import declare_catch_alls
from authentikate.strawberry import AuthExtension, AuthSubscribeExtension
from graphql import GraphQLError
from kante.types import Info
from strawberry.schema.config import StrawberryConfig
from strawberry_django.optimizer import DjangoOptimizerExtension

from authapp.extension import AuthAppExtension
from fakts import models as fakts_models
from fakts import types as fakts_types
from fakts.graphql import mutations as fakts_mutations
from fakts.graphql import queries as fakts_queries
from fakts.scalars import scalar_map as fakts_scalar_map
from karakter import models as karakter_models
from karakter import types as karakter_types
from karakter.authz import (
    DENIED,
    get_or_denied,
    get_organization,
    get_scoped_or_denied,
)
from karakter.datalayer import DatalayerExtension
from karakter.graphql import mutations as karakter_mutations
from karakter.graphql import queries as karakter_queries
from karakter.graphql import subscriptions as karakter_subscriptions
from karakter.hashers import hash_device_id
from karakter.scalars import scalar_map as karakter_scalar_map
from karakter.scopes import (
    ALL,
    MANDATES,
    MESSAGES,
    ORGANIZATIONS,
    PROFILE,
    READ,
    SERVICES,
)
from lok_server.logs import QuietErrorsSchema


def _needs(scope: str) -> list[AuthExtension]:
    """What a root field asks of the app: one of lok's own scopes (karakter.scopes)."""
    return [AuthExtension(scopes=[scope])]


@strawberry.type
class Query:
    organizations: list[karakter_types.Organization] = kante.django_field(
        extensions=_needs(READ)
    )

    mycontext = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_queries.mycontext
    )

    devices: list[fakts_types.Device] = strawberry_django.field(extensions=_needs(READ))
    apps: list[fakts_types.App] = strawberry_django.field(extensions=_needs(READ))
    releases: list[fakts_types.Release] = strawberry_django.field(
        extensions=_needs(READ)
    )
    clients: list[fakts_types.Client] = strawberry_django.field(extensions=_needs(READ))
    bug_reports: list[fakts_types.BugReport] = strawberry_django.field(
        extensions=_needs(READ),
        description="Bugs people reported from inside a client. Every report of the organization for its admins, your own otherwise. Filter by client, release or app.",
    )
    bug_report = strawberry_django.field(
        extensions=_needs(READ), resolver=fakts_queries.bug_report
    )
    users: list[karakter_types.User] = strawberry_django.field(extensions=_needs(READ))
    roles: list[karakter_types.Role] = strawberry_django.field(extensions=_needs(READ))
    groups: list[karakter_types.Group] = strawberry_django.field(
        extensions=_needs(READ)
    )
    services: list[fakts_types.Service] = strawberry_django.field(
        extensions=_needs(READ)
    )
    hubs: list[fakts_types.Hub] = strawberry_django.field(extensions=_needs(READ))
    device_groups: list[fakts_types.DeviceGroup] = strawberry_django.field(
        extensions=_needs(READ)
    )
    service_instances: list[fakts_types.ServiceInstance] = strawberry_django.field(
        extensions=_needs(READ)
    )
    service_releases: list[fakts_types.ServiceRelease] = strawberry_django.field(
        extensions=_needs(READ)
    )
    invites: list[karakter_types.Invite] = strawberry_django.field(
        extensions=_needs(READ)
    )
    collaborations: list[karakter_types.Collaboration] = strawberry_django.field(
        extensions=_needs(READ),
        description="The active organization's collaborations (a guest: theirs).",
    )
    membership_requests: list[karakter_types.MembershipRequest] = (
        strawberry_django.field(
            extensions=_needs(READ),
            description="Requests of non-members to join the active organization. Only its owner and admins see any.",
        )
    )

    user = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_queries.user
    )
    me = strawberry_django.field(extensions=_needs(READ), resolver=karakter_queries.me)
    group = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_queries.group
    )
    mygroups = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_queries.mygroups
    )

    app = strawberry_django.field(extensions=_needs(READ), resolver=fakts_queries.app)
    release = strawberry_django.field(
        extensions=_needs(READ), resolver=fakts_queries.release
    )
    client = strawberry_django.field(
        extensions=_needs(READ), resolver=fakts_queries.client
    )
    clients_by_client_id = strawberry_django.field(
        extensions=_needs(READ),
        resolver=fakts_queries.clients_by_client_id,
        description="The clients of the organization other services know by these ids (a token's `client_id` claim), in one query.",
    )
    my_managed_clients = strawberry_django.field(
        extensions=_needs(READ), resolver=fakts_queries.my_managed_clients
    )
    layers: list[fakts_types.Layer] = strawberry_django.field(extensions=_needs(READ))

    scopes = strawberry_django.field(
        extensions=_needs(READ), resolver=fakts_queries.scopes
    )

    mandates: list[fakts_types.Mandate] = strawberry_django.field(
        extensions=_needs(READ),
        description="Mandates you granted, your app issued, or (as an agent app) you may issue under or did. Org admins see all.",
    )
    mandate = strawberry_django.field(
        extensions=_needs(READ), resolver=fakts_mutations.mandate
    )

    my_active_messages = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_queries.my_active_messages
    )
    message = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_queries.message
    )

    # Stats
    user_stats: karakter_types.UserStats = strawberry_django.field(
        extensions=_needs(READ), resolver=karakter_types.UserStatsResolver
    )
    membership_stats: karakter_types.MembershipStats = strawberry_django.field(
        extensions=_needs(READ),
        resolver=karakter_types.MembershipStatsResolver,
        description="Counts over the memberships of the active organization, e.g. per role with `filters: { role }`.",
    )

    @kante.django_field(extensions=_needs(READ))
    def hallo(self, info: Info) -> str:
        return "hallo"

    @kante.django_field(extensions=_needs(READ))
    def collaboration(
        self,
        info: Info,
        id: strawberry.ID | None = None,
        key: strawberry.ID | None = None,
    ) -> karakter_types.Collaboration:
        """One collaboration of the active organization, by its id or its key."""
        if (id is None) == (key is None):
            raise GraphQLError("Give either the id or the key of a collaboration.")
        visible = karakter_types.Collaboration.get_queryset(
            karakter_models.Collaboration.objects.all(), info
        )
        if id is not None:
            return get_or_denied(visible, id=id)  # type: ignore[return-value]
        return get_or_denied(visible, key=str(key))  # type: ignore[return-value]

    # NOTE on the single-object roots below: strawberry-django only applies a
    # type's `get_queryset` to resolvers that return a *QuerySet*. These return
    # one row by pk, so each must scope itself to the caller's organization (or
    # user) via `get_scoped_or_denied` / `get_or_denied` — which also turn a
    # malformed id into the same uniform denial instead of a 500.

    @kante.django_field(extensions=_needs(READ), name="service")
    def detail_service(self, info: Info, id: strawberry.ID) -> fakts_types.Service:
        return get_scoped_or_denied(fakts_models.Service.objects, info, id=id)

    @kante.django_field(extensions=_needs(READ))
    def device(self, info: Info, id: strawberry.ID) -> fakts_types.Device:
        return get_scoped_or_denied(fakts_models.Device.objects, info, id=id)

    @kante.django_field(
        extensions=_needs(READ),
        description="Look a device up by its device id: the id the other services know it by (`Device.deviceId`, a token's `client_device` claim, rekuest's `Agent.device.deviceId`). The raw id a client reports about itself is accepted too.",
    )
    def device_by_device_id(self, info: Info, id: strawberry.ID) -> fakts_types.Device:
        organization = get_organization(info)
        # Every write path stores `hash_device_id(raw, organization)`, and that
        # stored hash is the id that leaves lok (the `client_device` claim, so
        # rekuest's device ids). Hashing it again could never match, so the id is
        # looked up as it is first, and as a raw id second.
        try:
            hashed = hash_device_id(str(id), organization)
        except Exception as exc:
            raise GraphQLError(DENIED) from exc
        return get_scoped_or_denied(
            fakts_models.Device.objects.filter(node_id__in=[str(id), hashed]), info
        )

    @kante.django_field(extensions=_needs(READ))
    def device_group(self, info: Info, id: strawberry.ID) -> fakts_types.DeviceGroup:
        return get_scoped_or_denied(fakts_models.DeviceGroup.objects, info, id=id)

    @kante.django_field(extensions=_needs(READ))
    def service_release(
        self, info: Info, id: strawberry.ID
    ) -> fakts_types.ServiceRelease:
        return get_scoped_or_denied(
            fakts_models.ServiceRelease.objects,
            info,
            field="service__organization",
            id=id,
        )

    @kante.django_field(extensions=_needs(READ))
    def role(self, info: Info, id: strawberry.ID) -> karakter_types.Role:
        return get_scoped_or_denied(karakter_models.Role.objects, info, id=id)

    @kante.django_field(extensions=_needs(READ))
    def organization(
        self, info: Info, id: strawberry.ID
    ) -> karakter_types.Organization:
        # Only the caller's active organization is addressable on this schema.
        organization = get_organization(info)
        if str(organization.id) != str(id):
            raise GraphQLError(DENIED)
        return organization

    @kante.django_field(extensions=_needs(READ))
    def layer(self, info: Info, id: strawberry.ID) -> fakts_types.Layer:
        return get_scoped_or_denied(fakts_models.Layer.objects, info, id=id)

    @kante.django_field(extensions=_needs(READ))
    def service_instance(
        self, info: Info, id: strawberry.ID
    ) -> fakts_types.ServiceInstance:
        return get_scoped_or_denied(fakts_models.ServiceInstance.objects, info, id=id)

    @kante.django_field(
        extensions=_needs(READ),
        description="A hub of the caller's organization, including its last reported health. The caller's own hub is also reachable as `mycontext { hub }`.",
    )
    def hub(self, info: Info, id: strawberry.ID) -> fakts_types.Hub:
        return get_scoped_or_denied(fakts_models.Hub.objects, info, id=id)


@strawberry.type
class Mutation:
    # NOTE: `create_user` was removed. It was unauthenticated user creation, and it
    # could never have worked — the resolver read `input.user` from an input that only
    # declares `name`, and assigned it to a `name` field the User model does not have,
    # so every call raised before touching the database. Nothing can depend on it.

    add_user_to_organization = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.add_user_to_organization,
    )
    create_organization = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.create_organization,
    )

    register_com_channel = strawberry_django.mutation(
        extensions=_needs(MESSAGES),
        resolver=karakter_mutations.register_com_channel,
    )
    notify_user = strawberry_django.mutation(
        extensions=_needs(MESSAGES),
        resolver=karakter_mutations.notify_user,
    )

    create_mandate = strawberry_django.mutation(
        extensions=_needs(MANDATES), resolver=fakts_mutations.create_mandate
    )
    issue_mandate_key = strawberry_django.mutation(
        extensions=_needs(MANDATES), resolver=fakts_mutations.issue_mandate_key
    )
    issue_child_mandate_key = strawberry_django.mutation(
        extensions=_needs(MANDATES), resolver=fakts_mutations.issue_child_mandate_key
    )
    revoke_mandate = strawberry_django.mutation(
        extensions=_needs(MANDATES), resolver=fakts_mutations.revoke_mandate
    )

    render = strawberry_django.mutation(
        extensions=_needs(SERVICES),
        resolver=fakts_mutations.render_hub,
    )
    acknowledge_message = strawberry_django.mutation(
        extensions=_needs(MESSAGES), resolver=karakter_mutations.acknowledge_message
    )

    # A person reporting a bug from inside a client, and its triage by the organization's admins.
    create_bug_report = strawberry_django.mutation(
        extensions=_needs(MESSAGES), resolver=fakts_mutations.create_bug_report
    )
    delete_bug_report = strawberry_django.mutation(
        extensions=_needs(MESSAGES), resolver=fakts_mutations.delete_bug_report
    )
    resolve_bug_report = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS), resolver=fakts_mutations.resolve_bug_report
    )
    unresolve_bug_report = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS), resolver=fakts_mutations.unresolve_bug_report
    )
    forward_bug_report = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS), resolver=fakts_mutations.forward_bug_report
    )

    create_service_instance = strawberry_django.mutation(
        extensions=_needs(SERVICES),
        resolver=fakts_mutations.create_service_instance,
    )

    update_service_instance = strawberry_django.mutation(
        extensions=_needs(SERVICES),
        resolver=fakts_mutations.update_service_instance,
    )

    request_media_upload = strawberry_django.mutation(
        extensions=_needs(PROFILE),
        resolver=karakter_mutations.request_media_upload,
    )

    update_profile = strawberry_django.mutation(
        extensions=_needs(PROFILE),
        resolver=karakter_mutations.update_profile,
    )
    create_profile = strawberry_django.mutation(
        extensions=_needs(PROFILE),
        resolver=karakter_mutations.create_profile,
    )

    update_group_profile = strawberry_django.mutation(
        extensions=_needs(PROFILE),
        resolver=karakter_mutations.update_group_profile,
    )
    create_group_profile = strawberry_django.mutation(
        extensions=_needs(PROFILE),
        resolver=karakter_mutations.create_group_profile,
    )

    create_invite = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.create_invite,
    )

    create_collaboration = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.create_collaboration,
    )
    update_collaboration = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.update_collaboration,
    )
    delete_collaboration = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.delete_collaboration,
        description="Delete a collaboration, and every grant of it.",
    )

    accept_invite = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.accept_invite,
    )

    decline_invite = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.decline_invite,
    )

    cancel_invite = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.cancel_invite,
    )

    update_organization = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.update_organization,
    )

    update_membership_colors = strawberry_django.mutation(
        extensions=_needs(PROFILE),
        resolver=karakter_mutations.update_membership_colors,
    )

    request_membership = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.request_membership,
    )
    approve_membership_request = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.approve_membership_request,
    )
    decline_membership_request = strawberry_django.mutation(
        extensions=_needs(ORGANIZATIONS),
        resolver=karakter_mutations.decline_membership_request,
    )

    update_device = strawberry_django.mutation(
        extensions=_needs(PROFILE), resolver=fakts_mutations.update_device
    )


@strawberry.type
class Subscription:
    my_messages = strawberry.subscription(
        extensions=[AuthSubscribeExtension(scopes=[READ])],
        resolver=karakter_subscriptions.my_messages,
    )
    membership_requests = strawberry.subscription(
        extensions=[AuthSubscribeExtension(scopes=[READ])],
        resolver=karakter_subscriptions.membership_requests,
    )
    hub_health = strawberry.subscription(
        extensions=[AuthSubscribeExtension(scopes=[READ])],
        resolver=karakter_subscriptions.hub_health,
    )


class Schema(QuietErrorsSchema, kante.Schema):
    """kante.Schema, logging expected resolver errors as one line and bugs with a traceback (see logs.py)."""


schema = Schema(
    query=Query,
    subscription=Subscription,
    mutation=Mutation,
    extensions=[
        TelemetryExtension,
        DjangoOptimizerExtension,
        AuthAppExtension,
        DatalayerExtension,
    ],
    config=StrawberryConfig(scalar_map={**fakts_scalar_map, **karakter_scalar_map}),
)

# `lok_all` stands for every scope above. lok's roles are its own business (its resolvers check
# owners and admins), so no role stands for all of them.
declare_catch_alls(scope=ALL, role=None)

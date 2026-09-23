
import strawberry
from lok_server.logs import QuietErrorsSchema
import strawberry_django
from kante.types import Info
from fakts import types as fakts_types
from fakts.graphql import mutations as fakts_mutations
from fakts.graphql import queries as fakts_queries
from fakts import models as fakts_models
from karakter import types as karakter_types
from karakter import models as karakter_models
from karakter.hashers import hash_device_id
from karakter.authz import DENIED, get_organization, get_scoped_or_denied, get_user
from graphql import GraphQLError
from karakter.graphql import mutations as karakter_mutations
from karakter.graphql import queries as karakter_queries
from karakter.graphql import subscriptions as karakter_subscriptions
from karakter.datalayer import DatalayerExtension
from strawberry_django.optimizer import DjangoOptimizerExtension
from authapp.extension import AuthAppExtension
from strawberry.schema.config import StrawberryConfig
from fakts.scalars import scalar_map as fakts_scalar_map
from karakter.scalars import scalar_map as karakter_scalar_map
import kante


@strawberry.type
class Query:
    organizations: list[karakter_types.Organization] = kante.django_field()

    mycontext = strawberry_django.field(resolver=karakter_queries.mycontext)

    devices: list[fakts_types.Device] = strawberry_django.field()
    apps: list[fakts_types.App] = strawberry_django.field()
    releases: list[fakts_types.Release] = strawberry_django.field()
    clients: list[fakts_types.Client] = strawberry_django.field()
    users: list[karakter_types.User] = strawberry_django.field()
    roles: list[karakter_types.Role] = strawberry_django.field()
    groups: list[karakter_types.Group] = strawberry_django.field()
    services: list[fakts_types.Service] = strawberry_django.field()
    hubs: list[fakts_types.Hub] = strawberry_django.field()
    device_groups: list[fakts_types.DeviceGroup] = strawberry_django.field()
    service_instances: list[fakts_types.ServiceInstance] = strawberry_django.field()
    service_releases: list[fakts_types.ServiceRelease] = strawberry_django.field()
    invites: list[karakter_types.Invite] = strawberry_django.field()

    user = strawberry_django.field(resolver=karakter_queries.user)
    me = strawberry_django.field(resolver=karakter_queries.me)
    group = strawberry_django.field(resolver=karakter_queries.group)
    mygroups = strawberry_django.field(resolver=karakter_queries.mygroups)

    app = strawberry_django.field(resolver=fakts_queries.app)
    release = strawberry_django.field(resolver=fakts_queries.release)
    client = strawberry_django.field(resolver=fakts_queries.client)
    my_managed_clients = strawberry_django.field(resolver=fakts_queries.my_managed_clients)
    layers: list[fakts_types.Layer] = strawberry_django.field()

    scopes = strawberry_django.field(resolver=fakts_queries.scopes)

    redeem_tokens: list[fakts_types.RedeemToken] = strawberry_django.field()

    my_active_messages = strawberry_django.field(resolver=karakter_queries.my_active_messages)
    message = strawberry_django.field(resolver=karakter_queries.message)

    # Stats
    user_stats: karakter_types.UserStats = strawberry_django.field(resolver=karakter_types.UserStatsResolver)

    @kante.django_field()
    def hallo(self, info: Info) -> str:
        return "hallo"

    # NOTE on the single-object roots below: strawberry-django only applies a
    # type's `get_queryset` to resolvers that return a *QuerySet*. These return
    # one row by pk, so each must scope itself to the caller's organization (or
    # user) via `get_scoped_or_denied` / `get_or_denied` — which also turn a
    # malformed id into the same uniform denial instead of a 500.

    @kante.django_field(name="service")
    def detail_service(self, info: Info, id: strawberry.ID) -> fakts_types.Service:
        return get_scoped_or_denied(fakts_models.Service.objects, info, id=id)

    @kante.django_field()
    def device(self, info: Info, id: strawberry.ID) -> fakts_types.Device:
        return get_scoped_or_denied(fakts_models.Device.objects, info, id=id)

    @kante.django_field(description="Look a device up by its raw device id, as reported by the client. Device ids are stored as a per-organization hash, so the raw id is hashed with the caller's organization before lookup.")
    def device_by_device_id(self, info: Info, id: strawberry.ID) -> fakts_types.Device:
        organization = get_organization(info)
        # Every write path stores `hash_device_id(node_id, organization)`, never
        # the raw id — so a raw-id lookup could never match a row.
        try:
            hashed = hash_device_id(str(id), organization)
        except Exception:
            raise GraphQLError(DENIED)
        return get_scoped_or_denied(fakts_models.Device.objects, info, node_id=hashed)

    @kante.django_field()
    def device_group(self, info: Info, id: strawberry.ID) -> fakts_types.DeviceGroup:
        return get_scoped_or_denied(fakts_models.DeviceGroup.objects, info, id=id)

    @kante.django_field()
    def service_release(self, info: Info, id: strawberry.ID) -> fakts_types.ServiceRelease:
        return get_scoped_or_denied(fakts_models.ServiceRelease.objects, info, field="service__organization", id=id)

    @kante.django_field()
    def role(self, info: Info, id: strawberry.ID) -> karakter_types.Role:
        return get_scoped_or_denied(karakter_models.Role.objects, info, id=id)

    @kante.django_field()
    def organization(self, info: Info, id: strawberry.ID) -> karakter_types.Organization:
        # Only the caller's active organization is addressable on this schema.
        organization = get_organization(info)
        if str(organization.id) != str(id):
            raise GraphQLError(DENIED)
        return organization

    @kante.django_field()
    def redeem_token(self, info: Info, id: strawberry.ID) -> fakts_types.RedeemToken:
        # A redeem token is a bearer credential: it is visible only to the user
        # it was issued to, and only within their active organization.
        return get_scoped_or_denied(
            fakts_models.RedeemToken.objects,
            info,
            field="hub__organization",
            id=id,
            user=get_user(info),
        )

    @kante.django_field()
    def my_redeem_tokens(self, info: Info) -> list[fakts_types.RedeemToken]:
        return fakts_models.RedeemToken.objects.filter(user=get_user(info), hub__organization=get_organization(info))

    @kante.django_field()
    def layer(self, info: Info, id: strawberry.ID) -> fakts_types.Layer:
        return get_scoped_or_denied(fakts_models.Layer.objects, info, id=id)

    @kante.django_field()
    def service_instance(self, info: Info, id: strawberry.ID) -> fakts_types.ServiceInstance:
        return get_scoped_or_denied(fakts_models.ServiceInstance.objects, info, id=id)

    @kante.django_field(description="A hub of the caller's organization, including its last reported health. The caller's own hub is also reachable as `mycontext { hub }`.")
    def hub(self, info: Info, id: strawberry.ID) -> fakts_types.Hub:
        return get_scoped_or_denied(fakts_models.Hub.objects, info, id=id)


@strawberry.type
class Mutation:
    # NOTE: `create_user` was removed. It was unauthenticated user creation, and it
    # could never have worked — the resolver read `input.user` from an input that only
    # declares `name`, and assigned it to a `name` field the User model does not have,
    # so every call raised before touching the database. Nothing can depend on it.

    add_user_to_organization = strawberry_django.mutation(
        resolver=karakter_mutations.add_user_to_organization,
    )
    create_organization = strawberry_django.mutation(
        resolver=karakter_mutations.create_organization,
    )

    register_com_channel = strawberry_django.mutation(
        resolver=karakter_mutations.register_com_channel,
    )
    notify_user = strawberry_django.mutation(
        resolver=karakter_mutations.notify_user,
    )

    create_redeem_token = strawberry_django.mutation(
        resolver=fakts_mutations.create_redeem_token,
    )
    delete_redeem_token = strawberry_django.mutation(
        resolver=fakts_mutations.delete_redeem_token,
    )

    create_developmental_client = strawberry_django.mutation(
        resolver=fakts_mutations.create_developmental_client,
    )
    render = strawberry_django.mutation(
        resolver=fakts_mutations.render_hub,
    )
    acknowledge_message = strawberry_django.mutation(resolver=karakter_mutations.acknowledge_message)

    create_service_instance = strawberry_django.mutation(
        resolver=fakts_mutations.create_service_instance,
    )

    update_service_instance = strawberry_django.mutation(
        resolver=fakts_mutations.update_service_instance,
    )

    request_media_upload = strawberry_django.mutation(
        resolver=karakter_mutations.request_media_upload,
    )

    update_profile = strawberry_django.mutation(
        resolver=karakter_mutations.update_profile,
    )
    create_profile = strawberry_django.mutation(
        resolver=karakter_mutations.create_profile,
    )

    update_group_profile = strawberry_django.mutation(
        resolver=karakter_mutations.update_group_profile,
    )
    create_group_profile = strawberry_django.mutation(
        resolver=karakter_mutations.create_group_profile,
    )

    create_invite = strawberry_django.mutation(
        resolver=karakter_mutations.create_invite,
    )

    accept_invite = strawberry_django.mutation(
        resolver=karakter_mutations.accept_invite,
    )

    decline_invite = strawberry_django.mutation(
        resolver=karakter_mutations.decline_invite,
    )

    cancel_invite = strawberry_django.mutation(
        resolver=karakter_mutations.cancel_invite,
    )

    update_organization = strawberry_django.mutation(
        resolver=karakter_mutations.update_organization,
    )

    update_membership_colors = strawberry_django.mutation(
        resolver=karakter_mutations.update_membership_colors,
    )

    update_device = strawberry_django.mutation(resolver=fakts_mutations.update_device)


@strawberry.type
class Subscription:
    communications = strawberry.subscription(resolver=karakter_subscriptions.communications)


class Schema(QuietErrorsSchema, kante.Schema):
    """kante.Schema, logging expected resolver errors as one line and bugs with a traceback (see logs.py)."""


schema = Schema(
    query=Query,
    subscription=Subscription,
    mutation=Mutation,
    extensions=[DjangoOptimizerExtension, AuthAppExtension, DatalayerExtension],
    config=StrawberryConfig(scalar_map={**fakts_scalar_map, **karakter_scalar_map}),
)

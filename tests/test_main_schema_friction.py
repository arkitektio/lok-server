"""Friction fixes on the main `/graphql` schema: resolvers and filters that
raised (FieldError, ValidationError, AttributeError, NotImplementedError) or
silently returned nothing for legitimate input.
"""

import pytest
from asgiref.sync import sync_to_async
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLScalarType,
)

from karakter.hashers import hash_device_id
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _principal():
    membership = factories.make_membership()
    client = factories.make_client(membership=membership)
    return build_auth_context(membership.user, membership.organization, client), membership, client


def _assert_denied(result):
    assert result.errors, f"expected a denial, got data: {result.data}"
    assert "not authorized" in result.errors[0].message, result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_device_by_device_id_hashes_the_raw_id():
    """Every write path stores `hash_device_id(node_id, org)`; the lookup must too."""
    context, membership, _client = await sync_to_async(_principal)()

    def _device():
        from fakts.models import Device

        return Device.objects.create(
            organization=membership.organization,
            node_id=hash_device_id("my-laptop", membership.organization),
            name="laptop",
        )

    device = await sync_to_async(_device)()

    result = await schema.execute(
        'query { deviceByDeviceId(id: "my-laptop") { id name } }', context_value=context
    )
    assert not result.errors, result.errors
    assert result.data["deviceByDeviceId"] == {"id": str(device.id), "name": "laptop"}

    result = await schema.execute(
        'query { deviceByDeviceId(id: "never-registered") { id } }', context_value=context
    )
    _assert_denied(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_kind_covers_hub_and_relying_party():
    context, membership, _client = await sync_to_async(_principal)()

    def _clients():
        hub = factories.make_client(membership=membership, kind="hub")
        rp = factories.make_client(membership=membership, kind="relying_party")
        mobile = factories.make_client(membership=membership, kind="mobile")
        return hub, rp, mobile

    hub, rp, mobile = await sync_to_async(_clients)()

    result = await schema.execute("query { clients { id kind } }", context_value=context)
    assert not result.errors, result.errors
    kinds = {row["id"]: row["kind"] for row in result.data["clients"]}
    assert kinds[str(hub.id)] == "HUB"
    assert kinds[str(rp.id)] == "RELYING_PARTY"
    assert kinds[str(mobile.id)] == "MOBILE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_release_requirements_serialise():
    context, membership, _client = await sync_to_async(_principal)()

    def _release():
        app = factories.make_app(organization=membership.organization)
        return factories.make_release(
            app=app,
            requirements=[{"key": "mikro", "service": "live.arkitekt.mikro", "optional": False, "description": None}],
        )

    release = await sync_to_async(_release)()

    result = await schema.execute(
        "query ($id: ID!) { release(id: $id) { id requirements } }",
        context_value=context,
        variable_values={"id": str(release.id)},
    )
    assert not result.errors, result.errors
    assert result.data["release"]["requirements"] == [
        {"key": "mikro", "service": "live.arkitekt.mikro", "optional": False, "description": None}
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        'query { device(id: "not-a-number") { id } }',
        'query { serviceInstance(id: "abc") { id } }',
        'query { user(id: "abc") { id } }',
        'query { stash(id: "abc") { id } }',
        'query { comment(id: "abc") { id } }',
        'query { client(id: "abc") { id } }',
        'query { role(id: "abc") { id } }',
        'mutation { acceptInvite(input: {token: "not-a-uuid"}) { id } }',
        'mutation { declineInvite(input: {token: "not-a-uuid"}) { id } }',
        'mutation { cancelInvite(input: {id: "abc"}) { id } }',
    ],
)
async def test_malformed_ids_are_a_clean_denial(query):
    context, _membership, _client = await sync_to_async(_principal)()

    result = await schema.execute(query, context_value=context)

    _assert_denied(result)
    message = result.errors[0].message
    assert "invalid literal" not in message and "ValueError" not in message and "UUID" not in message, message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_null_filters_variable_on_a_scoped_list_works():
    """`build_prescoped_queryset` used to crash on an explicit `filters: null`."""
    context, membership, _client = await sync_to_async(_principal)()

    def _device():
        from fakts.models import Device

        return Device.objects.create(organization=membership.organization, node_id="n", name="d")

    device = await sync_to_async(_device)()

    result = await schema.execute(
        "query ($filters: DeviceFilter) { devices(filters: $filters) { id } }",
        context_value=context,
        variable_values={"filters": None},
    )
    assert not result.errors, result.errors
    assert {row["id"] for row in result.data["devices"]} == {str(device.id)}


# ---------------------------------------------------------------------------
# Every filter on every root list field must at least *execute*: several
# filters referenced columns that do not exist and raised FieldError.
# ---------------------------------------------------------------------------

_SKIP_FILTER_FIELDS = {"AND", "OR", "NOT"}


def _unwrap(gql_type):
    while isinstance(gql_type, (GraphQLNonNull, GraphQLList)):
        gql_type = gql_type.of_type
    return gql_type


def _sample(gql_type):
    """A plausible value for a GraphQL input type."""
    if isinstance(gql_type, GraphQLNonNull):
        return _sample(gql_type.of_type)
    if isinstance(gql_type, GraphQLList):
        return [_sample(gql_type.of_type)]
    if isinstance(gql_type, GraphQLEnumType):
        return next(iter(gql_type.values))
    if isinstance(gql_type, GraphQLScalarType):
        return {
            "String": "x",
            "ID": "1",
            "Int": 1,
            "Float": 1.0,
            "Boolean": True,
        }.get(gql_type.name, "x")
    if isinstance(gql_type, GraphQLInputObjectType):
        fields = gql_type.fields
        # Lookups (`FilterLookup`) expose many operators; `contains` is the
        # representative one — the others differ only in operator.
        if "contains" in fields:
            return {"contains": "x"}
        name, field = next(iter(fields.items()))
        return {name: _sample(field.type)}
    raise AssertionError(f"unhandled input type {gql_type}")


def _filterable_root_fields():
    query_type = schema._schema.query_type
    cases = []
    for field_name, field in query_type.fields.items():
        arg = field.args.get("filters")
        if arg is None:
            continue
        filter_type = _unwrap(arg.type)
        if not isinstance(filter_type, GraphQLInputObjectType):
            continue
        for filter_field_name, filter_field in filter_type.fields.items():
            if filter_field_name in _SKIP_FILTER_FIELDS:
                continue
            cases.append(
                pytest.param(
                    field_name,
                    filter_type.name,
                    {filter_field_name: _sample(filter_field.type)},
                    id=f"{field_name}.{filter_field_name}",
                )
            )
    return cases


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("field_name,filter_type_name,filters", _filterable_root_fields())
async def test_every_root_filter_executes_without_field_error(field_name, filter_type_name, filters):
    context, _membership, _client = await sync_to_async(_principal)()

    query = f"query ($filters: {filter_type_name}) {{ {field_name}(filters: $filters) {{ __typename }} }}"
    result = await schema.execute(query, context_value=context, variable_values={"filters": filters})

    assert not result.errors, f"{field_name} with {filters}: {result.errors}"
    assert result.data[field_name] is not None

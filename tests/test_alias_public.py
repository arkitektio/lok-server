"""Tests for the ``public`` attribute on instance-alias registration.

``public`` declares that an alias is publicly reachable, so the coordination
server can also check its health directly (in addition to client-side reports),
which lets the kontrol interface check the health from the client side.

These tests also pin down backward compatibility: ``public`` defaults to
``False`` everywhere it appears and old payloads / callers that never mention it
keep working unchanged.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from fakts import base_models, models
from fakts.services import hubs
from tests import factories
from tests.conftest import build_auth_context


# --------------------------------------------------------------------------- #
# Backward compatibility: the field defaults to False everywhere.
# --------------------------------------------------------------------------- #

def test_staging_alias_defaults_public_false():
    """A StagingAlias payload that omits ``public`` still parses (default False)."""
    alias = base_models.StagingAlias(id="a1", host="example.com")
    assert alias.public is False


def test_staging_alias_accepts_public_true():
    alias = base_models.StagingAlias(id="a1", host="example.com", public=True)
    assert alias.public is True


def test_alias_defaults_public_false():
    """The resolved Alias sent to the client defaults ``public`` to False."""
    alias = base_models.Alias(id="a1", host="example.com", challenge="ht")
    assert alias.public is False


@pytest.mark.django_db
def test_instance_alias_model_defaults_public_false():
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(instance=instance, host="example.com")
    alias.refresh_from_db()
    assert alias.public is False


# --------------------------------------------------------------------------- #
# to_url() must propagate ``public`` onto the resolved Alias.
# --------------------------------------------------------------------------- #

def _linking_context() -> base_models.LinkingContext:
    return base_models.LinkingContext(
        request=base_models.LinkingRequest(host="linked.example.com", port="8080", is_secure=True),
        manifest=base_models.Manifest(identifier="com.example.app", version="1.0.0"),
        client=base_models.LinkingClient(
            authorization_grant_type="authorization_code",
            client_type="confidential",
            client_id="cid",
            client_secret="secret",
            name="client",
        ),
    )


@pytest.mark.django_db
def test_to_url_propagates_public_for_absolute_alias():
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(
        instance=instance, host="example.com", kind="absolute", public=True
    )
    url = alias.to_url(_linking_context())
    assert url.public is True


@pytest.mark.django_db
def test_to_url_propagates_public_for_relative_alias():
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(
        instance=instance, host="example.com", kind="relative", public=True
    )
    url = alias.to_url(_linking_context())
    assert url.public is True


@pytest.mark.django_db
def test_to_url_defaults_public_false():
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(instance=instance, host="example.com", kind="absolute")
    url = alias.to_url(_linking_context())
    assert url.public is False


# --------------------------------------------------------------------------- #
# Hub service persists ``public`` from the staging alias.
# --------------------------------------------------------------------------- #

def _hub_manifest(*, public: bool) -> base_models.HubManifest:
    return base_models.HubManifest(
        identifier="com.example.comp",
        instances=[
            base_models.InstanceRequest(
                identifier="inst-1",
                manifest=base_models.ServiceManifest(identifier="com.example.svc", version="1.0.0"),
                aliases=[
                    base_models.StagingAlias(id="alias-1", host="example.com", challenge="ht", public=public),
                ],
            )
        ],
    )


@pytest.mark.django_db
def test_create_hub_persists_public_true():
    organization = factories.make_organization()
    hubs.create_hub_from_manifest(_hub_manifest(public=True), organization)

    alias = models.InstanceAlias.objects.get(name="alias-1")
    assert alias.public is True


@pytest.mark.django_db
def test_create_hub_defaults_public_false():
    organization = factories.make_organization()
    hubs.create_hub_from_manifest(_hub_manifest(public=False), organization)

    alias = models.InstanceAlias.objects.get(name="alias-1")
    assert alias.public is False


# --------------------------------------------------------------------------- #
# The field is exposed on the GraphQL types.
# --------------------------------------------------------------------------- #

def test_public_field_exposed_on_management_graphql_types():
    # ManagementInstanceAlias and the pydantic StagingAlias both carry `public`.
    sdl = str(management_schema)
    assert sdl.count("public: Boolean!") >= 2, sdl


def test_public_field_exposed_on_fakts_alias_type():
    from lok_server.schema import schema as lok_schema

    assert "public: Boolean!" in str(lok_schema)


# --------------------------------------------------------------------------- #
# create/update/delete alias mutations resolve against the real model fields.
# (Regression: they previously passed non-existent `service_instance`/`created_by`
# kwargs and threw on every call.)
# --------------------------------------------------------------------------- #

CREATE_ALIAS = """
    mutation Create($input: CreateAliasInput!) {
        createAlias(input: $input) { id host port kind public }
    }
"""

UPDATE_ALIAS = """
    mutation Update($input: UpdateAliasInput!) {
        updateAlias(input: $input) { id host public }
    }
"""

DELETE_ALIAS = """
    mutation Delete($input: DeleteAliasInput!) {
        deleteAlias(input: $input)
    }
"""


def _mutation_setup():
    """Sync setup: an authenticated user/org plus a service instance to alias.

    The instance is created inside the caller's own organization: alias mutations
    are restricted to members of the instance's org (an alias is a routing entry,
    so editing one redirects that service's traffic). Previously this built the
    instance in an unrelated org, which only passed because there was no check.
    """
    # Alias mutations are owner/admin-only (an alias is a routing entry), so the
    # caller is the organization's owner.
    organization = factories.make_organization()
    membership = factories.make_membership(user=organization.owner, organization=organization)
    # auth resolves the OAuth2Client back to its backing fakts Client
    request_client = factories.make_client(membership=membership)
    hub = factories.make_hub(organization=membership.organization)
    instance = factories.make_service_instance(hub=hub)
    context = build_auth_context(membership.user, membership.organization, request_client)
    return context, instance


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_alias_mutation_creates_alias_with_public():
    context, instance = await sync_to_async(_mutation_setup)()

    result = await management_schema.execute(
        CREATE_ALIAS,
        context_value=context,
        variable_values={
            "input": {
                "instance": str(instance.id),
                "port": 8080,
                "host": "example.com",
                "kind": "absolute",
                "public": True,
            }
        },
    )

    assert not result.errors, result.errors
    data = result.data["createAlias"]
    assert data["host"] == "example.com"
    assert data["port"] == 8080
    assert data["public"] is True

    created = await sync_to_async(models.InstanceAlias.objects.get)(id=data["id"])
    assert created.instance_id == instance.id
    assert created.public is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_alias_mutation_defaults_public_false():
    context, instance = await sync_to_async(_mutation_setup)()

    result = await management_schema.execute(
        CREATE_ALIAS,
        context_value=context,
        variable_values={
            "input": {
                "instance": str(instance.id),
                "port": 8080,
                "host": "example.com",
                "kind": "absolute",
            }
        },
    )

    assert not result.errors, result.errors
    assert result.data["createAlias"]["public"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_alias_mutation_updates_public():
    context, instance = await sync_to_async(_mutation_setup)()
    alias = await sync_to_async(models.InstanceAlias.objects.create)(
        instance=instance, host="old.example.com", kind="absolute", public=False
    )

    result = await management_schema.execute(
        UPDATE_ALIAS,
        context_value=context,
        variable_values={
            "input": {
                "id": str(alias.id),
                "port": 9090,
                "host": "new.example.com",
                "kind": "absolute",
                "public": True,
            }
        },
    )

    assert not result.errors, result.errors
    data = result.data["updateAlias"]
    assert data["host"] == "new.example.com"
    assert data["public"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_alias_mutation_leaves_public_untouched_when_omitted():
    context, instance = await sync_to_async(_mutation_setup)()
    alias = await sync_to_async(models.InstanceAlias.objects.create)(
        instance=instance, host="old.example.com", kind="absolute", public=True
    )

    result = await management_schema.execute(
        UPDATE_ALIAS,
        context_value=context,
        variable_values={
            "input": {
                "id": str(alias.id),
                "port": 9090,
                "host": "new.example.com",
                "kind": "absolute",
            }
        },
    )

    assert not result.errors, result.errors
    # omitting `public` must not reset it to the default
    assert result.data["updateAlias"]["public"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_alias_mutation_removes_alias():
    context, instance = await sync_to_async(_mutation_setup)()
    alias = await sync_to_async(models.InstanceAlias.objects.create)(
        instance=instance, host="gone.example.com", kind="absolute"
    )

    result = await management_schema.execute(
        DELETE_ALIAS,
        context_value=context,
        variable_values={"input": {"id": str(alias.id)}},
    )

    assert not result.errors, result.errors
    assert result.data["deleteAlias"] == str(alias.id)
    exists = await sync_to_async(models.InstanceAlias.objects.filter(id=alias.id).exists)()
    assert exists is False

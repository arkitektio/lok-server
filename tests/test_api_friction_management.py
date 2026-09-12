"""Friction regressions for the management API: mutations that always crashed,
malformed ids that 500'd, filters on non-existent columns, and fields whose
shape was wrong (``Client.kind`` as a bare string, ``Release.requirements`` as a
list of strings).
"""

import pytest
from asgiref.sync import sync_to_async
from graphql import get_named_type

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from fakts import models as fakts_models
from fakts.enums import ClientKindChoices
from karakter import models as karakter_models
from tests import factories
from tests.conftest import build_auth_context


def _owner_context(organization):
    membership = factories.make_membership(user=organization.owner, organization=organization)
    request_client = factories.make_client(membership=membership)
    return build_auth_context(organization.owner, organization, request_client)


def _owner_setup():
    org = factories.make_organization()
    return org, _owner_context(org)


def _assert_clean_denial(result):
    """A clean GraphQL error carrying the shared denial text — not a 500 with a
    Django traceback message such as "Field 'id' expected a number"."""
    assert result.errors, f"expected an error, got data: {result.data}"
    message = result.errors[0].message
    assert message == DENIED, message


# --------------------------------------------------------------------------- #
# mutations that could never succeed
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_profile_succeeds_despite_auto_created_row():
    """A post_save signal creates the Profile with the user, so the old
    ``Profile(...).save()`` always hit the OneToOne constraint."""
    org, context = await sync_to_async(_owner_setup)()

    result = await management_schema.execute(
        "mutation ($user: ID!) { createProfile(input: {user: $user, name: \"Display Name\"}) { id name } }",
        context_value=context,
        variable_values={"user": str(org.owner.id)},
    )
    assert not result.errors, result.errors
    assert result.data["createProfile"]["name"] == "Display Name"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_organization_profile_succeeds_despite_auto_created_row():
    org, context = await sync_to_async(_owner_setup)()

    result = await management_schema.execute(
        "mutation ($org: ID!) { createOrganizationProfile(input: {organization: $org, name: \"Org Display\"}) { id name } }",
        context_value=context,
        variable_values={"org": str(org.id)},
    )
    assert not result.errors, result.errors
    assert result.data["createOrganizationProfile"]["name"] == "Org Display"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_change_organization_owner_works():
    """Used ``models.AbstractUser.objects`` (an abstract model: no manager) and so
    crashed on every call."""

    def _setup():
        org, context = _owner_setup()
        new_owner = factories.make_membership(organization=org).user
        return org, context, new_owner

    org, context, new_owner = await sync_to_async(_setup)()

    # A malformed / unknown new owner is a clean error, not a 500.
    result = await management_schema.execute(
        "mutation ($org: ID!, $new: ID!) { changeOrganizationOwner(input: {organization: $org, newOwner: $new}) { id } }",
        context_value=context,
        variable_values={"org": str(org.id), "new": "abc"},
    )
    assert result.errors
    assert "member" in result.errors[0].message

    result = await management_schema.execute(
        "mutation ($org: ID!, $new: ID!) { changeOrganizationOwner(input: {organization: $org, newOwner: $new}) { id amIOwner } }",
        context_value=context,
        variable_values={"org": str(org.id), "new": str(new_owner.id)},
    )
    assert not result.errors, result.errors
    assert result.data["changeOrganizationOwner"]["amIOwner"] is False
    owner_id = await sync_to_async(lambda: karakter_models.Organization.objects.get(pk=org.pk).owner_id)()
    assert owner_id == new_owner.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accept_hub_device_code_duplicate_identifier_is_a_clean_error():
    def _setup():
        org, context = _owner_setup()
        factories.make_hub(organization=org, identifier="dup")
        dc = factories.make_device_code(
            kind="hub",
            staging_manifest={"identifier": "dup", "instances": [], "clients": []},
        )
        return org, context, dc

    org, context, dc = await sync_to_async(_setup)()
    result = await management_schema.execute(
        "mutation ($input: AcceptHubDeviceCodeInput!) { acceptHubDeviceCode(input: $input) { id } }",
        context_value=context,
        variable_values={"input": {"deviceCode": str(dc.id), "code": dc.code, "organization": str(org.id), "allowIonscale": False}},
    )
    assert result.errors
    assert "already exists" in result.errors[0].message
    count = await sync_to_async(lambda: fakts_models.Hub.objects.filter(organization=org, identifier="dup").count())()
    assert count == 1


# --------------------------------------------------------------------------- #
# malformed ids
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_malformed_ids_are_denied_not_500():
    _org, context = await sync_to_async(_owner_setup)()

    for field in ("organization", "client", "invite", "hub", "device", "service", "app"):
        result = await management_schema.execute(
            f'query {{ {field}(id: "abc") {{ id }} }}',
            context_value=context,
        )
        _assert_clean_denial(result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_invite_by_non_uuid_code_is_a_clean_error():
    """``Invite.token`` is a UUIDField: a non-UUID code raised a ValidationError (500)."""
    _org, context = await sync_to_async(_owner_setup)()

    result = await management_schema.execute(
        'query { inviteByCode(inviteCode: "not-a-uuid") { id } }',
        context_value=context,
    )
    _assert_clean_denial(result)

    result = await management_schema.execute(
        'mutation { acceptInvite(input: {token: "not-a-uuid"}) { id } }',
        context_value=context,
    )
    assert result.errors
    assert result.errors[0].message == "Invalid invite token"


# --------------------------------------------------------------------------- #
# filters
# --------------------------------------------------------------------------- #


def _searchable_root_list_fields():
    """Every root Query field that accepts ``filters`` whose input has ``search``."""
    query_type = management_schema._schema.query_type
    out = []
    for name, field in query_type.fields.items():
        filters_arg = field.args.get("filters")
        if filters_arg is None:
            continue
        input_type = get_named_type(filters_arg.type)
        if "search" in getattr(input_type, "fields", {}):
            out.append(name)
    return out


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_every_search_filter_executes():
    """Several ``search`` filters targeted columns their model does not have
    (``name`` on Membership/Role/Scope/ServiceInstanceMapping, ``backend`` on
    ServiceInstance, ``token``/``key`` on credentials, and borrowed filter classes
    from other models) and 500'd with a FieldError on every search."""
    _org, context = await sync_to_async(_owner_setup)()

    fields = _searchable_root_list_fields()
    assert fields, "expected at least one searchable root list"
    for field in fields:
        result = await management_schema.execute(
            f'{{ {field}(filters: {{search: "x"}}) {{ id }} }}',
            context_value=context,
        )
        assert not result.errors, f"{field}: {result.errors}"

    # Nested lists that carried a foreign filter class before.
    for query in (
        '{ organizations { invites(filters: {search: "x"}) { id } } }',
        '{ me { comChannels(filters: {search: "x"}) { id } } }',
        '{ serviceReleases { instances(filters: {search: "x"}) { id } } }',
    ):
        result = await management_schema.execute(query, context_value=context)
        assert not result.errors, f"{query}: {result.errors}"


# --------------------------------------------------------------------------- #
# field shapes
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_kind_and_role_are_enums():
    def _setup():
        org, context = _owner_setup()
        membership = karakter_models.Membership.objects.get(user=org.owner, organization=org)
        hub_client = factories.make_client(membership=membership, kind=ClientKindChoices.HUB.value)
        return hub_client, context

    hub_client, context = await sync_to_async(_setup)()
    result = await management_schema.execute(
        "query ($id: ID!) { client(id: $id) { id kind role } }",
        context_value=context,
        variable_values={"id": str(hub_client.id)},
    )
    assert not result.errors, result.errors
    assert result.data["client"]["kind"] == "HUB"
    assert result.data["client"]["role"] == "INTERFACE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_release_requirements_are_structured():
    def _setup():
        org, context = _owner_setup()
        app = factories.make_app(organization=org)
        release = factories.make_release(
            app=app,
            requirements=[
                {"key": "db", "service": "com.example.db", "optional": False},
                "garbage",
                {"not": "a requirement"},
            ],
        )
        return release, context

    release, context = await sync_to_async(_setup)()
    result = await management_schema.execute(
        "query ($id: ID!) { release(id: $id) { id requirements { key service optional } } }",
        context_value=context,
        variable_values={"id": str(release.id)},
    )
    assert not result.errors, result.errors
    assert result.data["release"]["requirements"] == [{"key": "db", "service": "com.example.db", "optional": False}]

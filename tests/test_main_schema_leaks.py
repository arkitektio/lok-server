"""Cross-tenant / cross-user object leaks on the main `/graphql` schema.

strawberry-django only runs a type's ``get_queryset`` for resolvers that return
a *QuerySet*; single-object roots that ``.get()`` by pk bypass it entirely, and
several types had no ``get_queryset`` at all. These tests pin the scoping that
now exists for both shapes.

Denials assert on the shared "Not found, or you are not authorized" text so the
error cannot be used as an existence oracle.
"""

import pytest
from asgiref.sync import sync_to_async

from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _two_principals():
    """Two authenticated principals in unrelated organizations."""
    mine = factories.make_membership()
    my_client = factories.make_client(membership=mine)
    my_context = build_auth_context(mine.user, mine.organization, my_client)

    theirs = factories.make_membership()
    their_client = factories.make_client(membership=theirs)
    their_context = build_auth_context(theirs.user, theirs.organization, their_client)
    return my_context, mine, my_client, their_context, theirs, their_client


def _assert_denied(result):
    assert result.errors, f"expected a denial, got data: {result.data}"
    assert "not authorized" in result.errors[0].message, result.errors[0].message


REDEEM_TOKEN = """
    query ($id: ID!) { redeemToken(id: $id) { id token } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_redeem_token_is_not_readable_by_another_user():
    """`redeemToken(id)` returns the bearer `token`; it used to fetch by bare pk."""
    my_context, mine, _my_client, _their_context, theirs, _their_client = await sync_to_async(_two_principals)()

    def _tokens():
        their_hub = factories.make_hub(organization=theirs.organization)
        their_token = factories.make_redeem_token(hub=their_hub, user=theirs.user)
        my_hub = factories.make_hub(organization=mine.organization)
        my_token = factories.make_redeem_token(hub=my_hub, user=mine.user)
        # Same tenant, different user: still not mine.
        colleague = factories.make_membership(organization=mine.organization)
        colleague_token = factories.make_redeem_token(hub=my_hub, user=colleague.user)
        return their_token, my_token, colleague_token

    their_token, my_token, colleague_token = await sync_to_async(_tokens)()

    result = await schema.execute(REDEEM_TOKEN, context_value=my_context, variable_values={"id": str(their_token.id)})
    _assert_denied(result)

    result = await schema.execute(REDEEM_TOKEN, context_value=my_context, variable_values={"id": str(colleague_token.id)})
    _assert_denied(result)

    result = await schema.execute(REDEEM_TOKEN, context_value=my_context, variable_values={"id": str(my_token.id)})
    assert not result.errors, result.errors
    assert result.data["redeemToken"]["token"] == my_token.token


DELETE_REDEEM_TOKEN = """
    mutation ($id: ID!) { deleteRedeemToken(input: {id: $id}) }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_redeem_token_is_only_revocable_by_its_issuer():
    """`deleteRedeemToken` has the same scoping as `redeemToken(id)`: another tenant's
    token and a colleague's token are 'not found', only the issuer's own goes."""
    my_context, mine, _my_client, _their_context, theirs, _their_client = await sync_to_async(_two_principals)()

    def _tokens():
        their_hub = factories.make_hub(organization=theirs.organization)
        their_token = factories.make_redeem_token(hub=their_hub, user=theirs.user)
        my_hub = factories.make_hub(organization=mine.organization)
        my_token = factories.make_redeem_token(hub=my_hub, user=mine.user)
        colleague = factories.make_membership(organization=mine.organization)
        colleague_token = factories.make_redeem_token(hub=my_hub, user=colleague.user)
        return their_token, my_token, colleague_token

    their_token, my_token, colleague_token = await sync_to_async(_tokens)()

    for foreign in (their_token, colleague_token):
        result = await schema.execute(DELETE_REDEEM_TOKEN, context_value=my_context, variable_values={"id": str(foreign.id)})
        _assert_denied(result)

    result = await schema.execute(DELETE_REDEEM_TOKEN, context_value=my_context, variable_values={"id": str(my_token.id)})
    assert not result.errors, result.errors
    assert result.data["deleteRedeemToken"] == str(my_token.id)

    def _remaining():
        from fakts import models

        return set(models.RedeemToken.objects.values_list("id", flat=True))

    remaining = await sync_to_async(_remaining)()
    assert my_token.id not in remaining
    assert {their_token.id, colleague_token.id} <= remaining


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_user_by_id_is_denied_across_organizations():
    my_context, mine, _my_client, _their_context, theirs, _their_client = await sync_to_async(_two_principals)()

    query = "query ($id: ID!) { user(id: $id) { id email } }"

    result = await schema.execute(query, context_value=my_context, variable_values={"id": str(theirs.user.id)})
    _assert_denied(result)

    # A colleague in my organization is still visible.
    colleague = await sync_to_async(factories.make_membership)(organization=mine.organization)
    result = await schema.execute(query, context_value=my_context, variable_values={"id": str(colleague.user.id)})
    assert not result.errors, result.errors
    assert result.data["user"]["id"] == str(colleague.user.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_service_instance_by_id_is_denied_across_organizations():
    my_context, _mine, _my_client, _their_context, theirs, _their_client = await sync_to_async(_two_principals)()

    def _their_instance():
        hub = factories.make_hub(organization=theirs.organization)
        service = factories.make_service(organization=theirs.organization)
        release = factories.make_service_release(service=service)
        return factories.make_service_instance(hub=hub, release=release)

    instance = await sync_to_async(_their_instance)()

    for query in (
        "query ($id: ID!) { serviceInstance(id: $id) { id } }",
        "query ($id: ID!) { service(id: $id) { id } }",
        "query ($id: ID!) { serviceRelease(id: $id) { id } }",
    ):
        target = {
            "serviceInstance": instance.id,
            "service": instance.release.service_id,
            "serviceRelease": instance.release_id,
        }[query.split("{ ")[1].split("(")[0]]
        result = await schema.execute(query, context_value=my_context, variable_values={"id": str(target)})
        _assert_denied(result)


NESTED_INSTANCES = """
    query {
        serviceInstances { id mappings { id client { id user { email } } } }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nested_service_instance_mappings_do_not_leak_another_tenant():
    my_context, _mine, _my_client, _their_context, theirs, their_client = await sync_to_async(_two_principals)()

    def _their_topology():
        from fakts.models import ServiceInstanceMapping

        hub = factories.make_hub(organization=theirs.organization)
        service = factories.make_service(organization=theirs.organization)
        release = factories.make_service_release(service=service)
        instance = factories.make_service_instance(hub=hub, release=release)
        ServiceInstanceMapping.objects.create(client=their_client, instance=instance, key="svc")
        return instance

    await sync_to_async(_their_topology)()

    result = await schema.execute(NESTED_INSTANCES, context_value=my_context)
    assert not result.errors, result.errors
    assert result.data["serviceInstances"] == []

    # And the same topology *is* visible to its own tenant, so the scoping is
    # not just "everything is empty".
    their_context = await sync_to_async(
        lambda: build_auth_context(theirs.user, theirs.organization, their_client)
    )()
    result = await schema.execute(NESTED_INSTANCES, context_value=their_context)
    assert not result.errors, result.errors
    emails = {m["client"]["user"]["email"] for row in result.data["serviceInstances"] for m in row["mappings"]}
    assert emails == {theirs.user.email}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_organization_roots_are_limited_to_the_active_organization():
    my_context, mine, _my_client, _their_context, theirs, _their_client = await sync_to_async(_two_principals)()

    result = await schema.execute("query { organizations { id } }", context_value=my_context)
    assert not result.errors, result.errors
    assert {row["id"] for row in result.data["organizations"]} == {str(mine.organization.id)}

    result = await schema.execute(
        "query ($id: ID!) { organization(id: $id) { id } }",
        context_value=my_context,
        variable_values={"id": str(theirs.organization.id)},
    )
    _assert_denied(result)

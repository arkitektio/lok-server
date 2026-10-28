"""What an app may do at lok itself is what its token's lok scopes say: every root field of the
client-facing schema asks for one (the ``@auth`` directive publishes it, unlok gathers it), every
organization holds them built in, and ``lok_all`` stands for all of them."""

import pytest
from asgiref.sync import sync_to_async

from authentikate.strawberry.directives import Auth
from karakter import models
from karakter.scopes import LOK_SCOPES
from lok_server.schema import schema as main_schema
from tests import factories
from tests.conftest import build_auth_context

KEYS = {scope.key for scope in LOK_SCOPES}


def test_every_root_field_asks_for_one_of_loks_scopes():
    core = main_schema._schema
    gaps = []
    for root in (core.query_type, core.mutation_type, core.subscription_type):
        for name, field in root.fields.items():
            if name.startswith("_"):
                continue  # kante's federation fields
            definition = (field.extensions or {}).get("strawberry-definition")
            required = [scope for d in definition.directives if isinstance(d, Auth) for scope in (d.required_scopes or ())]
            if not required or not set(required) <= KEYS:
                gaps.append(f"{root.name}.{name}: {required}")
    assert gaps == []


@pytest.mark.django_db
def test_every_organization_holds_loks_scopes_built_in():
    organization = factories.make_organization()
    held = {scope.identifier: scope for scope in models.Scope.objects.filter(organization=organization, identifier__in=KEYS)}
    assert set(held) == KEYS
    assert all(scope.is_builtin and scope.description for scope in held.values())
    assert [key for key, scope in held.items() if scope.is_all_scope] == ["lok_all"]


def _member(scope: str):
    organization = factories.make_organization()
    membership = factories.make_membership(user=organization.owner, organization=organization)
    return build_auth_context(organization.owner, organization, factories.make_client(membership=membership), scope=scope)


ME = "query { me { id } }"
ACKNOWLEDGE = 'mutation { acknowledgeMessage(input: {id: "1", acknowledged: true}) { id } }'


def _reason(result) -> str | None:
    return result.errors[0].extensions.get("reason") if result.errors else None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_app_granted_reads_reads_and_writes_nothing():
    context = await sync_to_async(_member)("openid lok_read")
    assert (await main_schema.execute(ME, context_value=context)).errors is None
    refused = await main_schema.execute(ACKNOWLEDGE, context_value=context)
    assert _reason(refused) == "INSUFFICIENT_SCOPE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_app_without_loks_scopes_reads_nothing():
    context = await sync_to_async(_member)("openid profile email")
    assert _reason(await main_schema.execute(ME, context_value=context)) == "INSUFFICIENT_SCOPE"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_all_scope_passes_every_check():
    context = await sync_to_async(_member)("openid lok_all")
    result = await main_schema.execute(ACKNOWLEDGE, context_value=context)
    # Past the scope gate: what refuses it now is the resolver (no such message), not a scope.
    assert _reason(result) != "INSUFFICIENT_SCOPE"

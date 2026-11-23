"""Collaborations on the main schema, with a bearer token: what apps (orkestrator) reach.

The rules are the management schema's (``karakter.collaborations``): any full member but a
guest makes one in the active organization; its maker and the owner and admins change or
delete it; a full member lists the organization's, a guest their own.
"""

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from karakter import models
from lok_server.schema import schema as main_schema
from tests.test_collaborations import World, _world

CREATE = """
mutation ($name: String!) { createCollaboration(input: {name: $name}) { id key name } }
"""
UPDATE = """
mutation ($id: ID!, $name: String!) { updateCollaboration(input: {id: $id, name: $name}) { name } }
"""
DELETE = "mutation ($id: ID!) { deleteCollaboration(input: {id: $id}) }"
LIST = "query { collaborations { key name } }"
BY_KEY = "query ($key: ID!) { collaboration(key: $key) { id name } }"
BY_ID = "query ($id: ID!) { collaboration(id: $id) { key } }"


async def _run(
    query: str, context: HttpContext, **variables: object
) -> ExecutionResult:
    return await main_schema.execute(
        query, variable_values=variables, context_value=context
    )


def _data(result: ExecutionResult) -> dict[str, Any]:
    assert result.errors is None, result.errors
    assert result.data is not None
    return result.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_member_makes_one_in_the_active_organization() -> None:
    world: World = await sync_to_async(_world)()
    made = _data(await _run(CREATE, world.contexts["member"], name="  Run 7 "))
    collaboration = await models.Collaboration.objects.aget(
        pk=made["createCollaboration"]["id"]
    )
    assert collaboration.name == "Run 7"
    assert collaboration.organization_id == world.organization.pk
    assert made["createCollaboration"]["key"] == str(collaboration.key)

    for who in ("guest", "guest_role"):
        refused = await _run(CREATE, world.contexts[who], name="Nope")
        assert refused.errors, who
    blank = await _run(CREATE, world.contexts["member"], name="  ")
    assert blank.errors and "needs a name" in blank.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_its_maker_and_the_owner_change_it_and_nobody_else() -> None:
    world: World = await sync_to_async(_world)()
    plates = str(world.plates.pk)
    assert _data(await _run(UPDATE, world.contexts["member"], id=plates, name="A"))
    assert _data(await _run(UPDATE, world.contexts["owner"], id=plates, name="B"))
    for who in ("colleague", "guest", "outsider"):
        assert (await _run(UPDATE, world.contexts[who], id=plates, name="X")).errors
    # Another organization's collaboration is not found from this one.
    foreign = str(world.foreign.pk)
    assert (await _run(UPDATE, world.contexts["owner"], id=foreign, name="X")).errors
    await world.plates.arefresh_from_db()
    assert world.plates.name == "B"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_takes_the_grants_and_the_guest_left_with_none() -> None:
    world: World = await sync_to_async(_world)()
    assert (await _run(DELETE, world.contexts["colleague"], id=world.plates.pk)).errors
    _data(await _run(DELETE, world.contexts["member"], id=world.plates.pk))
    assert not await models.Collaboration.objects.filter(pk=world.plates.pk).aexists()
    assert not await models.Membership.objects.filter(pk=world.guest.pk).aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_members_list_all_a_guest_their_own_and_lookups_follow() -> None:
    world: World = await sync_to_async(_world)()
    listed = _data(await _run(LIST, world.contexts["member"]))["collaborations"]
    assert {c["name"] for c in listed} == {"Plates", "Notes"}
    guest = _data(await _run(LIST, world.contexts["guest"]))["collaborations"]
    assert [c["key"] for c in guest] == [str(world.plates.key)]

    found = _data(
        await _run(BY_KEY, world.contexts["guest"], key=str(world.plates.key))
    )
    assert found["collaboration"]["name"] == "Plates"
    assert (
        await _run(BY_KEY, world.contexts["guest"], key=str(world.notes.key))
    ).errors
    assert (await _run(BY_ID, world.contexts["owner"], id=world.foreign.pk)).errors
    assert (await _run(BY_KEY, world.contexts["owner"], key="not-a-key")).errors

"""Collaborations are lok's: made, renamed and deleted on the management API.

A collaboration is one key across all of an organization's services. Any full member
but a guest may make one (the same bar as granting one), its maker and the
organization's owner and admins may change or delete it, and deleting it takes every
grant of it along, so a guest left with none leaves the organization.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from api.management.schema import schema as management_schema
from karakter import collaboration_grants, models
from karakter.invites import redeem_invite
from tests import factories
from tests.conftest import build_auth_context

CREATE = """
    mutation ($input: CreateCollaborationInput!) {
        createCollaboration(input: $input) {
            id key name description organization { id } createdBy { id } grants { id }
        }
    }
"""
UPDATE = """
    mutation ($input: UpdateCollaborationInput!) {
        updateCollaboration(input: $input) { id name description }
    }
"""
DELETE = """
    mutation ($input: DeleteCollaborationInput!) { deleteCollaboration(input: $input) }
"""
LIST = "query { collaborations { key name } }"
ONE = "query ($id: ID!) { collaboration(id: $id) { key name grants { id } } }"
GRANTS = "query { collaborationGrants { collaboration { key } membership { id } } }"


def _role(organization: models.Organization, identifier: str) -> models.Role:
    return models.Role.objects.get_or_create(
        identifier=identifier, organization=organization
    )[0]


def _member(
    organization: models.Organization, roles: Sequence[str] = ("user",)
) -> models.Membership:
    member = factories.make_membership(organization=organization)
    member.roles.add(*[_role(organization, r) for r in roles])
    return member


def _context(membership: models.Membership) -> HttpContext:
    return build_auth_context(
        membership.user,
        membership.organization,
        factories.make_client(membership=membership),
        roles=(),
    )


def _grant(
    organization: models.Organization,
    granter: models.Membership,
    *collaborations: models.Collaboration,
) -> models.Membership:
    """A new guest of ``organization``, granted ``collaborations``."""
    invite = models.Invite.objects.create(
        created_by=granter.user,
        created_for=organization,
        shared_collaborations=collaboration_grants.normalize_collaborations(
            [{"collaboration": str(c.key)} for c in collaborations], organization
        ),
    )
    return redeem_invite(invite, factories.make_user())


@dataclass
class World:
    organization: models.Organization
    member: models.Membership  # made `plates`
    guest: models.Membership  # granted `plates`
    plates: models.Collaboration
    notes: models.Collaboration
    foreign: models.Collaboration
    contexts: dict[str, HttpContext]


def _world() -> World:
    organization = factories.make_organization()
    owner = factories.make_membership(
        user=organization.owner, organization=organization
    )
    member = _member(organization)
    colleague = _member(organization)
    guest_role = _member(organization, ("guest",))
    plates = models.Collaboration.objects.create(
        organization=organization, name="Plates", created_by=member.user
    )
    notes = models.Collaboration.objects.create(organization=organization, name="Notes")
    foreign = models.Collaboration.objects.create(
        organization=factories.make_organization(), name="Foreign"
    )
    guest = _grant(organization, member, plates)
    outsider = factories.make_membership()  # a member of another organization only
    contexts = {
        "owner": _context(owner),
        "member": _context(member),
        "colleague": _context(colleague),
        "guest_role": _context(guest_role),
        "guest": _context(guest),
        "outsider": _context(outsider),
    }
    return World(
        organization=organization,
        member=member,
        guest=guest,
        plates=plates,
        notes=notes,
        foreign=foreign,
        contexts=contexts,
    )


async def _run(
    query: str, context: HttpContext, **variables: object
) -> ExecutionResult:
    return await management_schema.execute(
        query, variable_values=variables, context_value=context
    )


async def _exists(
    model: type[models.Collaboration] | type[models.Membership], pk: int
) -> bool:
    return await sync_to_async(model.objects.filter(pk=pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_any_full_member_makes_a_collaboration_and_no_guest_or_outsider_does() -> (
    None
):
    world = await sync_to_async(_world)()
    organization = str(world.organization.pk)

    made = await _run(
        CREATE,
        world.contexts["colleague"],
        input={"organization": organization, "name": "  Screens ", "description": "x"},
    )

    assert not made.errors, made.errors
    collaboration = made.data["createCollaboration"]
    assert uuid.UUID(collaboration["key"])
    assert collaboration["name"] == "Screens"
    assert collaboration["description"] == "x"
    assert collaboration["organization"] == {"id": organization}
    assert collaboration["grants"] == []
    stored = await sync_to_async(models.Collaboration.objects.get)(
        pk=collaboration["id"]
    )
    assert str(stored.key) == collaboration["key"]
    assert collaboration["createdBy"] == {"id": str(stored.created_by_id)}

    for who in ("guest_role", "guest", "outsider"):
        refused = await _run(
            CREATE,
            world.contexts[who],
            input={"organization": organization, "name": "Mine"},
        )
        assert refused.errors, who
    blank = await _run(
        CREATE,
        world.contexts["colleague"],
        input={"organization": organization, "name": "  "},
    )
    assert blank.errors and "needs a name" in blank.errors[0].message
    assert (
        await sync_to_async(
            models.Collaboration.objects.filter(organization=world.organization).count
        )()
        == 3
    )  # plates, notes and screens


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_its_maker_and_the_owner_change_a_collaboration_and_nobody_else() -> None:
    world = await sync_to_async(_world)()
    plates = str(world.plates.pk)

    renamed = await _run(
        UPDATE, world.contexts["member"], input={"id": plates, "name": "Wells"}
    )
    assert not renamed.errors, renamed.errors
    assert renamed.data["updateCollaboration"]["name"] == "Wells"

    described = await _run(
        UPDATE,
        world.contexts["owner"],
        input={"id": plates, "description": "All plates"},
    )
    assert not described.errors, described.errors
    assert described.data["updateCollaboration"] == {
        "id": plates,
        "name": "Wells",
        "description": "All plates",
    }

    for who in ("colleague", "guest_role", "guest", "outsider"):
        refused = await _run(
            UPDATE, world.contexts[who], input={"id": plates, "name": "Mine"}
        )
        assert refused.errors, who
        refused_delete = await _run(DELETE, world.contexts[who], input={"id": plates})
        assert refused_delete.errors, who
    assert await _exists(models.Collaboration, world.plates.pk)
    stored = await sync_to_async(models.Collaboration.objects.get)(pk=world.plates.pk)
    assert stored.name == "Wells"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["left", "guest_role_only"])
async def test_a_maker_who_is_no_full_member_any_more_changes_nothing(how: str) -> None:
    world = await sync_to_async(_world)()

    def demote() -> None:
        if how == "left":
            world.member.delete()
        else:
            world.member.roles.set([_role(world.organization, "guest")])

    await sync_to_async(demote)()

    renamed = await _run(
        UPDATE,
        world.contexts["member"],
        input={"id": str(world.plates.pk), "name": "Mine"},
    )
    assert renamed.errors
    deleted = await _run(
        DELETE, world.contexts["member"], input={"id": str(world.plates.pk)}
    )
    assert deleted.errors
    assert await _exists(models.Collaboration, world.plates.pk)
    assert await _exists(models.Membership, world.guest.pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_deleting_a_collaboration_takes_its_grants_and_a_guest_left_with_none() -> (
    None
):
    world = await sync_to_async(_world)()
    # Another guest, of both: they stay, with the other one.
    both = await sync_to_async(_grant)(
        world.organization, world.member, world.plates, world.notes
    )

    deleted = await _run(
        DELETE, world.contexts["owner"], input={"id": str(world.plates.pk)}
    )

    assert not deleted.errors, deleted.errors
    assert deleted.data["deleteCollaboration"] == str(world.plates.pk)
    assert not await _exists(models.Collaboration, world.plates.pk)
    assert not await sync_to_async(
        models.CollaborationGrant.objects.filter(
            collaboration_id=world.plates.pk
        ).exists
    )()
    assert not await _exists(models.Membership, world.guest.pk)
    assert await _exists(models.Membership, both.pk)
    remaining = await sync_to_async(
        lambda: list(both.collaboration_grants.values_list("collaboration", flat=True))
    )()
    assert remaining == [world.notes.pk]
    assert await _exists(models.Membership, world.member.pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_members_list_the_organizations_collaborations_and_a_guest_their_own() -> (
    None
):
    world = await sync_to_async(_world)()

    as_member = await _run(LIST, world.contexts["colleague"])
    assert not as_member.errors, as_member.errors
    assert {c["key"] for c in as_member.data["collaborations"]} == {
        str(world.plates.key),
        str(world.notes.key),
    }

    as_guest = await _run(LIST, world.contexts["guest"])
    assert not as_guest.errors, as_guest.errors
    assert as_guest.data["collaborations"] == [
        {"key": str(world.plates.key), "name": "Plates"}
    ]
    hidden = await _run(ONE, world.contexts["guest"], id=str(world.notes.pk))
    assert hidden.errors
    foreign = await _run(ONE, world.contexts["colleague"], id=str(world.foreign.pk))
    assert foreign.errors

    as_outsider = await _run(LIST, world.contexts["outsider"])
    assert as_outsider.data["collaborations"] == []

    grants = await _run(GRANTS, world.contexts["guest"])
    assert not grants.errors, grants.errors
    assert grants.data["collaborationGrants"] == [
        {
            "collaboration": {"key": str(world.plates.key)},
            "membership": {"id": str(world.guest.pk)},
        }
    ]
    one = await _run(ONE, world.contexts["colleague"], id=str(world.plates.pk))
    assert not one.errors, one.errors
    assert len(one.data["collaboration"]["grants"]) == 1

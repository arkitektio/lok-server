"""Lists the app narrows on the server instead of fetching them whole: mandates by
claimant, parent, agent, subject and mesh permission (finding the standing
mandate a device is keyed under), how many clients a mandate or a device has,
releases paged and filtered, and member invites told apart from collaboration
links."""

from typing import Any, TypedDict

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext

from fakts import models as fmodels
from fakts.base_models import MandatePin, Manifest
from fakts.services import mandates
from karakter import models as kmodels
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

AGENT = "live.arkitekt.deployer"
SUBJECT = {
    "identifier": "com.example.subject",
    "version": "1.2.0",
    "scopes": [],
    "requirements": [],
}


async def _run(context: HttpContext, query: str, **variables: object) -> dict[str, Any]:
    result = await schema.execute(
        query, context_value=context, variable_values=variables
    )
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


class World(TypedDict):
    grantor: kmodels.Membership
    org: kmodels.Organization
    agent_client: fmodels.Client
    standing: fmodels.Mandate
    children: list[fmodels.Mandate]
    keyed: fmodels.Mandate
    hub: fmodels.Hub


def _world() -> World:
    """An owner (admin) with a hub, a deployer agent app, and a standing agent mandate with two device keys."""
    owner = factories.make_user()
    org = factories.make_organization(owner=owner)
    grantor = factories.make_membership(user=owner, organization=org)
    hub = factories.make_hub(organization=org)
    agent_client = factories.make_client(
        membership=grantor,
        hub=hub,
        release=factories.make_release(
            app=factories.make_app(organization=org, identifier=AGENT)
        ),
    )
    standing = mandates.issue_mandate(
        issuer=mandates.ClientIssuer(client=agent_client),
        grantor=grantor,
        hub=hub,
        pin=MandatePin.from_manifest(Manifest(**SUBJECT)),
        claimant=mandates.AgentClaimant(identifier=AGENT),
        allow_mesh=True,
    ).mandate
    children = [
        mandates.issue_mandate(
            issuer=mandates.AgentIssuer(parent=standing, client=agent_client),
            grantor=standing.membership,
            hub=standing.hub,
            pin=standing.pin.narrowed_to_device(device),
            claimant=mandates.KeyClaimant(),
        ).mandate
        for device in ("dev-a", "dev-b")
    ]
    keyed, _ = factories.make_mandate_key(hub=hub, membership=grantor)
    return {
        "grantor": grantor,
        "org": org,
        "agent_client": agent_client,
        "standing": standing,
        "children": children,
        "keyed": keyed,
        "hub": hub,
    }


def _context(world: World) -> HttpContext:
    return build_auth_context(
        world["grantor"].user, world["org"], world["agent_client"]
    )


MANDATES = """
query ($filters: MandateFilter) { mandates(filters: $filters) { id } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_standing_mandate_is_found_by_filters() -> None:
    world = await sync_to_async(_world)()
    context = await sync_to_async(_context)(world)
    standing = str(world["standing"].pk)

    found = await _run(
        context,
        MANDATES,
        filters={
            "live": True,
            "claimant": "AGENT",
            "hasParent": False,
            "agentIdentifier": AGENT,
            "subjectIdentifier": SUBJECT["identifier"],
            "allowMesh": True,
        },
    )
    assert [m["id"] for m in found["mandates"]] == [standing]

    # Another mesh permission is another standing mandate: none yet.
    assert (
        await _run(
            context,
            MANDATES,
            filters={"claimant": "AGENT", "hasParent": False, "allowMesh": False},
        )
    )["mandates"] == []

    children = await _run(context, MANDATES, filters={"hasParent": True})
    assert {m["id"] for m in children["mandates"]} == {
        str(c.pk) for c in world["children"]
    }

    keyed = await _run(
        context, MANDATES, filters={"claimant": "KEY", "hasParent": False}
    )
    assert [m["id"] for m in keyed["mandates"]] == [str(world["keyed"].pk)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_counts_of_a_mandate_and_a_device() -> None:
    world = await sync_to_async(_world)()
    context = await sync_to_async(_context)(world)

    def _clients() -> fmodels.Device:
        device = fmodels.Device.objects.create(
            node_id="node-1", organization=world["org"]
        )
        other = fmodels.Device.objects.create(
            node_id="node-2", organization=world["org"]
        )
        # The key logs in on two devices; the first device also runs a client of its own.
        factories.make_client(
            membership=world["grantor"],
            hub=world["hub"],
            mandate=world["keyed"],
            node=device,
        )
        factories.make_client(
            membership=world["grantor"],
            hub=world["hub"],
            mandate=world["keyed"],
            node=other,
        )
        factories.make_client(
            membership=world["grantor"], hub=world["hub"], node=device
        )
        return device

    device = await sync_to_async(_clients)()

    counts = await _run(
        context,
        "query ($ids: [ID!]) { mandates(filters: { ids: $ids }) { id clientCount } }",
        ids=[str(world["keyed"].pk), str(world["standing"].pk)],
    )
    assert {m["id"]: m["clientCount"] for m in counts["mandates"]} == {
        str(world["keyed"].pk): 2,
        str(world["standing"].pk): 0,
    }

    devices = await _run(
        context,
        "query ($ids: [ID!]) { devices(filters: { ids: $ids }) { id clientCount } }",
        ids=[str(device.pk)],
    )
    assert devices["devices"] == [{"id": str(device.pk), "clientCount": 2}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_releases_page_and_filter() -> None:
    world = await sync_to_async(_world)()
    context = await sync_to_async(_context)(world)

    def _releases() -> tuple[fmodels.App, list[fmodels.Release]]:
        app = factories.make_app(
            organization=world["org"], name="Napari", identifier="org.napari.napari"
        )
        return app, [
            factories.make_release(app=app, version=f"0.{i}.0") for i in range(3)
        ]

    app, releases = await sync_to_async(_releases)()

    by_app = await _run(
        context,
        "query ($f: ReleaseFilter) { releases(filters: $f) { id } }",
        f={"apps": [str(app.pk)]},
    )
    assert {r["id"] for r in by_app["releases"]} == {str(r.pk) for r in releases}

    searched = await _run(
        context,
        "query ($f: ReleaseFilter) { releases(filters: $f) { id } }",
        f={"search": "napari"},
    )
    assert {r["id"] for r in searched["releases"]} == {str(r.pk) for r in releases}

    page = await _run(
        context,
        "query ($f: ReleaseFilter, $p: OffsetPaginationInput) { releases(filters: $f, pagination: $p, ordering: [{ id: ASC }]) { id } }",
        f={"apps": [str(app.pk)]},
        p={"offset": 1, "limit": 1},
    )
    assert [r["id"] for r in page["releases"]] == [str(releases[1].pk)]

    nested = await _run(
        context,
        "query ($id: ID!) { app(id: $id) { releaseCount releases(pagination: { limit: 2 }) { id clientCount } } }",
        id=str(app.pk),
    )
    assert len(nested["app"]["releases"]) == 2
    assert nested["app"]["releaseCount"] == 3

    def _run_release() -> None:
        factories.make_client(membership=world["grantor"], release=releases[0])

    await sync_to_async(_run_release)()
    counted = await _run(
        context,
        "query ($id: ID!) { release(id: $id) { clientCount } }",
        id=str(releases[0].pk),
    )
    assert counted["release"]["clientCount"] == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_invites_apart_from_collaboration_links() -> None:
    world = await sync_to_async(_world)()
    context = await sync_to_async(_context)(world)

    def _invites() -> tuple[kmodels.Invite, kmodels.Invite]:
        user = world["grantor"].user
        member = kmodels.Invite.objects.create(
            created_by=user, created_for=world["org"]
        )
        link = kmodels.Invite.objects.create(
            created_by=user,
            created_for=world["org"],
            shared_collaborations=[
                {"instance": "1", "collaboration": "2", "roles": []}
            ],
        )
        return member, link

    member, link = await sync_to_async(_invites)()
    query = "query ($f: InviteFilter) { invites(filters: $f) { id } }"

    members = await _run(context, query, f={"grantsCollaborations": False})
    assert [i["id"] for i in members["invites"]] == [str(member.pk)]

    links = await _run(context, query, f={"grantsCollaborations": True})
    assert [i["id"] for i in links["invites"]] == [str(link.pk)]

"""Rows looked up by the ids other services and links carry: device ids, client
ids, app identifiers, and the collaboration an invite grants."""

import datetime
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from fakts import models as fakts_models
from karakter import models
from karakter.hashers import hash_device_id
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


async def _run(
    context: HttpContext, query: str, **variables: object
) -> ExecutionResult:
    result = await schema.execute(
        query, context_value=context, variable_values=variables
    )
    return result


async def _data(
    context: HttpContext, query: str, **variables: object
) -> dict[str, Any]:
    result = await _run(context, query, **variables)
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


def _member() -> tuple[models.Membership, HttpContext]:
    membership = factories.make_membership()
    return membership, build_auth_context(
        membership.user,
        membership.organization,
        factories.make_client(membership=membership),
    )


DEVICE_BY_ID = "query ($id: ID!) { deviceByDeviceId(id: $id) { id deviceId } }"
DEVICES = "query ($filters: DeviceFilter) { devices(filters: $filters) { id } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_device_is_found_by_the_id_other_services_know_it_by() -> None:
    def setup() -> tuple[HttpContext, fakts_models.Device, str]:
        membership, context = _member()
        stored = hash_device_id("raw-laptop", membership.organization)
        device = fakts_models.Device.objects.create(
            organization=membership.organization, node_id=stored, name="laptop"
        )
        return context, device, stored

    context, device, stored = await sync_to_async(setup)()

    # What rekuest reports (the token's client_device claim) is the stored id.
    by_stored = await _data(context, DEVICE_BY_ID, id=stored)
    assert by_stored["deviceByDeviceId"] == {"id": str(device.pk), "deviceId": stored}
    # The raw id a client knows about itself still works.
    assert (await _data(context, DEVICE_BY_ID, id="raw-laptop"))["deviceByDeviceId"][
        "id"
    ] == str(device.pk)
    assert (await _run(context, DEVICE_BY_ID, id="unknown")).errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_devices_filter_by_device_ids_and_by_group() -> None:
    def setup() -> tuple[
        HttpContext, list[fakts_models.Device], fakts_models.DeviceGroup
    ]:
        membership, context = _member()
        organization = membership.organization
        devices = [
            fakts_models.Device.objects.create(
                organization=organization, node_id=f"node-{i}"
            )
            for i in range(4)
        ]
        group = fakts_models.DeviceGroup.objects.create(
            name="scopes", organization=organization
        )
        group.devices.add(devices[0], devices[2])
        other = factories.make_membership()
        fakts_models.Device.objects.create(
            organization=other.organization, node_id="node-1"
        )
        return context, devices, group

    context, devices, group = await sync_to_async(setup)()

    by_ids = await _data(
        context, DEVICES, filters={"deviceIds": ["node-1", "node-3", "nowhere"]}
    )
    assert {d["id"] for d in by_ids["devices"]} == {
        str(devices[1].pk),
        str(devices[3].pk),
    }

    in_group = await _data(context, DEVICES, filters={"deviceGroup": str(group.pk)})
    assert {d["id"] for d in in_group["devices"]} == {
        str(devices[0].pk),
        str(devices[2].pk),
    }


CLIENTS = "query ($ids: [ID!]!) { clientsByClientId(clientIds: $ids) { requestedId client { id clientId } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_clients_are_looked_up_in_one_query_by_any_id_they_had() -> None:
    def setup() -> tuple[
        HttpContext,
        fakts_models.Client,
        fakts_models.Client,
        fakts_models.ClientIdentity,
        fakts_models.Client,
    ]:
        membership, context = _member()
        mine = factories.make_client(membership=membership)
        moved = factories.make_client(membership=membership)
        later = factories.make_release(app=moved.release.app, version="2.0.0")
        identity = fakts_models.ClientIdentity.objects.create(
            client=moved, release=later
        )
        theirs = factories.make_client()
        return context, mine, moved, identity, theirs

    context, mine, moved, identity, theirs = await sync_to_async(setup)()

    data = await _data(
        context,
        CLIENTS,
        ids=[
            mine.client_id,
            identity.token_client_id,
            moved.client_id,
            theirs.client_id,
            "nobody",
        ],
    )
    answers = {
        row["requestedId"]: row["client"]["id"] for row in data["clientsByClientId"]
    }
    assert answers == {
        mine.client_id: str(mine.pk),
        identity.token_client_id: str(moved.pk),
        moved.client_id: str(moved.pk),
    }


APPS = "query ($filters: AppFilter) { apps(filters: $filters) { identifier } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_apps_filter_by_exact_identifiers() -> None:
    def setup() -> HttpContext:
        membership, context = _member()
        for identifier in [
            "com.example.napari",
            "com.example.napari-plugin",
            "com.example.fiji",
        ]:
            factories.make_app(
                organization=membership.organization, identifier=identifier
            )
        return context

    context = await sync_to_async(setup)()
    data = await _data(
        context,
        APPS,
        filters={"identifiers": ["com.example.napari", "com.example.fiji"]},
    )
    assert sorted(a["identifier"] for a in data["apps"]) == [
        "com.example.fiji",
        "com.example.napari",
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_app_and_device_search_ignores_case_and_matches_identifiers() -> None:
    def setup() -> HttpContext:
        membership, context = _member()
        factories.make_app(
            organization=membership.organization,
            name="Napari Viewer",
            identifier="org.napari.viewer",
        )
        factories.make_app(
            organization=membership.organization, name="Fiji", identifier="sc.fiji"
        )
        fakts_models.Device.objects.create(
            organization=membership.organization, node_id="n1", name="Lab Laptop"
        )
        return context

    context = await sync_to_async(setup)()
    assert [
        a["identifier"]
        for a in (await _data(context, APPS, filters={"search": "napari viewer"}))[
            "apps"
        ]
    ] == ["org.napari.viewer"]
    assert [
        a["identifier"]
        for a in (await _data(context, APPS, filters={"search": "SC.FIJI"}))["apps"]
    ] == ["sc.fiji"]
    assert (
        len(
            (await _data(context, DEVICES, filters={"search": "lab laptop"}))["devices"]
        )
        == 1
    )


INVITES = "query ($filters: InviteFilter) { invites(filters: $filters) { id } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_invites_filter_by_granted_collaboration_and_validity() -> None:
    def setup() -> tuple[
        HttpContext,
        models.Invite,
        models.Invite,
        models.Invite,
        models.Invite,
        models.Invite,
        models.Invite,
    ]:
        owner = factories.make_user()
        organization = factories.make_organization(owner=owner)
        membership = factories.make_membership(user=owner, organization=organization)
        context = build_auth_context(
            owner, organization, factories.make_client(membership=membership)
        )

        def invite(
            collaborations: list[dict[str, object]] | None, **kw: Any
        ) -> models.Invite:
            return models.Invite.objects.create(
                created_by=owner,
                created_for=organization,
                shared_collaborations=collaborations,
                **kw,
            )

        microscope = invite(
            [{"instance": "7", "collaboration": "12", "roles": ["editor"]}]
        )
        both = invite(
            [
                {"instance": "7", "collaboration": "3", "roles": []},
                {"instance": "7", "collaboration": "12", "roles": []},
            ]
        )
        elsewhere = invite([{"instance": "8", "collaboration": "12", "roles": []}])
        expired = invite(
            [{"instance": "7", "collaboration": "12", "roles": []}],
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )
        accepted = invite(
            [{"instance": "7", "collaboration": "12", "roles": []}],
            status=models.Invite.Status.ACCEPTED,
        )
        membership_invite = invite(None)
        return (
            context,
            microscope,
            both,
            elsewhere,
            expired,
            accepted,
            membership_invite,
        )

    (
        context,
        microscope,
        both,
        elsewhere,
        expired,
        accepted,
        membership_invite,
    ) = await sync_to_async(setup)()

    granting = await _data(
        context,
        INVITES,
        filters={"collaboration": {"instance": "7", "collaboration": "12"}},
    )
    assert {i["id"] for i in granting["invites"]} == {
        str(x.pk) for x in [microscope, both, expired, accepted]
    }

    valid = await _data(
        context,
        INVITES,
        filters={
            "collaboration": {"instance": "7", "collaboration": "12"},
            "valid": True,
        },
    )
    assert {i["id"] for i in valid["invites"]} == {str(microscope.pk), str(both.pk)}

    invalid = await _data(context, INVITES, filters={"valid": False})
    assert {i["id"] for i in invalid["invites"]} == {str(expired.pk), str(accepted.pk)}

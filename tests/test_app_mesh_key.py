"""Apps asking for a mesh key on the app-authorization grant.

An app sets ``request_auth_key`` on ``/o/app-authorization/``; accepting mints a
key for the organization's mesh, and the device-code token response hands it out
once as ``auth.ionscale_auth_key``. Re-grants of the same installation go through
one ``AppMeshEnrollment``, so re-authenticating never accumulates keys or nodes.
"""

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

from api.management.mutations.device_code import (
    AcceptDeviceCodeInput,
    accept_device_code,
)
from api.management.types import ManagementClient
from fakts import models
from fakts.services.hubs import APP_MESH_KEY_EXPIRY_SECONDS
from ionscale.base_models import Machine
from ionscale.testing import FakeIonscaleRepository
from karakter.models import Membership, Organization
from tests import factories

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _start(
    client: Client,
    *,
    version: str = "1.0.0",
    device_id: str = "laptop-1",
    request_auth_key: bool = True,
) -> dict[str, Any]:
    manifest = {
        "identifier": "com.example.meshapp",
        "version": version,
        "scopes": [],
        "requirements": [],
    }
    if device_id:
        manifest["device_id"] = device_id
    payload = {
        "manifest": manifest,
        "requested_client_kind": "development",
        "requested_client_role": "agent",
        "request_auth_key": request_auth_key,
    }
    resp = client.post(
        reverse("app_authorization"),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    return resp.json()


def _poll(client: Client, body: dict[str, Any]) -> "TestResponse":
    return client.post(
        reverse("token"),
        data={
            "grant_type": DEVICE_CODE_GRANT,
            "device_code": body["device_code"],
            "client_id": body["client_id"],
        },
        secure=True,
    )


def _accept(
    body: dict[str, Any],
    membership: Membership,
    hub: models.Hub,
    *,
    allow_ionscale: bool = True,
) -> ManagementClient:
    device_code = models.DeviceCode.objects.get(secret=body["device_code"])
    info = SimpleNamespace(
        context=SimpleNamespace(request=SimpleNamespace(user=membership.user))
    )
    return accept_device_code(
        info,
        AcceptDeviceCodeInput(
            device_code=str(device_code.pk),
            code=device_code.code,
            hub=str(hub.pk),
            allow_ionscale=allow_ionscale,
        ),
    )


def _grant(
    client: Client, membership: Membership, hub: models.Hub, **start: Any
) -> dict[str, Any]:
    allow_ionscale = start.pop("allow_ionscale", True)
    body = _start(client, **start)
    _accept(body, membership, hub, allow_ionscale=allow_ionscale)
    resp = _poll(client, body)
    assert resp.status_code == 200, resp.content
    return resp.json()


@dataclass
class MeshOrg:
    organization: Organization
    layer: models.IonscaleLayer
    membership: Membership
    hub: models.Hub


@pytest.fixture
def mesh_org() -> MeshOrg:
    """A plain (non-admin) member of an organization that has a mesh, plus a hub."""
    organization = factories.make_organization()
    layer = models.IonscaleLayer.objects.create(
        organization=organization,
        name="Default",
        kind="ionscale",
        identifier=organization.slug,
        tailnet_name=organization.slug,
    )
    membership = factories.make_membership(organization=organization)
    hub = factories.make_hub(organization=organization)
    return MeshOrg(
        organization=organization, layer=layer, membership=membership, hub=hub
    )


@pytest.mark.django_db
def test_requested_key_rides_the_initial_token_response_once(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg
) -> None:
    token = _grant(client, mesh_org.membership, mesh_org.hub)

    assert token["mesh"] == {
        "ionscale_auth_key": ionscale_repo.auth_key,
        "ionscale_coord_url": settings.IONSCALE_COORD_URL,
    }
    # What the app keys its persisted logins by: the same values as the token's
    # `sub` / `org` claims, and the hub its client is bound to.
    identity = {
        "sub": str(mesh_org.membership.user_id),
        "organization": str(mesh_org.organization.pk),
        "hub": str(mesh_org.hub.pk),
    }
    assert {k: token["self"][k] for k in identity} == identity
    enrollment = models.AppMeshEnrollment.objects.get()
    assert enrollment.membership == mesh_org.membership
    minted = ionscale_repo.created_auth_keys[-1]
    assert minted["tailnet"] == mesh_org.layer.tailnet_name
    # Sidecar tag only: tag:mesh-<org> is for member-reachable machines.
    assert minted["tags"] == [enrollment.tag]
    assert minted["expiry_seconds"] == APP_MESH_KEY_EXPIRY_SECONDS
    assert minted["ephemeral"] is False

    # Never on refresh.
    refreshed = client.post(
        reverse("token"),
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": token["client_id"],
        },
        secure=True,
    ).json()
    assert refreshed["refresh_token"]
    assert "mesh" not in refreshed
    # The identity is in `self` on every response, refreshes included.
    assert {k: refreshed["self"][k] for k in identity} == identity


@pytest.mark.django_db
@pytest.mark.parametrize(
    "request_auth_key, allow_ionscale", [(False, True), (True, False)]
)
def test_no_key_unless_requested_and_allowed(
    client: Client,
    ionscale_repo: FakeIonscaleRepository,
    mesh_org: MeshOrg,
    request_auth_key: bool,
    allow_ionscale: bool,
) -> None:
    token = _grant(
        client,
        mesh_org.membership,
        mesh_org.hub,
        request_auth_key=request_auth_key,
        allow_ionscale=allow_ionscale,
    )
    assert "mesh" not in token
    # The identity does not depend on a mesh key: it is in `self` either way.
    assert token["self"]["sub"] == str(mesh_org.membership.user_id)
    assert token["self"]["organization"] == str(mesh_org.organization.pk)
    assert token["self"]["hub"] == str(mesh_org.hub.pk)
    assert ionscale_repo.created_auth_keys == []
    assert not models.AppMeshEnrollment.objects.exists()


@pytest.mark.django_db
def test_org_without_mesh_still_authorizes_the_app(
    client: Client, ionscale_repo: FakeIonscaleRepository
) -> None:
    hub = factories.make_hub()
    membership = factories.make_membership(organization=hub.organization)
    token = _grant(client, membership, hub)
    assert token["access_token"]
    assert "mesh" not in token
    assert ionscale_repo.created_auth_keys == []


@pytest.mark.django_db
def test_ionscale_failure_still_authorizes_the_app(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg
) -> None:
    from ionscale.errors import IonscaleError

    ionscale_repo.fail_with["create_auth_key"] = IonscaleError("unavailable", "down")
    token = _grant(client, mesh_org.membership, mesh_org.hub)
    assert token["access_token"]
    assert "mesh" not in token


@pytest.mark.django_db
def test_regrant_reuses_the_enrollment_and_revokes_the_previous_key(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg
) -> None:
    ionscale_repo.unique_auth_keys = True
    first = _grant(client, mesh_org.membership, mesh_org.hub)
    # An upgrade is the same installation: enrollment keys on the app, not the release.
    second = _grant(client, mesh_org.membership, mesh_org.hub, version="2.0.0")

    assert first["mesh"]["ionscale_auth_key"] != second["mesh"]["ionscale_auth_key"]
    enrollment = models.AppMeshEnrollment.objects.get()
    assert enrollment.auth_key.key == second["mesh"]["ionscale_auth_key"]
    assert ionscale_repo.deleted_auth_keys == [
        (mesh_org.layer.tailnet_name, first["mesh"]["ionscale_auth_key"])
    ]
    # Only the live key is kept on our side too.
    assert list(models.IonscaleAuthKey.objects.values_list("key", flat=True)) == [
        second["mesh"]["ionscale_auth_key"]
    ]


@pytest.mark.django_db
def test_regrant_prunes_stale_nodes_but_keeps_the_newest_and_the_online(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg
) -> None:
    _grant(client, mesh_org.membership, mesh_org.hub)
    tag = models.AppMeshEnrollment.objects.get().tag
    other = "tag:app-999999"
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="100", name="orphan-offline", tags=[tag], connected=False),
        Machine(id="200", name="other-online", tags=[tag], connected=True),
        # Newest, but offline: the node the app is about to re-register as.
        Machine(id="300", name="current-offline", tags=[tag], connected=False),
        Machine(id="50", name="someone-else", tags=[other], connected=False),
    ]

    _grant(client, mesh_org.membership, mesh_org.hub)

    assert ionscale_repo.deleted_machines == ["100"]
    assert models.AppMeshEnrollment.objects.count() == 1


@pytest.mark.django_db
def test_without_device_id_nodes_are_never_pruned(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg
) -> None:
    _grant(client, mesh_org.membership, mesh_org.hub, device_id=None)
    tag = models.AppMeshEnrollment.objects.get().tag
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="100", name="a", tags=[tag], connected=False),
        Machine(id="200", name="b", tags=[tag], connected=False),
    ]

    _grant(client, mesh_org.membership, mesh_org.hub, device_id=None)

    # One enrollment even though device is NULL (nulls_distinct=False).
    assert models.AppMeshEnrollment.objects.count() == 1
    assert ionscale_repo.deleted_machines == []


@pytest.mark.django_db
def test_different_devices_get_separate_enrollments(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg
) -> None:
    _grant(client, mesh_org.membership, mesh_org.hub, device_id="laptop-1")
    _grant(client, mesh_org.membership, mesh_org.hub, device_id="laptop-2")
    assert models.AppMeshEnrollment.objects.count() == 2
    assert ionscale_repo.deleted_auth_keys == []


def _seed_nodes(
    ionscale_repo: FakeIonscaleRepository, mesh_org: MeshOrg, tag: str
) -> None:
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(
            id="100",
            name="app-node",
            tags=[f"tag:mesh-{mesh_org.organization.pk}", tag],
            connected=True,
        ),
        Machine(id="200", name="someone-else", tags=["tag:app-999999"], connected=True),
    ]


@pytest.mark.django_db
def test_leaving_the_organization_removes_the_apps_mesh_nodes(
    client: Client,
    ionscale_repo: FakeIonscaleRepository,
    mesh_org: MeshOrg,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    """Key-joined nodes belong to the tailnet's service user, so account
    revocation alone would leave them behind."""
    ionscale_repo.unique_auth_keys = True
    token = _grant(client, mesh_org.membership, mesh_org.hub)
    _seed_nodes(ionscale_repo, mesh_org, models.AppMeshEnrollment.objects.get().tag)

    with django_capture_on_commit_callbacks(execute=True):
        mesh_org.membership.delete()

    assert not models.AppMeshEnrollment.objects.exists()
    assert ionscale_repo.deleted_machines == ["100"]
    assert (
        mesh_org.layer.tailnet_name,
        token["mesh"]["ionscale_auth_key"],
    ) in ionscale_repo.deleted_auth_keys


@pytest.mark.django_db
def test_deactivation_removes_the_apps_mesh_nodes(
    client: Client,
    ionscale_repo: FakeIonscaleRepository,
    mesh_org: MeshOrg,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    _grant(client, mesh_org.membership, mesh_org.hub)
    _seed_nodes(ionscale_repo, mesh_org, models.AppMeshEnrollment.objects.get().tag)

    user = mesh_org.membership.user
    user.is_active = False
    with django_capture_on_commit_callbacks(execute=True):
        user.save()

    assert ionscale_repo.deleted_machines == ["100"]


@pytest.mark.django_db
def test_deleting_the_organization_leaves_node_cleanup_to_the_tailnet_teardown(
    client: Client,
    ionscale_repo: FakeIonscaleRepository,
    mesh_org: MeshOrg,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    _grant(client, mesh_org.membership, mesh_org.hub)
    _seed_nodes(ionscale_repo, mesh_org, models.AppMeshEnrollment.objects.get().tag)

    with django_capture_on_commit_callbacks(execute=True):
        mesh_org.organization.delete()

    assert ionscale_repo.deleted_machines == []
    assert (mesh_org.layer.tailnet_name, True) in ionscale_repo.deleted_tailnets


@pytest.mark.django_db(transaction=True)
def test_approval_is_not_redeemable_until_the_mesh_key_is_attached(
    client: Client,
    ionscale_repo: FakeIonscaleRepository,
    mesh_org: MeshOrg,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approve + mint is one transaction, so a poll can't redeem the code before the key lands.

    Regression: `validate_device_code` committed on its own, the device's poll
    redeemed (and burned) the code while ionscale minted the key, and the key's
    save then raised `NotUpdated`, so kontrol showed "access denied" for an app
    that had in fact been authorized, without its mesh key.
    """
    from django.db import connection

    from fakts import logic

    seen = {}
    real_enroll = logic.enroll_app_on_mesh

    def enroll(**kwargs: Any) -> models.IonscaleAuthKey | None:
        seen["in_atomic_block"] = connection.in_atomic_block
        return real_enroll(**kwargs)

    monkeypatch.setattr(logic, "enroll_app_on_mesh", enroll)
    token = _grant(client, mesh_org.membership, mesh_org.hub)

    assert seen["in_atomic_block"] is True
    assert token["mesh"]["ionscale_auth_key"] == ionscale_repo.auth_key


@pytest.mark.django_db
def test_code_burned_during_minting_does_not_fail_the_approval(
    client: Client,
    ionscale_repo: FakeIonscaleRepository,
    mesh_org: MeshOrg,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fakts import logic

    body = _start(client)
    real_enroll = logic.enroll_app_on_mesh

    def enroll_then_burn(**kwargs: Any) -> models.IonscaleAuthKey | None:
        key = real_enroll(**kwargs)
        models.DeviceCode.objects.filter(secret=body["device_code"]).delete()
        return key

    monkeypatch.setattr(logic, "enroll_app_on_mesh", enroll_then_burn)
    assert _accept(body, mesh_org.membership, mesh_org.hub) is not None

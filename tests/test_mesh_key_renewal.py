"""A client fetching a fresh key for the mesh sidecar it was granted.

``POST /f/meshkey/`` is for an app or hub that lost its mesh state: it must not
need a human again, and it must not get anything an approval did not grant.
See ``fakts.services.hubs.renew_mesh_key``.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from authapp.models import OAuth2Token
from fakts import models
from fakts.services.hubs import MESH_KEY_RENEWAL_INTERVAL_SECONDS
from ionscale.base_models import Machine
from tests import factories
from tests.test_app_mesh_key import _grant as grant_app
from tests.test_hub_mesh import _grant as grant_hub


@pytest.fixture
def org(ionscale_repo):
    ionscale_repo.unique_auth_keys = True
    organization = factories.make_organization()
    layer = models.IonscaleLayer.objects.create(
        organization=organization, name="Default", kind="ionscale",
        identifier=organization.slug, tailnet_name=organization.slug,
    )
    admin = factories.make_membership(user=organization.owner, organization=organization)
    member = factories.make_membership(organization=organization)
    hub = factories.make_hub(organization=organization)
    return SimpleNamespace(
        organization=organization, layer=layer, member=member, hub=hub,
        hub_granter=SimpleNamespace(organization=organization, membership=admin),
    )


def _renew(client, token):
    return client.post(reverse("fakts:meshkey"), HTTP_AUTHORIZATION=f"Bearer {token['access_token']}", secure=True)


def _let_the_key_age():
    old = timezone.now() - timedelta(seconds=MESH_KEY_RENEWAL_INTERVAL_SECONDS + 1)
    models.IonscaleAuthKey.objects.update(created_at=old)


@pytest.mark.django_db
def test_a_live_app_gets_a_fresh_key_for_the_same_sidecar(client, ionscale_repo, org):
    token = grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    # The node it lost the state of: offline.
    ionscale_repo.machines_by_tailnet[org.layer.tailnet_name] = [Machine(id="100", name="app", tags=[enrollment.tag], connected=False)]
    _let_the_key_age()

    resp = _renew(client, token)

    assert resp.status_code == 200, resp.content
    mesh = resp.json()["mesh"]
    assert mesh["ionscale_coord_url"] == settings.IONSCALE_COORD_URL
    assert mesh["ionscale_auth_key"] != token["mesh"]["ionscale_auth_key"]
    assert ionscale_repo.created_auth_keys[-1]["tags"] == [enrollment.tag]
    # The previous key is revoked, and no second enrollment appears.
    assert (org.layer.tailnet_name, token["mesh"]["ionscale_auth_key"]) in ionscale_repo.deleted_auth_keys
    assert models.AppMeshEnrollment.objects.get().pk == enrollment.pk


@pytest.mark.django_db
def test_a_revoked_client_gets_no_key_although_its_access_token_still_verifies(client, ionscale_repo, org):
    token = grant_app(client, org.member, org.hub)
    _let_the_key_age()
    OAuth2Token.objects.filter(client_id=token["client_id"]).update(revoked=True)
    minted = len(ionscale_repo.created_auth_keys)

    resp = _renew(client, token)

    assert resp.status_code == 401
    assert resp.json()["reason"] == "not_live"
    assert len(ionscale_repo.created_auth_keys) == minted


@pytest.mark.django_db
def test_an_app_that_was_not_granted_mesh_access_cannot_take_it(client, ionscale_repo, org):
    token = grant_app(client, org.member, org.hub, allow_ionscale=False)

    resp = _renew(client, token)

    assert resp.status_code == 403
    assert resp.json()["reason"] == "not_enrolled"
    assert ionscale_repo.created_auth_keys == []


@pytest.mark.django_db
def test_an_app_without_a_device_must_be_approved_again(client, ionscale_repo, org):
    token = grant_app(client, org.member, org.hub, device_id=None)
    _let_the_key_age()
    minted = len(ionscale_repo.created_auth_keys)

    resp = _renew(client, token)

    assert resp.status_code == 403
    assert len(ionscale_repo.created_auth_keys) == minted


@pytest.mark.django_db
def test_no_key_while_the_sidecars_node_is_online(client, ionscale_repo, org):
    token = grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    ionscale_repo.machines_by_tailnet[org.layer.tailnet_name] = [Machine(id="100", name="app", tags=[enrollment.tag], connected=True)]
    _let_the_key_age()
    minted = len(ionscale_repo.created_auth_keys)

    resp = _renew(client, token)

    assert resp.status_code == 409
    assert resp.json()["reason"] == "connected"
    assert len(ionscale_repo.created_auth_keys) == minted


@pytest.mark.django_db
def test_renewal_is_throttled(client, ionscale_repo, org):
    token = grant_app(client, org.member, org.hub)

    # Right after the approval the key is brand new.
    assert _renew(client, token).status_code == 429
    _let_the_key_age()
    assert _renew(client, token).status_code == 200
    assert _renew(client, token).status_code == 429


@pytest.mark.django_db
def test_a_hub_renews_its_own_key(client, ionscale_repo, org):
    hub, token = grant_hub(client, org.hub_granter)
    _let_the_key_age()

    resp = _renew(client, token)

    assert resp.status_code == 200, resp.content
    assert ionscale_repo.created_auth_keys[-1]["tags"] == [hub.mesh_tag]
    hub.refresh_from_db()
    assert hub.auth_key.key == resp.json()["mesh"]["ionscale_auth_key"]


@pytest.mark.django_db
def test_no_token_no_key(client, ionscale_repo, org):
    assert client.post(reverse("fakts:meshkey"), secure=True).status_code == 401


@pytest.mark.django_db
def test_discovery_names_the_endpoint(client):
    data = client.get("/lok/.well-known/fakts").json()
    assert data["mesh_key_endpoint"].endswith("/f/meshkey/")

"""A hub's manifest can ask for clients of its own: approving the hub approves them.

Each request brings the mandate key the hub already wrote into the app's environment;
approving the hub issues a mandate under it, pinned to the app but not to a version.
lok keeps only the key's hash and names it (``key_sha256``) in the hub's envelope. This
is how a hub runs an app beside its services (a plugin engine) without anyone
approving that app a second time.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest
from django.urls import reverse
from pydantic import ValidationError

from fakts import base_models, models
from fakts.services.hubs import create_hub_from_manifest
from tests import factories

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GRANT = "urn:fakts:grant-type:mandate-key"

ENGINE = {
    "identifier": "plugin_engine",
    "version": "1.0.0",
    # The test organization has no scopes and no services: a login asking for any fails.
    "scopes": [],
    "requirements": [],
}
PROPOSED = "p" * 43


def _hub_manifest(*clients, identifier="enginehub"):
    return {"identifier": identifier, "instances": [], "clients": list(clients)}


def _engine_request(**overrides):
    return {"identifier": "engine", "manifest": ENGINE, "mandate_key": PROPOSED, **overrides}


def _approve(client, hub_manifest, membership=None):
    """Run the whole hub grant: start, accept as an org admin, poll the tokens."""
    from api.management.mutations.hub_device_code import AcceptHubDeviceCodeInput, accept_hub_device_code

    body = client.post(
        reverse("hub_authorization"), data=json.dumps({"hub": hub_manifest}), content_type="application/json"
    ).json()
    code = models.DeviceCode.objects.get(secret=body["device_code"])
    if membership is None:
        org = factories.make_organization()
        membership = factories.make_membership(user=org.owner, organization=org)
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=membership.user)))
    hub = accept_hub_device_code(
        info,
        AcceptHubDeviceCodeInput(
            device_code=str(code.id),
            code=code.code,
            organization=str(membership.organization.id),
            allow_ionscale=False,
        ),
    )
    response = client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": body["device_code"], "client_id": body["client_id"]},
        secure=True,
    )
    assert response.status_code == 200, response.json()
    return response.json(), membership, hub


def _login(client, key, manifest):
    return client.post(
        reverse("token"),
        data={"grant_type": GRANT, "key": key, "manifest": json.dumps(manifest)},
        secure=True,
    )


def _sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _refresh(client, hub_token):
    return client.post(
        reverse("token"),
        data={
            "grant_type": "refresh_token",
            "refresh_token": hub_token["refresh_token"],
            "client_id": hub_token["client_id"],
        },
        secure=True,
    ).json()


@pytest.mark.django_db
def test_approving_a_hub_approves_the_clients_it_asked_for(client):
    hub_token, membership, hub = _approve(client, _hub_manifest(_engine_request()))

    request = hub_token["client_requests"]["engine"]
    assert request["key_sha256"] == _sha256(PROPOSED)
    assert request["client_ids"] == []
    assert PROPOSED not in json.dumps(hub_token)

    logged_in = _login(client, PROPOSED, ENGINE)
    assert logged_in.status_code == 200, logged_in.json()

    app = models.Client.objects.get(client_id=logged_in.json()["client_id"])
    # The app acts as the one who approved the hub, inside that hub.
    assert app.membership == membership
    assert app.hub_id == hub.id
    # The envelope says the request has become a client.
    assert _refresh(client, hub_token)["client_requests"]["engine"]["client_ids"] == [app.client_id]


def test_a_hub_must_propose_a_real_secret():
    with pytest.raises(ValidationError):
        base_models.ClientRequest(identifier="engine", manifest=ENGINE, mandate_key="short")
    with pytest.raises(ValidationError):
        base_models.ClientRequest(identifier="engine", manifest=ENGINE)


@pytest.mark.django_db
def test_the_next_release_of_the_app_logs_in_on_the_same_key_as_the_same_client(client):
    _approve(client, _hub_manifest(_engine_request()))

    first = _login(client, PROPOSED, ENGINE)
    upgraded = _login(client, PROPOSED, {**ENGINE, "version": "1.1.0"})

    assert first.status_code == 200
    assert upgraded.status_code == 200, upgraded.json()
    assert first.json()["client_id"] == upgraded.json()["client_id"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"identifier": "someone_else"}, "pinned to app"),
        ({"scopes": ["read"]}, "scope"),
        ({"requirements": [{"key": "mikro", "service": "live.arkitekt.mikro"}]}, "requirement"),
    ],
)
def test_the_key_is_only_good_for_the_app_the_hub_asked_for(client, change, fragment):
    _approve(client, _hub_manifest(_engine_request()))

    response = _login(client, PROPOSED, {**ENGINE, **change})

    assert response.status_code == 400
    assert fragment in response.json()["error_description"]


@pytest.mark.django_db
def test_a_pinned_device_must_be_the_one_that_logs_in(client):
    _approve(client, _hub_manifest(_engine_request(manifest={**ENGINE, "device_id": "hub-a"})))

    assert _login(client, PROPOSED, {**ENGINE, "device_id": "hub-b"}).status_code == 400
    assert _login(client, PROPOSED, {**ENGINE, "device_id": "hub-a"}).status_code == 200


@pytest.mark.django_db
def test_approving_again_with_the_same_key_keeps_the_client(client):
    _, membership, hub = _approve(client, _hub_manifest(_engine_request()))
    first = _login(client, PROPOSED, ENGINE).json()

    _approve(client, _hub_manifest(_engine_request()), membership=membership)

    assert models.Mandate.objects.filter(hub=hub, hub_request="engine").count() == 1
    assert _login(client, PROPOSED, ENGINE).json()["client_id"] == first["client_id"]


@pytest.mark.django_db
def test_approving_again_with_a_new_key_supersedes_the_old_one(client):
    _, membership, hub = _approve(client, _hub_manifest(_engine_request()))
    first = _login(client, PROPOSED, ENGINE).json()
    renewed = "r" * 43

    hub_token, _, _ = _approve(client, _hub_manifest(_engine_request(mandate_key=renewed)), membership=membership)

    assert hub_token["client_requests"]["engine"]["key_sha256"] == _sha256(renewed)
    assert models.Mandate.objects.get(hub=hub, hub_request="engine", revoked_at__isnull=False).revocation_reason == "superseded"
    assert not models.Client.objects.filter(client_id=first["client_id"]).exists()
    assert _login(client, PROPOSED, ENGINE).status_code == 400
    assert _login(client, renewed, ENGINE).status_code == 200


@pytest.mark.django_db
def test_a_client_the_hub_no_longer_asks_for_stops_working(client):
    _, membership, hub = _approve(client, _hub_manifest(_engine_request()))
    logged_in = _login(client, PROPOSED, ENGINE).json()

    hub_token, _, _ = _approve(client, _hub_manifest(), membership=membership)

    assert hub_token["client_requests"] == {}
    assert not models.Client.objects.filter(client_id=logged_in["client_id"]).exists()
    assert _login(client, PROPOSED, ENGINE).status_code == 400


@pytest.mark.django_db
def test_deleting_the_hub_kills_its_clients_keys(client):
    _, _, hub = _approve(client, _hub_manifest(_engine_request()))
    assert _login(client, PROPOSED, ENGINE).status_code == 200

    models.Hub.objects.filter(pk=hub.pk).delete()

    assert _login(client, PROPOSED, ENGINE).status_code == 400


@pytest.mark.django_db
def test_a_preconfigured_hub_grants_its_clients_as_the_organizations_owner(client):
    org = factories.make_organization()
    factories.make_membership(user=org.owner, organization=org)
    manifest = base_models.HubManifest(**_hub_manifest(_engine_request(), identifier="local"))

    hub = create_hub_from_manifest(manifest=manifest, organization=org)

    mandate = models.Mandate.objects.get(hub=hub, hub_request="engine")
    assert mandate.membership.user == org.owner
    assert mandate.key_hash == _sha256(PROPOSED)
    assert mandate.pin.version is None
    response = _login(client, PROPOSED, ENGINE)
    assert response.status_code == 200, response.json()

"""A hub's manifest can ask for clients of its own: approving the hub approves them.

Each request gets a redeem token of the hub's, pinned to the app but not to a version,
and handed over in the hub's envelope. This is how a hub runs an app beside its
services (a plugin engine) without anyone approving that app a second time.
"""

import json
from types import SimpleNamespace

import pytest
from django.urls import reverse
from pydantic import ValidationError

from fakts import base_models, models
from fakts.services.hubs import create_hub_from_manifest
from tests import factories

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REDEEM_GRANT = "urn:fakts:grant-type:redeem"

ENGINE = {
    "identifier": "plugin_engine",
    "version": "1.0.0",
    # The test organization has no scopes and no services: a redeem asking for any fails.
    "scopes": [],
    "requirements": [],
}
PROPOSED = "p" * 43


def _hub_manifest(*clients, identifier="enginehub"):
    return {"identifier": identifier, "instances": [], "clients": list(clients)}


def _engine_request(**overrides):
    return {"identifier": "engine", "manifest": ENGINE, **overrides}


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


def _redeem(client, token, manifest):
    return client.post(
        reverse("token"),
        data={"grant_type": REDEEM_GRANT, "redeem_token": token, "manifest": json.dumps(manifest)},
        secure=True,
    )


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
def test_approving_a_hub_hands_it_a_token_for_each_client_it_asked_for(client):
    hub_token, membership, hub = _approve(client, _hub_manifest(_engine_request()))

    request = hub_token["client_requests"]["engine"]
    assert request["client_id"] is None

    redeemed = _redeem(client, request["redeem_token"], ENGINE)
    assert redeemed.status_code == 200, redeemed.json()

    app = models.Client.objects.get(client_id=redeemed.json()["client_id"])
    # The app acts as the one who approved the hub, inside that hub.
    assert app.membership == membership
    assert app.hub_id == hub.id
    # The envelope says the request has become a client.
    assert _refresh(client, hub_token)["client_requests"]["engine"]["client_id"] == app.client_id


@pytest.mark.django_db
def test_a_hub_can_bring_the_token_it_already_wrote_into_the_apps_environment(client):
    hub_token, _, _ = _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED)))

    assert hub_token["client_requests"]["engine"]["redeem_token"] == PROPOSED
    response = _redeem(client, PROPOSED, ENGINE)
    assert response.status_code == 200, response.json()


def test_a_proposed_token_must_be_a_real_secret():
    with pytest.raises(ValidationError):
        base_models.ClientRequest(identifier="engine", manifest=ENGINE, redeem_token="short")


@pytest.mark.django_db
def test_the_next_release_of_the_app_logs_in_on_the_same_token(client):
    _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED)))

    first = _redeem(client, PROPOSED, ENGINE)
    upgraded = _redeem(client, PROPOSED, {**ENGINE, "version": "1.1.0"})

    assert first.status_code == 200
    assert upgraded.status_code == 200, upgraded.json()


@pytest.mark.django_db
def test_a_redeem_after_a_lost_session_gets_the_same_client_back(client):
    _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED)))

    first = _redeem(client, PROPOSED, ENGINE).json()
    again = _redeem(client, PROPOSED, ENGINE).json()

    assert first["client_id"] == again["client_id"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"identifier": "someone_else"}, "pinned to app"),
        ({"scopes": ["read"]}, "scope"),
        ({"requirements": [{"key": "mikro", "service": "live.arkitekt.mikro"}]}, "requirement"),
    ],
)
def test_the_token_is_only_good_for_the_app_the_hub_asked_for(client, change, fragment):
    _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED)))

    response = _redeem(client, PROPOSED, {**ENGINE, **change})

    assert response.status_code == 400
    assert fragment in response.json()["error_description"]


@pytest.mark.django_db
def test_a_pinned_device_must_be_the_one_that_redeems(client):
    _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED, manifest={**ENGINE, "device_id": "hub-a"})))

    assert _redeem(client, PROPOSED, {**ENGINE, "device_id": "hub-b"}).status_code == 400
    assert _redeem(client, PROPOSED, {**ENGINE, "device_id": "hub-a"}).status_code == 200


@pytest.mark.django_db
def test_approving_again_takes_a_new_secret_and_keeps_one_token(client):
    _, membership, hub = _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED)))
    renewed = "r" * 43

    hub_token, _, _ = _approve(client, _hub_manifest(_engine_request(redeem_token=renewed)), membership=membership)

    assert hub_token["client_requests"]["engine"]["redeem_token"] == renewed
    assert models.RedeemToken.objects.filter(hub=hub, hub_client="engine").count() == 1
    assert _redeem(client, PROPOSED, ENGINE).status_code == 400
    assert _redeem(client, renewed, ENGINE).status_code == 200


@pytest.mark.django_db
def test_a_client_the_hub_no_longer_asks_for_loses_its_token(client):
    _, membership, hub = _approve(client, _hub_manifest(_engine_request(redeem_token=PROPOSED)))
    redeemed = _redeem(client, PROPOSED, ENGINE).json()

    hub_token, _, _ = _approve(client, _hub_manifest(), membership=membership)

    assert hub_token["client_requests"] == {}
    assert not models.RedeemToken.objects.filter(hub=hub, hub_client__isnull=False).exists()
    # What was redeemed lives on its own refresh chain; only the token is gone.
    assert models.Client.objects.filter(client_id=redeemed["client_id"]).exists()
    assert _redeem(client, PROPOSED, ENGINE).status_code == 400


@pytest.mark.django_db
def test_a_preconfigured_hub_mints_its_client_tokens_for_the_organizations_owner(client):
    org = factories.make_organization()
    manifest = base_models.HubManifest(**_hub_manifest(_engine_request(redeem_token=PROPOSED), identifier="local"))

    hub = create_hub_from_manifest(manifest=manifest, organization=org)

    token = models.RedeemToken.objects.get(hub=hub, hub_client="engine")
    assert token.user == org.owner
    assert token.token == PROPOSED
    assert "version" not in token.pinned_manifest
    response = _redeem(client, PROPOSED, ENGINE)
    assert response.status_code == 200, response.json()

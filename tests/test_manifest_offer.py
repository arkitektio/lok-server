"""An app that changes sends its manifest with its refresh (``offer_manifest``).

The installation keeps its client and its session. When the new manifest asks
for nothing more than a person approved, the client moves to the new release by
itself, and from then on its tokens carry an identity of that release
(``fakts.ClientIdentity``): the services remember a caller by the ``client_id``
claim together with the release they first saw it with, so what the client did
as the earlier version must stay that version's. When it asks for more, nothing
changes and the app is told that a person has to approve it.
"""

import json

import pytest
from django.conf import settings
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import RSAKey

from fakts import models
from fakts.services.device_codes import validate_device_code
from tests import factories

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

V1 = {
    "identifier": "com.example.updates",
    "version": "1.0.0",
    "scopes": ["openid"],
    "requirements": [],
    "device_id": "bench-pc",
}
V2 = {**V1, "version": "1.1.0"}


def _decode(access_token: str) -> dict:
    return jwt.decode(access_token, RSAKey.import_key(settings.PUBLIC_KEY), algorithms=["RS256"]).claims


def _login(client, manifest=V1, user=None, hub=None):
    """Device-code login as an approved app; returns (token response, hub, user)."""
    body = client.post(
        reverse("app_authorization"),
        data=json.dumps({"manifest": manifest, "requested_client_kind": "desktop"}),
        content_type="application/json",
    ).json()
    hub = hub or factories.make_hub()
    if user is None:
        user = factories.make_user()
        factories.make_membership(user=user, organization=hub.organization)
    validate_device_code(models.DeviceCode.objects.get(secret=body["device_code"]), user=user, organization=hub.organization, hub=hub)
    token = client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": body["device_code"], "client_id": body["client_id"]},
        secure=True,
    ).json()
    return token, hub, user


def _refresh(client, token, manifest=None):
    data = {"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": token["client_id"]}
    if manifest is not None:
        data["manifest"] = json.dumps(manifest)
    response = client.post(reverse("token"), data=data, secure=True)
    assert response.status_code == 200, response.content
    return response.json()


@pytest.mark.django_db
def test_the_same_manifest_changes_nothing(client):
    token, _, _ = _login(client)

    refreshed = _refresh(client, token, V1)

    assert "manifest_status" not in refreshed
    assert refreshed["client_id"] == token["client_id"]
    assert _decode(refreshed["access_token"])["client_id"] == token["client_id"]
    assert models.ClientIdentity.objects.count() == 0


@pytest.mark.django_db
def test_a_new_version_moves_the_client_and_gives_it_a_new_identity(client):
    token, _, _ = _login(client)
    before = _decode(token["access_token"])

    refreshed = _refresh(client, token, V2)
    claims = _decode(refreshed["access_token"])

    # The installation is the same client, with the same credential.
    assert models.Client.objects.filter(membership__isnull=False).count() == 1
    assert refreshed["client_id"] == token["client_id"]
    fakts_client = models.Client.objects.get(client_id=token["client_id"])
    assert fakts_client.release.version == "1.1.0"

    # To the services it is a different client, of the new release.
    assert claims["client_release"] == "1.1.0"
    assert claims["client_id"] != before["client_id"]
    assert before["client_release"] == "1.0.0"
    assert claims["client_device"] == before["client_device"]

    # The earlier release keeps the id the services knew it by.
    assert set(fakts_client.identities.values_list("release__version", "token_client_id")) == {
        ("1.0.0", before["client_id"]),
        ("1.1.0", claims["client_id"]),
    }

    # And it stays that way on the next refresh, with or without the manifest.
    again = _refresh(client, refreshed, V2)
    assert _decode(again["access_token"])["client_id"] == claims["client_id"]
    plain = _refresh(client, again)
    assert _decode(plain["access_token"])["client_id"] == claims["client_id"]


@pytest.mark.django_db
def test_going_back_to_a_release_finds_its_identity_again(client):
    token, _, _ = _login(client)
    first = _decode(token["access_token"])["client_id"]

    upgraded = _refresh(client, token, V2)
    downgraded = _refresh(client, upgraded, V1)

    assert _decode(downgraded["access_token"])["client_id"] == first
    assert _decode(downgraded["access_token"])["client_release"] == "1.0.0"
    assert models.ClientIdentity.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        {"scopes": ["openid", "mikro_all"]},
        {"requirements": [{"key": "mikro", "service": "live.arkitekt.mikro"}]},
        {"device_id": "another-pc"},
        {"identifier": "com.example.other"},
    ],
    ids=["scope", "service", "device", "app"],
)
def test_asking_for_more_changes_nothing_and_says_so(client, change):
    token, _, _ = _login(client)

    refreshed = _refresh(client, token, {**V2, **change})

    assert refreshed["manifest_status"] == "approval_required"
    claims = _decode(refreshed["access_token"])
    assert claims["client_release"] == "1.0.0"
    assert claims["client_id"] == token["client_id"]
    assert models.Client.objects.get(client_id=token["client_id"]).release.version == "1.0.0"
    assert models.ClientIdentity.objects.count() == 0


@pytest.mark.django_db
def test_asking_for_less_is_adopted(client):
    token, _, _ = _login(client, {**V1, "scopes": ["openid", "profile"]})

    refreshed = _refresh(client, token, {**V2, "scopes": ["openid"]})

    assert "manifest_status" not in refreshed
    assert _decode(refreshed["access_token"])["client_release"] == "1.1.0"


@pytest.mark.django_db
def test_a_client_is_not_moved_onto_another_login_of_the_same_person(client):
    """Two browsers have no device id, so they are one device to lok. One of them
    updating must not take the place, and with it the session, of the other."""
    deviceless = {k: v for k, v in V1.items() if k != "device_id"}
    old, hub, user = _login(client, deviceless)
    new, _, _ = _login(client, {**deviceless, "version": "1.1.0"}, user=user, hub=hub)

    refreshed = _refresh(client, old, {**deviceless, "version": "1.1.0"})

    assert refreshed["manifest_status"] == "approval_required"
    assert models.Client.objects.filter(client_id=new["client_id"]).exists()
    _refresh(client, new)  # the other session still lives


@pytest.mark.django_db
def test_a_manifest_that_cannot_be_read_does_not_fail_the_refresh(client):
    token, _, _ = _login(client)

    response = client.post(
        reverse("token"),
        data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": token["client_id"], "manifest": "{not json"},
        secure=True,
    )

    assert response.status_code == 200
    assert _decode(response.json()["access_token"])["client_release"] == "1.0.0"


@pytest.mark.django_db
def test_a_client_is_found_by_any_id_it_has_had(client):
    token, _, _ = _login(client)
    first = _decode(token["access_token"])["client_id"]
    second = _decode(_refresh(client, token, V2)["access_token"])["client_id"]

    fakts_client = models.Client.objects.get(client_id=token["client_id"])
    assert models.Client.for_token_client_id(first) == fakts_client
    assert models.Client.for_token_client_id(second) == fakts_client
    assert fakts_client.token_client_id == second
    with pytest.raises(models.Client.DoesNotExist):
        models.Client.for_token_client_id("nobody")

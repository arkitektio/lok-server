"""Instance keys: a hub's manifest brings each instance's public key; lok vouches for it.

The hub device-code flow end to end: the manifest's ``challenge_key`` is stored on accept, the
hub's trust bundle (``/.well-known/hub-keys/<hub>``) lists it under its RFC 7638 thumbprint with
its service, ``?service=`` narrows it, the token response points at it (``self.hub_keys_url``),
and re-authorizing with a new key rotates it.
"""

import base64
import json
from types import SimpleNamespace

import pytest
from django.urls import reverse
from joserfc.jwk import OKPKey

from fakts import models
from tests import factories
from tests.test_fakts_flows import _poll


def _raw(key: OKPKey) -> str:
    x = key.as_dict(private=False)["x"]
    return base64.b64encode(base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))).decode()


def _instance(name: str, key: OKPKey | None) -> dict:
    manifest = {"identifier": f"live.arkitekt.{name}", "version": "1.0.0"}
    if key is not None:
        manifest["challenge_key"] = _raw(key)
    return {"identifier": name, "manifest": manifest, "aliases": []}


def _enroll(client, organization_membership, instances, identifier="keyhub"):
    """Start, accept (as an org admin) and poll a hub device code; the token response."""
    from api.management.mutations.hub_device_code import AcceptHubDeviceCodeInput, accept_hub_device_code

    body = client.post(
        reverse("hub_authorization"),
        data=json.dumps({"hub": {"identifier": identifier, "instances": instances, "clients": []}}),
        content_type="application/json",
    ).json()
    code = models.DeviceCode.objects.get(secret=body["device_code"])
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=organization_membership.user)))
    hub = accept_hub_device_code(
        info,
        AcceptHubDeviceCodeInput(device_code=str(code.id), code=code.code, organization=str(organization_membership.organization.id), allow_ionscale=False),
    )
    token = _poll(client, body["device_code"], body["client_id"])
    assert token.status_code == 200, token.content
    return hub, token.json()


@pytest.fixture
def admin():
    org = factories.make_organization()
    return factories.make_membership(user=org.owner, organization=org)


@pytest.mark.django_db
def test_the_manifest_keys_become_the_hubs_trust_bundle(client, admin):
    mikro, rekuest = OKPKey.generate_key("Ed25519"), OKPKey.generate_key("Ed25519")
    hub, token = _enroll(client, admin, [_instance("mikro", mikro), _instance("rekuest", rekuest), _instance("nokey", None)])

    assert token["self"]["hub_keys_url"].endswith(f"/.well-known/hub-keys/{hub.pk}")
    bundle = client.get(reverse("hub_keys", kwargs={"hub_id": hub.pk})).json()
    by_service = {k["service"]: k for k in bundle["keys"]}
    assert set(by_service) == {"live.arkitekt.mikro", "live.arkitekt.rekuest"}  # the keyless one is not vouched for
    assert by_service["live.arkitekt.mikro"]["kid"] == mikro.thumbprint()
    assert by_service["live.arkitekt.mikro"]["x"] == mikro.as_dict(private=False)["x"]
    assert "d" not in by_service["live.arkitekt.mikro"]  # never a private part

    only_rekuest = client.get(reverse("hub_keys", kwargs={"hub_id": hub.pk}), {"service": "live.arkitekt.rekuest"}).json()
    assert [k["kid"] for k in only_rekuest["keys"]] == [rekuest.thumbprint()]


@pytest.mark.django_db
def test_reauthorizing_with_a_new_key_rotates_it(client, admin):
    first, second = OKPKey.generate_key("Ed25519"), OKPKey.generate_key("Ed25519")
    hub, _ = _enroll(client, admin, [_instance("mikro", first)])
    hub_again, _ = _enroll(client, admin, [_instance("mikro", second)])
    assert hub_again.pk == hub.pk

    bundle = client.get(reverse("hub_keys", kwargs={"hub_id": hub.pk})).json()
    assert [k["kid"] for k in bundle["keys"]] == [second.thumbprint()]


@pytest.mark.django_db
def test_a_malformed_key_is_refused(client, admin):
    with pytest.raises(ValueError, match="32-byte"):
        _enroll(client, admin, [{"identifier": "mikro", "manifest": {"identifier": "live.arkitekt.mikro", "version": "1.0.0", "challenge_key": base64.b64encode(b"short").decode()}, "aliases": []}])


@pytest.mark.django_db
def test_an_unknown_hub_has_an_empty_bundle(client):
    assert client.get(reverse("hub_keys", kwargs={"hub_id": 999999})).json() == {"keys": []}

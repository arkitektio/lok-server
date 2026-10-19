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


def _enroll(client, organization_membership, instances, identifier="keyhub", uid=None):
    """Start, accept (as an org admin) and poll a hub device code; the token response."""
    from api.management.mutations.hub_device_code import AcceptHubDeviceCodeInput, accept_hub_device_code

    body = client.post(
        reverse("hub_authorization"),
        data=json.dumps({"hub": {"identifier": identifier, **({"uid": uid} if uid else {}), "instances": instances, "clients": []}}),
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
def test_the_manifest_keys_are_what_apps_check_an_instance_by(client, admin):
    """A hub's services trust each other through the hub's own bundle: lok keeps each public key
    only to hand it to apps, which check an alias by it (``challenge_key``)."""
    mikro = OKPKey.generate_key("Ed25519")
    hub, token = _enroll(client, admin, [_instance("mikro", mikro), _instance("nokey", None)])

    assert "hub_keys_url" not in token["self"]
    keyed, keyless = hub.instances.get(instance_id="mikro"), hub.instances.get(instance_id="nokey")
    assert keyed.public_key == _raw(mikro)
    assert keyless.public_key in (None, "")


@pytest.mark.django_db
def test_reauthorizing_with_a_new_key_rotates_it(client, admin):
    first, second = OKPKey.generate_key("Ed25519"), OKPKey.generate_key("Ed25519")
    hub, _ = _enroll(client, admin, [_instance("mikro", first)])
    hub_again, _ = _enroll(client, admin, [_instance("mikro", second)])
    assert hub_again.pk == hub.pk
    assert hub.instances.get().public_key == _raw(second)


@pytest.mark.django_db
def test_a_malformed_key_is_refused(client, admin):
    with pytest.raises(ValueError, match="32-byte"):
        _enroll(client, admin, [{"identifier": "mikro", "manifest": {"identifier": "live.arkitekt.mikro", "version": "1.0.0", "challenge_key": base64.b64encode(b"short").decode()}, "aliases": []}])


def test_lok_publishes_no_trust_bundle():
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("hub_keys", kwargs={"hub_id": 1})


@pytest.mark.django_db
def test_authorized_again_under_its_uid_a_hub_is_replaced_by_what_it_says_now(client, admin):
    """The hub names itself: the same uid is the same hub, with the services it names now."""
    uid = "0192f3c4-7a2b-7c1d-9e8f-1a2b3c4d5e6f"
    hub, _ = _enroll(client, admin, [_instance("mikro", None), _instance("kraph", None)], uid=uid, identifier="first-label")
    again, _ = _enroll(client, admin, [_instance("mikro", None)], uid=uid, identifier="renamed")
    assert again.pk == hub.pk and str(again.uid) == uid
    assert again.identifier == "renamed"
    assert sorted(again.instances.values_list("instance_id", flat=True)) == ["mikro"]


@pytest.mark.django_db
def test_two_organizations_may_each_hold_the_same_hub(client, admin):
    """A kommunity partner's one deployment, authorized in two organizations: one uid, the same names."""
    uid = "0192f3c4-0000-7000-8000-000000000001"
    other_org = factories.make_organization()
    other_admin = factories.make_membership(user=other_org.owner, organization=other_org)
    first, _ = _enroll(client, admin, [_instance("mikro", None)], uid=uid)
    second, _ = _enroll(client, other_admin, [_instance("mikro", None)], uid=uid)
    assert first.pk != second.pk and first.uid == second.uid
    assert first.instances.get().instance_id == second.instances.get().instance_id == "mikro"

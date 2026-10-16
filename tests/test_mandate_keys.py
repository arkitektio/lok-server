"""The mandate-key grant: a standing key logs a headless app in as the mandate's grantor.

``POST /o/token/`` with ``grant_type=urn:fakts:grant-type:mandate-key``, the key and
the app's manifest returns an access token and the fakts envelope, never a refresh
token: the app runs the grant again when its token expires, and every run checks the
mandate again.
"""

import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from joserfc import jwt
from joserfc.jwk import RSAKey

from authapp.models import OAuth2Token
from fakts import models
from fakts.base_models import MandateKeyGrantRequest, Manifest
from fakts.services import mandates
from tests import factories

GRANT = "urn:fakts:grant-type:mandate-key"
APP = {"identifier": "com.example.keyed", "version": "1.0.0", "scopes": [], "requirements": []}


def _login(client, key, manifest=None, **extra):
    return client.post(
        reverse("token"),
        data={"grant_type": GRANT, "key": key, "manifest": json.dumps(manifest or APP), **extra},
        secure=True,
    )


def _decode(access_token: str) -> dict:
    return jwt.decode(access_token, RSAKey.import_key(settings.PUBLIC_KEY), algorithms=["RS256"]).claims


def _pinned(**overrides):
    return {"identifier": APP["identifier"], "version": "1.0.0", "scopes": [], "requirements": [], **overrides}


@pytest.mark.django_db
def test_a_key_logs_the_app_in_as_the_grantor_without_a_refresh_token(client):
    mandate, key = factories.make_mandate_key(pin=_pinned())

    resp = _login(client, key)

    assert resp.status_code == 200, resp.json()
    token = resp.json()
    assert "refresh_token" not in token
    assert "instances" in token
    client_row = models.Client.objects.get(client_id=token["client_id"])
    assert client_row.mandate == mandate
    assert client_row.membership == mandate.membership
    claims = _decode(token["access_token"])
    assert claims["sub"] == str(mandate.membership.user_id)
    assert claims["org"] == str(mandate.organization_id)


@pytest.mark.django_db
def test_a_key_client_cannot_use_the_refresh_grant(client):
    _, key = factories.make_mandate_key(pin=_pinned())
    token = _login(client, key).json()
    client_row = models.Client.objects.get(client_id=token["client_id"])

    assert not client_row.check_grant_type("refresh_token")
    assert client_row.check_grant_type(GRANT)


@pytest.mark.django_db
def test_logging_in_again_is_the_same_client(client):
    _, key = factories.make_mandate_key(pin=_pinned())

    first = _login(client, key).json()
    second = _login(client, key).json()

    assert first["client_id"] == second["client_id"]
    assert models.Release.objects.filter(app__identifier=APP["identifier"]).count() == 1


@pytest.mark.django_db
def test_a_new_release_updates_the_same_client(client):
    _, key = factories.make_mandate_key(pin=_pinned(version=None))

    first = _login(client, key).json()
    second = _login(client, key, {**APP, "version": "2.0.0"}).json()

    assert first["client_id"] == second["client_id"]
    client_row = models.Client.objects.get(client_id=second["client_id"])
    assert client_row.release.version == "2.0.0"
    assert models.Client.objects.filter(mandate__isnull=False).count() == 1


@pytest.mark.django_db
def test_a_key_client_does_not_replace_the_grantors_own_login(client):
    """A key client acts as the grantor; the grantor's own client of the same app survives."""
    mandate, key = factories.make_mandate_key(pin=_pinned())
    release = factories.make_release(app=factories.make_app(organization=mandate.organization, identifier=APP["identifier"]))
    own = factories.make_client(membership=mandate.membership, release=release, hub=mandate.hub)

    assert _login(client, key).status_code == 200

    assert models.Client.objects.filter(pk=own.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "manifest,fragment",
    [
        ({**APP, "identifier": "com.example.other"}, "pinned to app"),
        ({**APP, "version": "9.9.9"}, "pinned to version"),
        ({**APP, "device_id": "node-b"}, "different device"),
        ({**APP, "scopes": ["write"]}, "scope(s) write"),
        ({**APP, "requirements": [{"key": "x", "service": "live.arkitekt.extra"}]}, "requirement(s) x="),
    ],
)
def test_a_manifest_outside_the_pin_is_refused_before_any_client_exists(client, manifest, fragment):
    _, key = factories.make_mandate_key(pin=_pinned(device_id="node-a"))
    if "device_id" not in manifest:
        manifest = {**manifest, "device_id": "node-a"}

    resp = _login(client, key, manifest)

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"
    assert fragment in resp.json()["error_description"]
    assert not models.Client.objects.filter(mandate__isnull=False).exists()


@pytest.mark.django_db
def test_a_subset_of_the_pinned_scopes_is_fine(client):
    hub = factories.make_hub()
    from karakter.models import Scope

    Scope.objects.create(organization=hub.organization, identifier="read")
    Scope.objects.create(organization=hub.organization, identifier="write")
    _, key = factories.make_mandate_key(hub=hub, pin=_pinned(scopes=["read", "write"]))

    resp = _login(client, key, {**APP, "scopes": ["read"]})

    assert resp.status_code == 200, resp.json()


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["nope-" * 8, "x" * 40])
def test_an_unknown_key_is_refused(client, key):
    resp = _login(client, key)

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_a_short_key_is_a_malformed_request(client):
    resp = _login(client, "short")

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


@pytest.mark.django_db
def test_a_revoked_key_stops_working(client):
    mandate, key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, key).json()

    mandates.revoke_mandate(mandate)

    resp = _login(client, key)
    assert resp.status_code == 400
    assert "revoked" in resp.json()["error_description"]
    assert not models.Client.objects.filter(client_id=first["client_id"]).exists()
    assert not OAuth2Token.objects.filter(client_id=first["client_id"], revoked=False).exists()


@pytest.mark.django_db
def test_an_expired_key_stops_working(client):
    mandate, key = factories.make_mandate_key(pin=_pinned())
    models.Mandate.objects.filter(pk=mandate.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

    resp = _login(client, key)

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_the_key_dies_with_the_grantors_membership(client):
    member = factories.make_membership(organization=factories.make_organization())
    hub = factories.make_hub(organization=member.organization)
    mandate, key = factories.make_mandate_key(hub=hub, membership=member, pin=_pinned())
    assert _login(client, key).status_code == 200

    member.delete()

    assert not models.Mandate.objects.filter(pk=mandate.pk).exists()
    assert _login(client, key).status_code == 400


@pytest.mark.django_db
def test_the_key_stops_working_while_the_grantor_is_deactivated(client):
    mandate, key = factories.make_mandate_key(pin=_pinned())
    user = mandate.membership.user
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert _login(client, key).status_code == 400


@pytest.mark.django_db
def test_an_open_key_is_fixed_to_the_first_app(client):
    mandate, key = factories.make_mandate_key()

    assert _login(client, key).status_code == 200
    resp = _login(client, key, {**APP, "identifier": "com.example.intruder"})

    assert resp.status_code == 400
    mandate.refresh_from_db()
    assert mandate.pin.identifier == APP["identifier"]


@pytest.mark.django_db
def test_one_client_per_device_up_to_max_clients(client):
    _, key = factories.make_mandate_key(pin=_pinned(), max_clients=2)

    a = _login(client, key, {**APP, "device_id": "a"})
    b = _login(client, key, {**APP, "device_id": "b"})
    a_again = _login(client, key, {**APP, "device_id": "a"})
    c = _login(client, key, {**APP, "device_id": "c"})

    assert a.status_code == b.status_code == a_again.status_code == 200
    assert a.json()["client_id"] == a_again.json()["client_id"] != b.json()["client_id"]
    assert c.status_code == 400
    assert "as many devices" in c.json()["error_description"]


@pytest.mark.django_db(transaction=True)
def test_max_clients_holds_under_concurrent_logins():
    """Real threads on their own connections, racing the same key from different devices."""
    mandate, key = factories.make_mandate_key(pin=_pinned(), max_clients=1)
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def login(device: str) -> None:
        try:
            barrier.wait()
            mandates.authenticate_key(MandateKeyGrantRequest(key=key, manifest=Manifest(**{**APP, "device_id": device})))
            outcomes.append("ok")
        except mandates.MandateExhausted:
            outcomes.append("exhausted")
        finally:
            connection.close()

    threads = [threading.Thread(target=login, args=(f"device-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["exhausted", "exhausted", "exhausted", "ok"]
    assert models.Client.objects.filter(mandate=mandate).count() == 1


@pytest.mark.django_db
def test_the_requested_kind_and_role_are_honoured(client):
    _, key = factories.make_mandate_key(pin=_pinned())

    token = _login(client, key, requested_client_kind="desktop", requested_client_role="agent").json()

    client_row = models.Client.objects.get(client_id=token["client_id"])
    assert (client_row.kind, client_row.role) == ("desktop", "agent")


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["hub", "relying_party", "website"])
def test_a_key_cannot_log_in_a_hub_or_relying_party(client, kind):
    _, key = factories.make_mandate_key(pin=_pinned())

    resp = _login(client, key, requested_client_kind=kind)

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


@pytest.fixture
def mesh_hub():
    organization = factories.make_organization()
    models.IonscaleLayer.objects.create(
        organization=organization, name="Default", kind="ionscale", identifier=organization.slug, tailnet_name=organization.slug
    )
    return factories.make_hub(organization=organization)


@pytest.mark.django_db
@pytest.mark.parametrize("allow_mesh,request_auth_key,expected", [(True, "true", True), (True, "false", False), (False, "true", False)])
def test_a_mesh_key_only_when_allowed_and_asked(client, ionscale_repo, mesh_hub, allow_mesh, request_auth_key, expected):
    _, key = factories.make_mandate_key(hub=mesh_hub, pin=_pinned(), allow_mesh=allow_mesh)

    token = _login(client, key, {**APP, "device_id": "box"}, request_auth_key=request_auth_key).json()

    assert ("mesh" in token) is expected
    if expected:
        assert token["mesh"]["ionscale_auth_key"] == ionscale_repo.auth_key


@pytest.mark.django_db
def test_a_key_client_stays_mesh_live_while_its_mandate_does(client):
    from fakts.services.mesh import BACKER_GRACE_SECONDS, live_clients

    mandate, key = factories.make_mandate_key(pin=_pinned())
    token = _login(client, key).json()
    models.Client.objects.filter(client_id=token["client_id"]).update(created_at=timezone.now() - timedelta(seconds=BACKER_GRACE_SECONDS * 3))
    OAuth2Token.objects.filter(client_id=token["client_id"]).update(issued_at=0)

    assert live_clients(models.Client.objects.filter(client_id=token["client_id"])).exists()
    models.Mandate.objects.filter(pk=mandate.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert not live_clients(models.Client.objects.filter(client_id=token["client_id"])).exists()


@pytest.mark.django_db
def test_the_old_redeem_grant_is_gone(client):
    resp = client.post(
        reverse("token"),
        data={"grant_type": "urn:fakts:grant-type:redeem", "redeem_token": "x" * 40, "manifest": json.dumps(APP)},
        secure=True,
    )

    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


@pytest.mark.django_db
def test_rejecting_a_key_client_withdraws_its_key(client):
    from fakts.services.mesh import reject_app_installation

    mandate, key = factories.make_mandate_key(pin=_pinned())
    token = _login(client, key).json()

    reject_app_installation(models.Client.objects.get(client_id=token["client_id"]))

    mandate.refresh_from_db()
    assert mandate.revoked_at is not None
    assert _login(client, key).status_code == 400


@pytest.mark.django_db
def test_the_org_kill_switch_revokes_every_mandate(client):
    from api.management.mutations.revoke import RevokeOrganizationSessionsInput, revoke_organization_sessions

    mandate, key = factories.make_mandate_key(pin=_pinned())
    assert _login(client, key).status_code == 200
    owner = mandate.organization.owner
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=owner)))

    revoke_organization_sessions(info, RevokeOrganizationSessionsInput(organization=str(mandate.organization_id)))

    mandate.refresh_from_db()
    assert mandate.revocation_reason == "organization_revoked"
    assert _login(client, key).status_code == 400


# --------------------------------------------------------------------------- #
# A new key for a device keeps the device's client (and so its rekuest agent)
# --------------------------------------------------------------------------- #

DEVICE = {**APP, "device_id": "esp32-a"}


@pytest.mark.django_db
def test_a_new_key_for_a_device_keeps_its_client_after_the_old_one_expired(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key, DEVICE).json()
    models.Mandate.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

    new, new_key = factories.make_mandate_key(hub=old.hub, membership=old.membership, pin=_pinned())
    second = _login(client, new_key, DEVICE).json()

    assert second["client_id"] == first["client_id"]
    assert models.Client.objects.get(client_id=second["client_id"]).mandate_id == new.pk
    assert not old.clients.exists()


@pytest.mark.django_db
def test_a_new_key_takes_the_client_over_early_and_the_old_key_cannot_take_it_back(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key, DEVICE).json()

    _, new_key = factories.make_mandate_key(hub=old.hub, membership=old.membership, pin=_pinned())
    second = _login(client, new_key, DEVICE).json()
    old_again = _login(client, old_key, DEVICE).json()
    new_again = _login(client, new_key, DEVICE).json()

    assert second["client_id"] == first["client_id"] == new_again["client_id"]
    assert old_again["client_id"] != first["client_id"]


@pytest.mark.django_db
def test_a_new_key_for_another_device_gets_its_own_client(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key, DEVICE).json()

    _, new_key = factories.make_mandate_key(hub=old.hub, membership=old.membership, pin=_pinned())
    other = _login(client, new_key, {**APP, "device_id": "esp32-b"}).json()

    assert other["client_id"] != first["client_id"]
    assert models.Client.objects.filter(client_id=first["client_id"], mandate=old).exists()


@pytest.mark.django_db
def test_a_new_key_of_another_grantor_gets_its_own_client(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key, DEVICE).json()

    someone_else = factories.make_membership(organization=old.organization)
    _, new_key = factories.make_mandate_key(hub=old.hub, membership=someone_else, pin=_pinned())
    second = _login(client, new_key, DEVICE).json()

    assert second["client_id"] != first["client_id"]


@pytest.mark.django_db
def test_a_new_key_for_another_app_on_the_device_gets_its_own_client(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key, DEVICE).json()

    other_app = {**DEVICE, "identifier": "com.example.other"}
    _, new_key = factories.make_mandate_key(hub=old.hub, membership=old.membership, pin=_pinned(identifier=other_app["identifier"]))
    second = _login(client, new_key, other_app).json()

    assert second["client_id"] != first["client_id"]


@pytest.mark.django_db
def test_after_a_revoke_a_new_key_is_a_new_client(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key, DEVICE).json()
    mandates.revoke_mandate(old)

    _, new_key = factories.make_mandate_key(hub=old.hub, membership=old.membership, pin=_pinned())
    second = _login(client, new_key, DEVICE).json()

    assert second["client_id"] != first["client_id"]


@pytest.mark.django_db
def test_without_a_device_id_a_new_key_is_a_new_client(client):
    old, old_key = factories.make_mandate_key(pin=_pinned())
    first = _login(client, old_key).json()

    _, new_key = factories.make_mandate_key(hub=old.hub, membership=old.membership, pin=_pinned())
    second = _login(client, new_key).json()

    assert second["client_id"] != first["client_id"]


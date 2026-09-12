"""Round-3 hardening coverage: revocation, refresh-chain cap, bearer claim
checks, and the anonymous-endpoint throttle."""

import json
import time

import pytest
from django.urls import reverse
from joserfc.jwk import RSAKey  # noqa: F401  (kept close to the jwt usage below)

from authapp.models import OAuth2Token
from fakts import models
from tests import factories
from tests.test_fakts_flows import _accept, _poll, _start


def _granted_session(client):
    """Run the device-code grant end to end, returning (start body, token body)."""
    body = _start(client)
    _accept(models.DeviceCode.objects.get(secret=body["device_code"]))
    token = _poll(client, body["device_code"], body["client_id"]).json()
    return body, token


def _refresh(client, token, client_id):
    return client.post(
        reverse("token"),
        data={"grant_type": "refresh_token", "refresh_token": token, "client_id": client_id},
        secure=True,
    )


# --------------------------------------------------------------------------- #
# RFC 7009 revocation
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_revoke_endpoint_kills_the_refresh_chain(client):
    body, token = _granted_session(client)

    resp = client.post(
        reverse("revoke"),
        data={"token": token["refresh_token"], "token_type_hint": "refresh_token", "client_id": body["client_id"]},
        secure=True,
    )
    assert resp.status_code == 200

    refreshed = _refresh(client, token["refresh_token"], body["client_id"])
    assert refreshed.status_code == 400


@pytest.mark.django_db
def test_revoke_refuses_another_clients_token(client):
    """A token can only be revoked by the client it was issued to."""
    body, token = _granted_session(client)
    other, _other_token = _granted_session(client)

    resp = client.post(
        reverse("revoke"),
        data={"token": token["refresh_token"], "client_id": other["client_id"]},
        secure=True,
    )
    assert resp.status_code == 400

    # And the chain still works.
    assert _refresh(client, token["refresh_token"], body["client_id"]).status_code == 200


@pytest.mark.django_db
def test_revoke_client_sessions_mutation(client):
    from types import SimpleNamespace

    from api.management.mutations.revoke import RevokeClientSessionsInput, revoke_client_sessions

    body, token = _granted_session(client)
    row = OAuth2Token.objects.get(access_token=token["access_token"])
    fakts_client = models.Client.objects.get(client_id=body["client_id"])

    # Revoking a client's sessions is owner/admin-only: act as the organization's owner.
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=fakts_client.organization.owner)))
    revoke_client_sessions(info, RevokeClientSessionsInput(client=str(fakts_client.id)))

    row.refresh_from_db()
    assert row.revoked
    assert _refresh(client, token["refresh_token"], body["client_id"]).status_code == 400


# --------------------------------------------------------------------------- #
# Refresh-chain absolute cap
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_rotation_carries_the_chain_start(client):
    body, token = _granted_session(client)
    first = OAuth2Token.objects.get(access_token=token["access_token"])

    refreshed = _refresh(client, token["refresh_token"], body["client_id"]).json()
    second = OAuth2Token.objects.get(access_token=refreshed["access_token"])

    assert second.chain_started_at == first.chain_started_at


@pytest.mark.django_db
def test_chain_older_than_the_absolute_cap_is_dead(client):
    body, token = _granted_session(client)

    OAuth2Token.objects.filter(access_token=token["access_token"]).update(
        chain_started_at=int(time.time()) - OAuth2Token.REFRESH_CHAIN_MAX_LIFETIME - 60
    )

    assert _refresh(client, token["refresh_token"], body["client_id"]).status_code == 400


# --------------------------------------------------------------------------- #
# Bearer claim checks (iss / aud)
# --------------------------------------------------------------------------- #


def _report(client, headers):
    return client.post(
        reverse("fakts:report"),
        data=json.dumps({"functional": True, "alias_reports": {}}),
        content_type="application/json",
        headers=headers,
    )


@pytest.mark.django_db
def test_report_rejects_wrong_issuer(client):
    from tests.test_fakts_views_errors import _bearer_headers

    fakts_client = factories.make_client()
    resp = _report(client, _bearer_headers(fakts_client, iss="https://not-this-server.example"))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_report_rejects_wrong_audience(client):
    from tests.test_fakts_views_errors import _bearer_headers

    fakts_client = factories.make_client()
    resp = _report(client, _bearer_headers(fakts_client, aud=["somewhere-else"]))
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Throttle
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_authorization_endpoint_throttles_past_the_limit(client, monkeypatch):
    import fakts.views as fakts_views

    monkeypatch.setattr(fakts_views, "AUTHORIZATION_LIMIT_PER_MINUTE", 2)

    payload = {"manifest": {"identifier": "com.example.throttle", "version": "1", "scopes": [], "requirements": []}}
    for _ in range(2):
        resp = client.post(
            reverse("app_authorization"), data=json.dumps(payload), content_type="application/json"
        )
        assert resp.status_code == 200

    third = client.post(reverse("app_authorization"), data=json.dumps(payload), content_type="application/json")
    assert third.status_code == 429
    assert third.json()["error"] == "slow_down"


# --- plain-HTTP opt-out ------------------------------------------------------
#
# authlib's transport check reads AUTHLIB_INSECURE_TRANSPORT at call time;
# settings.py sets that variable from `django.allow_insecure_transport`. These
# tests exercise the env half directly so they don't depend on settings import
# order, and pin both the secure default and the opt-out.


def _poll_plain_http(client, device_code, client_id):
    from tests.test_fakts_flows import DEVICE_CODE_GRANT

    return client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": device_code, "client_id": client_id},
        secure=False,
    )


@pytest.mark.django_db
def test_token_endpoint_rejects_plain_http_by_default(client, monkeypatch):
    monkeypatch.delenv("AUTHLIB_INSECURE_TRANSPORT", raising=False)
    body = _start(client)
    _accept(models.DeviceCode.objects.get(secret=body["device_code"]))
    resp = _poll_plain_http(client, body["device_code"], body["client_id"])
    assert resp.status_code == 400
    assert resp.json()["error"] == "insecure_transport"


@pytest.mark.django_db
def test_token_endpoint_accepts_plain_http_when_opted_out(client, monkeypatch):
    """What `django.allow_insecure_transport: true` turns on for lok servers
    deliberately running without TLS."""
    monkeypatch.setenv("AUTHLIB_INSECURE_TRANSPORT", "1")
    body = _start(client)
    _accept(models.DeviceCode.objects.get(secret=body["device_code"]))
    resp = _poll_plain_http(client, body["device_code"], body["client_id"])
    assert resp.status_code == 200, resp.content
    assert "access_token" in resp.json()

"""End-to-end HTTP tests for the canonical fakts grant.

The flow under test:

1. ``POST /f/start/`` — dynamic client registration + device authorization
   (RFC 8628 shaped): mints a *public* OAuth2 client and a device code.
2. A human accepts the code (simulated via ``validate_device_code``), binding
   it to a hub → organization → membership.
3. ``POST /o/token/`` with ``grant_type=urn:ietf:params:oauth:grant-type:device_code``
   — returns access token + refresh token + rendered instances in one response
   and burns the code.
4. ``grant_type=refresh_token`` — rotates the refresh token and re-renders the
   instances.
5. ``grant_type=urn:fakts:grant-type:redeem`` — the headless equivalent.
"""

import json

import pytest
from django.conf import settings
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import RSAKey

from authapp.models import OAuth2Token
from fakts import models
from fakts.services.device_codes import validate_device_code
from tests import factories

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REDEEM_GRANT = "urn:fakts:grant-type:redeem"


def _start(client, manifest=None, **extra):
    payload = {
        "manifest": manifest
        or {"identifier": "com.example.flow", "version": "1.0.0", "scopes": [], "requirements": []},
        "requested_client_kind": "development",
        "requested_client_role": "agent",
    }
    payload.update(extra)
    return client.post(reverse("app_authorization"), data=json.dumps(payload), content_type="application/json").json()


def _poll(client, device_code, client_id):
    return client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": device_code, "client_id": client_id},
        secure=True,  # authlib rejects the token endpoint over plain HTTP
    )


def _accept(device_code, hub=None):
    """Simulate the human approval in the kontrol frontend."""
    if hub is None:
        hub = factories.make_hub()
    user = factories.make_user()
    factories.make_membership(user=user, organization=hub.organization)
    return validate_device_code(device_code, user=user, organization=hub.organization, hub=hub)


def _decode(access_token: str) -> dict:
    key = RSAKey.import_key(settings.PUBLIC_KEY)
    return jwt.decode(access_token, key, algorithms=["RS256"]).claims


@pytest.mark.django_db
def test_start_registers_a_public_client(client):
    body = _start(client)

    assert body["status"] == "granted"
    # The polling secret and the human user code are distinct: a
    # shoulder-surfed configure URL must not leak a polling credential.
    assert body["device_code"] != body["user_code"]
    assert body["user_code"] in body["verification_uri_complete"]
    assert body["token_endpoint"].endswith("/o/token/")
    assert "{code}" not in body["verification_uri_complete"]
    assert body["interval"] == 5

    device_code = models.DeviceCode.objects.get(secret=body["device_code"])
    assert device_code.client.role == "agent"

    staged = device_code.client
    assert staged.client_id == body["client_id"]
    assert staged.client_secret == ""
    assert staged.token_endpoint_auth_method == "none"
    assert staged.membership is None  # unbound until a human approves
    assert DEVICE_CODE_GRANT in staged.grant_types
    assert "refresh_token" in staged.grant_types


def test_every_client_kind_maps_to_a_graphql_enum():
    """A kind missing from `_CLIENT_KINDS` does not error — it silently reports as
    DEVELOPMENT over GraphQL — so assert the mapping covers every DB choice."""
    from fakts.enums import ClientKindChoices
    from fakts.types import _CLIENT_KINDS

    for choice in ClientKindChoices:
        assert choice.value in _CLIENT_KINDS, choice.value
        # `ClientKind` members are built from `strawberry.enum_value(...)`, so their
        # `.value` is an EnumValueDefinition — compare by member name.
        assert _CLIENT_KINDS[choice.value].name == choice.name


@pytest.mark.django_db
def test_start_accepts_the_mobile_client_kind(client):
    """A mobile app may register itself: `mobile` is a valid requested kind, and
    the staged client keeps it (rather than being rejected by request validation)."""
    body = _start(client, requested_client_kind="mobile", request_public=True)

    assert body["status"] == "granted"
    staged = models.DeviceCode.objects.get(secret=body["device_code"]).client
    assert staged.kind == "mobile"
    assert staged.public is True


@pytest.mark.django_db
def test_poll_before_approval_is_authorization_pending(client):
    body = _start(client)

    resp = _poll(client, body["device_code"], body["client_id"])
    assert resp.status_code == 400
    assert resp.json()["error"] == "authorization_pending"


@pytest.mark.django_db
def test_denied_code_yields_access_denied(client):
    body = _start(client)
    models.DeviceCode.objects.filter(secret=body["device_code"]).update(denied=True)

    resp = _poll(client, body["device_code"], body["client_id"])
    assert resp.status_code == 400
    assert resp.json()["error"] == "access_denied"


@pytest.mark.django_db
def test_full_device_code_grant_returns_tokens_and_instances(client):
    body = _start(client)
    device_code = models.DeviceCode.objects.get(secret=body["device_code"])

    device_code = _accept(device_code)
    fakts_client = device_code.client
    assert fakts_client.membership is not None
    # The staged row IS the client: registration and approval share one row,
    # so the client_id is stable from start to grant.
    assert fakts_client.pk == device_code.client_id
    assert fakts_client.client_id == body["client_id"]
    assert "openid" in fakts_client.scope

    resp = _poll(client, body["device_code"], body["client_id"])
    assert resp.status_code == 200
    token = resp.json()

    # Standard OAuth response fields...
    assert token["token_type"] == "Bearer"
    assert token["refresh_token"]
    assert token["expires_in"] == 3600
    # ...plus the fakts envelope.
    assert token["client_id"] == body["client_id"]
    assert token["self"]["alias"]["host"] == "testserver"
    assert token["instances"] == {}
    assert token["statuses"] == {}

    claims = _decode(token["access_token"])
    assert claims["org"] == str(fakts_client.organization_id)
    assert claims["client_id"] == body["client_id"]
    assert "openid" in claims["scope"]
    assert claims["aud"] == ["lok"]

    # The token row is org-scoped at the DB level: its subject is the membership.
    row = OAuth2Token.objects.get(access_token=token["access_token"])
    assert row.user_id == fakts_client.membership_id

    # Single use: the code is burned and cannot be polled again.
    assert not models.DeviceCode.objects.filter(secret=body["device_code"]).exists()
    again = _poll(client, body["device_code"], body["client_id"])
    assert again.status_code == 400


@pytest.mark.django_db
def test_refresh_rotates_and_rerenders_instances(client):
    body = _start(client)
    _accept(models.DeviceCode.objects.get(secret=body["device_code"]))
    token = _poll(client, body["device_code"], body["client_id"]).json()

    resp = client.post(
        reverse("token"),
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": body["client_id"],
        },
        secure=True,
    )
    assert resp.status_code == 200
    refreshed = resp.json()

    assert refreshed["refresh_token"] != token["refresh_token"]
    # The envelope rides on the refresh response too.
    assert refreshed["client_id"] == body["client_id"]
    assert "instances" in refreshed
    assert refreshed["self"]["alias"]["host"] == "testserver"

    # Rotation: the old refresh token is revoked.
    old_row = OAuth2Token.objects.get(access_token=token["access_token"])
    assert old_row.revoked

    # And cannot be used again.
    replay = client.post(
        reverse("token"),
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": body["client_id"],
        },
        secure=True,
    )
    assert replay.status_code == 400


@pytest.mark.django_db
def test_empty_refresh_token_is_rejected_cleanly(client):
    """Regression: an empty `refresh_token=` parameter must be a clean 400, not
    reach the DB lookup (where empty strings used to be stored)."""
    body = _start(client)
    _accept(models.DeviceCode.objects.get(secret=body["device_code"]))
    _poll(client, body["device_code"], body["client_id"])

    resp = client.post(
        reverse("token"),
        data={"grant_type": "refresh_token", "refresh_token": "", "client_id": body["client_id"]},
        secure=True,
    )
    assert resp.status_code == 400


def _redeem(client, token, version="1.0.0"):
    manifest = {"identifier": "com.example.redeemed", "version": version, "scopes": [], "requirements": []}
    return client.post(
        reverse("token"),
        data={"grant_type": REDEEM_GRANT, "redeem_token": token, "manifest": json.dumps(manifest)},
        secure=True,
    )


@pytest.mark.django_db
def test_redeem_grant_mints_client_and_tokens(client):
    hub = factories.make_hub()
    redeem = factories.make_redeem_token(hub=hub)

    resp = _redeem(client, redeem.token)
    assert resp.status_code == 200
    token = resp.json()

    assert token["refresh_token"]
    assert "instances" in token

    redeem.refresh_from_db()
    assert redeem.client is not None
    assert redeem.client.organization == hub.organization
    assert token["client_id"] == redeem.client.client_id

    claims = _decode(token["access_token"])
    assert claims["org"] == str(hub.organization_id)


@pytest.mark.django_db
def test_redeem_same_manifest_twice_returns_same_client(client):
    redeem = factories.make_redeem_token()

    first = _redeem(client, redeem.token).json()
    second = _redeem(client, redeem.token).json()

    assert first["client_id"] == second["client_id"]


@pytest.mark.django_db
def test_redeem_changed_manifest_rejected_without_allow_reredeem(client):
    redeem = factories.make_redeem_token()

    _redeem(client, redeem.token, "1.0.0")
    resp = _redeem(client, redeem.token, "2.0.0")

    assert resp.status_code == 400
    assert "allow_reredeem" in resp.json()["error_description"]


@pytest.mark.django_db
def test_redeem_changed_manifest_allowed_with_reredeem(client):
    redeem = factories.make_redeem_token(allow_reredeem=True)

    _redeem(client, redeem.token, "1.0.0")
    resp = _redeem(client, redeem.token, "2.0.0")

    assert resp.status_code == 200


@pytest.mark.django_db
def test_redeem_unknown_token_errors(client):
    resp = _redeem(client, "nope")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_full_hub_grant_returns_tokens_and_hub_config(client):
    """The hub flow rides the same canonical grant: hub-authorization registers
    a public client, accept creates the hub, and the token endpoint returns
    tokens + the full hub config (instances with private keys, clients by
    client_id) in one response. Refresh re-renders it."""
    from types import SimpleNamespace

    from api.management.mutations.hub_device_code import (
        AcceptHubDeviceCodeInput,
        accept_hub_device_code,
    )

    body = client.post(
        reverse("hub_authorization"),
        data=json.dumps({"hub": {"identifier": "hubgrant", "instances": [], "clients": []}}),
        content_type="application/json",
    ).json()
    assert body["status"] == "granted"
    assert body["device_code"] != body["user_code"]

    hub_code = models.DeviceCode.objects.get(secret=body["device_code"])

    # Pending before approval.
    pending = _poll(client, body["device_code"], body["client_id"])
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"

    # Human accepts in kontrol (adding a hub is an owner/admin operation, and the
    # org owner is made an admin member by the organization post_save signal).
    org = factories.make_organization()
    membership = factories.make_membership(user=org.owner, organization=org)
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=membership.user)))
    hub = accept_hub_device_code(
        info,
        AcceptHubDeviceCodeInput(
            device_code=str(hub_code.id),
            code=hub_code.code,
            organization=str(membership.organization.id),
            allow_ionscale=False,
        ),
    )
    assert hub.client_id == hub_code.client.pk
    assert hub.client.membership_id == membership.id

    resp = _poll(client, body["device_code"], body["client_id"])
    assert resp.status_code == 200
    token = resp.json()

    # Standard token response + the hub envelope.
    assert token["refresh_token"]
    assert token["client_id"] == body["client_id"]
    assert token["auth"]["jwks_url"].endswith("/.well-known/jwks.json")
    assert token["instances"] == {}
    assert token["clients"] == {}

    claims = _decode(token["access_token"])
    assert claims["org"] == str(membership.organization_id)
    assert claims["hub"] == "hubgrant"
    assert claims["aud"] == ["lok"]

    # The staged code is burned; continuity is the refresh chain, and the hub
    # config rides on every refresh.
    assert not models.DeviceCode.objects.filter(pk=hub_code.pk).exists()
    refreshed = client.post(
        reverse("token"),
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": body["client_id"],
        },
        secure=True,
    )
    assert refreshed.status_code == 200
    assert "auth" in refreshed.json()
    assert "clients" in refreshed.json()


@pytest.mark.django_db
def test_reapproval_rotates_identity(client):
    """Approving the same app again (same org, hub, user): the previous bound
    client row — and with it its client_id and refresh chain — is deleted, and
    the freshly registered row takes over."""
    hub = factories.make_hub()
    user = factories.make_user()
    factories.make_membership(user=user, organization=hub.organization)

    first = _start(client)
    validate_device_code(
        models.DeviceCode.objects.get(secret=first["device_code"]),
        user=user, organization=hub.organization, hub=hub,
    )
    assert _poll(client, first["device_code"], first["client_id"]).status_code == 200

    second = _start(client)
    validate_device_code(
        models.DeviceCode.objects.get(secret=second["device_code"]),
        user=user, organization=hub.organization, hub=hub,
    )
    assert second["client_id"] != first["client_id"]
    # The first installation's identity is gone entirely.
    assert not models.Client.objects.filter(client_id=first["client_id"]).exists()
    assert _poll(client, second["device_code"], second["client_id"]).status_code == 200


@pytest.mark.django_db
def test_same_app_on_same_device_can_be_approved_into_two_hubs(client):
    """A client's identity is (release, membership, node, hub): approving the
    same app from the same device into a *second* hub of the organization must
    create a second client, not trip the uniqueness constraint or rotate the
    first one away."""
    hub_a = factories.make_hub()
    hub_b = factories.make_hub(organization=hub_a.organization)
    user = factories.make_user()
    factories.make_membership(user=user, organization=hub_a.organization)
    manifest = {
        "identifier": "com.example.multihub", "version": "1.0.0",
        "scopes": [], "requirements": [], "node_id": "same-laptop",
    }

    first = _start(client, manifest=manifest)
    validate_device_code(
        models.DeviceCode.objects.get(secret=first["device_code"]),
        user=user, organization=hub_a.organization, hub=hub_a,
    )
    assert _poll(client, first["device_code"], first["client_id"]).status_code == 200

    second = _start(client, manifest=manifest)
    validate_device_code(
        models.DeviceCode.objects.get(secret=second["device_code"]),
        user=user, organization=hub_a.organization, hub=hub_b,
    )
    assert _poll(client, second["device_code"], second["client_id"]).status_code == 200

    a = models.Client.objects.get(client_id=first["client_id"])
    b = models.Client.objects.get(client_id=second["client_id"])
    assert (a.release_id, a.membership_id, a.node_id) == (b.release_id, b.membership_id, b.node_id)
    assert {a.hub_id, b.hub_id} == {hub_a.id, hub_b.id}


@pytest.mark.django_db
def test_relying_party_rows_are_invisible_to_tenant_scoping(client):
    """Config-provisioned relying parties are global (no organization), so the
    org-scoped client listings never include them."""
    membership = factories.make_membership()
    rp = models.Client.objects.create(
        client_id="global-rp",
        client_secret="s3cret",
        kind="relying_party",
        token_endpoint_auth_method="client_secret_post",
    )
    bound = factories.make_client(membership=membership)

    scoped = models.Client.objects.filter(organization=membership.organization)
    assert bound in scoped
    assert rp not in scoped


# --- pre-authorized (pinned) redeem tokens ---------------------------------------
#
# A deployer mints a token pinned to the manifest it approved; the container that
# receives it can only ever enrol as that app. Identity fields must match exactly,
# scopes and requirements are a ceiling.

PINNED = {
    "identifier": "com.example.pinned",
    "version": "1.0.0",
    "scopes": [],
    "requirements": [],
    "node_id": "node-a",
}


def _redeem_manifest(client, token, manifest):
    return client.post(
        reverse("token"),
        data={"grant_type": REDEEM_GRANT, "redeem_token": token, "manifest": json.dumps(manifest)},
        secure=True,
    )


def _pinned(**overrides):
    pinned = {**PINNED, **overrides}
    return factories.make_redeem_token(pinned_manifest=pinned)


@pytest.mark.django_db
def test_pinned_redeem_accepts_matching_manifest(client):
    redeem = _pinned()

    resp = _redeem_manifest(client, redeem.token, PINNED)

    assert resp.status_code == 200, resp.json()
    redeem.refresh_from_db()
    assert redeem.client is not None
    assert redeem.client.release.app.identifier == "com.example.pinned"
    assert redeem.redemption_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"identifier": "com.example.other"}, "pinned to app 'com.example.pinned'"),
        ({"version": "2.0.0"}, "pinned to version '1.0.0'"),
        ({"node_id": "node-b"}, "different node"),
        ({"scopes": ["admin"]}, "does not authorize the scope(s) admin"),
        (
            {"requirements": [{"key": "lok", "service": "live.arkitekt.lok"}]},
            "does not authorize the requirement(s) lok=live.arkitekt.lok",
        ),
    ],
)
def test_pinned_redeem_rejects_deviating_manifest(client, change, fragment):
    redeem = _pinned()

    resp = _redeem_manifest(client, redeem.token, {**PINNED, **change})

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_grant"
    assert fragment in body["error_description"]
    redeem.refresh_from_db()
    assert redeem.client is None, "a refused redeem must not provision a client"
    assert redeem.redemption_count == 0


@pytest.mark.django_db
def test_pinned_redeem_allows_subset_of_scopes_and_requirements(client):
    redeem = _pinned(
        scopes=["read"],
        requirements=[{"key": "rekuest", "service": "live.arkitekt.rekuest", "optional": False}],
    )

    # Asking for less than the pin authorizes is fine.
    resp = _redeem_manifest(client, redeem.token, PINNED)

    assert resp.status_code == 200, resp.json()


@pytest.mark.django_db
def test_pinned_redeem_is_checked_on_every_redeem(client):
    redeem = _pinned()

    assert _redeem_manifest(client, redeem.token, PINNED).status_code == 200
    resp = _redeem_manifest(client, redeem.token, {**PINNED, "version": "2.0.0"})

    assert resp.status_code == 400
    # The pin, not the first-redeem hash, is what refuses it.
    assert "pinned to version" in resp.json()["error_description"]


@pytest.mark.django_db
def test_pinned_node_is_optional(client):
    redeem = _pinned(node_id=None)

    resp = _redeem_manifest(client, redeem.token, {**PINNED, "node_id": "any-node"})

    assert resp.status_code == 200, resp.json()


@pytest.mark.django_db
def test_max_redemptions_is_enforced(client):
    redeem = _pinned()
    redeem.max_redemptions = 1
    redeem.save()

    assert _redeem_manifest(client, redeem.token, PINNED).status_code == 200
    resp = _redeem_manifest(client, redeem.token, PINNED)

    assert resp.status_code == 400
    assert "maximum number of times" in resp.json()["error_description"]

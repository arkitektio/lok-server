"""HTTP / OAuth / config hardening regressions.

Covers the browser- and machine-facing surface hardened in one pass:

- ``/f/claimhub/`` is throttled and answers unknown hub tokens with a structured
  ``400 invalid_grant`` (no more 200-with-error bodies);
- the mesh device-code flow is throttled, speaks RFC 8628 vocabulary, and burns
  a granted challenge after the first successful poll (single-use);
- ``report_client`` never applies an alias id from another organization;
- the token endpoint turns every domain failure of the redeem grant into an
  OAuth error (``invalid_scope`` / ``invalid_grant``), never a 500/HTML page;
- an unknown ``requested_client_role`` is a ``400 invalid_request`` both at app
  authorization and in the redeem grant;
- no published JWKS (``/o/jwks/``, the bearer validator) carries private members;
- CORS headers are served on /o/, /f/ and /.well-known/ only, driven by the
  ``django.cors_*`` config keys.
"""

import json
import re

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from fakts import base_models, models
from fakts.services import clients, device_codes
from lok_server.configuration import Settings
from tests import factories

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REDEEM_GRANT = "urn:fakts:grant-type:redeem"
PRIVATE_JWK_MEMBERS = {"d", "p", "q", "dp", "dq", "qi", "oth"}


def _post(client, name, payload, **kw):
    return client.post(reverse(name), data=json.dumps(payload), content_type="application/json", **kw)


def _prefixed(path: str) -> str:
    """Absolute request path for a root-relative lok path, honouring FORCE_SCRIPT_NAME."""
    prefix = settings.MY_SCRIPT_NAME.strip("/")
    return f"/{prefix}{path}" if prefix else path


# --------------------------------------------------------------------------- #
# /f/claimhub/
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_claimhub_unknown_token_is_400_invalid_grant(client):
    resp = _post(client, "fakts:hubclaim", {"token": "not-a-hub-token"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] == "invalid_grant"
    assert body["error_description"]


@pytest.mark.django_db
def test_claimhub_throttles_past_the_limit(client):
    from authapp.throttle import AUTHORIZATION_LIMIT_PER_MINUTE

    # The first `limit` requests go through (to a 400 — unknown token), the
    # next one in the same window is a 429 slow_down.
    for _ in range(AUTHORIZATION_LIMIT_PER_MINUTE):
        resp = _post(client, "fakts:hubclaim", {"token": "guess"})
        assert resp.status_code == 400, resp.content

    over = _post(client, "fakts:hubclaim", {"token": "guess"})
    assert over.status_code == 429
    assert over.json()["error"] == "slow_down"


@pytest.mark.django_db
def test_claimhub_malformed_body_is_400_invalid_request(client):
    resp = client.post(reverse("fakts:hubclaim"), data="{nope", content_type="application/json")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_request"
    # legacy key this endpoint historically used for its human text
    assert body["message"].startswith("Malformed request")


def test_hub_token_is_unique():
    """The hub token is a bearer secret looked up by value: it must identify one hub."""
    assert models.Hub._meta.get_field("token").unique is True


# --------------------------------------------------------------------------- #
# Mesh device-code flow
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_mesh_start_is_throttled(client, monkeypatch):
    import fakts.views as fakts_views

    monkeypatch.setattr(fakts_views, "AUTHORIZATION_LIMIT_PER_MINUTE", 2)

    for _ in range(2):
        resp = _post(client, "fakts:meshstart", {"requested_machine_name": "box"})
        assert resp.status_code == 200, resp.content
        assert resp.json()["status"] == "granted"

    third = _post(client, "fakts:meshstart", {"requested_machine_name": "box"})
    assert third.status_code == 429
    assert third.json()["error"] == "slow_down"


@pytest.mark.django_db
def test_mesh_challenge_is_throttled(client, monkeypatch):
    import fakts.views as fakts_views

    monkeypatch.setattr(fakts_views, "AUTHORIZATION_LIMIT_PER_MINUTE", 1)

    _post(client, "fakts:meshchallenge", {"code": "nope"})
    second = _post(client, "fakts:meshchallenge", {"code": "nope"})
    assert second.status_code == 429
    assert second.json()["error"] == "slow_down"


@pytest.mark.django_db
def test_mesh_start_rejects_invalid_body_with_400(client):
    resp = client.post(reverse("fakts:meshstart"), data="nope", content_type="application/json")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


@pytest.mark.django_db
def test_mesh_start_domain_error_is_400_not_500(client, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("ionscale is unhappy")

    monkeypatch.setattr(device_codes, "start_mesh_device_code", _boom)
    resp = _post(client, "fakts:meshstart", {"requested_machine_name": "box"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] == "invalid_request"
    assert "ionscale is unhappy" in body["error_description"]


def _mesh_code_and_key(ionscale_repo):
    """A staged mesh code plus a minted auth key in the org's mesh (the accept
    mutation's effect, without going through the GraphQL layer)."""
    from ionscale.manager import ensure_org_mesh

    org = factories.make_organization()
    layer = ensure_org_mesh(org)
    dc = device_codes.start_mesh_device_code(base_models.MeshDeviceCodeStartRequest(requested_machine_name="gpu-01"))
    key = models.IonscaleAuthKey.objects.create(layer=layer, key="tskey-auth-test", creator=org.owner)
    return dc, key


@pytest.mark.django_db
def test_mesh_challenge_speaks_rfc8628_while_pending(client, ionscale_repo):
    dc, _ = _mesh_code_and_key(ionscale_repo)
    resp = _post(client, "fakts:meshchallenge", {"code": dc.challenge_code})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "authorization_pending"
    assert body["status"] == "pending"  # legacy field kept


@pytest.mark.django_db
def test_mesh_challenge_unknown_code_is_invalid_grant(client):
    resp = _post(client, "fakts:meshchallenge", {"code": "never-issued"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_mesh_challenge_grant_is_single_use(client, ionscale_repo):
    dc, key = _mesh_code_and_key(ionscale_repo)
    dc.auth_key = key
    dc.machine_name = "gpu-01"
    dc.save()

    first = _post(client, "fakts:meshchallenge", {"code": dc.challenge_code})
    assert first.status_code == 200
    granted = first.json()
    assert granted["status"] == "granted"
    assert granted["ionscale_auth_key"] == "tskey-auth-test"
    assert granted["machine_name"] == "gpu-01"

    # The code is burned: the secret cannot be replayed to fetch the key again.
    assert not models.MeshDeviceCode.objects.filter(pk=dc.pk).exists()
    second = _post(client, "fakts:meshchallenge", {"code": dc.challenge_code})
    assert second.status_code == 400
    assert second.json()["error"] in ("invalid_grant", "expired_token")

    # ...while the minted key itself survives (SET_NULL the other way round).
    assert models.IonscaleAuthKey.objects.filter(pk=key.pk).exists()


@pytest.mark.django_db
def test_mesh_challenge_expired_and_denied_vocabulary(client, ionscale_repo):
    dc, _ = _mesh_code_and_key(ionscale_repo)
    dc.denied = True
    dc.save()
    denied = _post(client, "fakts:meshchallenge", {"code": dc.challenge_code})
    assert denied.status_code == 400
    assert denied.json()["error"] == "access_denied"
    assert denied.json()["status"] == "denied"
    assert not models.MeshDeviceCode.objects.filter(pk=dc.pk).exists()

    dc2, _ = _mesh_code_and_key(ionscale_repo)
    dc2.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    dc2.save()
    expired = _post(client, "fakts:meshchallenge", {"code": dc2.challenge_code})
    assert expired.status_code == 400
    assert expired.json()["error"] == "expired_token"
    assert expired.json()["status"] == "expired"


# --------------------------------------------------------------------------- #
# report_client alias scoping
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_report_client_ignores_alias_from_another_organization():
    reporter = factories.make_client()  # its own organization
    foreign_instance = factories.make_service_instance()  # hub in a different organization
    foreign_alias = models.InstanceAlias.objects.create(instance=foreign_instance, host="foreign.example", kind="absolute")
    assert foreign_instance.hub.organization_id != reporter.organization_id

    clients.report_client(
        reporter,
        base_models.ReportRequest(alias_reports={"db": base_models.AliasReport(alias_id=str(foreign_alias.id), valid=True)}),
    )

    usage = models.UsedAlias.objects.get(client=reporter, key="db")
    assert usage.alias is None  # the foreign reference is not applied
    assert usage.valid is True  # ...but the self-report itself is recorded


@pytest.mark.django_db
def test_report_client_applies_alias_from_own_organization():
    reporter = factories.make_client()
    hub = factories.make_hub(organization=reporter.organization)
    instance = factories.make_service_instance(hub=hub)
    alias = models.InstanceAlias.objects.create(instance=instance, host="mine.example", kind="absolute")

    clients.report_client(
        reporter,
        base_models.ReportRequest(alias_reports={"db": base_models.AliasReport(alias_id=str(alias.id), valid=True)}),
    )

    assert models.UsedAlias.objects.get(client=reporter, key="db").alias_id == alias.id


@pytest.mark.django_db
def test_report_client_ignores_unknown_alias_id():
    reporter = factories.make_client()
    clients.report_client(
        reporter,
        base_models.ReportRequest(alias_reports={"db": base_models.AliasReport(alias_id="999999", valid=False, reason="x")}),
    )
    usage = models.UsedAlias.objects.get(client=reporter, key="db")
    assert usage.alias is None
    assert usage.reason == "x"


# --------------------------------------------------------------------------- #
# Token endpoint: redeem grant never 500s
# --------------------------------------------------------------------------- #


def _redeem(client, token, manifest, **extra):
    return client.post(
        reverse("token"),
        data={"grant_type": REDEEM_GRANT, "redeem_token": token, "manifest": json.dumps(manifest), **extra},
        secure=True,
    )


@pytest.mark.django_db
def test_redeem_with_unknown_scope_is_an_oauth_error(client):
    redeem = factories.make_redeem_token()
    manifest = {
        "identifier": "com.example.scoped",
        "version": "1.0.0",
        "scopes": ["definitely-not-a-scope-of-this-org"],
        "requirements": [],
    }
    resp = _redeem(client, redeem.token, manifest)
    assert resp.status_code == 400, resp.content
    assert resp["Content-Type"].startswith("application/json")
    body = resp.json()
    assert body["error"] in ("invalid_scope", "invalid_grant")
    assert "definitely-not-a-scope-of-this-org" in body["error_description"]


@pytest.mark.django_db
def test_redeem_unexpected_domain_failure_is_invalid_grant(client, monkeypatch):
    def _boom(*a, **kw):
        raise ValueError("Could not download logo boom")

    monkeypatch.setattr(clients, "redeem_token", _boom)
    redeem = factories.make_redeem_token()
    manifest = {"identifier": "com.example.boom", "version": "1.0.0", "scopes": [], "requirements": []}
    resp = _redeem(client, redeem.token, manifest)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_grant"
    assert "boom" in body["error_description"]


@pytest.mark.django_db
def test_redeem_with_bogus_client_role_is_invalid_request(client):
    redeem = factories.make_redeem_token()
    manifest = {"identifier": "com.example.role", "version": "1.0.0", "scopes": [], "requirements": []}
    resp = _redeem(client, redeem.token, manifest, requested_client_role="bogus")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_request"
    assert "requested_client_role" in body["error_description"]
    # nothing was provisioned
    redeem.refresh_from_db()
    assert redeem.client is None


@pytest.mark.django_db
def test_app_authorization_with_bogus_client_role_is_invalid_request(client):
    resp = _post(
        client,
        "app_authorization",
        {
            "manifest": {"identifier": "com.example.role", "version": "1.0.0", "scopes": [], "requirements": []},
            "requested_client_role": "bogus",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] == "invalid_request"
    assert any("requested_client_role" in d.get("loc", []) for d in body["details"])
    assert not models.DeviceCode.objects.exists()


# --------------------------------------------------------------------------- #
# JWKS never leaks private members
# --------------------------------------------------------------------------- #


def _assert_public_only(jwks: dict):
    assert jwks["keys"], "empty JWKS"
    for key in jwks["keys"]:
        assert key["kty"] == "RSA"
        assert "n" in key and "e" in key
        leaked = PRIVATE_JWK_MEMBERS & set(key)
        assert not leaked, f"private JWK members published: {sorted(leaked)}"


@pytest.mark.django_db
def test_jwks_endpoint_has_no_private_members(client):
    resp = client.get(reverse("jwks"))
    assert resp.status_code == 200
    _assert_public_only(resp.json())


def test_bearer_validator_jwks_has_no_private_members():
    from authapp.server import MyBearerTokenValidator

    validator = MyBearerTokenValidator(issuer=settings.OIDC_ISSUER, resource_server=None)
    _assert_public_only(validator.get_jwks())


def test_signing_key_still_private():
    """The generator must keep signing with the private key — only *published*
    sets are public."""
    from authapp.token_generators import jwk_dict, public_jwk_dict

    assert "d" in jwk_dict
    assert "d" not in public_jwk_dict
    assert public_jwk_dict["kid"] == jwk_dict["kid"] == settings.KEY_ID


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #


def test_cors_is_wired_first_and_regex_covers_only_api_surfaces():
    assert "corsheaders" in settings.INSTALLED_APPS
    assert settings.MIDDLEWARE[0] == "corsheaders.middleware.CorsMiddleware"
    assert "authorization" in settings.CORS_ALLOW_HEADERS
    assert settings.CORS_ALLOW_CREDENTIALS is False

    rx = settings.CORS_URLS_REGEX
    for path in ("/o/token/", "/f/report/", "/.well-known/fakts", "/lok/o/token/", "/lok/f/claimhub/", "/lok/.well-known/openid-configuration"):
        assert re.match(rx, path), path
    for path in ("/managementgraphql/", "/lok/managementgraphql/", "/admin/", "/_allauth/browser/v1/config", "/lok/_allauth/app/v1/auth/login"):
        assert not re.match(rx, path), path


@pytest.mark.django_db
@override_settings(CORS_ALLOWED_ORIGINS=["https://app.example"])
def test_cors_headers_on_well_known_for_allowed_origin(client):
    resp = client.get(_prefixed("/.well-known/fakts"), HTTP_ORIGIN="https://app.example")
    assert resp.status_code == 200
    assert resp["Access-Control-Allow-Origin"] == "https://app.example"

    preflight = client.options(
        _prefixed("/.well-known/fakts"),
        HTTP_ORIGIN="https://app.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
    )
    assert preflight.status_code == 200
    assert preflight["Access-Control-Allow-Origin"] == "https://app.example"
    assert "authorization" in preflight["Access-Control-Allow-Headers"].lower()


@pytest.mark.django_db
@override_settings(CORS_ALLOWED_ORIGINS=["https://app.example"])
def test_cors_headers_on_token_endpoint_but_not_for_unlisted_origin(client):
    resp = client.options(
        reverse("token"),
        HTTP_ORIGIN="https://app.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert resp["Access-Control-Allow-Origin"] == "https://app.example"

    other = client.options(
        reverse("token"),
        HTTP_ORIGIN="https://evil.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert "Access-Control-Allow-Origin" not in other


@pytest.mark.django_db
@override_settings(CORS_ALLOWED_ORIGINS=["https://app.example"])
def test_cors_headers_absent_on_graphql(client):
    resp = client.options(
        _prefixed("/managementgraphql/"),
        HTTP_ORIGIN="https://app.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert "Access-Control-Allow-Origin" not in resp


@pytest.mark.django_db
@override_settings(CORS_ALLOW_ALL_ORIGINS=True, CORS_ALLOWED_ORIGINS=[])
def test_cors_allow_all_origins_answers_any_origin(client):
    resp = client.get(_prefixed("/.well-known/fakts"), HTTP_ORIGIN="https://anything.example")
    assert resp["Access-Control-Allow-Origin"] == "*"


@pytest.mark.django_db
def test_cors_default_config_sends_no_headers(client):
    """With the config defaults (no allowed origins, allow-all off) nothing is
    served cross-origin — the pre-CORS behaviour."""
    with override_settings(CORS_ALLOWED_ORIGINS=[], CORS_ALLOW_ALL_ORIGINS=False):
        resp = client.get(_prefixed("/.well-known/fakts"), HTTP_ORIGIN="https://app.example")
        assert "Access-Control-Allow-Origin" not in resp


# --------------------------------------------------------------------------- #
# Config keys (lok_server.configuration.DjangoSettings)
# --------------------------------------------------------------------------- #


def test_cors_config_defaults():
    s = Settings()
    assert s.django.cors_allowed_origins == []
    assert s.django.cors_allow_all_origins is False


def test_cors_config_env_override(monkeypatch):
    monkeypatch.setenv("DJANGO__CORS_ALLOW_ALL_ORIGINS", "true")
    monkeypatch.setenv("DJANGO__CORS_ALLOWED_ORIGINS", '["https://a.example", "https://b.example:8443"]')
    s = Settings()
    assert s.django.cors_allow_all_origins is True
    assert s.django.cors_allowed_origins == ["https://a.example", "https://b.example:8443"]


def test_cors_config_is_documented():
    """Every django.cors_* key must appear in CONFIG.md's `django` table."""
    from pathlib import Path

    doc = Path(settings.BASE_DIR, "CONFIG.md").read_text()
    assert "`cors_allowed_origins` | `DJANGO__CORS_ALLOWED_ORIGINS`" in doc
    assert "`cors_allow_all_origins` | `DJANGO__CORS_ALLOW_ALL_ORIGINS`" in doc

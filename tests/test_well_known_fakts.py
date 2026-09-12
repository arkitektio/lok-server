"""The fakts well-known advertises an absolute `configure` endpoint.

`deployment.configure_url` may be configured as a root-relative path, an absolute
URL with a scheme, or a bare host — the well-known must always hand the client an
**absolute** URL, with the literal `{code}` placeholder preserved for the client to
substitute with the device code.
"""

import pytest
from django.test import override_settings

WELL_KNOWN = "/lok/.well-known/fakts"


@pytest.mark.django_db
@override_settings(DEPLOYMENT_CONFIGURE_URL="/configure/{code}")
def test_root_relative_configure_url_joins_base_domain(client):
    data = client.get(WELL_KNOWN).json()
    # The base domain is the configured issuer with lok's script-name stripped.
    # Deliberately *not* the request host: see
    # test_discovery_endpoints_ignore_a_spoofed_forwarded_host.
    assert data["configure"] == "http://lok/configure/{code}"


@pytest.mark.django_db
@override_settings(DEPLOYMENT_CONFIGURE_URL="https://go.arkitekt.live/configure/{code}")
def test_absolute_configure_url_used_verbatim(client):
    data = client.get(WELL_KNOWN).json()
    assert data["configure"] == "https://go.arkitekt.live/configure/{code}"


@pytest.mark.django_db
@override_settings(DEPLOYMENT_CONFIGURE_URL="go.arkitekt.live/configure/{code}")
def test_bare_host_configure_url_promoted_to_https(client):
    """The exact value the user configures — a host without a scheme — resolves to
    an https absolute URL rather than being treated as a relative path."""
    data = client.get(WELL_KNOWN).json()
    assert data["configure"] == "https://go.arkitekt.live/configure/{code}"


@pytest.mark.django_db
@override_settings(DEPLOYMENT_CONFIGURE_URL="https://go.arkitekt.live/configure/{code}")
def test_code_placeholder_is_preserved_literally(client):
    """`{code}` must survive verbatim — the client substitutes it, not the server."""
    data = client.get(WELL_KNOWN).json()
    assert "{code}" in data["configure"]


@pytest.mark.django_db
def test_base_fields_are_present(client):
    data = client.get(WELL_KNOWN).json()
    assert set(data) >= {"base_url", "frontend_url", "configure", "issuer", "token_endpoint", "jwks_uri"}
    # The old claim/challenge endpoints (and the pre-RFC-8414 field names) are
    # gone: the canonical grant lives on the token endpoint.
    for legacy in ("claim", "challenge_url", "device_code_start", "token_url", "jwks_url"):
        assert legacy not in data


@pytest.mark.django_db
def test_oauth_metadata_is_advertised(client):
    """The fakts well-known speaks RFC 8414 vocabulary: the app authorization
    (device authorization + dynamic registration) and token endpoints as
    absolute URLs, plus the supported grants and client auth methods."""
    data = client.get(WELL_KNOWN).json()
    assert data["device_authorization_endpoint"] == "http://lok/lok/o/app-authorization/"
    assert data["token_endpoint"] == "http://lok/lok/o/token/"
    assert data["jwks_uri"] == "http://lok/lok/o/jwks/"
    assert "urn:ietf:params:oauth:grant-type:device_code" in data["grant_types_supported"]
    assert "urn:fakts:grant-type:redeem" in data["grant_types_supported"]
    assert "refresh_token" in data["grant_types_supported"]
    assert "none" in data["token_endpoint_auth_methods_supported"]


@pytest.mark.django_db
def test_oauth_authorization_server_document(client):
    """/.well-known/oauth-authorization-server (RFC 8414) serves the same core
    the openid-configuration builds on."""
    rfc8414 = client.get("/lok/.well-known/oauth-authorization-server").json()
    oidc = client.get("/lok/.well-known/openid-configuration").json()

    for key in ("issuer", "token_endpoint", "jwks_uri",
                "grant_types_supported", "token_endpoint_auth_methods_supported"):
        assert rfc8414[key] == oidc[key]

    # The OIDC-specific fields live only on openid-configuration.
    assert "userinfo_endpoint" in oidc and "userinfo_endpoint" not in rfc8414
    assert "claims_supported" in oidc and "claims_supported" not in rfc8414


@pytest.mark.django_db
def test_standard_metadata_does_not_advertise_the_private_device_endpoint(client):
    """The device *grant* is standard; the endpoint that mints device codes is not.

    /o/app-authorization/ takes a JSON manifest and doubles as dynamic client
    registration, so it is not RFC 8628 §3.1. Advertising it as
    `device_authorization_endpoint` pointed generic device-flow libraries at a
    protocol lok does not answer. It stays in /.well-known/fakts (a private
    protocol document) only.
    """
    fakts = client.get(WELL_KNOWN).json()
    rfc8414 = client.get("/lok/.well-known/oauth-authorization-server").json()
    oidc = client.get("/lok/.well-known/openid-configuration").json()

    assert "device_authorization_endpoint" not in rfc8414
    assert "device_authorization_endpoint" not in oidc
    assert fakts["device_authorization_endpoint"] == "http://lok/lok/o/app-authorization/"

    # The grant itself is conforming and stays advertised everywhere.
    for doc in (fakts, rfc8414, oidc):
        assert "urn:ietf:params:oauth:grant-type:device_code" in doc["grant_types_supported"]


@pytest.mark.django_db
@override_settings(DEPLOYMENT_HUB_CONFIGURE_URL="/hubconfigure/{code}")
def test_hub_endpoints_are_advertised(client):
    """The hub authorization, (deprecated) claim, and configure endpoints are
    advertised as absolute URLs (configure resolves the template against the
    base domain with `{code}` preserved)."""
    data = client.get(WELL_KNOWN).json()
    assert data["hub_authorization_endpoint"] == "http://lok/lok/o/hub-authorization/"
    assert "hub_device_code_start" not in data
    assert "hub_challenge_url" not in data
    assert data["hub_claim"] == "http://lok/lok/f/claimhub/"
    assert data["hub_configure"] == "http://lok/hubconfigure/{code}"


@pytest.mark.django_db
def test_discovery_endpoints_ignore_a_spoofed_forwarded_host(client):
    """Regression for the host-header poisoning finding.

    Endpoint URLs were built with `request.build_absolute_uri`, and with
    `ALLOWED_HOSTS = ["*"]` plus `USE_X_FORWARDED_HOST = True` (both defaults)
    the host came from the caller's own `X-Forwarded-Host` header. A relying
    party bootstrapping from a poisoned document would post its `code` and
    `client_secret` to the attacker's token endpoint, and fetch `jwks_uri` —
    i.e. the signing key it validates id_tokens against — from the attacker.
    """
    for path in (
        "/lok/.well-known/openid-configuration",
        "/lok/.well-known/oauth-authorization-server",
        "/lok/.well-known/fakts",
    ):
        response = client.get(path, HTTP_X_FORWARDED_HOST="evil.tld")
        assert response.status_code == 200, path
        body = response.content.decode()
        assert "evil.tld" not in body, f"{path} echoed an attacker-supplied host"

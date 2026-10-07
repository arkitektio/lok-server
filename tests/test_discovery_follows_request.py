"""`discovery_follows_request`: one deployment, logged into at every address it answers on.

By default every endpoint the discovery documents advertise is anchored to
`oidc_issuer`, so a caller cannot choose where clients are sent (see
`test_discovery_endpoints_ignore_a_spoofed_forwarded_host`). A deployment that is
reached at several addresses it owns — a self-contained hub: `localhost` from the
host, the gateway's name from its own docker network — opts out of that anchor,
and `oidc_issuer` becomes what it is in a token: a name.
"""

import pytest
from django.test import override_settings

WELL_KNOWN = "/lok/.well-known/fakts"

FOLLOWING = override_settings(
    DISCOVERY_FOLLOWS_REQUEST=True,
    OIDC_ISSUER="lok",
    # Root-relative, as a deployment that follows the request has to leave it: an
    # absolute configure URL is used verbatim, whatever the request.
    DEPLOYMENT_CONFIGURE_URL="/configure/{code}",
)


@pytest.mark.django_db
def test_off_by_default_the_request_host_changes_nothing(client):
    here = client.get(WELL_KNOWN, HTTP_HOST="localhost:7190").json()
    there = client.get(WELL_KNOWN, HTTP_HOST="gateway").json()
    assert here == there
    assert here["token_endpoint"] == "http://lok/lok/o/token/"


@pytest.mark.django_db
@FOLLOWING
def test_endpoints_are_advertised_at_the_address_that_was_asked(client):
    here = client.get(WELL_KNOWN, HTTP_HOST="localhost:7190").json()
    assert here["token_endpoint"] == "http://localhost:7190/lok/o/token/"
    assert here["device_authorization_endpoint"] == "http://localhost:7190/lok/o/app-authorization/"
    assert here["jwks_uri"] == "http://localhost:7190/lok/o/jwks/"
    assert here["base_url"] == "http://localhost:7190/lok/f/"
    # The page a person approves a device code on is at the deployment's root,
    # not behind lok's script name.
    assert here["configure"] == "http://localhost:7190/configure/{code}"

    there = client.get(WELL_KNOWN, HTTP_HOST="gateway").json()
    assert there["token_endpoint"] == "http://gateway/lok/o/token/"
    assert there["configure"] == "http://gateway/configure/{code}"


@pytest.mark.django_db
@FOLLOWING
def test_the_issuer_stays_the_configured_name(client):
    """The `iss` every service matches by string equality must not move with the
    address — a token got at one address has to be good at all of them."""
    for host in ("localhost:7190", "gateway", "192.168.1.5:7190"):
        assert client.get(WELL_KNOWN, HTTP_HOST=host).json()["issuer"] == "lok"
        oidc = client.get("/lok/.well-known/openid-configuration", HTTP_HOST=host).json()
        assert oidc["issuer"] == "lok"
        assert oidc["token_endpoint"] == f"http://{host}/lok/o/token/"


@pytest.mark.django_db
@FOLLOWING
def test_the_trust_bundle_url_follows_too(rf):
    from fakts.services.rendering import _hub_keys_base, _jwks_url

    request = rf.get(WELL_KNOWN, HTTP_HOST="gateway")
    assert _hub_keys_base(request) == "http://gateway/lok/.well-known/hub-keys/"
    assert _jwks_url(request) == "http://gateway/lok/o/jwks/"

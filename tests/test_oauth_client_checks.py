"""The OAuth2 client validation hooks that authlib relies on.

`check_redirect_uri` returned `True` unconditionally, with the real comparison
sitting unreachable below it. That is the single defence stopping a client's
authorization code from being redirected to a host the attacker controls, and
with no PKCE bound to the code it was enough for full account takeover.

`check_grant_type` and `check_response_type` were similarly non-functional (the
latter read a field that does not exist and would have raised `AttributeError`).
"""

import pytest

from authapp.models import AuthorizationCode
from fakts.models import Client as OAuth2Client
from tests import factories


@pytest.fixture
def client(db):
    return factories.make_oauth2_client(
        redirect_uris="https://rp.example/callback https://rp.example/other"
    )


class TestRedirectUri:
    def test_registered_uri_is_accepted(self, client):
        assert client.check_redirect_uri("https://rp.example/callback")
        assert client.check_redirect_uri("https://rp.example/other")

    def test_unregistered_host_is_refused(self, client):
        assert not client.check_redirect_uri("https://evil.tld/callback")

    def test_registered_uri_as_a_substring_is_refused(self, client):
        """The unreachable line under the old `return True` was `in self.redirect_uris`
        — a *substring* test on the space-joined string, which an attacker passes
        by embedding the real URI in their own URL."""
        assert not client.check_redirect_uri(
            "https://evil.tld/?next=https://rp.example/callback"
        )

    def test_path_prefix_is_refused(self, client):
        assert not client.check_redirect_uri("https://rp.example/callback/../../evil")
        assert not client.check_redirect_uri("https://rp.example/call")

    def test_empty_is_refused(self, client):
        assert not client.check_redirect_uri("")
        assert not client.check_redirect_uri(None)

    def test_client_with_no_registered_uris_accepts_nothing(self, db):
        bare = factories.make_oauth2_client(redirect_uris="")
        assert not bare.check_redirect_uri("https://anything.example/cb")


class TestClientSecret:
    def test_correct_secret_accepted_wrong_refused(self, client):
        assert client.check_client_secret(client.client_secret)
        assert not client.check_client_secret("wrong")
        assert not client.check_client_secret("")
        assert not client.check_client_secret(None)


class TestGrantAndResponseType:
    def test_registered_grants_allowed(self, client):
        for grant in ("authorization_code", "refresh_token", "client_credentials"):
            assert client.check_grant_type(grant)

    def test_unregistered_grant_refused(self, client):
        """Previously every grant returned True regardless of registration."""
        client.grant_types = "client_credentials"
        assert not client.check_grant_type("authorization_code")
        assert client.check_grant_type("client_credentials")

    def test_unknown_grant_refused_without_raising(self, client):
        assert not client.check_grant_type("implicit")
        assert not client.check_grant_type("password")

    def test_response_type_does_not_raise(self, client):
        """It read `self.response_type`; the field is `response_types`."""
        assert client.check_response_type("code")
        assert not client.check_response_type("token")


@pytest.mark.django_db
def test_authorization_code_stores_pkce_challenge():
    """PKCE fields must persist, or `CodeChallenge` has nothing to verify against."""
    membership = factories.make_membership()
    code = AuthorizationCode.objects.create(
        membership=membership,
        client_id="cid",
        code="abc",
        redirect_uri="https://rp.example/callback",
        code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        code_challenge_method="S256",
    )
    fresh = AuthorizationCode.objects.get(pk=code.pk)
    assert fresh.code_challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert fresh.code_challenge_method == "S256"


@pytest.mark.django_db
def test_pkce_extension_is_registered():
    """Registration is the whole fix — without it a `code_challenge` is ignored."""
    from authlib.oauth2.rfc7636 import CodeChallenge

    from authapp.server import server

    grant_extensions = [
        ext
        for _grant_cls, extensions in [
            (g[0], g[1]) for g in getattr(server, "_authorization_grants", [])
        ]
        for ext in (extensions or [])
    ]
    assert any(isinstance(ext, CodeChallenge) for ext in grant_extensions), (
        "CodeChallenge is not registered on any grant"
    )


@pytest.mark.django_db
def test_access_tokens_expire_in_an_hour_not_ten_days():
    """authlib's default for `authorization_code` and `client_credentials` is
    864000s. Verification never touches the DB, so `revoked` is not consulted
    outside `/o/user_info/` — expiry is the only control there is."""
    from authapp.server import ACCESS_TOKEN_EXPIRES_IN, server

    generator = server._token_generators["default"]

    for grant_type in ("authorization_code", "client_credentials", "refresh_token"):
        assert generator._get_expires_in(None, grant_type) == ACCESS_TOKEN_EXPIRES_IN

    assert ACCESS_TOKEN_EXPIRES_IN <= 3600

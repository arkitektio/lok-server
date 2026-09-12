"""OIDC Core compliance for claim/scope coupling, UserInfo access, and `nonce`.

Three deviations this covers, all found by auditing lok against the specs
after the fakts-grant consolidation:

- §5.3.1: UserInfo MUST serve any token obtained with the `openid` scope. It
  used to demand `profile`, which only ever worked because `ensureopenid`
  provisions every relying party with "openid profile email".
- §5.4: claims are bought by scopes. Both claim producers took a `scope`
  argument and discarded it, so an RP granted nothing but `openid` still got
  the user's email — in its id_token *and* from UserInfo. authlib does not
  filter downstream, so the filtering has to happen in lok.
- §3.1.2.1: `nonce` is OPTIONAL for the code flow, and no discovery field can
  advertise a stricter rule. It is now per-client, off by default.
"""

import base64
import hashlib
import json
import secrets as _secrets

import pytest
from django.urls import reverse
from django.utils import timezone

from authapp.models import AuthorizationCode
from tests import factories


REDIRECT_URI = "https://rp.example/cb"
VERIFIER = "a-very-long-code-verifier-string-for-pkce-testing-1234567890"


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _exchange(client, scope: str):
    """Run a code exchange for `scope` and return (membership, token response body)."""
    membership = factories.make_membership()
    membership.user.email = "real@example.com"
    membership.user.save()
    rp = factories.make_oauth2_client(membership=membership, redirect_uris=REDIRECT_URI)

    code = _secrets.token_urlsafe(48)
    AuthorizationCode.objects.create(
        membership=membership,
        client_id=rp.client_id,
        code=code,
        redirect_uri=REDIRECT_URI,
        scope=scope,
        nonce=None,
        code_challenge=_s256(VERIFIER),
        code_challenge_method="S256",
        auth_time=int(timezone.now().timestamp()),
    )

    resp = client.post(
        reverse("token"),
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": rp.client_id,
            "client_secret": rp.client_secret,
            "code_verifier": VERIFIER,
        },
        secure=True,
    )
    assert resp.status_code == 200, resp.content
    return membership, resp.json()


# --- §5.3.1 / §5.4: UserInfo access and claim gating ----------------------


@pytest.mark.django_db(transaction=True)
def test_userinfo_serves_a_token_scoped_only_openid(client):
    """§5.3.1: `openid` alone is enough to reach UserInfo.

    The endpoint used to be gated on `profile`, so a relying party asking for
    nothing but identity got a 403 from the one endpoint OIDC guarantees it.
    """
    membership, body = _exchange(client, "openid")

    resp = client.get(
        reverse("user_info"),
        HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        secure=True,
    )

    assert resp.status_code == 200, resp.content
    claims = resp.json()
    assert claims["sub"] == str(membership.user.id)
    # ...and nothing the granted scope did not buy.
    assert "email" not in claims
    assert "name" not in claims
    assert "preferred_username" not in claims


@pytest.mark.django_db(transaction=True)
def test_userinfo_claims_follow_the_granted_scope(client):
    """§5.4: `profile` buys the name claims, `email` buys the address."""
    membership, body = _exchange(client, "openid email")

    claims = client.get(
        reverse("user_info"),
        HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        secure=True,
    ).json()

    assert claims["email"] == "real@example.com"
    assert "name" not in claims
    assert "nickname" not in claims

    _membership2, body2 = _exchange(client, "openid profile")
    claims2 = client.get(
        reverse("user_info"),
        HTTP_AUTHORIZATION=f"Bearer {body2['access_token']}",
        secure=True,
    ).json()

    assert claims2["preferred_username"]
    assert "email" not in claims2


@pytest.mark.django_db(transaction=True)
def test_id_token_claims_follow_the_granted_scope(client):
    """The id_token is filtered by the same helper — authlib does not filter it.

    `OpenIDCode` copies whatever `generate_user_info` returns straight into the
    id_token payload, so an unfiltered producer leaked the email there too,
    where it is signed and long-lived.
    """
    _membership, body = _exchange(client, "openid")
    claims = _decode_jwt_payload(body["id_token"])

    assert claims["sub"]
    assert "email" not in claims
    assert "name" not in claims

    _membership2, body2 = _exchange(client, "openid profile email")
    claims2 = _decode_jwt_payload(body2["id_token"])

    assert claims2["email"] == "real@example.com"
    assert claims2["preferred_username"]


@pytest.mark.django_db(transaction=True)
def test_id_token_and_userinfo_sub_agree_over_the_wire(client):
    """§5.3.2: the two `sub` values must match for the same client."""
    _membership, body = _exchange(client, "openid profile email")

    id_token_sub = _decode_jwt_payload(body["id_token"])["sub"]
    userinfo_sub = client.get(
        reverse("user_info"),
        HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        secure=True,
    ).json()["sub"]

    assert id_token_sub == userinfo_sub


@pytest.mark.django_db(transaction=True)
def test_userinfo_does_not_emit_the_literal_scope_placeholder(client):
    """Regression: the response carried `"scope": "scope"` — a literal string."""
    _membership, body = _exchange(client, "openid profile email")

    claims = client.get(
        reverse("user_info"),
        HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        secure=True,
    ).json()

    assert claims.get("scope") != "scope"


@pytest.mark.django_db(transaction=True)
def test_org_and_roles_are_still_emitted_unconditionally(client):
    """lok's own claims are not scope-gated: authentikate and every resource
    server already read them, and no client requests a scope for them."""
    membership, body = _exchange(client, "openid")

    claims = client.get(
        reverse("user_info"),
        HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        secure=True,
    ).json()

    assert claims["org"] == str(membership.organization_id)
    assert claims["roles"] == []


# --- §3.1.2.1: nonce is optional unless the client opts in ----------------


def _authorize(client, rp, **extra):
    params = {
        "response_type": "code",
        "client_id": rp.client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid",
        "state": "s",
        "code_challenge": _s256(VERIFIER),
        "code_challenge_method": "S256",
        "allow": "true",
    }
    params.update(extra)
    return client.post(reverse("authorize"), params, secure=True)


@pytest.mark.django_db
def test_code_flow_without_a_nonce_succeeds_by_default(client):
    """§3.1.2.1 makes `nonce` OPTIONAL for the code flow.

    lok required it server-wide, and no discovery field can advertise such a
    requirement — so a conforming relying party had no way to learn about it
    and just failed with an opaque error.
    """
    from urllib.parse import parse_qs, urlparse

    membership = factories.make_membership()
    rp = factories.make_oauth2_client(membership=membership, redirect_uris=REDIRECT_URI)
    client.force_login(membership.user)

    resp = _authorize(client, rp, organization=str(membership.organization_id))

    assert resp.status_code == 302
    qs = parse_qs(urlparse(resp["Location"]).query)
    assert qs["code"] and qs["code"][0]


@pytest.mark.django_db
def test_a_client_can_opt_in_to_requiring_a_nonce(client):
    """The stricter rule survives, but per-client and agreed out of band."""
    from urllib.parse import parse_qs, urlparse

    membership = factories.make_membership()
    rp = factories.make_oauth2_client(
        membership=membership,
        redirect_uris=REDIRECT_URI,
        require_nonce=True,
    )
    client.force_login(membership.user)

    refused = _authorize(client, rp, organization=str(membership.organization_id))
    assert refused.status_code == 302
    qs = parse_qs(urlparse(refused["Location"]).query)
    assert qs["error"] == ["invalid_request"]
    assert "code" not in qs

    accepted = _authorize(
        client, rp, organization=str(membership.organization_id), nonce="n-0S6"
    )
    assert accepted.status_code == 302
    assert "code" in parse_qs(urlparse(accepted["Location"]).query)


@pytest.mark.django_db
def test_a_replayed_nonce_is_still_rejected(client):
    """The UsedNonce replay defence is unaffected by the relaxation."""
    from urllib.parse import parse_qs, urlparse

    membership = factories.make_membership()
    rp = factories.make_oauth2_client(membership=membership, redirect_uris=REDIRECT_URI)
    client.force_login(membership.user)

    first = _authorize(client, rp, organization=str(membership.organization_id), nonce="n-replay")
    assert "code" in parse_qs(urlparse(first["Location"]).query)

    replay = _authorize(client, rp, organization=str(membership.organization_id), nonce="n-replay")
    qs = parse_qs(urlparse(replay["Location"]).query)
    assert "code" not in qs
    assert qs["error"]


# --- provisioning ---------------------------------------------------------


@pytest.mark.django_db
def test_ensureopenid_plumbs_scope_and_require_nonce(settings):
    from django.core.management import call_command
    from fakts.models import Client as OAuth2Client

    settings.ENSURED_OPENID_APPS = [
        {
            "client_id": "identity-only-rp",
            "client_secret": "s3cret",
            "redirect_uris": ["https://rp.example/cb"],
            "scope": "openid",
            "require_nonce": True,
        }
    ]
    call_command("ensureopenid")

    rp = OAuth2Client.objects.get(client_id="identity-only-rp")
    assert rp.scope == "openid"
    assert rp.require_nonce is True


def test_config_rejects_a_scope_lok_does_not_advertise():
    from lok_server.configuration import OpenIDAppSettings

    base = dict(
        client_name="rp",
        client_id="rp",
        client_secret="s",
        redirect_uris=["https://rp.example/cb"],
    )

    with pytest.raises(ValueError):
        OpenIDAppSettings(**base, scope="openid groups")
    # `openid` itself is mandatory for a relying party.
    with pytest.raises(ValueError):
        OpenIDAppSettings(**base, scope="profile email")

    assert OpenIDAppSettings(**base, scope="openid").scope == "openid"

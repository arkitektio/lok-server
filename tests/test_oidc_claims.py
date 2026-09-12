"""Per-client OIDC `sub`/`email` shaping (email_template) and sub/userinfo agreement.

Covers the pure helpers, config-time template validation, provisioning via
`ensureopenid`, and — most importantly — that the id_token path
(`grants.OpenIDCode.generate_user_info`) and the userinfo path agree on `sub`
for the same client (OIDC Core §5.3.2).
"""

import pytest

from authapp import oidc_claims
from authapp.grants import OpenIDCode
from tests import factories


# --- pure helpers ---------------------------------------------------------


def test_validate_email_template_accepts_known_variables():
    oidc_claims.validate_email_template("{username}+{org_slug}@corp.example")


@pytest.mark.parametrize(
    "template",
    [
        "{unknown}@corp.example",   # unknown variable
        "{user.email}",             # attribute access is rejected
        "static@corp.example",      # no variables at all
        "{username",                # malformed braces
    ],
)
def test_validate_email_template_rejects_bad_templates(template):
    with pytest.raises(ValueError):
        oidc_claims.validate_email_template(template)


@pytest.mark.django_db
def test_resolve_sub_is_always_the_user_id():
    """One human is one subject, in every organization they belong to.

    Relying parties tell a person's organizations apart on (sub, org), not by
    the subject alone -- which is why the old membership_is_subject option is
    gone. A membership-shaped sub here would silently split one human into
    several identities downstream.
    """
    membership = factories.make_membership()
    assert oidc_claims.resolve_sub(membership) == str(membership.user.id)
    assert oidc_claims.resolve_sub(membership) != str(membership.id) or membership.id == membership.user.id


@pytest.mark.django_db
def test_resolve_email_template_and_default():
    membership = factories.make_membership()
    membership.user.email = "real@example.com"
    membership.user.save()

    assert oidc_claims.resolve_email(membership, "{username}@corp.example") == f"{membership.user.username}@corp.example"
    # No template -> user's own email.
    assert oidc_claims.resolve_email(membership, None) == "real@example.com"


@pytest.mark.django_db
def test_resolve_email_coerces_none_and_falls_back():
    membership = factories.make_membership()
    membership.user.email = None
    membership.user.save()

    # Nullable source (email) renders empty, never the literal "None".
    assert oidc_claims.resolve_email(membership, "x{email}y") == "xy"
    # No template + no email -> synthetic noreply address.
    assert oidc_claims.resolve_email(membership, None) == f"{membership.pk}@users.noreply"


# --- config validation ----------------------------------------------------


def test_config_rejects_invalid_email_template():
    from lok_server.configuration import OpenIDAppSettings

    with pytest.raises(ValueError):
        OpenIDAppSettings(
            client_name="X",
            client_id="x",
            client_secret="s",
            email_template="{nope}@corp.example",
        )


# --- provisioning ---------------------------------------------------------


@pytest.mark.django_db
def test_ensureopenid_provisions_new_fields(settings):
    from django.core.management import call_command
    from fakts.models import Client as OAuth2Client

    settings.ENSURED_OPENID_APPS = [
        {
            "client_id": "rp-1",
            "client_secret": "sekret",
            "redirect_uris": ["https://rp.example/cb"],
            "email_template": "{username}@corp.example",
        }
    ]
    call_command("ensureopenid")

    client = OAuth2Client.objects.get(client_id="rp-1")
    assert client.email_template == "{username}@corp.example"


# --- id_token vs userinfo agreement --------------------------------------


@pytest.mark.django_db
def test_id_token_and_userinfo_sub_agree():
    """The `sub` computed for the id_token must equal the one the userinfo
    endpoint computes for the same client (OIDC Core §5.3.2)."""
    membership = factories.make_membership()
    client = factories.make_oauth2_client(
        membership=membership,
        email_template="{username}@corp.example",
    )

    # id_token path (grants.OpenIDCode): client is stashed on the request.user
    # membership by encode_id_token(); replicate that stash here.
    membership._oauth_client = client
    info = OpenIDCode(require_nonce=True).generate_user_info(membership, "openid profile email")

    # userinfo path (views.user_info): client recovered by client_id.
    userinfo_sub = oidc_claims.resolve_sub(membership)

    expected = str(membership.user.id)
    assert info["sub"] == expected
    assert info["sub"] == userinfo_sub
    assert info["email"] == f"{membership.user.username}@corp.example"


@pytest.mark.django_db(transaction=True)
def test_token_endpoint_id_token_carries_configured_sub_and_email(client):
    """End-to-end: exchange an auth code at the token endpoint and decode the
    real id_token, asserting the per-client `sub`/`email` policy took effect."""
    import base64
    import json
    import secrets as _secrets
    from django.urls import reverse
    from django.utils import timezone
    from authapp.models import AuthorizationCode

    membership = factories.make_membership()
    rp = factories.make_oauth2_client(
        membership=membership,
        redirect_uris="https://rp.example/cb",
        email_template="{username}@corp.example",
    )

    code = _secrets.token_urlsafe(48)
    AuthorizationCode.objects.create(
        membership=membership,
        client_id=rp.client_id,
        code=code,
        redirect_uri="https://rp.example/cb",
        scope="openid profile email",
        nonce="n-0S6",
        auth_time=int(timezone.now().timestamp()),
    )

    resp = client.post(
        reverse("token"),
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://rp.example/cb",
            "client_id": rp.client_id,
            "client_secret": rp.client_secret,
        },
        secure=True,  # authlib rejects the token endpoint over plain HTTP
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "id_token" in body, body

    payload_b64 = body["id_token"].split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # pad for urlsafe_b64decode
    claims = json.loads(base64.urlsafe_b64decode(payload_b64))

    expected = str(membership.user.id)
    assert claims["sub"] == expected
    assert claims["email"] == f"{membership.user.username}@corp.example"

"""Passkeys/WebAuthn are advertised to the SPA, and configured coherently.

allauth defaults ``MFA_SUPPORTED_TYPES`` to ``["recovery_codes", "totp"]`` and
gates both passkey flags on ``"webauthn"`` being in that list, so enabling
passkeys is three coupled settings rather than one — and setting
``MFA_SUPPORTED_TYPES`` at all *replaces* the default, which is an easy way to
silently disable TOTP for everyone already enrolled. These tests pin the wire
contract the kontrol SPA reads off ``/config``, and the config-level guards that
turn allauth's boot-time system checks into a config error with a reason.

NOTE: allauth registers its webauthn URLs at IMPORT time
(``allauth/mfa/urls.py:18``), so ``override_settings`` cannot make those routes
appear or disappear. That is why everything here asserts on ``/config``, which
reads the MFA app settings per request, and never on URL resolution.
"""

import pytest
from django.conf import settings
from django.test import override_settings
from pydantic import ValidationError

from lok_server.configuration import AccountSettings

BROWSER = "/lok/_allauth/browser/v1"


@pytest.mark.django_db
def test_config_advertises_webauthn_and_passkey_login(client):
    """The SPA sees webauthn among the supported types, and passkey login on."""
    mfa = client.get(f"{BROWSER}/config").json()["data"]["mfa"]
    assert "webauthn" in mfa["supported_types"]
    assert mfa["passkey_login_enabled"] is True


@pytest.mark.django_db
def test_enabling_webauthn_keeps_the_existing_factors(client):
    """MFA_SUPPORTED_TYPES replaces allauth's default rather than extending it,
    so TOTP and recovery codes must be re-listed — a regression here would log
    every enrolled user out of their second factor."""
    mfa = client.get(f"{BROWSER}/config").json()["data"]["mfa"]
    assert {"recovery_codes", "totp"} <= set(mfa["supported_types"])


@pytest.mark.django_db
@override_settings(MFA_SUPPORTED_TYPES=["recovery_codes", "totp"])
def test_config_hides_passkey_login_when_webauthn_off(client):
    """allauth ANDs PASSKEY_LOGIN_ENABLED with 'webauthn' in SUPPORTED_TYPES, so
    dropping the type is enough to hide the flow from the SPA."""
    mfa = client.get(f"{BROWSER}/config").json()["data"]["mfa"]
    assert "webauthn" not in mfa["supported_types"]
    assert mfa["passkey_login_enabled"] is False


@pytest.mark.django_db
def test_config_reports_passkey_signup_state(client):
    """allauth does not advertise MFA_PASSKEY_SIGNUP_ENABLED, so lok's config
    view adds it — without it the SPA cannot tell whether /auth/webauthn/signup
    is mounted, and would offer a sign-up route that 404s."""
    mfa = client.get(f"{BROWSER}/config").json()["data"]["mfa"]
    assert mfa["passkey_signup_enabled"] is False


@pytest.mark.django_db
@override_settings(MFA_PASSKEY_SIGNUP_ENABLED=True)
def test_config_reports_passkey_signup_when_enabled(client):
    mfa = client.get(f"{BROWSER}/config").json()["data"]["mfa"]
    assert mfa["passkey_signup_enabled"] is True


def test_settings_install_the_rp_id_adapter():
    """Without the custom adapter the rp_id knob is silently inert — allauth has
    no MFA_WEBAUTHN_RP_ID setting of its own."""
    assert settings.MFA_ADAPTER == "lok_server.mfa_adapter.LokMFAAdapter"


def test_totp_issuer_is_named():
    """allauth's default is an empty string, which leaves the entry unlabelled in
    the user's authenticator app."""
    assert settings.MFA_TOTP_ISSUER


def test_passkey_login_requires_webauthn():
    with pytest.raises(ValidationError, match="mfa_webauthn_enabled"):
        AccountSettings(mfa_webauthn_enabled=False, mfa_passkey_login_enabled=True)


def test_passkey_signup_requires_verification_by_code():
    """allauth raises a Critical system check for passkey signup without
    code-based email verification; fail at config load, with the reason."""
    with pytest.raises(ValidationError, match="email_verification_by_code_enabled"):
        AccountSettings(mfa_passkey_signup_enabled=True)


def test_passkey_signup_accepted_with_verification_by_code():
    account = AccountSettings(
        mfa_passkey_signup_enabled=True,
        email_verification_by_code_enabled=True,
    )
    assert account.mfa_passkey_signup_enabled


def test_rp_id_must_cover_every_host_the_spa_uses():
    """The rule that makes pinning necessary: a browser accepts an RP ID that is
    a registrable-domain suffix of the page origin, but not a sibling host."""
    from fido2.rpid import verify_rp_id

    assert verify_rp_id("arkitekt.live", "https://go.arkitekt.live")
    assert verify_rp_id("arkitekt.live", "https://beta.arkitekt.live")
    # Exactly the failure the pinning exists to prevent: a passkey enrolled on
    # `go.` is not usable on `beta.` when the RP ID came from the Host header.
    assert not verify_rp_id("go.arkitekt.live", "https://beta.arkitekt.live")


def test_passkey_config_is_documented():
    """Every new account.* key must appear in CONFIG.md's account table."""
    from pathlib import Path

    doc = Path(settings.BASE_DIR, "CONFIG.md").read_text()
    for key, env in [
        ("mfa_webauthn_enabled", "ACCOUNT__MFA_WEBAUTHN_ENABLED"),
        ("mfa_passkey_login_enabled", "ACCOUNT__MFA_PASSKEY_LOGIN_ENABLED"),
        ("mfa_passkey_signup_enabled", "ACCOUNT__MFA_PASSKEY_SIGNUP_ENABLED"),
        ("email_verification_by_code_enabled", "ACCOUNT__EMAIL_VERIFICATION_BY_CODE_ENABLED"),
        ("mfa_webauthn_rp_id", "ACCOUNT__MFA_WEBAUTHN_RP_ID"),
        ("mfa_totp_issuer", "ACCOUNT__MFA_TOTP_ISSUER"),
    ]:
        assert f"`{key}` | `{env}`" in doc, f"{key} missing from CONFIG.md"

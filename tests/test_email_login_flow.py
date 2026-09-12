"""Behavioral test: login-via-email + mandatory account verification.

Exercises the real allauth-headless HTTP endpoints under the email-login config
(``ACCOUNT_LOGIN_METHODS={"email"}``, ``ACCOUNT_EMAIL_VERIFICATION="mandatory"``)
to prove both halves of the feature actually work end to end — not just that the
settings parse. Uses the in-memory email backend so we can assert a verification
mail is really sent.
"""

import json

import pytest
from django.core import mail
from django.test import override_settings

BROWSER = "/lok/_allauth/browser/v1"


def _post(client, path, payload):
    return client.post(f"{BROWSER}{path}", data=json.dumps(payload), content_type="application/json")


EMAIL_LOGIN = override_settings(
    ACCOUNT_LOGIN_METHODS={"email"},
    ACCOUNT_SIGNUP_FIELDS=["email*", "password1*", "password2*"],
    ACCOUNT_EMAIL_VERIFICATION="mandatory",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


@pytest.mark.django_db
@EMAIL_LOGIN
def test_config_advertises_email_login(client):
    """The headless capability config the SPA reads reflects email login."""
    resp = client.get(f"{BROWSER}/config")
    account = resp.json()["data"]["account"]
    assert account["login_methods"] == ["email"]
    assert account["authentication_method"] == "email"


@pytest.mark.django_db
@EMAIL_LOGIN
def test_email_signup_sends_verification_and_gates_login(client):
    """Signing up with an email sends a verification mail and blocks login
    until the address is confirmed."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    # 1. Sign up with the exact payload shape the SPA now sends ({email, password}).
    resp = _post(client, "/auth/signup", {"email": "newuser@example.com", "password": "sup3r-secret-pw"})

    # Account created, email recorded but unverified.
    user = get_user_model().objects.get(email="newuser@example.com")
    assert not EmailAddress.objects.get(user=user, email="newuser@example.com").verified

    # Verification mail actually sent to the new user (the "verification" half).
    assert len(mail.outbox) >= 1
    assert any("newuser@example.com" in m.to for m in mail.outbox)

    # Login is gated: mandatory verification means auth is not complete (401),
    # with a pending verify_email flow — not a 200 success.
    assert resp.status_code == 401
    flows = resp.json()["data"]["flows"]
    assert any(f["id"] == "verify_email" for f in flows)


@pytest.mark.django_db
@EMAIL_LOGIN
def test_verify_then_login_succeeds(client):
    """After verifying the email, logging in with email + password succeeds."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    _post(client, "/auth/signup", {"email": "verify@example.com", "password": "sup3r-secret-pw"})
    user = get_user_model().objects.get(email="verify@example.com")

    # Simulate the user clicking the emailed verification link.
    ea = EmailAddress.objects.get(user=user, email="verify@example.com")
    ea.verified = True
    ea.set_as_primary(conditional=True)
    ea.save()

    # Fresh session: log in with the email as the identifier.
    client.logout()
    resp = _post(client, "/auth/login", {"email": "verify@example.com", "password": "sup3r-secret-pw"})
    assert resp.status_code == 200, resp.content
    assert resp.json()["meta"]["is_authenticated"] is True


@pytest.mark.django_db
@EMAIL_LOGIN
def test_seeded_user_logs_in_by_email_under_mandatory_verification(client, settings):
    """A user provisioned from config (`ensureusers`) can log in by email even
    under mandatory verification — `ensureusers` registers a verified, primary
    EmailAddress, so no manual confirmation is needed."""
    from django.core.management import call_command

    settings.ENSURED_USERS = [
        {"username": "seeded", "password": "seeded-pw-12345", "email": "seeded@example.com"}
    ]
    call_command("ensureusers")

    resp = _post(client, "/auth/login", {"email": "seeded@example.com", "password": "seeded-pw-12345"})
    assert resp.status_code == 200, resp.content
    assert resp.json()["meta"]["is_authenticated"] is True

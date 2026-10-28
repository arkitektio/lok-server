"""`account.allow_signup`: whether anybody can create an account.

A coordination server on the internet whose accounts an administrator makes closes signup;
everything else about accounts — logging in, the headless config the frontend reads — keeps
working, and says so.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, override_settings

BROWSER = "/lok/_allauth/browser/v1"

# Accounts by username, as the test config has them — pinned, so another test's account
# settings cannot decide what a signup here has to carry.
USERNAME_ACCOUNTS = override_settings(
    ACCOUNT_LOGIN_METHODS={"username"},
    ACCOUNT_SIGNUP_FIELDS=["username*", "password1*"],
    ACCOUNT_EMAIL_VERIFICATION="none",
)


def _signup(client: Client, username: str) -> HttpResponse:
    return client.post(
        f"{BROWSER}/auth/signup",
        data=json.dumps({"username": username, "password": "sup3r-secret-pw"}),
        content_type="application/json",
    )


@pytest.mark.django_db
@USERNAME_ACCOUNTS
def test_signup_is_open_by_default(client: Client) -> None:
    """Unless told otherwise, anybody can sign up, and the frontend is told so."""
    assert client.get(f"{BROWSER}/config").json()["data"]["account"]["is_open_for_signup"] is True
    response = _signup(client, "newcomer")
    assert response.status_code == 200, response.content
    assert get_user_model().objects.filter(username="newcomer").exists()


@pytest.mark.django_db
@USERNAME_ACCOUNTS
@override_settings(LOK_ALLOW_SIGNUP=False)
def test_closed_signup_creates_no_account_and_says_so(client: Client) -> None:
    """Closed, a signup is refused and creates nothing, and the frontend is told so."""
    assert client.get(f"{BROWSER}/config").json()["data"]["account"]["is_open_for_signup"] is False
    response = _signup(client, "stranger")
    assert response.status_code == 403, response.content
    assert not get_user_model().objects.filter(username="stranger").exists()


@pytest.mark.django_db
@USERNAME_ACCOUNTS
@override_settings(LOK_ALLOW_SIGNUP=False)
def test_closed_signup_still_lets_an_account_log_in(client: Client) -> None:
    """Closed, an account that exists still logs in."""
    get_user_model().objects.create_user(username="member", password="sup3r-secret-pw")
    response = client.post(
        f"{BROWSER}/auth/login",
        data=json.dumps({"username": "member", "password": "sup3r-secret-pw"}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content


def test_the_setting_is_read_from_the_config() -> None:
    """`account.allow_signup` is a setting of the config, open unless said."""
    from lok_server.configuration import AccountSettings

    assert AccountSettings().allow_signup is True
    assert AccountSettings(allow_signup=False).allow_signup is False

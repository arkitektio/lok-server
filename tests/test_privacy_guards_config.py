"""The headless capability config advertises the `privacy_guards` policy.

Proves the custom config-view override (lok_server.headless_config) both injects
`privacy_guards` and preserves allauth's own config blocks (account/socialaccount),
so the SPA can gate integrated widgets (Google One Tap) without losing anything.
"""

import pytest
from django.test import override_settings

BROWSER = "/lok/_allauth/browser/v1"


@pytest.mark.django_db
def test_config_advertises_privacy_guards_default(client):
    """Default deployment reports the opt-in policy alongside allauth's blocks."""
    data = client.get(f"{BROWSER}/config").json()["data"]
    assert data["privacy_guards"] == "opt-in"
    # The override must not drop allauth's own assembly.
    assert "account" in data
    assert "socialaccount" in data


@pytest.mark.django_db
@override_settings(PRIVACY_GUARDS="strict")
def test_config_reflects_strict_policy(client):
    """Setting the policy is reflected verbatim on the wire."""
    data = client.get(f"{BROWSER}/config").json()["data"]
    assert data["privacy_guards"] == "strict"


@pytest.mark.django_db
@override_settings(PRIVACY_GUARDS="disabled")
def test_config_reflects_disabled_policy(client):
    data = client.get(f"{BROWSER}/config").json()["data"]
    assert data["privacy_guards"] == "disabled"

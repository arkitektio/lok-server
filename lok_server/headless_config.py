"""Privacy-aware override of allauth's headless ``/config`` endpoint.

allauth's :class:`allauth.headless.base.response.ConfigResponse` assembles a fixed
capability dict from per-module ``get_config_data`` functions and exposes no hook
to add extra keys. The SPA (kontrol) fetches this endpoint anonymously on the
login page, so it is the natural — and only pre-auth — channel for advertising the
``privacy_guards`` policy that gates integrated login widgets (Google One Tap).

We reproduce allauth's own assembly verbatim (reusing its module functions, so the
account / socialaccount / mfa / usersessions blocks stay identical and drift-free)
and append the single ``privacy_guards`` key. If a future allauth adds another
config module, mirror it here with one more ``if ... update`` line.
"""

from typing import Any

from allauth import app_settings as allauth_settings
from allauth.headless.base.response import get_config_data as _account_config_data
from allauth.headless.base.views import ConfigView
from allauth.headless.internal.restkit.response import APIResponse
from allauth.mfa import app_settings as mfa_settings
from django.conf import settings
from django.http import HttpRequest, HttpResponse


class PrivacyConfigResponse(APIResponse):
    """allauth's config payload plus the ``privacy_guards`` policy."""

    def __init__(self, request: HttpRequest) -> None:
        data: dict[str, Any] = _account_config_data(request)
        if allauth_settings.SOCIALACCOUNT_ENABLED:
            from allauth.headless.socialaccount.response import (
                get_config_data as socialaccount_config_data,
            )

            data.update(socialaccount_config_data(request))
        if allauth_settings.MFA_ENABLED:
            from allauth.headless.mfa.response import get_config_data as mfa_config_data

            data.update(mfa_config_data(request))
            # allauth reports `passkey_login_enabled` but not the signup flag, so
            # a SPA has no way to know whether /auth/webauthn/signup exists —
            # allauth only mounts that route when MFA_PASSKEY_SIGNUP_ENABLED is
            # on. Add it, so the sign-up page can hide an option that would
            # otherwise lead to a 404.
            data["mfa"]["passkey_signup_enabled"] = mfa_settings.PASSKEY_SIGNUP_ENABLED
        if allauth_settings.USERSESSIONS_ENABLED:
            from allauth.headless.usersessions.response import (
                get_config_data as usersessions_config_data,
            )

            data.update(usersessions_config_data(request))
        data["privacy_guards"] = settings.PRIVACY_GUARDS
        super().__init__(request, data=data)


class PrivacyConfigView(ConfigView):
    """Drop-in replacement for allauth's headless ConfigView."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return PrivacyConfigResponse(request)

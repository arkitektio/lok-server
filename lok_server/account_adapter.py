"""Account adapter that closes signup when the deployment says so.

allauth asks ``is_open_for_signup`` before every signup — headless, the server-rendered
form, and a social login that would create an account, whose adapter defers to this one —
and reports the answer to the frontend as ``account.is_open_for_signup``. A deployment on the
internet whose accounts are made by an administrator sets ``account.allow_signup: false``;
logging in, and accepting an invite as an account that exists, are untouched.
"""

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.http import HttpRequest


class LokAccountAdapter(DefaultAccountAdapter):
    """allauth's account adapter, with signup open only where `account.allow_signup` says."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Whether a new account may be created: the deployment's `account.allow_signup`."""
        return bool(getattr(settings, "LOK_ALLOW_SIGNUP", True))

"""MFA adapter that pins the WebAuthn Relying Party ID.

allauth derives the RP ID per request from ``request.get_host()``
(``allauth/mfa/adapter.py:149-153``) and exposes no setting to override it. That
binds every passkey to the exact hostname it was registered on, which breaks
quietly on a deployment answering from several subdomains off one database: a
credential enrolled on ``go.<domain>`` is not offered on ``beta.<domain>``, and
because ``get_credentials()`` hands fido2 all of the user's WebAuthn
authenticators regardless of RP, the user sees a generic "incorrect code" rather
than anything pointing at the cause. Deriving it from the Host header also means
an unvalidated ``X-Forwarded-Host`` feeds straight into the RP ID.

``MFA_WEBAUTHN_RP_ID`` pins it to the registrable parent domain instead. Browsers
accept an RP ID that is a registrable-domain suffix of the page origin
(``fido2/rpid.py:101-104``), so one credential then works from every subdomain.
"""

from typing import Dict

from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings


class LokMFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self) -> Dict[str, str]:
        entity = super().get_public_key_credential_rp_entity()
        rp_id = getattr(settings, "MFA_WEBAUTHN_RP_ID", None)
        if rp_id:
            entity["id"] = rp_id
        # `django.contrib.sites` is not installed, so allauth's _get_site_name()
        # falls back to the raw Host header. Show the deployment's own name —
        # this is what the user sees in their password manager / authenticator.
        deployment_name = getattr(settings, "DEPLOYMENT_NAME", None)
        if deployment_name:
            entity["name"] = deployment_name
        return entity

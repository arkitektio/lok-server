"""
authapp.revocation

RFC 7009 token revocation. Registered on the AuthorizationServer and served at
``/o/revoke/``.

Revoking a token sets ``OAuth2Token.revoked`` — which kills the refresh chain
immediately (the refresh grant is DB-backed), while the short-lived (1h) JWT
access token ages out on its own, matching the deployment's pure-JWT
verification model.
"""

from authlib.oauth2.rfc7009 import RevocationEndpoint

from .models import OAuth2Token


class MyRevocationEndpoint(RevocationEndpoint):
    # Public fakts clients authenticate with `none` (client_id only) — the
    # per-client gate is OAuth2Client.check_endpoint_auth_method; a token can
    # only ever be revoked by the client it was issued to (check_client).
    CLIENT_AUTH_METHODS = ["client_secret_basic", "client_secret_post", "none"]

    def query_token(self, token_string, token_type_hint):
        if not token_string:
            return None
        if token_type_hint == "access_token":
            return OAuth2Token.objects.filter(access_token=token_string).first()
        if token_type_hint == "refresh_token":
            return OAuth2Token.objects.filter(refresh_token=token_string).first()
        # No hint: try both (RFC 7009 §2.1).
        return (
            OAuth2Token.objects.filter(refresh_token=token_string).first()
            or OAuth2Token.objects.filter(access_token=token_string).first()
        )

    def revoke_token(self, token, request):
        token.revoked = True
        token.save(update_fields=["revoked"])

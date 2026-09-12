"""
authapp.bearer

Minimal Bearer access-token verification for plain Django views (outside the
GraphQL stack). Fakts clients authenticate ongoing requests — e.g. the
``/f/report/`` telemetry endpoint — with their JWT access token; the old
non-expiring opaque client token no longer exists.

Verification is pure-JWT (signature + expiry), matching how every other
consumer of these tokens validates them.
"""

import time

from django.conf import settings
from joserfc import jwt
from joserfc.jwk import RSAKey

_public_key = RSAKey.import_key(settings.PUBLIC_KEY)


class InvalidBearerToken(Exception):
    """Raised when a request carries no valid Bearer access token."""


def decode_bearer_token(request) -> dict:
    """Verify the request's Bearer JWT and return its claims.

    Raises :class:`InvalidBearerToken` on a missing header, bad signature, or
    expired token.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise InvalidBearerToken("Missing Bearer authorization header")

    token = header[len("Bearer ") :].strip()

    try:
        decoded = jwt.decode(token, _public_key, algorithms=["RS256"])
    except Exception as e:
        raise InvalidBearerToken(f"Invalid token: {e}") from e

    claims = decoded.claims
    exp = claims.get("exp")
    if exp is None or exp < time.time():
        raise InvalidBearerToken("Token expired")

    if claims.get("iss") != settings.OIDC_ISSUER:
        raise InvalidBearerToken("Token was not issued by this server")

    aud = claims.get("aud") or []
    if isinstance(aud, str):
        aud = [aud]
    if "lok" not in aud:
        raise InvalidBearerToken("Token is not addressed to lok")

    return claims

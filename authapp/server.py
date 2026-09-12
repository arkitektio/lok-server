"""
authapp.server

Creates and configures the application's AuthorizationServer instance.

This module registers the supported grant types and the token
generator used to produce JWT bearer tokens. Other modules should import
``server`` and use its helpers (for example
``server.create_token_response``) to handle protocol endpoints.
"""

from django.conf import settings
from authlib.integrations.django_oauth2 import AuthorizationServer, BearerTokenValidator, ResourceProtector
from fakts.models import Client
from .models import OAuth2Token
from .grants import AuthorizationCodeGrant, OpenIDCode, RefreshTokenGrant
from .fakts_grants import FaktsDeviceCodeGrant, FaktsRedeemGrant
from .token_generators import MyJWTBearerTokenGenerator
from authlib.oidc.core import UserInfo
from .token_generators import jwk_dict, public_jwks
from authlib.oidc.core.userinfo import UserInfoEndpoint


from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oauth2.rfc9068 import JWTBearerTokenValidator
from authlib.oauth2.rfc6749.errors import InvalidRequestError


class RequiredCodeChallenge(CodeChallenge):
    """PKCE required for confidential clients too, and S256 only.

    Two gaps in `CodeChallenge(required=True)`:

    1. It only enforces the verifier when `request.auth_method == "none"`
       (`validate_code_verifier`, authlib/oauth2/rfc7636/challenge.py), and
       `validate_code_challenge` returns early when neither `code_challenge`
       nor `code_challenge_method` is present. So every `client_secret_*`
       relying party could run a code flow with no challenge at all.
       Authorization-code interception is not a public-client-only problem —
       RFC 9700 §2.1.1 wants PKCE everywhere — so a challenge is required here
       at the authorization endpoint, which makes the base class demand the
       matching verifier at the token endpoint for free.
    2. authlib defaults a challenge with no method to `plain`, but the
       discovery documents advertise `code_challenge_methods_supported:
       ["S256"]`. `plain` is dropped so the advertisement is true and the weak
       method is not silently reachable.
    """

    DEFAULT_CODE_CHALLENGE_METHOD = "S256"
    SUPPORTED_CODE_CHALLENGE_METHOD = ["S256"]

    def validate_code_challenge(self, grant, redirect_uri):
        challenge = grant.request.payload.data.get("code_challenge")
        if not challenge:
            raise InvalidRequestError(
                description="Missing 'code_challenge' in request.",
                redirect_uri=redirect_uri,
            )
        return super().validate_code_challenge(grant, redirect_uri)


# The grant surface this server exposes, as advertised by every discovery
# document (/.well-known/fakts, /.well-known/oauth-authorization-server,
# /.well-known/openid-configuration). Keep in sync with the register_grant
# calls below.
#
# `urn:ietf:params:oauth:grant-type:device_code` is advertised even though the
# OIDC and RFC 8414 documents carry no `device_authorization_endpoint`: the
# grant here at /o/token/ is conforming RFC 8628, but the endpoint that mints
# device codes (/o/app-authorization/) is a private, manifest-driven one that
# also does dynamic client registration. See the comment in
# authapp.views._authorization_server_metadata.
GRANT_TYPES_SUPPORTED = [
    "authorization_code",
    "refresh_token",
    "urn:ietf:params:oauth:grant-type:device_code",
    "urn:fakts:grant-type:redeem",
]

# `none` is how the dynamically registered public fakts clients authenticate
# (client_id only); the secret methods serve confidential OIDC relying parties.
TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED = ["client_secret_basic", "client_secret_post", "none"]

# The AuthorizationServer is backed by the unified fakts Client (which
# implements authlib's ClientMixin directly) and OAuth2Token.
server = AuthorizationServer(Client, OAuth2Token)

# Register the project's supported grants.
#
# ClientCredentialsGrant is deliberately NOT registered: fakts clients use the
# device-code/redeem grants and the OIDC relying parties use authorization_code
# — nothing in the deployment exchanges client credentials, so the grant would
# only widen the attack surface of every confidential client's secret. The
# class is kept in grants.py for easy re-enable if a machine-to-machine
# integration ever needs it.
#
# PKCE (RFC 7636) for *every* client, public and confidential alike — see
# RequiredCodeChallenge below.
#
# `require_nonce=False` is the server-wide default because OIDC Core §3.1.2.1
# makes `nonce` OPTIONAL for the code flow and no discovery field can advertise
# a stricter rule. Clients that want it required opt in through
# `fakts.Client.require_nonce`, which
# `grants.OpenIDCode.validate_openid_authorization_request` applies per request.
# The `UsedNonce` replay defence is unaffected: it still fires for every client
# that does send a nonce.
server.register_grant(AuthorizationCodeGrant, [OpenIDCode(require_nonce=False), RequiredCodeChallenge()])
server.register_grant(RefreshTokenGrant)

# The canonical fakts grants: RFC 8628 device-code (interactive, approved in the
# kontrol frontend) and the headless redeem grant. Both issue the combined
# response — access token + refresh token + rendered service instances — for
# dynamically registered public clients. See authapp/fakts_grants.py.
server.register_grant(FaktsDeviceCodeGrant)
server.register_grant(FaktsRedeemGrant)

# RFC 7009 token revocation, served at /o/revoke/.
from .revocation import MyRevocationEndpoint  # noqa: E402  (needs `server` grants above conceptually grouped)

server.register_endpoint(MyRevocationEndpoint)
# Register a JWT bearer token generator under the default key. The
# generator is used to emit signed JWTs for access tokens.


class MyUserInfoEndpoint(UserInfoEndpoint):
    def get_issuer(self):
        return settings.OIDC_ISSUER

    def generate_user_info(self, user, scope):
        return UserInfo(
            sub=user.id,
            name=user.name,
        )

    def resolve_private_key(self):
        return jwk_dict


class MyBearerTokenValidator(JWTBearerTokenValidator):
    def authenticate_token(self, token_string):
        return OAuth2Token.objects.get(access_token=token_string)

    def get_jwks(self) -> dict:
        # Verification needs the public key only — never hand the private JWK
        # to a validator (it would be one ``.get_jwks()`` away from leaking).
        return public_jwks()


class Oauth2TokenValidator(BearerTokenValidator):
    pass


class RefreshTokenGenerator:
    def __call__(self, client, grant_type, user, scope):
        import secrets

        return secrets.token_urlsafe(48)


# Access-token lifetime, in seconds.
#
# Without an explicit generator authlib falls back to its own defaults
# (rfc6750/token.py: GRANT_TYPES_EXPIRES_IN), which are **864000 seconds — ten
# days** for both `authorization_code` and `client_credentials`, the two grants
# lok actually issues. That is the whole exposure window for a leaked token,
# because token verification on the consuming side is pure-JWT: it never reads
# the database, so `OAuth2Token.revoked` is not consulted and revocation has no
# effect outside `/o/user_info/`. Expiry is therefore the only control, and ten
# days is far too long for one.
#
# One hour matches the id_token's `exp` (authapp/grants.py) and leaves refresh
# tokens (30 days, `OAuth2Token.is_refresh_token_active`) to provide continuity —
# and those *are* revocable, since the refresh grant does hit the database.
ACCESS_TOKEN_EXPIRES_IN = 3600

# The bounds an organization may move its access-token lifetime between
# (`karakter.Organization.access_token_lifetime`). Some deployments run agents on
# links that cannot refresh hourly — a long-running acquisition, an instrument PC
# behind a firewall — and for those an hour is operationally too short.
#
# The cap is deliberately conservative, for the reason spelled out above: token
# verification on the consuming side is pure JWT, so `OAuth2Token.revoked` is
# never read and expiry is the *only* revocation control there is. A day is long
# enough to cover an unattended run and still well inside the refresh token's
# 30-day sliding window (`OAuth2Token.REFRESH_TOKEN_LIFETIME`), so the two
# lifetimes stay coherent: the refresh chain remains the thing that provides
# continuity, and it *is* revocable.
#
# The floor keeps a fat-fingered `0`/`30` from making every client's token expire
# before it can be used.
MIN_ACCESS_TOKEN_EXPIRES_IN = 300
MAX_ACCESS_TOKEN_EXPIRES_IN = 86400


def access_token_expires_in(client, grant_type) -> int:
    """The access-token lifetime for this client, in seconds.

    The server default (one hour) unless the client's organization has set its
    own `access_token_lifetime` — clamped into
    [MIN_ACCESS_TOKEN_EXPIRES_IN, MAX_ACCESS_TOKEN_EXPIRES_IN] *here* rather than
    trusted from the row, so neither a value stored before the cap existed nor one
    written straight into the database can outlive it.

    Must stay deterministic for a given client: authlib calls it twice per token
    (once for the JWT's `exp` claim, once for the response's `expires_in`), and
    the two have to agree.

    `organization` is null for staged registrations and for the global relying
    parties, so the attribute is read defensively — an OIDC login must not 500
    because there is no organization to ask.
    """
    organization = getattr(client, "organization", None)
    lifetime = getattr(organization, "access_token_lifetime", None) if organization else None
    if not lifetime:
        return ACCESS_TOKEN_EXPIRES_IN
    return max(MIN_ACCESS_TOKEN_EXPIRES_IN, min(int(lifetime), MAX_ACCESS_TOKEN_EXPIRES_IN))


server.register_token_generator(
    "default",
    MyJWTBearerTokenGenerator(
        issuer=settings.OIDC_ISSUER,
        refresh_token_generator=RefreshTokenGenerator(),
        expires_generator=access_token_expires_in,
    ),
)

resource_protector = ResourceProtector()
resource_protector.register_token_validator(Oauth2TokenValidator(OAuth2Token))
# server.register_endpoint(MyUserInfoEndpoint(server=server, resource_protector=resource_protector))

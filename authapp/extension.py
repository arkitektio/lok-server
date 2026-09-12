from typing import cast

from authentikate import errors as authentikate_errors
from authentikate.strawberry.extension import AuthentikateExtension, UserModel, JWTToken
from django.conf import settings
from django.core.exceptions import ValidationError

from karakter.models import User, Organization, Membership
from fakts.models import Client


# The audience every token addressed to *this* service carries. `get_audiences`
# in authapp/token_generators.py mints ["lok", *services] for a fakts client and
# ["lok"] for a hub, but [client_id] for a plain OIDC relying party — an RP's
# access token is deliberately *not* for lok.
LOK_AUDIENCE = "lok"


def assert_addressed_to_lok(token: JWTToken) -> None:
    """Reject a token that was not issued by us, or not issued *for* us.

    authentikate's `decode_token` validates the signature and `exp` and nothing
    else — no `iss`, no `aud`. Without this gate any lok-signed JWT authenticated
    the main GraphQL API, so an OIDC relying party (or anyone who obtained a
    token from one: its logs, a compromised or malicious RP) could replay a
    user's access token against /graphql and act as that user.

    This is the same check `authapp.bearer.decode_bearer_token` already applies
    at /f/report/; it belongs on every token-authenticated surface, not one.

    Static tokens are exempt only in the sense that they must still carry the
    right claims — `AuthentikateSettings.static_tokens` bypasses signature
    verification upstream of us and is a separate (test-only) concern.
    """
    if token.iss != settings.OIDC_ISSUER:
        raise authentikate_errors.InvalidJwtTokenError(
            "Token was not issued by this server"
        )

    audiences = token.aud or []
    if LOK_AUDIENCE not in audiences:
        raise authentikate_errors.InvalidJwtTokenError(
            "Token is not addressed to lok"
        )


def read_org_claim(token: JWTToken) -> str:
    """The organization pk this token is scoped to.

    ``org`` is a field authentikate declares on ``JWTToken``/``StaticToken`` (as
    of v4), so it is parsed off the verified payload like any other claim. This
    used to base64-decode ``token.raw`` by hand, because the library was
    ``extra="ignore"`` and silently dropped every claim it did not declare —
    ``org`` among them. That workaround is gone now that the field exists, which
    also means static tokens (whose ``raw`` is the literal "static_token", not a
    JWT) no longer need a separate carrier field.
    """
    org = token.org

    if not org:
        raise authentikate_errors.InvalidJwtTokenError(
            "Token does not name an organization"
        )
    return str(org)


async def expand_user_from_token(token: str):
    """Expand the user from the token"""
    # Implement your logic to expand the user from the token
    pass


class AuthAppExtension(AuthentikateExtension):
    """This is the extension class for directly authenticating users and
    clients from the token or header. It sets the user and client in the"""

    async def aexpand_token_context(self, token: JWTToken) -> tuple[User, Client, Organization, Membership]:
        """Expand the full auth context for a token using this project's models.

        authentikate (v2) drives ``on_operation`` through this single method
        rather than the per-entity ``aexpand_*`` helpers, so we compose them
        here to keep authentication backed by the karakter/fakts models.
        """
        assert_addressed_to_lok(token)
        organization = await self.aexpand_organization_from_token(token)
        user = await self.aexpand_user_from_token(token)
        client = await self.aexpand_client_from_token(token)
        membership = await self.aexpand_membership_from_user_and_organization(user, organization, token)
        return (cast(User, user), client, organization, membership)

    async def aexpand_user_from_token(self, token: JWTToken) -> "UserModel":
        """Expand a user from the provided JWT token"""

        return cast("UserModel", await User.objects.aget(id=token.sub))

    async def aexpand_client_from_token(self, token: JWTToken) -> "Client":
        """Expand a client from the provided JWT token"""

        return cast("Client", await Client.objects.aget(client_id=token.client_id))

    async def aexpand_organization_from_token(self, token: JWTToken) -> "Organization":
        """Expand an organization from the provided JWT token.

        Resolved by primary key. This used to be `aget(slug=token.active_org)`,
        which keyed the tenancy boundary for the entire token-authenticated
        schema on a user-chosen, mutable string.
        """
        org_pk = read_org_claim(token)
        try:
            return cast("Organization", await Organization.objects.aget(pk=org_pk))
        except (Organization.DoesNotExist, ValueError, ValidationError) as exc:
            # A malformed pk must fail closed like an unknown one, not 500.
            raise authentikate_errors.InvalidJwtTokenError(
                "Token names an organization that does not exist"
            ) from exc

    async def aexpand_membership_from_user_and_organization(self, user: "UserModel", organization: "Organization", token: JWTToken) -> "Membership":
        """Expand membership from user and organization"""
        membership = await user.memberships.aget(organization=organization)
        return membership

"""
authapp.token_generators

Implements a JWT Bearer token generator for the AuthorizationServer.

This module:
- Loads the signing key (RSA) from Django settings.
- Exposes a public JWK set via ``get_jwks`` used by token consumers to
  validate signatures.
- Adds application-specific claims (roles, preferred_username, sub,
  scope, org) to tokens emitted for clients/users.

Notes:
- The module intentionally exports only the public JWK (is_private=False)
  for inclusion in discovery endpoints; the private key is used for
  signing and must remain secret in settings.
"""

from authlib.oauth2.rfc9068 import JWTBearerTokenGenerator
from authlib.oauth2.rfc6749.errors import InvalidClientError
from joserfc.jwk import RSAKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from django.conf import settings
from typing import Any, Optional
from django.core.exceptions import ObjectDoesNotExist

# Load RSA private key (used for signing). The settings.PRIVATE_KEY must
# contain the PEM-encoded private key string.
private_key = serialization.load_pem_private_key(settings.PRIVATE_KEY.encode("utf-8"), password=None, backend=default_backend())

# Generate a JWK representation from the private key. This JWK is the
# signing key returned by get_jwks(); it must retain the private material
# (private=True). The public JWK set published to consumers is built
# separately from settings.PUBLIC_KEY in authapp/views.py.
jwk = RSAKey.import_key(settings.PRIVATE_KEY)
jwk_dict = jwk.as_dict(private=True, kid=settings.KEY_ID, use="sig")  # signing key — MUST include private material

# The *public* half of the same key, as published at /o/jwks/ and used by every
# in-process verifier (e.g. the bearer validator). This is the ONLY JWK that may
# ever leave this module towards anything that is not the signer.
public_jwk_dict = jwk.as_dict(private=False, kid=settings.KEY_ID, use="sig")
assert not any(k in public_jwk_dict for k in ("d", "p", "q", "dp", "dq", "qi")), "public JWK leaked private members"


def public_jwks() -> dict:
    """The published JWK set ({"keys": [...]}) — public members only."""
    return {"keys": [public_jwk_dict]}



def _actor_claim(client: Any) -> dict:
    """``act`` for a client logging in with a mandate key that an app handed out, else nothing.

    ``client_app`` is the app that issued the key (the agent, for a key issued under
    an agent mandate); ``mandate`` is the agent mandate the key was issued under
    (a kabinet release approval points at it), or the key's own mandate."""
    mandate = getattr(client, "mandate", None) if client is not None else None
    if mandate is None:
        return {}
    if mandate.parent_id is not None:
        return {"act": {"client_app": mandate.parent.agent_identifier, "mandate": str(mandate.parent_id)}}
    issuer = mandate.issuing_client
    if issuer is not None and issuer.release_id is not None:
        return {"act": {"client_app": issuer.release.app.identifier, "mandate": str(mandate.pk)}}
    return {}

class MyJWTBearerTokenGenerator(JWTBearerTokenGenerator):
    """Custom JWT Bearer token generator that adds application claims.

    The generator extends authlib's JWTBearerTokenGenerator and overrides
    a small set of hooks used during token creation.
    """

    def get_jwks(self) -> dict:
        """Return the JWK set used to sign issued access tokens.

        authlib signs with the key returned here. Returning a JWK *set*
        (``{"keys": [...]}``) rather than a bare key lets joserfc stamp the
        key's ``kid`` into the JWT header, which consumers require to select
        the verification key.

        Returns:
            dict: a JWKS dict containing the (private) signing key.
        """
        return {"keys": [jwk_dict]}

    def _get_app_client(self, client: Any) -> Any | None:
        """The client itself when it is a bound app client (has a release)."""
        return client if getattr(client, "release_id", None) else None

    def _get_hub(self, client: Any) -> Any | None:
        try:
            return client.hub_identity
        except ObjectDoesNotExist:
            return None

    def _get_membership(self, client: Any, user: Any) -> Any:
        if user:
            return user

        try:
            return client.resolve_membership()
        except ObjectDoesNotExist as exc:
            raise InvalidClientError(
                description="Client is no longer attached to an organization membership."
            ) from exc

    def get_extra_claims(self, client: Any, grant_type: Any, user: Any, scope: Optional[str]) -> dict:
        """Construct application-specific claims to include in the JWT.

        Behavior and assumptions:
        - If ``user`` is falsy (client credentials flows), the method
          attempts to use ``client.user`` as a fallback.
        - If ``scope`` is falsy, the client's stored scope is used.
        - Raises ValueError when required contextual data is missing.

        Returns a dict with keys:
        - roles: list of role identifiers the user has in the client's
                 organization
        - preferred_username: the user's username
        - sub: the user's id (subject)
        - scope: the resolved scope string
        - org: the client's organization pk (identity; the slug is a mutable handle)

        For a guest (a scoped member, only granted some collaborations) ``roles``
        is empty and ``collabs`` lists the collaborations they hold in the
        instances this token is minted for, with the roles that hold inside each:
        ``{"7": {"12": ["editor"], "9": []}}``.
        """
        membership = self._get_membership(client, user)

        if not scope:
            # fall back to the client's configured scope
            scope = client.scope

        fakts_client = self._get_app_client(client)
        hub = self._get_hub(client)
        token_hub = hub or self._app_hub(fakts_client)

        # The organization *pk*, not its slug. A slug is a user-chosen, mutable
        # handle; keying the tenancy boundary on it meant the boundary moved
        # whenever someone renamed their organization. Matches what `sub` already
        # does for users (`str(user.id)`).
        scope_claims: dict[str, Any] = {"org": str(membership.organization_id)}
        if membership.is_scoped:
            # A guest (karakter.collaboration_grants): no roles in the organization,
            # and the collaborations granted to them in this token's instances, each
            # with the roles that hold there. Services narrow to those (authentikate).
            from karakter.collaboration_grants import live_collaborations

            scope_claims["roles"] = []
            scope_claims["collabs"] = live_collaborations(membership, self._instance_pks(client))
        else:
            scope_claims["roles"] = [role.identifier for role in membership.roles.all()]

        # TODO: Implement correct scoping rules; for now expose roles and
        # some basic user identifiers used by resource servers.
        return {
            **scope_claims,
            "nickname": membership.user.username,
            "preferred_username": membership.user.username,
            "sub": str(membership.user.id),
            "scope": scope,
            "client_app": fakts_client.release.app.identifier if fakts_client and fakts_client.release and fakts_client.release.app else None,
            "client_release": fakts_client.release.version if fakts_client and fakts_client.release else None,
            "client_device": fakts_client.node.node_id if fakts_client and fakts_client.node else None,
            "client_role": fakts_client.role if fakts_client else None,
            # The hub the token is for: its own uuid. A service names itself only within its
            # hub (`aud: mikro`), so this is what tells two hubs' services apart. An app's token
            # is for the hub its client was composed for, a hub's for the hub itself.
            "hub": str(token_hub.uid) if token_hub is not None else None,
            # RFC 8693 §4.1: the client acts as the grantor, but was provisioned by
            # the mandate's agent app — resource servers can tell who started it.
            **_actor_claim(fakts_client),
        }

    @staticmethod
    def _app_hub(fakts_client: Any) -> Any:
        """The hub an app's token is for: its client's, else the one hub its services run in."""
        if fakts_client is None:
            return None
        if fakts_client.hub_id:
            return fakts_client.hub
        from fakts.models import Hub

        hubs = Hub.objects.filter(instances__mappings__client=fakts_client).distinct()[:2]
        return hubs[0] if len(hubs) == 1 else None

    def _instance_pks(self, client: Any) -> list[int]:
        """The ServiceInstance pks a token for ``client`` is composed for: a guest's collaborations are listed for these."""
        fakts_client = self._get_app_client(client)
        if fakts_client is None:
            return []
        return sorted(set(fakts_client.mappings.values_list("instance_id", flat=True)))

    def _instance_audiences(self, client: Any) -> list[str]:
        """The names of the services a token for ``client`` is composed for, sorted: its ``aud``.

        A fakts client is composed for the services of one hub (its own), each named as that hub
        names it (``mikro``); none for any other client.
        """
        fakts_client = self._get_app_client(client)
        if fakts_client is None:
            return []
        return sorted(set(fakts_client.mappings.values_list("instance__instance_id", flat=True)))

    def get_audiences(self, client: Any, user: Any, scope: Optional[str]) -> str | list[str]:
        """Return the audience claim(s) for the token.

        For a fakts client the audiences are the **names of the services** it was composed for,
        as its hub names them (``mikro``), plus ``lok`` itself (lok consumes its own tokens, e.g.
        at ``/f/report/``). A name is unique only within its hub, so the token's ``hub`` claim
        (the hub's own uuid) is what makes it name exactly one service: a service accepts a token
        whose ``aud`` holds its name *and* whose ``hub`` is its hub's. For non-fakts clients
        (plain OIDC relying parties) the audience is the client itself, per RFC 9068 practice.
        """
        if self._get_app_client(client) is not None:
            return ["lok", *self._instance_audiences(client)]
        if self._get_hub(client) is not None:
            return ["lok"]
        return [client.client_id]

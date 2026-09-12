import logging

from authlib.oauth2.rfc6749 import grants
from authlib.oauth2.rfc6749.errors import InvalidGrantError
from .models import OAuth2Token, AuthorizationCode, UsedNonce
from .oidc_claims import build_claims
from .fakts_grants import FaktsEnvelopeMixin
from authlib.oidc.core import grants as oidcgrants, UserInfo
from karakter.models import Membership
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


class ClientCredentialsGrant(grants.ClientCredentialsGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_basic", "client_secret_post"]


class OpenIDCode(oidcgrants.OpenIDCode):
    def exists_nonce(self, nonce, request):
        """Whether this client has already used this nonce.

        Consulting only `AuthorizationCode` could never detect a replay: the row
        is deleted at token exchange, so an *already-consumed* nonce always
        reported "does not exist" and was accepted again. `require_nonce=True`
        therefore enforced nonce presence but not nonce uniqueness — which is
        the property that makes it an id_token replay defence.

        A consumed nonce is now recorded in `UsedNonce` and checked here as
        well. `.exists()` rather than `.get()` also avoids the
        `MultipleObjectsReturned` 500 the old lookup raised whenever one client
        happened to have two live codes carrying the same nonce.
        """
        client_id = request.payload.client_id
        if AuthorizationCode.objects.filter(client_id=client_id, nonce=nonce).exists():
            return True
        return UsedNonce.objects.filter(client_id=client_id, nonce=nonce).exists()

    def validate_openid_authorization_request(self, grant, redirect_uri):
        """Decide `nonce`-required *per client*, not once at construction.

        OIDC Core §3.1.2.1 makes `nonce` OPTIONAL for the authorization-code
        flow (it is REQUIRED only where an id_token comes back from the
        authorization endpoint), and — worse for interop — no discovery field
        can advertise a stricter rule, so a conforming relying party has no way
        to learn about one and just fails with an opaque error. The server-wide
        `require_nonce=True` therefore had to go; clients that want the tighter
        rule opt in through `fakts.Client.require_nonce`.

        authlib fixes `require_nonce` at construction time
        (`authlib/oidc/core/grants/code.py`), so the flag is applied here
        instead. This runs as the `after_validate_authorization_request_payload`
        hook, which fires *after* `grant.request.client` is set
        (`authlib/oauth2/rfc6749/grants/authorization_code.py`) — the client is
        always present, so the flag can never silently read as False.
        """
        self.require_nonce = bool(getattr(grant.request.client, "require_nonce", False))
        return super().validate_openid_authorization_request(grant, redirect_uri)

    def get_jwt_config(self, grant, client):
        # Implement key rotation and retrieval as needed
        return {
            "key": settings.PRIVATE_KEY,
            "alg": "RS256",
            "iss": settings.OIDC_ISSUER,
            "exp": 3600,
            "kid": settings.KEY_ID,
        }

    def encode_id_token(self, token, request):
        # generate_user_info() only receives (user, scope), but the per-client
        # `sub`/`email` policy lives on the client. Stash the client on the
        # per-request membership instance (request.user is a freshly loaded
        # Membership, so this is request-scoped and thread-safe).
        request.user._oauth_client = request.client
        return super().encode_id_token(token, request)

    def generate_user_info(self, user: Membership, scope):
        # The user is actually a membership object (see token_generators.py)
        membership = user
        client = getattr(membership, "_oauth_client", None)
        email_template = getattr(client, "email_template", None)

        # Scope-filtered, and built by the same helper the userinfo endpoint
        # uses: `sub` MUST agree between the two (OIDC Core §5.3.2) and the
        # claims each scope buys MUST agree with them too (§5.4). `roles` is
        # left out of the id_token — resource servers read it off the access
        # token or userinfo, and the list is unbounded in size.
        return UserInfo(**build_claims(membership, scope, email_template, roles=False))


class AuthorizationCodeGrant(FaktsEnvelopeMixin, grants.AuthorizationCodeGrant):
    # `none` lets *public* fakts clients (website kind, PKCE-protected) exchange
    # codes without a secret; confidential clients keep the secret methods. The
    # per-client gate is OAuth2Client.check_endpoint_auth_method.
    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_basic", "client_secret_post", "none"]

    def query_authorization_code(self, code, client):
        """Claim the code for this exchange, atomically.

        authlib runs query -> generate_token -> save_token -> delete with no
        transaction and no row lock, so two concurrent POSTs with the same code
        both passed this lookup before either delete landed, and both received a
        full token pair with independent refresh chains.

        `select_for_update` serialises the claim: the second request blocks here
        and then finds the row gone (the first has deleted it) or, on a backend
        without row locking, still races only as far as the delete below, which
        is now guarded by an affected-row count. This is the same pattern
        `fakts.services.clients` already uses for the redeem path.
        """
        with transaction.atomic():
            try:
                item = (
                    AuthorizationCode.objects.select_for_update()
                    .get(code=code, client_id=client.client_id)
                )
            except AuthorizationCode.DoesNotExist:
                return None

            if item.is_expired():
                return None
            return item

    def delete_authorization_code(self, authorization_code: AuthorizationCode):
        # Delete by pk and check the affected-row count: if another concurrent
        # exchange already consumed this code, we must not let this one issue a
        # second token pair from it.
        deleted, _ = AuthorizationCode.objects.filter(pk=authorization_code.pk).delete()
        if not deleted:
            raise InvalidGrantError(description="Authorization code has already been used.")

    def authenticate_user(self, authorization_code: AuthorizationCode):
        # Remember the nonce before the code row disappears, so `exists_nonce`
        # can still recognise it as spent (see OpenIDCode.exists_nonce).
        if authorization_code.nonce:
            UsedNonce.objects.get_or_create(
                client_id=authorization_code.client_id,
                nonce=authorization_code.nonce,
            )
        return authorization_code.user

    def save_authorization_code(self, code: str, request):
        # openid request MAY have "nonce" parameter
        nonce = request.payload.data.get("nonce")
        client = request.client
        auth_code = AuthorizationCode(
            code=code,
            client_id=client.client_id,
            redirect_uri=request.redirect_uri,
            scope=request.payload.scope,
            # The grant user *is* a Membership (the consent decision binds an
            # organization), and AuthorizationCode stores it as such.
            membership=request.user,
            nonce=nonce,
            # PKCE: persisted so `CodeChallenge` can require and verify a matching
            # `code_verifier` at token exchange. Empty when the client sent none.
            code_challenge=request.payload.data.get("code_challenge") or "",
            code_challenge_method=request.payload.data.get("code_challenge_method") or "",
        )
        auth_code.save()
        return auth_code

    def create_token_response(self):
        # Post-process with the fakts envelope so website-kind fakts clients
        # receive their instances through the standard code flow (this replaced
        # the org-less /f/retrieve/ handout). The id_token hook runs inside
        # super(), so appending afterwards is safe.
        status, token, headers = super().create_token_response()
        if status == 200:
            self.append_fakts_envelope(token)
        return status, token, headers


class RefreshTokenGrant(FaktsEnvelopeMixin, grants.RefreshTokenGrant):
    INCLUDE_NEW_REFRESH_TOKEN = True
    # `none` lets public fakts clients refresh with client_id + rotated refresh
    # token alone — for them the refresh chain *is* the credential. Confidential
    # clients still authenticate with their secret; the per-client gate is
    # OAuth2Client.check_endpoint_auth_method.
    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_basic", "client_secret_post", "none"]

    def authenticate_refresh_token(self, refresh_token):
        # Guard *falsy*, not just None: authlib only rejects a missing
        # `refresh_token` parameter, so an empty string would otherwise reach
        # the lookup.
        if not refresh_token:
            return None
        try:
            item = OAuth2Token.objects.get(refresh_token=refresh_token)
        except OAuth2Token.DoesNotExist:
            return None

        if item.is_refresh_token_active():
            return item

        # Reuse detection (RFC 9700 §4.14.2). Rotation already revoked this row
        # when it was consumed, and replaying it was rejected — but rejection
        # was the end of it, so the *legitimate* chain kept working and the
        # thief's branch survived alongside it, unflagged, for up to the
        # 180-day chain cap.
        #
        # Presenting an already-revoked refresh token means the token leaked:
        # either the attacker is replaying what the client already spent, or the
        # client is replaying what the attacker spent. We cannot tell which, and
        # that is precisely why the whole chain has to go.
        if item.revoked:
            revoked_count = OAuth2Token.objects.filter(
                client_id=item.client_id,
                chain_started_at=item.chain_started_at,
                revoked=False,
            ).update(revoked=True)
            logger.warning(
                "Refresh token reuse detected for client %s (chain started %s); "
                "revoked %s live token(s) in that chain.",
                item.client_id,
                item.chain_started_at,
                revoked_count,
            )
        return None

    def authenticate_user(self, credential: OAuth2Token):
        return credential.user

    def revoke_old_credential(self, credential: OAuth2Token):
        credential.revoked = True
        credential.save()

    def create_token_response(self):
        # Post-process the standard response: carry the chain start through
        # rotation (so the absolute refresh-chain cap in
        # OAuth2Token.is_refresh_token_active cannot be reset by refreshing),
        # then append the fakts envelope — a fakts client's hourly refresh
        # re-renders its instances (aliases are host-aware), so configuration
        # drift propagates without re-approval.
        status, token, headers = super().create_token_response()
        if status == 200:
            old_credential = self.request.refresh_token
            OAuth2Token.objects.filter(access_token=token["access_token"]).update(
                chain_started_at=old_credential.chain_started_at
            )
            self.append_fakts_envelope(token)
        return status, token, headers

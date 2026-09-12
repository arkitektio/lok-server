import time
from django.db import models
from django.contrib.auth import get_user_model
from authlib.oauth2.rfc6749 import TokenMixin, AuthorizationCodeMixin

from karakter.models import Membership

User = get_user_model()


def now_timestamp():
    return int(time.time())


class OAuth2Token(models.Model, TokenMixin):
    """Model representing an OAuth2 token."""

    user = models.ForeignKey(Membership, on_delete=models.CASCADE)  # membership
    client_id = models.CharField(max_length=48, db_index=True)
    token_type = models.CharField(max_length=40)
    access_token = models.CharField(max_length=10000, unique=True, null=False)
    # `null=True` + `unique=True` is load-bearing: tokens issued without a refresh
    # token must store NULL, never "". A "" here made
    # `.get(refresh_token="")` reachable from a request carrying an *empty*
    # `refresh_token=` parameter (authlib only rejects a missing one), which with
    # multiple such rows 500s the token endpoint — and with exactly one could
    # refresh a session without presenting any refresh token at all.
    refresh_token = models.CharField(max_length=10000, db_index=True, unique=True, null=True, blank=True)
    scope = models.TextField(default="")
    revoked = models.BooleanField(default=False)
    issued_at = models.IntegerField(null=False, default=now_timestamp)
    expires_in = models.IntegerField(null=False, default=0)
    chain_started_at = models.IntegerField(
        null=False,
        default=now_timestamp,
        help_text="When this token's refresh chain began. Copied through rotation, so the "
        "30-day sliding refresh window cannot be extended forever — the whole chain "
        "dies at the absolute cap and a human must re-authorize.",
    )

    def check_client(self, client):
        return self.client_id == client.client_id

    def get_client_id(self):
        return self.client_id

    def get_scope(self):
        return self.scope

    def get_expires_in(self):
        return self.expires_in

    def get_expires_at(self):
        return self.issued_at + self.expires_in

    def validate(self):
        if self.revoked:
            return False
        if self.get_expires_at() < now_timestamp():
            return False
        return True

    # Sliding per-token lifetime (30 days) and absolute chain cap (180 days).
    # Rotation issues a fresh row with a fresh issued_at, so the sliding window
    # alone would keep a monthly-refreshing session alive forever; the chain cap
    # bounds the total session lifetime since the original human authorization.
    REFRESH_TOKEN_LIFETIME = 2592000
    REFRESH_CHAIN_MAX_LIFETIME = 15552000

    def is_refresh_token_active(self) -> bool:
        if self.revoked:
            return False
        now = now_timestamp()
        if self.issued_at + self.REFRESH_TOKEN_LIFETIME < now:
            return False
        if self.chain_started_at + self.REFRESH_CHAIN_MAX_LIFETIME < now:
            return False
        return True

    def is_revoked(self) -> bool:
        return self.revoked

    def is_expired(self) -> bool:
        return self.get_expires_at() < now_timestamp()

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class AuthorizationCode(models.Model, AuthorizationCodeMixin):
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE)
    client_id = models.CharField(max_length=48, db_index=True)
    code = models.CharField(max_length=120, unique=True, null=False)
    redirect_uri = models.TextField(default="", null=True)
    response_type = models.TextField(default="")
    scope = models.TextField(default="", null=True)
    auth_time = models.IntegerField(null=False, default=now_timestamp)

    # add nonce
    nonce = models.CharField(max_length=120, default="", null=True)

    # PKCE (RFC 7636). Populated from the authorization request when the client
    # sends one; `CodeChallenge` (registered in authapp.server with
    # required=True) then requires and verifies a matching `code_verifier` at
    # token exchange.
    #
    # `required=True` does not reject a challenge-less /o/authorize/ — authlib
    # returns early there when neither code_challenge nor code_challenge_method
    # is present. Enforcement happens at the *token* request: a public client
    # (auth method "none") that skipped PKCE can obtain a code but can never
    # exchange it. Confidential relying parties authenticate with a secret and
    # are not PKCE-gated.
    #
    # (This comment previously said the extension was registered with
    # required=False, which was the opposite of the actual configuration.)
    code_challenge = models.CharField(max_length=128, blank=True, default="")
    code_challenge_method = models.CharField(max_length=10, blank=True, default="")
    # ... other fields and methods ...

    @property
    def user(self):
        """Return the membership associated with this authorization code."""
        return self.membership
    
    def get_user_id(self):
        return str(self.membership.id)

    def is_expired(self):
        # Authorization code is valid for 10 minutes
        expiration_time = self.auth_time + 600
        return now_timestamp() > expiration_time

    def get_redirect_uri(self):
        return self.redirect_uri

    def get_scope(self):
        return self.scope or ""

    def get_auth_time(self):
        return self.auth_time

    def get_nonce(self):
        return self.nonce

    def get_acr(self):
        return "1"  # Authentication Context Class Reference (check what this should be)

    def get_amr(self):
        return ["pwd"]  # Authentication Methods References (check what this should be)


class UsedNonce(models.Model):
    """A nonce this client has already spent on a completed token exchange.

    `AuthorizationCode` rows are deleted at exchange, so they cannot answer
    "has this nonce been used before?" — a consumed nonce simply vanished and
    was accepted again. This table keeps the answer after the code is gone, so
    `OpenIDCode.exists_nonce` can actually enforce uniqueness (the property that
    makes `require_nonce=True` an id_token replay defence rather than just a
    presence check).

    Rows are only useful while an id_token minted with that nonce could still be
    replayed, so they are prunable by `created_at`.
    """

    client_id = models.CharField(max_length=48, db_index=True)
    nonce = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        unique_together = ("client_id", "nonce")

    def __str__(self):
        return f"{self.client_id}:{self.nonce}"

import logging
import uuid
from datetime import timedelta

import strawberry
from django.utils import timezone
from graphql import GraphQLError
from kante.types import Info

from fakts import inputs, models, types
from fakts.base_models import Manifest
from karakter.authz import DENIED, get_scoped_or_denied, get_user

logger = logging.getLogger(__name__)

# A redeem token is a bearer credential for creating a client; it must not be
# valid forever. Matches the invite default.
REDEEM_TOKEN_TTL = timedelta(days=7)
# The longest a caller may ask for. An unattended deployer mints short-lived,
# pinned tokens; a month is already generous for anything interactive.
REDEEM_TOKEN_MAX_TTL_DAYS = 30

# Re-exported for callers that imported the input from here before it moved to
# `fakts.inputs` (where its pydantic model lives, next to ManifestInput).
RedeemTokenInput = inputs.RedeemTokenInput


def _pinned_manifest(manifest: inputs.ManifestInput) -> Manifest:
    """Assemble the pin from the input.

    Built by hand rather than via ``input.to_pydantic()``: the nested requirement and
    public-source inputs are backed by their own pydantic models, which the base
    ``Manifest`` does not accept as instances (the same reason
    ``create_developmental_client`` assembles its Manifest this way).
    """
    return Manifest(
        identifier=manifest.identifier,
        version=manifest.version,
        scopes=manifest.scopes or [],
        device_id=manifest.device_id or manifest.node_id,
        requirements=[strawberry.asdict(x) for x in manifest.requirements],
        public_sources=[strawberry.asdict(x) for x in manifest.public_sources] if manifest.public_sources else [],
    )


def create_redeem_token(info: Info, input: inputs.RedeemTokenInput) -> types.RedeemToken:
    """Mint a *pre-authorized* redeem token on the calling client's hub.

    The token is pinned to ``manifest`` at mint time: a redeem presenting anything
    else (other app, version or node, or more scopes/requirements) is refused before a
    client is provisioned. Unpinned tokens exist only for operator provisioning (the
    deployment config's ``redeem_tokens`` and the management API).
    """
    manifest = _pinned_manifest(input.manifest)
    uuid_token = uuid.uuid4().hex

    user = get_user(info)
    client = getattr(info.context.request, "client", None)
    hub = getattr(client, "hub", None)
    if hub is None:
        # Tokens are issued *for a hub*; a caller whose client composes against
        # no hub has nothing to issue a token for.
        raise GraphQLError(DENIED)

    expires_in_days = input.expires_in_days
    if expires_in_days is None:
        expires_at = timezone.now() + REDEEM_TOKEN_TTL
    elif 1 <= expires_in_days <= REDEEM_TOKEN_MAX_TTL_DAYS:
        expires_at = timezone.now() + timedelta(days=expires_in_days)
    else:
        raise GraphQLError(f"expiresInDays must be between 1 and {REDEEM_TOKEN_MAX_TTL_DAYS}.")

    if input.max_redemptions is not None and input.max_redemptions < 1:
        raise GraphQLError("maxRedemptions must be at least 1.")

    token, _ = models.RedeemToken.objects.update_or_create(
        token=uuid_token,
        defaults={
            "user": user,
            "hub": hub,
            "expires_at": expires_at,
            "max_redemptions": input.max_redemptions,
            "pinned_manifest": manifest.model_dump(mode="json"),
        },
    )

    logger.info(
        "Redeem token %s created for user %s and hub %s (pinned to %s:%s)",
        token.id,
        user.id,
        hub.id,
        manifest.identifier,
        manifest.version,
    )

    return token


@strawberry.input
class DeleteRedeemTokenInput:
    """Input for revoking a redeem token."""

    id: strawberry.ID


def delete_redeem_token(info: Info, input: DeleteRedeemTokenInput) -> strawberry.ID:
    """Revoke a redeem token, addressed by id.

    Only the user who issued it, inside their active organization, may revoke it —
    the same scoping as the ``redeemToken`` query. A client already redeemed from the
    token is untouched: it continues on its refresh chain, the token itself just stops
    being redeemable. This is how a deployer *spends* a pinned token: mint, start the
    container, confirm the client it produced, delete the token.
    """
    token = get_scoped_or_denied(
        models.RedeemToken.objects,
        info,
        field="hub__organization",
        id=input.id,
        user=get_user(info),
    )
    token.delete()
    logger.info("Redeem token %s revoked by user %s", input.id, get_user(info).id)
    return input.id

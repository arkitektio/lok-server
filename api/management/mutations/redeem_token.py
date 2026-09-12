from datetime import timedelta

import strawberry
from django.utils import timezone
from kante import Info

import api.management.types as types
import kante
from api.management.authz import assert_owner_or_admin, get_or_denied
from fakts import models as fakts_models


@kante.input
class CreateRedeemTokenInput:
    """Input for minting a redeem token for a hub."""

    hub: strawberry.ID
    expires_in_days: int | None = None


def create_redeem_token(info: Info, input: CreateRedeemTokenInput) -> types.ManagementRedeemToken:
    """Mint a redeem token for a hub. A redeem token is a bearer credential that
    lets whoever holds it enrol a client into the hub, so only the hub
    organization's owner or admins may create one."""
    hub = get_or_denied(fakts_models.Hub.objects.select_related("organization"), id=input.hub)

    assert_owner_or_admin(info, hub.organization)

    expires_at = None
    if input.expires_in_days:
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)

    # Org scoping rides on the hub: RedeemToken has no organization column, the
    # hub (required) carries it. (An `organization=` kwarg was passed here
    # before, which TypeError'd on every call since the model has no such field.)
    return fakts_models.RedeemToken.objects.create(
        user=info.context.request.user,
        hub=hub,
        expires_at=expires_at,
    )
from django.utils import timezone
from datetime import timedelta
import secrets
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings
from fakts import models
from fakts.config_models import RedeemTokenConfigs

# assign directory
directory = "files"

# iterate over files in
# that directory


class Command(BaseCommand):
    help = "Creates redeem tokens for users defined in settings.REDEEM_TOKENS"

    def handle(self, *args, **kwargs):
        TOKENS = settings.REDEEM_TOKENS

        tokens = RedeemTokenConfigs(tokens=TOKENS)

        for spec in tokens.tokens:
            user = get_user_model().objects.get(username=spec.user)
            hub = models.Hub.objects.get(organization__slug=spec.organization, identifier=spec.hub)

            # An operator-typed token value is the weakest secret in this flow:
            # it is an unauthenticated bearer credential at the token endpoint,
            # and the committed-key guard does not cover it. When the config
            # omits `token`, mint one with real entropy instead.
            configured_token = (spec.token or "").strip()
            if not configured_token:
                configured_token = secrets.token_urlsafe(32)
                generated = True
            else:
                generated = False

            # An expiry by default: these used to be created with expires_at
            # NULL, which made the expiry check in `redeem_token` unreachable, so
            # every config-provisioned token was valid forever.
            expires_at = None
            if spec.expires_in_days is not None:
                expires_at = timezone.now() + timedelta(days=spec.expires_in_days)

            token, created = models.RedeemToken.objects.update_or_create(
                token=configured_token,
                defaults={
                    "user": user,
                    "hub": hub,
                    "expires_at": expires_at,
                    "max_redemptions": spec.max_redemptions,
                },
            )

            if generated and created:
                # Only chance to show it: the value is not recoverable from config.
                print(f"Generated redeem token for user {user} and hub {hub}: {token.token}")
            else:
                print(f"Token for user {user} and hub {hub} ensured (expires {expires_at or 'never'})")

from django.core.management.base import BaseCommand
from django.conf import settings
from fakts import models
from pydantic import BaseModel


class OpenIDAppConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uris: list[str]
    email_template: str | None = None
    scope: str = "openid profile email"
    require_nonce: bool = False


class Command(BaseCommand):
    help = "Creates all configured apps or overwrites them"

    def handle(self, *args, **options):
        apps = settings.ENSURED_OPENID_APPS or []

        if not apps:
            self.stdout.write(
                self.style.WARNING(
                    "No OpenID clients configured (`openid_apps` is empty). OIDC relying "
                    "parties (e.g. ionscale) will fail with 'client does not exist'. Add an "
                    "`openid_apps` entry per relying party to the lok config."
                )
            )

        for app in apps:
            config = OpenIDAppConfig(**app)

            try:
                client = models.Client.objects.get(client_id=config.client_id)
                client.client_secret = config.client_secret
                client.redirect_uris = " ".join(config.redirect_uris)
                client.scope = config.scope
                # Relying parties only run the code flow; carrying more grants
                # than needed just widens what a leaked secret can do.
                client.grant_types = "authorization_code refresh_token"
                client.token_endpoint_auth_method = "client_secret_post"
                client.kind = "relying_party"
                client.name = getattr(config, "client_name", None) or config.client_id
                client.email_template = config.email_template
                client.require_nonce = config.require_nonce
                client.save()

                self.stdout.write(f"Updated OpenID client {client.client_id}")

            except models.Client.DoesNotExist:
                client = models.Client.objects.create(
                    client_id=config.client_id,
                    client_secret=config.client_secret,
                    redirect_uris=" ".join(config.redirect_uris),
                    scope=config.scope,
                    grant_types="authorization_code refresh_token",
                    token_endpoint_auth_method="client_secret_post",
                    kind="relying_party",
                    name=getattr(config, "client_name", None) or config.client_id,
                    email_template=config.email_template,
                    require_nonce=config.require_nonce,
                )

                self.stdout.write(f"Created OpenID client {client.client_id}")

from django.core.management.base import BaseCommand
from fakts.models import KommunityPartner
from fakts.models import Client
from fakts.config_models import KommunityPartnerConfigModel  # <-- Your validated Pydantic schema
from django.contrib.auth import get_user_model
from django.conf import settings


User = get_user_model()


class Command(BaseCommand):
    help = "Import kommunity partners from YAML configuration. This command should be run before users and organizations are created."

    def handle(self, *args, **options):
        partners_config = settings.KOMMUNITY_PARTNERS

        # Validate YAML structure
        config = KommunityPartnerConfigModel(partners=partners_config)

        for partner in config.partners:
            # Prepare preconfigured hub data if present
            preconfigured_hub_data = None
            if partner.preconfigured_hub:
                preconfigured_hub_data = partner.preconfigured_hub.model_dump()

            # First, handle the OAuth2 client if present
            oauth_client = None
            if partner.oauth2:
                try:
                    oauth_client = Client.objects.get(client_id=partner.oauth2.client_id)
                    oauth_client.client_secret = partner.oauth2.client_secret
                    oauth_client.redirect_uris = " ".join(partner.oauth2.redirect_uris)
                    oauth_client.scope = "openid profile email"
                    oauth_client.save()
                    self.stdout.write(self.style.SUCCESS(f"Updated OpenID client {oauth_client.client_id}"))
                except Client.DoesNotExist:
                    oauth_client = Client.objects.create(
                        client_id=partner.oauth2.client_id,
                        client_secret=partner.oauth2.client_secret,
                        redirect_uris=" ".join(partner.oauth2.redirect_uris),
                        token_endpoint_auth_method="client_secret_post",
                        kind="relying_party",
                        scope="openid profile email",
                    )
                    self.stdout.write(self.style.SUCCESS(f"Created OpenID client {oauth_client.client_id}"))

            # Create or update the KommunityPartner
            filter_config_data = partner.filter_config.model_dump() if partner.filter_config else {}

            kommunity_partner, created = KommunityPartner.objects.update_or_create(
                identifier=partner.identifier,
                defaults={
                    "name": partner.name,
                    "website_url": partner.website_url,
                    "description": partner.description,
                    "short_description": partner.short_description,
                    "logo_url": partner.logo_url,
                    "image_url": partner.image_url,
                    "auth_url": partner.auth_url,
                    "license_agreement": partner.license_agreement,
                    "pre_authorize_hook": partner.pre_authorize_hook,
                    "pre_authorize_token": partner.pre_authorize_token,
                    "partner_kind": partner.partner_kind.value,
                    "kommunity_kind": partner.kommunity_kind.value,
                    "auto_configure": partner.auto_configure,
                    "preconfigured_hub": preconfigured_hub_data,
                    "oauth_client": oauth_client,
                    "filter_config": filter_config_data,
                },
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f"Created KommunityPartner: {kommunity_partner.identifier}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Updated KommunityPartner: {kommunity_partner.identifier}"))

            if partner.preconfigured_hub:
                self.stdout.write(self.style.SUCCESS(f"  -> Preconfigured hub: {partner.preconfigured_hub.identifier}"))
            if partner.auto_configure:
                self.stdout.write(self.style.WARNING(f"  -> Auto-configure enabled: hubs will be created for new organizations"))

            if filter_config_data:
                self.stdout.write(self.style.SUCCESS(f"  -> Filter config: {filter_config_data}"))

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.conf import settings
from django.db import connection
from karakter.base_models import OrganizationConfig
from karakter.models import Organization
from fakts.logic import auto_configure_kommunity_partners

User = get_user_model()


class Command(BaseCommand):
    help = "Creates all default organisations if it doesn't exist and applies auto-configured kommunity partners"

    def handle(self, *args, **kwargs):
        organizations = settings.ENSURE_ORGANIZATIONS

        for lokuser in organizations:
            org_config = OrganizationConfig(**lokuser)

            owner = None
            if org_config.owner:
                owner = User.objects.filter(username=org_config.owner).first()
                if not owner:
                    self.stdout.write(self.style.WARNING(f"Owner {org_config.owner} not found for org {org_config.identifier}"))

            if not owner:
                # Fallback to superuser
                owner = User.objects.filter(is_superuser=True).first()

            # An organization is its id: the configuration names it, so every service's
            # configuration can too. The slug is only lok's handle for it.
            org = Organization.objects.filter(pk=org_config.id).first()
            if org is not None:
                org.slug = org_config.identifier
                org.description = org_config.description
                org.name = org_config.name
                if owner:
                    org.owner = owner
                org.save()

                self.stdout.write(self.style.SUCCESS(f"Updated org {org.pk}"))
            else:
                org = Organization.objects.create(pk=org_config.id, slug=org_config.identifier, name=org_config.name, description=org_config.description, owner=owner)
                # An explicit id leaves the sequence behind it: the next signup must not collide.
                with connection.cursor() as cursor:
                    for statement in connection.ops.sequence_reset_sql(no_style(), [Organization]):
                        cursor.execute(statement)
                self.stdout.write(self.style.SUCCESS(f"Created org {org.pk}"))

            # Ensure the ionscale mesh (enabled by default per organization).
            # Idempotent + guarded: a no-op when ionscale isn't configured.
            from ionscale.manager import ensure_org_mesh

            ensure_org_mesh(org)

            # Apply auto-configure kommunity partners for new organizations
            if org_config.auto_configure:
                auto_configure_kommunity_partners(organization=org)

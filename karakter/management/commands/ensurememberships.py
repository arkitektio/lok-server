from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from karakter.base_models import MembershipConfig
from karakter.models import Membership, Organization, Role

User = get_user_model()


class Command(BaseCommand):
    help = "Creates all memberships based on user config"

    def handle(self, *args: Any, **kwargs: Any) -> None:
        lokmemberships = settings.ENSURED_MEMBERSHIPS

        for membership in lokmemberships:
            membership_config = MembershipConfig(**membership)

            user = User.objects.filter(username=membership_config.user).first()
            if not user:
                self.stdout.write(
                    self.style.WARNING(
                        f"User {membership_config.user} not found. Skipping memberships."
                    )
                )
                continue

            # Clear existing memberships ?? Maybe only update/create, but clear might be safer for "ensure"
            # Membership.objects.filter(user=user).delete()
            # Better to update/create or just add. If we delete, we lose manual changes.
            # However, ensure usually implies strict state. The previous code did delete.
            # Let's keep deletion for now to match previous behavior but scoped.

            # Check if we should clear. If config has memberships, maybe we want to enforce ONLY those?

            try:
                org = Organization.objects.get(slug=membership_config.organization)
            except Organization.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f"Organization {membership_config.organization} not found for user {user.username}"
                    )
                )
                continue

            membership, created = Membership.objects.update_or_create(
                user=user,
                organization=org,
            )

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created membership {user.username} -> {org.slug}"
                    )
                )
            else:
                self.stdout.write(f"Updated membership {user.username} -> {org.slug}")

            current_roles = set()
            for role_identifier in membership_config.roles:
                try:
                    role = Role.objects.get(
                        organization=org, identifier=role_identifier
                    )
                    membership.roles.add(role)
                    current_roles.add(role)

                except Role.DoesNotExist as exc:
                    raise Exception(
                        f"Role {role_identifier} not found in org {org.slug} for user {user.username}"
                    ) from exc

            user.save()
            membership.save()

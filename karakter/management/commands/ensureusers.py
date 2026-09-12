from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings
from karakter.base_models import UserConfig

User = get_user_model()


def _ensure_verified_email(user, email: str) -> None:
    """Register the seeded user's email with allauth as verified + primary.

    Under email login with mandatory verification, allauth blocks sign-in until a
    *verified* ``EmailAddress`` exists — setting ``User.email`` alone is not enough.
    Provisioned users are trusted, so mark it verified so they work out of the box
    in the email world. Idempotent.
    """
    email_address, _ = EmailAddress.objects.get_or_create(
        user=user,
        email=email,
        defaults={"verified": True, "primary": True},
    )
    if not email_address.verified:
        email_address.verified = True
        email_address.save(update_fields=["verified"])
    if not email_address.primary:
        # Also unsets any other primary for this user and syncs User.email.
        email_address.set_as_primary()


class Command(BaseCommand):
    help = "Creates all lok user non-interactively if it doesn't exist"

    def handle(self, *args, **kwargs):
        lokusers = settings.ENSURED_USERS

        for lokuser in lokusers:
            try:
                user_config = UserConfig(**lokuser)

                if User.objects.filter(username=user_config.username).exists():
                    user = User.objects.get(username=user_config.username)
                    user.email = user_config.email
                    user.is_superuser = user_config.is_superuser
                    user.is_staff = user_config.is_staff
                    user.set_password(user_config.password.strip())
                    user.save()

                    self.stdout.write(self.style.SUCCESS(f"Updated user {user.username}"))
                else:
                    user = User.objects.create_user(username=user_config.username, email=user_config.email, password=user_config.password)
                    user.is_superuser = user_config.is_superuser
                    user.is_staff = user_config.is_staff
                    user.save()
                    self.stdout.write(self.style.SUCCESS(f"Created user {user.username}"))

                if user_config.email:
                    _ensure_verified_email(user, user_config.email)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating user {lokuser}: {e}"))

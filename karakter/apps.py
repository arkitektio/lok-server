from django.apps import AppConfig


class KarakterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "karakter"

    def ready(self):
        # Registers the signals that keep scoped memberships from turning into full ones
        # (karakter.collaboration_grants), Membership.is_admin recomputed from the
        # roles (karakter.roles), and the service roles lok hands out by itself
        # (karakter.service_roles).
        from karakter import (
            collaboration_grants,  # noqa: F401
            roles,  # noqa: F401
            service_roles,  # noqa: F401
        )

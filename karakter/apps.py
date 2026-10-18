from django.apps import AppConfig


class KarakterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "karakter"

    def ready(self):
        # Registers the signals that keep scoped memberships from turning into
        # full ones (see karakter.collaboration_grants).
        from karakter import collaboration_grants  # noqa: F401

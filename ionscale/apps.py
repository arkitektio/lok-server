from django.apps import AppConfig
from django.conf import settings


class IonscaleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ionscale"

    def ready(self):
        # Fail fast at boot when ionscale is configured: build the repository now so
        # a bad config (no credential, missing legacy binary) surfaces immediately
        # instead of on the first ionscale operation. Off in tests (IONSCALE_EAGER_INIT = False), where the
        # in-memory fake is used.
        if getattr(settings, "IONSCALE_EAGER_INIT", False):
            from .repo import get_ionscale_repo

            get_ionscale_repo()

from django.apps import AppConfig


class CvdConfig(AppConfig):
    """Coordinated vulnerability disclosure: the public report pages and their reports."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "cvd"
    verbose_name = "Vulnerability disclosure"

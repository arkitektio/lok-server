"""
ASGI config for rekuest project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lok_server.settings")

# Traces and metrics, when the config names a collector (`telemetry.endpoint`): set up before
# Django's application is built. Off, and costing nothing, otherwise.
from arkitekt_service import telemetry  # noqa: E402

telemetry.setup()

from django.core.asgi import get_asgi_application  # noqa: E402

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()


from .schema import schema  # noqa: E402
from kante.router import router  # noqa: E402


application = telemetry.wrap(
    router(
        schema=schema,
        django_asgi_app=django_asgi_app,
        schema_path="schema",
    )
)

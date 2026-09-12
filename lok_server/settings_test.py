# Point the config loader at the committed test config *before* importing
# `.settings`, which does `conf = Settings()` at module import. lok's real
# `config.yaml` is untracked (it was removed in e45feb1 because it shipped a live
# OIDC signing key), so without this every test collection dies on six
# missing-field ValidationErrors before a single test runs.
import os
from pathlib import Path

os.environ.setdefault(
    "ARKITEKT_CONFIG_FILE",
    str(Path(__file__).resolve().parent.parent / "tests" / "config.test.yaml"),
)

from .settings import *  # noqa
from .settings import DATABASES, AUTHENTIKATE
import logging

# A real postgres from tests/integration/docker-compose.yaml, not sqlite. The
# suite exercises JSONB, constraint and transaction behaviour that sqlite only
# approximates, so testing on it is its own kind of mock.
#
# The host port is not pinned: docker assigns it and `django_db_modify_db_settings`
# in tests/conftest.py overwrites the placeholder below with the real one before
# pytest-django creates the test database.
DATABASES["default"] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "testdb",
    "USER": "test",
    "PASSWORD": "test",
    "HOST": "localhost",
    "PORT": "5432",
}


# No process-wide static token: `build_auth_context` registers its own per-test
# token (and restores the snapshot afterwards). The one that used to live here
# was unreferenced and carried an org *slug* in `active_org`, which is no longer
# how tenancy is keyed — see authapp.extension.read_org_claim.
AUTHENTIKATE = {**AUTHENTIKATE, "static_tokens": {}}

# Never touch the real ionscale CLI in tests: build the in-memory fake by default.
# The ``_reset_ionscale_repo`` autouse fixture rebuilds it fresh per test.
IONSCALE_REPOSITORY = "ionscale.testing.FakeIonscaleRepository"
# Don't fail-fast / eagerly build the repo at boot during tests.
IONSCALE_EAGER_INIT = False


# Disable migrations for faster tests
class DisableMigrations:
    """Disable migrations during testing for faster test execution."""

    def __contains__(self, item: str) -> bool:
        """Check if item is in migration modules."""
        return True

    def __getitem__(self, item: str) -> None:
        """Get migration module for item."""
        return None


# For faster test execution, you can uncomment this:
# MIGRATION_MODULES = DisableMigrations()

# Disable logging during tests to reduce noise
logging.disable(logging.CRITICAL)

# Enable database access from async code in tests
DATABASE_ROUTERS = []

# Use in-memory channel layer for tests instead of Redis
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


# --- object storage -------------------------------------------------------------
# Credentials are the scoped user `initc` provisions (tests/integration/configs/
# rustfs.yaml), matching tests/config.test.yaml. AWS_S3_ENDPOINT_URL is a
# placeholder: the `s3_endpoint` fixture rewrites it with the mapped port once the
# stack is up, because the presigned-URL code also strips this prefix out of its
# own output -- a stale value corrupts URLs silently rather than failing.
AWS_S3_ENDPOINT_URL = "http://localhost:9000"
AWS_S3_USE_SSL = False
AWS_S3_SECURE_URLS = False

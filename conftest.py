"""Repo-root fixtures: the real backing services, available to *every* test.

These live at the root rather than in ``tests/`` on purpose. lok's pytest config
also collects the Django-style app-level ``tests.py`` files (authapp, fakts,
karakter), and a ``tests/conftest.py`` would not apply to them --
pytest-django would then build the session database from the placeholder port in
``settings_test`` and every DB test would fail with "connection refused" on 5432,
whichever directory happened to be collected first.
"""

import os
import time

import boto3
import psycopg
import pytest
from dokker import PortNotFoundError, testing

# --- the real backing services -------------------------------------------------
# Postgres and object storage come from tests/integration/docker-compose.yaml.
# There are no mocks: the moto fixtures that used to sit here (`s3`,
# `create_bucket1`, `create_bucket2`) were referenced by nothing -- dead template
# code -- while lok's actual S3 surface (presigned POST for avatar/banner uploads,
# presigned GET to read them) went untested.

# This file sits at the repo root, so the compose file is under tests/.
COMPOSE = os.path.join(os.path.dirname(__file__), "tests", "integration", "docker-compose.yaml")

#: Must match tests/integration/configs/rustfs.yaml and tests/config.test.yaml.
S3_ACCESS_KEY = "lok_access_key"
S3_SECRET_KEY = "lok_secret_key"
S3_BUCKET = "media"


def _s3_client(port: int):
    return boto3.client(
        "s3",
        endpoint_url=f"http://localhost:{port}",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",
    )


@pytest.fixture(scope="session")
def backend_stack():
    """Bring up the stack and yield ``(db_port, s3_port)`` as docker assigned them.

    Neither is pinned: dokker isolates runs by minting a unique compose project,
    and a pinned host port defeats that -- two projects with different names still
    cannot both bind one host port, so a pinned port collides with sibling suites
    and with any stack stranded by a crashed run.
    """
    with testing(COMPOSE) as e:
        e.up()

        # Ask the running stack, not the compose file: `get_port` shells out to
        # `docker compose port`. Resolved inside the loop because `up()` is not
        # called with `wait`, so it can return before the container is running and
        # compose prints nothing for one that is not up yet.
        db_port = s3_port = None
        deadline = time.monotonic() + 60
        while True:
            try:
                if db_port is None:
                    db_port = e.get_port("db", 5432)
                if s3_port is None:
                    s3_port = e.get_port("rustfs", 9000)
                with psycopg.connect(
                    dbname="testdb", user="test", password="test",
                    host="localhost", port=db_port, connect_timeout=1,
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                break
            except (psycopg.OperationalError, PortNotFoundError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

        # `initc` only waits for rustfs's container to *start*, not to serve, so it
        # can race the server and exit before provisioning. Gate on /health, then
        # run it explicitly so a failure surfaces here rather than as a confusing
        # NoSuchBucket later.
        _wait_for_rustfs(s3_port, time.monotonic() + 60)
        e.run("initc", command="python init.py")
        _wait_for_bucket(s3_port, time.monotonic() + 30)

        yield db_port, s3_port


def _wait_for_rustfs(port: int, deadline: float) -> None:
    import urllib.error
    import urllib.request

    while True:
        try:
            # RustFS's native health endpoint (it also serves MinIO's
            # /minio/health/live as a compat shim; prefer its own).
            if urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2).status == 200:
                return
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"rustfs on port {port} never became healthy")
        time.sleep(0.2)


def _wait_for_bucket(port: int, deadline: float) -> None:
    client = _s3_client(port)
    while True:
        try:
            if S3_BUCKET in {b["Name"] for b in client.list_buckets()["Buckets"]}:
                return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f"initc did not provision {S3_BUCKET!r} (port {port})")
        time.sleep(0.2)


@pytest.fixture(scope="session")
def django_db_modify_db_settings(backend_stack):
    """Point Django at the postgres port the stack came up on.

    pytest-django calls this before creating the test database, which is the only
    window in which the port can be set: ``settings_test`` is imported long before
    any fixture runs, so it cannot know a port docker had not assigned yet.
    """
    from django.conf import settings

    db_port, _ = backend_stack
    settings.DATABASES["default"]["PORT"] = str(db_port)
    yield


@pytest.fixture(scope="session", autouse=True)
def s3_endpoint(backend_stack):
    """Point the datalayer at the object store's mapped port.

    Autouse because the presigned-URL code strips this prefix out of its own
    output, so a stale value silently corrupts URLs instead of failing.
    """
    from django.conf import settings

    _, s3_port = backend_stack
    settings.AWS_S3_ENDPOINT_URL = f"http://localhost:{s3_port}"
    yield settings.AWS_S3_ENDPOINT_URL


@pytest.fixture
def s3_client(backend_stack, s3_endpoint):
    """boto3 client for the real object store, as the provisioned scoped user."""
    _, s3_port = backend_stack
    return _s3_client(s3_port)

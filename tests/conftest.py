import os
import time

import boto3
import psycopg
import pytest
from dokker import PortNotFoundError, testing

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from karakter.models import Organization, User, Membership
from karakter.managers import create_role
from kante.context import HttpContext, UniversalRequest, TemporalResponse
from authentikate.base_models import StaticToken
from authentikate.settings import get_settings

# Make the factories importable as `pytest` fixtures-adjacent helpers.
from tests import factories  # noqa: F401


@pytest.fixture(autouse=True)
def _clear_cache():
    """The OAuth throttle (authapp/throttle.py) counts per-IP requests in the
    default LocMem cache, which persists for the whole test process — without
    clearing it, the suite itself trips the rate limit."""
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture(autouse=True)
def _restore_static_tokens():
    """Undo any per-test static tokens registered via ``build_auth_context``.

    ``get_settings()`` caches a single ``AuthentikateSettings`` for the process,
    so tokens registered during a test would otherwise leak into later tests.
    Snapshot the configured tokens and restore them afterwards.
    """
    settings_obj = get_settings()
    original = dict(settings_obj.static_tokens)
    yield
    settings_obj.static_tokens.clear()
    settings_obj.static_tokens.update(original)


def build_auth_context(user, organization, oauth2_client, roles=("admin",)) -> HttpContext:
    """Build an authenticated ``HttpContext`` via a static token.

    authentikate authenticates by decoding the ``Authorization`` header, so
    tests register a static token whose claims (``sub``/``org``/``client_id``)
    match freshly-created fixtures and send it as a bearer token. Note ``org``
    here carries the organization *pk*, exactly as a real lok-issued token does
    (see ``authapp.extension.read_org_claim``).
    The ``AuthAppExtension`` then resolves the karakter/fakts models from those
    claims exactly as it does in production.
    """
    token_str = f"static-{user.id}-{oauth2_client.client_id}"
    get_settings().static_tokens[token_str] = StaticToken(
        sub=str(user.id),
        # `iss` and `aud` must match what a real lok-issued token carries:
        # `AuthAppExtension` now rejects a token that was not issued by this
        # server or not addressed to it (see `assert_addressed_to_lok`). Minting
        # test tokens with production-shaped claims keeps that gate exercised
        # rather than quietly bypassed.
        iss=django_settings.OIDC_ISSUER,
        aud=["lok"],
        # The org pk, in the same claim a real token uses — authentikate v4
        # declares `org` on both JWTToken and StaticToken.
        org=str(organization.pk),
        client_id=oauth2_client.client_id,
        roles=list(roles),
    )
    request = UniversalRequest(_extensions={})
    # Populate the request principal directly. On the main schema the
    # ``AuthAppExtension`` resolves these from the bearer token, but the
    # management schema has no token extension (the SPA authenticates by session),
    # so an "authenticated" context must set them itself to exercise resolvers
    # that read ``request.user`` / ``.organization`` / ``.membership``.
    request.set_user(user)
    request.set_organization(organization)
    return HttpContext(
        request=request,
        response=TemporalResponse(),
        headers={"Authorization": f"Bearer {token_str}"},
        type="http",
    )


@pytest.fixture(autouse=True)
def _reset_ionscale_repo():
    """Give every test a fresh ionscale repository with no leaked state.

    The test settings point ``IONSCALE_REPOSITORY`` at ``FakeIonscaleRepository``,
    so the rebuilt repo is the in-memory fake — no CLI, no binary, no network.
    """
    from ionscale.repo import reset_ionscale_repo

    reset_ionscale_repo()
    yield
    reset_ionscale_repo()


@pytest.fixture
def ionscale_repo():
    """The active (fake) ionscale repository, for seeding data and asserting calls."""
    from ionscale.repo import get_ionscale_repo

    return get_ionscale_repo()


@pytest.fixture
def commit_callbacks(django_capture_on_commit_callbacks):
    """Run ``transaction.on_commit`` hooks for a block of test code.

    ``ionscale.sync`` only talks to the control plane after the surrounding
    transaction commits, and ``django_db`` wraps each test in a transaction that
    never does. Wrap the statements whose side effects you want to observe::

        with commit_callbacks():
            Membership.objects.create(...)
        assert ionscale_repo.updated_policies == [...]
    """

    def _capture():
        return django_capture_on_commit_callbacks(execute=True)

    return _capture


# --- the real backing services -------------------------------------------------
# Postgres and object storage come from tests/integration/docker-compose.yaml.
# There are no mocks: the moto fixtures that used to sit here (`s3`,
# `create_bucket1`, `create_bucket2`) were referenced by nothing -- dead template
# code -- while lok's actual S3 surface (presigned POST for avatar/banner uploads,
# presigned GET to read them) went untested.

COMPOSE = os.path.join(os.path.dirname(__file__), "integration", "docker-compose.yaml")

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


@pytest.fixture
def testing_org(db):
    """An organization owned by ``testuser`` with the default roles.

    Creating the ``User`` triggers the signal that builds a personal default
    organization; creating the ``Organization`` with an owner triggers the
    signal that seeds default roles/scopes and makes the owner an admin.
    """
    user = User.objects.create(username="testuser", password="testpass")
    org = Organization.objects.create(slug="testorg", name="Test Org", owner=user)
    # ``ensure_owner_is_admin`` (org post_save signal) already added the admin
    # role; this keeps the helper explicit/idempotent for readers.
    membership = Membership.objects.get(user=user, organization=org)
    membership.roles.add(create_role(organization=org, identifier="admin"))
    return org


@pytest.fixture
def authenticated_context(db, testing_org) -> HttpContext:
    user = User.objects.create(username="fart", password="123456789")
    membership, _ = Membership.objects.get_or_create(user=user, organization=testing_org)
    # A fakts Client (with its backing OAuth2Client) so the auth extension can
    # resolve ``request.client`` from the token's ``client_id``.
    fakts_client = factories.make_client(membership=membership)
    return build_auth_context(user, testing_org, fakts_client)

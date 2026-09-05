import pytest
import boto3
from moto import mock_aws
import os

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


@pytest.fixture(scope="function")
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture(scope="function")
def s3(aws_credentials):
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture
def create_bucket1(s3):
    s3.create_bucket(Bucket="babanana")


@pytest.fixture
def create_bucket2(s3):
    s3.create_bucket(Bucket="cabanana")


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

"""``priorAuthorizations`` on a pending device code.

When an app's refresh chain dies it re-registers and the user lands on the
configure page again. The page can then say "you already approved this app on
this device into hub X" and preselect X — derived purely from the bound
``Client`` rows that survive token expiry. Because the manifest's ``device_id``
is self-asserted, the lookup must be scoped to the caller's own approvals: a
different user, or an organization the caller is not in, must never show up.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from authapp.models import OAuth2Token
from fakts import base_models
from fakts.services import clients
from karakter.models import Scope
from tests import factories
from tests.conftest import build_auth_context

DEVICE_ID = "machine-abc"
IDENTIFIER = "com.example.app"

QUERY = """
query ($code: String!) {
  deviceCodeByCode(deviceCode: $code) {
    id
    priorAuthorizations {
      hub { id name }
      client { id }
      version
      scopes
      accessState
    }
  }
}
"""


def _manifest(device_id=DEVICE_ID, identifier=IDENTIFIER, version="1.0.0", scopes=None):
    return base_models.Manifest(identifier=identifier, version=version, scopes=scopes or [], requirements=[], device_id=device_id)


def _approve(membership, hub, manifest=None):
    """A bound client for ``manifest`` on ``hub``, as an earlier approval left it."""
    return clients.bind_client(clients.create_public_client(), manifest or _manifest(), membership, hub=hub)


def _token(client, **kw):
    n = OAuth2Token.objects.count()
    kw.setdefault("access_token", f"access-{client.client_id}-{n}")
    kw.setdefault("refresh_token", f"refresh-{client.client_id}-{n}")
    return OAuth2Token.objects.create(user=client.membership, client_id=client.client_id, token_type="Bearer", **kw)


def _pending(manifest=None):
    return factories.make_device_code(staging_manifest=(manifest or _manifest()).model_dump())


def _context(membership):
    return build_auth_context(membership.user, membership.organization, factories.make_client(membership=membership))


async def _query(context, code):
    return await management_schema.execute(QUERY, context_value=context, variable_values={"code": code.code})


# --- service layer ---------------------------------------------------------


@pytest.mark.django_db
def test_prior_clients_match_same_user_app_and_device():
    membership = factories.make_membership()
    hub = factories.make_hub(organization=membership.organization)
    old = _approve(membership, hub)

    found = list(clients.find_prior_clients(_manifest(), membership.user))

    assert [c.pk for c in found] == [old.pk]
    assert found[0].hub == hub


@pytest.mark.django_db
def test_prior_clients_ignore_other_devices_and_other_apps():
    membership = factories.make_membership()
    hub = factories.make_hub(organization=membership.organization)
    _approve(membership, hub, _manifest(device_id="another-machine"))
    _approve(membership, hub, _manifest(identifier="com.example.other"))

    assert not clients.find_prior_clients(_manifest(), membership.user).exists()


@pytest.mark.django_db
def test_prior_clients_never_reveal_another_users_approval():
    """Same device id, same app, same organization — but approved by someone else."""
    organization = factories.make_organization()
    theirs = factories.make_membership(organization=organization)
    mine = factories.make_membership(organization=organization)
    _approve(theirs, factories.make_hub(organization=organization))

    assert not clients.find_prior_clients(_manifest(), mine.user).exists()


@pytest.mark.django_db
def test_prior_clients_stay_within_the_callers_organizations():
    """The hash is per-organization and only the caller's current memberships are
    hashed against, so leaving an organization leaves nothing to match there."""
    user = factories.make_user()
    membership = factories.make_membership(user=user)
    _approve(membership, factories.make_hub(organization=membership.organization))
    assert clients.find_prior_clients(_manifest(), user).exists()

    membership.delete()

    assert not clients.find_prior_clients(_manifest(), user).exists()


@pytest.mark.django_db
def test_manifest_without_device_id_matches_nothing():
    membership = factories.make_membership()
    _approve(membership, factories.make_hub(organization=membership.organization))

    assert not clients.find_prior_clients(_manifest(device_id=None), membership.user).exists()


@pytest.mark.django_db
def test_most_recently_seen_client_comes_first():
    membership = factories.make_membership()
    hub_a = factories.make_hub(organization=membership.organization)
    hub_b = factories.make_hub(organization=membership.organization)
    a = _approve(membership, hub_a)
    b = _approve(membership, hub_b)
    # `last_reported_at` is auto_now; push A into the past explicitly.
    type(a).objects.filter(pk=a.pk).update(last_reported_at="2020-01-01T00:00:00Z")

    found = list(clients.find_prior_clients(_manifest(), membership.user))

    assert [c.pk for c in found] == [b.pk, a.pk]


@pytest.mark.django_db
def test_access_state_distinguishes_expired_revoked_and_active():
    membership = factories.make_membership()
    hub = factories.make_hub(organization=membership.organization)

    never_issued = _approve(membership, hub)
    assert clients.client_access_state(never_issued) == clients.PRIOR_ACCESS_EXPIRED

    expired = _approve(membership, hub, _manifest(identifier="com.example.expired"))
    _token(expired, issued_at=1, chain_started_at=1)
    assert clients.client_access_state(expired) == clients.PRIOR_ACCESS_EXPIRED

    revoked = _approve(membership, hub, _manifest(identifier="com.example.revoked"))
    _token(revoked, revoked=True)
    assert clients.client_access_state(revoked) == clients.PRIOR_ACCESS_REVOKED

    active = _approve(membership, hub, _manifest(identifier="com.example.active"))
    _token(active)
    assert clients.client_access_state(active) == clients.PRIOR_ACCESS_ACTIVE

    # Only the newest token counts: a rotated-away (revoked) predecessor does not
    # make a live chain read as revoked.
    rotated = _approve(membership, hub, _manifest(identifier="com.example.rotated"))
    _token(rotated, revoked=True, issued_at=10, chain_started_at=10)
    _token(rotated)
    assert clients.client_access_state(rotated) == clients.PRIOR_ACCESS_ACTIVE


# --- GraphQL ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pending_code_lists_the_callers_earlier_approval():
    def setup():
        membership = factories.make_membership()
        Scope.objects.create(identifier="read", organization=membership.organization)
        hub = factories.make_hub(organization=membership.organization)
        old = _approve(membership, hub, _manifest(scopes=["read"]))
        _token(old, issued_at=1, chain_started_at=1)
        pending = _pending(_manifest(version="2.0.0", scopes=["read", "write"]))
        return _context(membership), hub, old, pending

    context, hub, old, pending = await sync_to_async(setup)()

    result = await _query(context, pending)

    assert not result.errors, result.errors
    prior = result.data["deviceCodeByCode"]["priorAuthorizations"]
    assert len(prior) == 1
    assert prior[0]["hub"]["id"] == str(hub.id)
    assert prior[0]["client"]["id"] == str(old.id)
    assert prior[0]["version"] == "1.0.0"
    assert prior[0]["scopes"] == ["read"]
    assert prior[0]["accessState"] == "EXPIRED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_user_opening_the_same_link_sees_no_prior_authorization():
    def setup():
        organization = factories.make_organization()
        approver = factories.make_membership(organization=organization)
        _approve(approver, factories.make_hub(organization=organization))
        other = factories.make_membership(organization=organization)
        return _context(other), _pending()

    context, pending = await sync_to_async(setup)()

    result = await _query(context, pending)

    assert not result.errors, result.errors
    assert result.data["deviceCodeByCode"]["priorAuthorizations"] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_code_without_device_id_has_no_prior_authorizations():
    def setup():
        membership = factories.make_membership()
        _approve(membership, factories.make_hub(organization=membership.organization))
        return _context(membership), _pending(_manifest(device_id=None))

    context, pending = await sync_to_async(setup)()

    result = await _query(context, pending)

    assert not result.errors, result.errors
    assert result.data["deviceCodeByCode"]["priorAuthorizations"] == []

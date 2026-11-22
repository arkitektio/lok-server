"""A deactivated account gets no new tokens from any grant, and its live access
tokens stop at lok's API at once, instead of working until the refresh chain's
lifetime ran out."""

import pytest
from asgiref.sync import sync_to_async

from authapp.models import OAuth2Token
from lok_server.schema import schema as main_schema
from tests import factories
from tests.conftest import build_auth_context
from tests.test_oauth_hardening import _granted_session, _refresh


def _deactivate(user):
    user.is_active = False
    user.save(update_fields=["is_active"])


@pytest.mark.django_db
def test_a_deactivated_account_refreshes_no_more(client):
    body, token = _granted_session(client)
    row = OAuth2Token.objects.get(access_token=token["access_token"])
    _deactivate(row.user.user)

    refreshed = _refresh(client, token["refresh_token"], body["client_id"])

    assert refreshed.status_code == 400
    assert "access_token" not in refreshed.json()


@pytest.mark.django_db
def test_an_active_account_still_refreshes(client):
    body, token = _granted_session(client)
    assert (
        _refresh(client, token["refresh_token"], body["client_id"]).status_code == 200
    )


def _context(active: bool):
    membership = factories.make_membership()
    if not active:
        _deactivate(membership.user)
    return build_auth_context(
        membership.user,
        membership.organization,
        factories.make_client(membership=membership),
        scope="openid lok_all",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_deactivated_accounts_access_token_is_refused_at_once():
    context = await sync_to_async(_context)(active=False)
    result = await main_schema.execute("query { me { id } }", context_value=context)
    assert result.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_active_accounts_access_token_works():
    context = await sync_to_async(_context)(active=True)
    result = await main_schema.execute("query { me { id } }", context_value=context)
    assert result.errors is None, result.errors

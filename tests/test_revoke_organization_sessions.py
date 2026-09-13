"""``revokeOrganizationSessions`` — the org-wide token kill switch.

Its sibling ``revokeClientSessions`` is covered in ``test_oauth_hardening``; the
organization-wide variant was not. These pin the owner-or-admin bar, that the
return value counts only newly revoked rows (so a second call returns 0), and
that another tenant's tokens are never touched.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from authapp.models import OAuth2Token
from tests import factories
from tests.test_management_tenant_isolation import _org_with_admin_and_member

REVOKE = "mutation ($org: ID!) { revokeOrganizationSessions(input: { organization: $org }) }"


def _token(membership, client, **kw):
    kw.setdefault("access_token", f"access-{client.client_id}-{OAuth2Token.objects.count()}")
    kw.setdefault("refresh_token", f"refresh-{client.client_id}-{OAuth2Token.objects.count()}")
    return OAuth2Token.objects.create(user=membership, client_id=client.client_id, token_type="Bearer", **kw)


def _setup():
    s = _org_with_admin_and_member()
    member = s["member_membership"]
    client_a = factories.make_client(membership=member)
    client_b = factories.make_client(membership=member)
    s["tokens"] = [_token(member, client_a), _token(member, client_a), _token(member, client_b)]

    theirs = factories.make_membership()
    s["their_token"] = _token(theirs, factories.make_client(membership=theirs))
    return s


async def _revoke(context, org):
    return await management_schema.execute(REVOKE, context_value=context, variable_values={"org": str(org.id)})


async def _revoked(tokens):
    return await sync_to_async(
        lambda: [OAuth2Token.objects.get(pk=t.pk).revoked for t in tokens]
    )()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_revokes_every_token_of_the_organization():
    s = await sync_to_async(_setup)()

    result = await _revoke(s["owner"], s["org"])

    assert not result.errors, result.errors
    assert result.data["revokeOrganizationSessions"] == 3
    assert await _revoked(s["tokens"]) == [True, True, True]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_may_revoke_too():
    s = await sync_to_async(_setup)()

    result = await _revoke(s["admin"], s["org"])

    assert not result.errors, result.errors
    assert result.data["revokeOrganizationSessions"] == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_is_denied():
    s = await sync_to_async(_setup)()

    result = await _revoke(s["member"], s["org"])

    assert result.errors and result.errors[0].message == DENIED
    assert await _revoked(s["tokens"]) == [False, False, False]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_second_call_revokes_nothing_new():
    s = await sync_to_async(_setup)()

    first = await _revoke(s["owner"], s["org"])
    second = await _revoke(s["owner"], s["org"])

    assert first.data["revokeOrganizationSessions"] == 3
    assert not second.errors, second.errors
    assert second.data["revokeOrganizationSessions"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_other_tenants_tokens_are_untouched():
    s = await sync_to_async(_setup)()

    await _revoke(s["owner"], s["org"])

    assert await _revoked([s["their_token"]]) == [False]

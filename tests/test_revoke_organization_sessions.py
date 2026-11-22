"""``revokeOrganizationSessions`` — the org-wide token kill switch.

Its sibling ``revokeClientSessions`` is covered in ``test_oauth_hardening``; the
organization-wide variant was not. These pin the owner-or-admin bar, that the
return value counts only newly revoked rows (so a second call returns 0), and
that another tenant's tokens are never touched.
"""

from typing import Any, TypedDict

import pytest
from asgiref.sync import sync_to_async
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from authapp.models import OAuth2Token
from fakts import models as fmodels
from karakter.models import Membership, Organization
from tests import factories
from tests.test_management_tenant_isolation import _org_with_admin_and_member

REVOKE = (
    "mutation ($org: ID!) { revokeOrganizationSessions(input: { organization: $org }) }"
)


def _token(membership: Membership, client: fmodels.Client, **kw: Any) -> OAuth2Token:
    kw.setdefault(
        "access_token", f"access-{client.client_id}-{OAuth2Token.objects.count()}"
    )
    kw.setdefault(
        "refresh_token", f"refresh-{client.client_id}-{OAuth2Token.objects.count()}"
    )
    return OAuth2Token.objects.create(
        user=membership, client_id=client.client_id, token_type="Bearer", **kw
    )


class Setup(TypedDict):
    org: Organization
    owner: HttpContext
    admin: HttpContext
    member: HttpContext
    member_membership: Membership
    tokens: list[OAuth2Token]
    their_token: OAuth2Token


def _setup() -> Setup:
    s = _org_with_admin_and_member()
    member = s["member_membership"]
    client_a = factories.make_client(membership=member)
    client_b = factories.make_client(membership=member)
    tokens = [
        _token(member, client_a),
        _token(member, client_a),
        _token(member, client_b),
    ]

    theirs = factories.make_membership()
    return {
        "org": s["org"],
        "owner": s["owner"],
        "admin": s["admin"],
        "member": s["member"],
        "member_membership": member,
        "tokens": tokens,
        "their_token": _token(theirs, factories.make_client(membership=theirs)),
    }


async def _revoke(context: HttpContext, org: Organization) -> ExecutionResult:
    return await management_schema.execute(
        REVOKE, context_value=context, variable_values={"org": str(org.id)}
    )


async def _revoked(tokens: list[OAuth2Token]) -> list[bool]:
    return await sync_to_async(
        lambda: [OAuth2Token.objects.get(pk=t.pk).revoked for t in tokens]
    )()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_revokes_every_token_of_the_organization() -> None:
    s = await sync_to_async(_setup)()

    result = await _revoke(s["owner"], s["org"])

    assert not result.errors, result.errors
    assert result.data["revokeOrganizationSessions"] == 3
    assert await _revoked(s["tokens"]) == [True, True, True]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_may_revoke_too() -> None:
    s = await sync_to_async(_setup)()

    result = await _revoke(s["admin"], s["org"])

    assert not result.errors, result.errors
    assert result.data["revokeOrganizationSessions"] == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_plain_member_is_denied() -> None:
    s = await sync_to_async(_setup)()

    result = await _revoke(s["member"], s["org"])

    assert result.errors and result.errors[0].message == DENIED
    assert await _revoked(s["tokens"]) == [False, False, False]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_second_call_revokes_nothing_new() -> None:
    s = await sync_to_async(_setup)()

    first = await _revoke(s["owner"], s["org"])
    second = await _revoke(s["owner"], s["org"])

    assert first.data["revokeOrganizationSessions"] == 3
    assert not second.errors, second.errors
    assert second.data["revokeOrganizationSessions"] == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_other_tenants_tokens_are_untouched() -> None:
    s = await sync_to_async(_setup)()

    await _revoke(s["owner"], s["org"])

    assert await _revoked([s["their_token"]]) == [False]

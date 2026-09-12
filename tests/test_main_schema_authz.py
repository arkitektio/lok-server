"""Authorization regressions for mutations on the main `/schema`.

Three mutations here had no authorization at all while their `api/management/`
twins asserted organization ownership. Any authenticated principal could rename
or re-slug any tenant, and mint invite tokens for one. These tests pin the guards
and, just as importantly, check the legitimate path still works.

Denials are asserted on the shared "Not found, or you are not authorized" text so
the error cannot be used as an existence oracle.
"""

import pytest
from asgiref.sync import sync_to_async

from karakter.models import Invite, Organization
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _owner_and_outsider():
    """An organization with its owner, plus an unrelated authenticated principal."""
    owner_membership = factories.make_membership()
    owner = owner_membership.user
    org = owner_membership.organization
    org.owner = owner
    org.save()

    owner_client = factories.make_client(membership=owner_membership)
    owner_context = build_auth_context(owner, org, owner_client)

    outsider_membership = factories.make_membership()
    outsider_client = factories.make_client(membership=outsider_membership)
    outsider_context = build_auth_context(
        outsider_membership.user, outsider_membership.organization, outsider_client
    )

    return owner_context, outsider_context, org


def _assert_denied(result):
    assert result.errors, f"expected a denial, got data: {result.data}"
    assert "not authorized" in result.errors[0].message, result.errors[0].message


UPDATE_ORG = """
    mutation ($input: UpdateOrganizationInput!) {
        updateOrganization(input: $input) { id name slug }
    }
"""

CREATE_INVITE = """
    mutation ($input: CreateInviteInput!) {
        createInvite(input: $input) { id token }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_outsider_cannot_rename_another_organization():
    _owner_context, outsider_context, org = await sync_to_async(_owner_and_outsider)()
    original_slug = org.slug

    result = await schema.execute(
        UPDATE_ORG,
        context_value=outsider_context,
        variable_values={"input": {"id": str(org.id), "name": "Pwned", "slug": "pwned"}},
    )

    _assert_denied(result)
    fresh = await sync_to_async(Organization.objects.get)(pk=org.pk)
    assert fresh.slug == original_slug
    assert fresh.name != "Pwned"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_still_update_their_organization():
    owner_context, _outsider_context, org = await sync_to_async(_owner_and_outsider)()

    result = await schema.execute(
        UPDATE_ORG,
        context_value=owner_context,
        variable_values={"input": {"id": str(org.id), "name": "Renamed"}},
    )

    assert not result.errors, result.errors
    assert result.data["updateOrganization"]["name"] == "Renamed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_outsider_cannot_mint_an_invite_for_another_organization():
    """An invite token grants membership and whatever roles it carries, so minting
    one must not be possible just by passing another organization's id."""
    _owner_context, outsider_context, org = await sync_to_async(_owner_and_outsider)()

    result = await schema.execute(
        CREATE_INVITE,
        context_value=outsider_context,
        variable_values={"input": {"organization": str(org.id), "roles": ["admin"]}},
    )

    _assert_denied(result)
    assert not await sync_to_async(Invite.objects.filter(created_for=org).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_still_create_an_invite():
    owner_context, _outsider_context, org = await sync_to_async(_owner_and_outsider)()

    result = await schema.execute(
        CREATE_INVITE,
        context_value=owner_context,
        variable_values={"input": {"organization": str(org.id)}},
    )

    assert not result.errors, result.errors
    assert result.data["createInvite"]["token"]

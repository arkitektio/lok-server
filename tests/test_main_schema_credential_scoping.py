"""Cross-tenant credential leaks on the main `/graphql` schema.

Three fields on this schema handed out bearer credentials belonging to other
tenants to any principal holding a valid token:

- ``clients { token }`` — a fakts client token, redeemable at the unauthenticated
  ``/f/claim/`` endpoint for that client's OAuth ``client_id``/``client_secret``.
- ``invites { token }`` — an invite token grants membership plus whatever roles
  the invite carries.
- ``renderHub(client:)`` — rendered the full configuration, including
  ``client_secret``, for an arbitrary client pk with no check at all.

The `api/management/` twins of all three were already scoped; these are the
regressions for the copies that were not.
"""

import pytest
from asgiref.sync import sync_to_async

from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _two_tenants():
    """Two unrelated organizations, each with a member, a client and an invite."""
    mine = factories.make_membership()
    my_client = factories.make_client(membership=mine)
    my_context = build_auth_context(mine.user, mine.organization, my_client)

    theirs = factories.make_membership()
    their_client = factories.make_client(membership=theirs)
    their_context = build_auth_context(
        theirs.user, theirs.organization, their_client
    )

    return my_context, my_client, their_context, their_client


LIST_CLIENTS = """
    query { clients { id } }
"""

LIST_INVITE_TOKENS = """
    query { invites { id token } }
"""

RENDER_HUB = """
    mutation ($input: RenderInput!) { render(input: $input) }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_list_does_not_leak_other_tenants_clients():
    my_context, my_client, _their_context, their_client = await sync_to_async(_two_tenants)()

    result = await schema.execute(LIST_CLIENTS, context_value=my_context)

    assert not result.errors, result.errors
    returned = {row["id"] for row in result.data["clients"]}
    assert str(my_client.id) in returned, "own client should still be listed"
    assert str(their_client.id) not in returned, "another tenant's client leaked"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_invite_list_does_not_leak_other_tenants_tokens():
    my_context, _my_client, their_context, _their_client = await sync_to_async(_two_tenants)()

    def _make_their_invite():
        # An invite belonging to the *other* tenant, carrying the admin role.
        org = their_context.request.organization
        from karakter.models import Invite

        return Invite.objects.create(created_by=org.owner, created_for=org)

    their_invite = await sync_to_async(_make_their_invite)()

    result = await schema.execute(LIST_INVITE_TOKENS, context_value=my_context)

    assert not result.errors, result.errors
    returned = {row["id"] for row in result.data["invites"]}
    assert str(their_invite.id) not in returned, "another tenant's invite token leaked"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_render_hub_refuses_another_tenants_client():
    """`renderHub` returns `client_secret`, so it must be owner-scoped."""
    my_context, _my_client, _their_context, their_client = await sync_to_async(_two_tenants)()

    result = await schema.execute(
        RENDER_HUB,
        context_value=my_context,
        variable_values={"input": {"client": str(their_client.id)}},
    )

    assert result.errors, f"expected a denial, got data: {result.data}"
    assert "not authorized" in result.errors[0].message, result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_render_hub_denies_before_reaching_the_broken_render_path():
    """The ownership check must run *before* rendering, not after.

    `create_fake_linking_context` (fakts/services/rendering.py:229-233) reads
    `client.client_id` / `client.client_secret` and `oauth2_client.client_type` /
    `.authorization_grant_type` / `.name` — none of which exist on either model. So
    this mutation raises `AttributeError` for its own tenant too and is dead code
    today. That is *why* the guard is worth having: the day someone repairs the
    render path, an unscoped lookup would start handing out `client_secret`.

    Pinning the denial as a distinct message proves the guard short-circuits ahead
    of that AttributeError rather than accidentally relying on it.
    """
    my_context, _my_client, _their_context, their_client = await sync_to_async(_two_tenants)()

    result = await schema.execute(
        RENDER_HUB,
        context_value=my_context,
        variable_values={"input": {"client": str(their_client.id)}},
    )

    assert result.errors
    assert "not authorized" in result.errors[0].message
    assert "client_id" not in result.errors[0].message, (
        "reached the render path — the ownership check did not short-circuit"
    )


OWN_CLIENT_TOKEN = """
    query { clients { id token } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_client_token_field_no_longer_exists():
    """The opaque `Client.token` credential is gone — client identity is the
    OAuth2 client_id + refresh chain, so the schema must not expose a `token`
    field on Client at all (including via FK traversal)."""
    my_context, _my_client, _their_context, _their_client = await sync_to_async(_two_tenants)()

    result = await schema.execute(OWN_CLIENT_TOKEN, context_value=my_context)

    assert result.errors, "the `token` field on Client should not exist anymore"
    assert any("token" in str(e) for e in result.errors)

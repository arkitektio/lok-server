"""GraphQL mutation tests that exercise the schema through the service layer."""

import pytest
from asgiref.sync import sync_to_async

from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _context(user, organization, client):
    # ``client`` is a fakts Client; authenticate through its backing OAuth2Client.
    return build_auth_context(user, organization, client)


def _setup():
    """Sync DB setup (must not run inside the async event loop)."""
    membership = factories.make_membership()
    # the mutation reads request.client.hub; a fakts Client provides it
    request_client = factories.make_client(membership=membership)
    return membership.user, membership.organization, request_client


CREATE_DEV_CLIENT = """
    mutation Create($input: DevelopmentClientInput!) {
        createDevelopmentalClient(input: $input) {
            id
            kind
            role
        }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_developmental_client_mutation():
    user, organization, request_client = await sync_to_async(_setup)()

    result = await schema.execute(
        CREATE_DEV_CLIENT,
        context_value=_context(user, organization, request_client),
        variable_values={
            "input": {
                "manifest": {
                    "identifier": "com.example.gql",
                    "version": "3.0.0",
                    "scopes": [],
                    "requirements": [],
                },
                "role": "AGENT",
            }
        },
    )

    assert not result.errors, result.errors
    data = result.data["createDevelopmentalClient"]
    assert data["kind"] == "DEVELOPMENT"
    assert data["role"] == "AGENT"

    # verify the created client landed in the DB with the right app/role
    created = await sync_to_async(_fetch_created)()
    assert created is not None


def _fetch_created():
    from fakts import models

    return models.Client.objects.filter(release__app__identifier="com.example.gql", role="agent").first()


# --- createRedeemToken -----------------------------------------------------------

CREATE_REDEEM_TOKEN = """
    mutation ($input: RedeemTokenInput!) {
        createRedeemToken(input: $input) {
            id
            token
            expiresAt
            maxRedemptions
            redemptionCount
            pinnedManifest
        }
    }
"""

PINNED_MANIFEST = {
    "identifier": "com.example.pinned",
    "version": "1.0.0",
    "scopes": ["read"],
    "deviceId": "node-a",
    "requirements": [{"key": "rekuest", "service": "live.arkitekt.rekuest"}],
}


def _setup_with_hub():
    """Like ``_setup`` but the calling client composes against a hub: redeem tokens
    are issued *for* the caller's hub, so a hub-less client is denied."""
    membership = factories.make_membership()
    hub = factories.make_hub(organization=membership.organization)
    request_client = factories.make_client(membership=membership, hub=hub)
    return membership.user, membership.organization, request_client


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_redeem_token_pins_the_manifest():
    user, organization, request_client = await sync_to_async(_setup_with_hub)()

    result = await schema.execute(
        CREATE_REDEEM_TOKEN,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": PINNED_MANIFEST, "expiresInDays": 1, "maxRedemptions": 1}},
    )

    assert not result.errors, result.errors
    data = result.data["createRedeemToken"]
    assert data["token"]
    assert data["maxRedemptions"] == 1
    assert data["redemptionCount"] == 0
    pinned = data["pinnedManifest"]
    assert pinned["identifier"] == "com.example.pinned"
    assert pinned["version"] == "1.0.0"
    assert pinned["device_id"] == "node-a"
    assert pinned["scopes"] == ["read"]
    assert [(r["key"], r["service"]) for r in pinned["requirements"]] == [("rekuest", "live.arkitekt.rekuest")]

    from datetime import datetime, timedelta, timezone

    expires_at = datetime.fromisoformat(data["expiresAt"])
    assert timedelta(hours=23) < expires_at - datetime.now(timezone.utc) <= timedelta(days=1)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_redeem_token_requires_a_manifest():
    """An app-minted token is always pinned: the schema itself refuses a mint without one."""
    user, organization, request_client = await sync_to_async(_setup_with_hub)()

    result = await schema.execute(
        CREATE_REDEEM_TOKEN,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {}},
    )

    assert result.errors
    assert "manifest" in result.errors[0].message

    def _count():
        from fakts import models

        return models.RedeemToken.objects.count()

    assert await sync_to_async(_count)() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_redeem_token_defaults():
    user, organization, request_client = await sync_to_async(_setup_with_hub)()

    result = await schema.execute(
        CREATE_REDEEM_TOKEN,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": PINNED_MANIFEST}},
    )

    assert not result.errors, result.errors
    data = result.data["createRedeemToken"]
    assert data["pinnedManifest"]["identifier"] == "com.example.pinned"
    assert data["maxRedemptions"] is None

    from datetime import datetime, timedelta, timezone

    expires_at = datetime.fromisoformat(data["expiresAt"])
    assert timedelta(days=6, hours=23) < expires_at - datetime.now(timezone.utc) <= timedelta(days=7)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad, message",
    [
        ({"expiresInDays": 90}, "expiresInDays"),
        ({"expiresInDays": 0}, "expiresInDays"),
        ({"maxRedemptions": 0}, "maxRedemptions"),
    ],
)
async def test_create_redeem_token_rejects_bad_budgets(bad, message):
    user, organization, request_client = await sync_to_async(_setup_with_hub)()

    result = await schema.execute(
        CREATE_REDEEM_TOKEN,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": PINNED_MANIFEST, **bad}},
    )

    assert result.errors
    assert message in result.errors[0].message


DELETE_REDEEM_TOKEN = """
    mutation ($id: ID!) { deleteRedeemToken(input: {id: $id}) }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_redeem_token_spends_it_but_keeps_the_client():
    user, organization, request_client = await sync_to_async(_setup_with_hub)()
    context = _context(user, organization, request_client)

    minted = await schema.execute(
        CREATE_REDEEM_TOKEN,
        context_value=context,
        variable_values={"input": {"manifest": PINNED_MANIFEST}},
    )
    assert not minted.errors, minted.errors
    token_id = minted.data["createRedeemToken"]["id"]

    def _redeem_it():
        from fakts import models
        from fakts.base_models import Manifest
        from fakts.services.clients import redeem_token

        token = models.RedeemToken.objects.get(id=token_id)
        # A subset of the pin: the test hub offers no rekuest instance and the org
        # defines no `read` scope, and the pin is a ceiling, not a demand.
        return redeem_token(token.token, Manifest(identifier="com.example.pinned", version="1.0.0", scopes=[], device_id="node-a", requirements=[]))

    client = await sync_to_async(_redeem_it)()

    deleted = await schema.execute(DELETE_REDEEM_TOKEN, context_value=context, variable_values={"id": token_id})
    assert not deleted.errors, deleted.errors
    assert deleted.data["deleteRedeemToken"] == token_id

    def _after():
        from fakts import models

        return models.RedeemToken.objects.filter(id=token_id).exists(), models.Client.objects.filter(pk=client.pk).exists()

    token_exists, client_exists = await sync_to_async(_after)()
    assert not token_exists
    assert client_exists, "revoking the token must not tear down the client it produced"

    again = await schema.execute(DELETE_REDEEM_TOKEN, context_value=context, variable_values={"id": token_id})
    assert again.errors and "not authorized" in again.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_deprecated_node_id_input_still_pins_the_device():
    """``nodeId`` on ``ManifestInput`` is deprecated but still accepted, and lands as
    ``device_id`` in the pinned manifest."""
    user, organization, request_client = await sync_to_async(_setup_with_hub)()
    manifest = {**PINNED_MANIFEST}
    manifest["nodeId"] = manifest.pop("deviceId")

    result = await schema.execute(
        CREATE_REDEEM_TOKEN,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": manifest, "expiresInDays": 1, "maxRedemptions": 1}},
    )

    assert not result.errors, result.errors
    pinned = result.data["createRedeemToken"]["pinnedManifest"]
    assert pinned["device_id"] == "node-a"
    assert "node_id" not in pinned


def test_the_deprecated_node_id_input_is_marked_deprecated_in_the_schema():
    sdl = schema.as_str()
    start = sdl.index("input ManifestInput ")
    block = sdl[start : sdl.index("\n}", start)]
    assert "deviceId" in block
    node_line = next(line for line in block.splitlines() if line.strip().startswith("nodeId"))
    assert "@deprecated" in node_line

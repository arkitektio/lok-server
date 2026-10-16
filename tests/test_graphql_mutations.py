"""GraphQL mutation tests that exercise the schema through the service layer."""

import pytest
from asgiref.sync import sync_to_async

from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _context(user, organization, client):
    # ``client`` is a fakts Client; authenticate through its backing OAuth2Client.
    return build_auth_context(user, organization, client)


# --- issueMandateKey ---------------------------------------------------------------

ISSUE_MANDATE_KEY = """
    mutation ($input: IssueMandateKeyInput!) {
        issueMandateKey(input: $input) {
            key
            mandate { id expiresAt maxClients subject { identifier version deviceId scopes requirements { key service } } }
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
    """A calling app that composes against a hub and holds what PINNED_MANIFEST asks for."""
    from karakter.models import Scope

    membership = factories.make_membership()
    hub = factories.make_hub(organization=membership.organization)
    read = Scope.objects.create(organization=membership.organization, identifier="read")
    request_client = factories.make_client(
        membership=membership,
        hub=hub,
        manifest={"identifier": "com.example.caller", "version": "1.0.0", "requirements": [{"key": "rekuest", "service": "live.arkitekt.rekuest"}]},
    )
    request_client.scopes.add(read)
    return membership.user, membership.organization, request_client


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_issue_mandate_key_pins_the_manifest():
    user, organization, request_client = await sync_to_async(_setup_with_hub)()

    result = await schema.execute(
        ISSUE_MANDATE_KEY,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": PINNED_MANIFEST, "expiresInDays": 3, "maxClients": 1}},
    )

    assert not result.errors, result.errors
    data = result.data["issueMandateKey"]
    assert len(data["key"]) >= 32
    assert data["mandate"]["maxClients"] == 1
    assert data["mandate"]["expiresAt"] is not None
    assert data["mandate"]["subject"] == {
        "identifier": "com.example.pinned",
        "version": "1.0.0",
        "deviceId": "node-a",
        "scopes": ["read"],
        "requirements": [{"key": "rekuest", "service": "live.arkitekt.rekuest"}],
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_issue_mandate_key_requires_an_expiry():
    user, organization, request_client = await sync_to_async(_setup_with_hub)()

    result = await schema.execute(
        ISSUE_MANDATE_KEY,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": PINNED_MANIFEST}},
    )

    assert result.errors and "expiresInDays" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_deprecated_node_id_input_still_pins_the_device():
    """``nodeId`` on ``ManifestInput`` is deprecated but still accepted, and lands as
    ``device_id`` in the pin."""
    user, organization, request_client = await sync_to_async(_setup_with_hub)()
    manifest = {**PINNED_MANIFEST}
    manifest["nodeId"] = manifest.pop("deviceId")

    result = await schema.execute(
        ISSUE_MANDATE_KEY,
        context_value=_context(user, organization, request_client),
        variable_values={"input": {"manifest": manifest, "expiresInDays": 1}},
    )

    assert not result.errors, result.errors
    assert result.data["issueMandateKey"]["mandate"]["subject"]["deviceId"] == "node-a"


def test_the_deprecated_node_id_input_is_marked_deprecated_in_the_schema():
    sdl = schema.as_str()
    start = sdl.index("input ManifestInput ")
    block = sdl[start : sdl.index("\n}", start)]
    assert "deviceId" in block
    node_line = next(line for line in block.splitlines() if line.strip().startswith("nodeId"))
    assert "@deprecated" in node_line


def test_the_redeem_token_surface_is_gone():
    sdl = schema.as_str()
    for name in ("createRedeemToken", "deleteRedeemToken", "redeemToken", "provision(", "createDevelopmentalClient"):
        assert name not in sdl

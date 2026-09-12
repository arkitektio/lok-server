"""Unit tests for the fakts service layer (clients, device codes, rendering)."""

import pytest

from fakts import base_models, enums, models
from fakts.services import clients, device_codes, rendering
from tests import factories


def _manifest(identifier="com.example.app", version="1.0.0", requirements=None):
    return base_models.Manifest(
        identifier=identifier,
        version=version,
        scopes=[],
        requirements=requirements or [],
    )


@pytest.mark.django_db
def test_bind_client_creates_development_client_with_role():
    membership = factories.make_membership()

    client = clients.create_public_client(role=enums.ClientRoleVanilla.AGENT.value)
    client = clients.bind_client(client, _manifest(), membership)

    assert client.kind == "development"
    assert client.role == "agent"
    assert client.release.app.identifier == "com.example.app"
    assert client.client_id
    assert client.membership == membership
    assert client.organization == membership.organization


@pytest.mark.django_db
def test_create_client_rejects_missing_node_id_when_org_requires_device_auth():
    membership = factories.make_membership()
    user = membership.user
    organization = membership.organization
    organization.require_device_auth = True
    organization.save()

    with pytest.raises(clients.DeviceAuthRequired):
        clients.bind_client(clients.create_public_client(), _manifest(), membership)


@pytest.mark.django_db
def test_create_client_allows_node_id_when_org_requires_device_auth():
    membership = factories.make_membership()
    user = membership.user
    organization = membership.organization
    organization.require_device_auth = True
    organization.save()

    manifest = base_models.Manifest(
        identifier="com.example.node",
        version="1.0.0",
        scopes=[],
        requirements=[],
        node_id="node-123",
    )

    client = clients.bind_client(clients.create_public_client(), manifest, membership)

    assert client.node is not None


@pytest.mark.django_db
def test_validate_redeem_token_creates_and_attaches_client():
    hub = factories.make_hub()
    redeem = factories.make_redeem_token(hub=hub)

    result = clients.validate_redeem_token(redeem, _manifest(identifier="com.example.redeem"))

    assert result.client is not None
    assert result.client.release.app.identifier == "com.example.redeem"
    redeem.refresh_from_db()
    assert redeem.client_id == result.client.id


@pytest.mark.django_db
def test_validate_device_code_creates_client():
    hub = factories.make_hub()
    user = hub.creator
    organization = hub.organization
    device_code = factories.make_device_code(
        staging_manifest={"identifier": "com.example.dc", "version": "1.0.0", "scopes": [], "requirements": []},
    )

    result = device_codes.validate_device_code(device_code, user, organization, hub)

    assert result.client is not None
    assert result.client.release.app.identifier == "com.example.dc"


@pytest.mark.django_db
def test_validate_device_code_uses_provided_name_for_node_device():
    hub = factories.make_hub()
    user = hub.creator
    organization = hub.organization
    device_code = factories.make_device_code(
        staging_manifest={
            "identifier": "com.example.node",
            "version": "1.0.0",
            "scopes": [],
            "requirements": [],
            "node_id": "node-123",
        },
    )

    result = device_codes.validate_device_code(device_code, user, organization, hub, device_name="Workbench")

    assert result.client is not None
    assert result.client.node is not None
    assert result.client.node.name == "Workbench"


@pytest.mark.django_db
def test_auto_compose_noop_without_requirements():
    client = factories.make_client()
    out = rendering.auto_compose(client, _manifest(), client.user, client.organization)
    assert out == client


@pytest.mark.django_db
def test_auto_compose_is_atomic_and_rolls_back_on_required_missing():
    """A failing required requirement must not delete the client's existing mappings."""
    client = factories.make_client()
    instance = factories.make_service_instance()
    models.ServiceInstanceMapping.objects.create(client=client, instance=instance, key="old")

    manifest = _manifest(
        requirements=[base_models.Requirement(key="db", service="com.missing.service", optional=False)],
    )

    with pytest.raises(Exception):
        rendering.auto_compose(client, manifest, client.user, client.organization)

    # the @transaction.atomic decorator should have rolled back the mapping deletion
    assert client.mappings.filter(key="old").exists()

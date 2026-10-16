"""Migration 0004 on a database that holds redeem tokens and old mandates.

Redeem tokens go, with every client a redeem produced and every client an old mandate
provisioned (and their tokens); existing mandates survive as agent mandates, so
kabinet's release approvals keep pointing at them.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("fakts", "0003_redeemtoken_hub_client"), ("authapp", "0002_initial")]
AFTER = [("fakts", "0005_device_code_polling_client_id")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def back_to_latest():
    yield
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_redeemed_and_provisioned_clients_go_and_mandates_stay(back_to_latest):
    # karakter is not rolled back: its rows are made with today's models.
    from karakter import models as karakter_models

    user_id = karakter_models.User.objects.create(username="migrator", email="m@example.com").pk
    org_id = karakter_models.Organization.objects.create(owner_id=user_id, slug="migrated", name="Migrated").pk
    membership_id = karakter_models.Membership.objects.get_or_create(user_id=user_id, organization_id=org_id)[0].pk

    apps = _migrate(BEFORE)
    User = apps.get_model("karakter", "User")
    Organization = apps.get_model("karakter", "Organization")
    Membership = apps.get_model("karakter", "Membership")
    Hub = apps.get_model("fakts", "Hub")
    Client = apps.get_model("fakts", "Client")
    Mandate = apps.get_model("fakts", "Mandate")
    RedeemToken = apps.get_model("fakts", "RedeemToken")
    OAuth2Token = apps.get_model("authapp", "OAuth2Token")

    user = User.objects.get(pk=user_id)
    org = Organization.objects.get(pk=org_id)
    membership = Membership.objects.get(pk=membership_id)
    hub = Hub.objects.create(name="h", organization=org, identifier="h", creator=user, token="hub-token")
    mandate = Mandate.objects.create(
        membership=membership, organization=org, hub=hub, agent_identifier="live.arkitekt.deployer", subject_manifest={"identifier": "a", "version": "1"}
    )
    kept = Client.objects.create(client_id="kept", membership=membership, organization=org, hub=hub)
    redeemed = Client.objects.create(client_id="redeemed", membership=membership, organization=org, hub=hub)
    provisioned = Client.objects.create(client_id="provisioned", membership=membership, organization=org, mandate=mandate)
    RedeemToken.objects.create(token="t", user=user, hub=hub, client=redeemed)
    for client_id in ("kept", "redeemed", "provisioned"):
        OAuth2Token.objects.create(user=membership, client_id=client_id, token_type="Bearer", access_token=f"at-{client_id}")

    apps = _migrate(AFTER)
    Client = apps.get_model("fakts", "Client")
    Mandate = apps.get_model("fakts", "Mandate")
    OAuth2Token = apps.get_model("authapp", "OAuth2Token")

    assert set(Client.objects.values_list("client_id", flat=True)) == {"kept"}
    assert set(OAuth2Token.objects.values_list("client_id", flat=True)) == {"kept"}
    migrated = Mandate.objects.get(pk=mandate.pk)
    assert (migrated.claimant, migrated.origin, migrated.agent_identifier) == ("agent", "member", "live.arkitekt.deployer")

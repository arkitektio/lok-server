"""The hub owner's alias policy: which aliases a client is handed, and in what order."""

import pytest

from fakts import base_models, enums, models
from fakts.services import alias_priority, aliases
from karakter import models as karakter_models
from tests import factories


def _linking_context() -> base_models.LinkingContext:
    return base_models.LinkingContext(
        request=base_models.LinkingRequest(host="linked.example.com", port="8080", is_secure=True),
        manifest=base_models.Manifest(identifier="com.example.app", version="1.0.0"),
        client=base_models.LinkingClient(client_id="cid", name="client"),
    )


def _rendered(instance: models.ServiceInstance, client: models.Client | None = None) -> list[tuple[str, int]]:
    rules = alias_priority.rules_for(client) if client is not None else None
    claim = instance.render(_linking_context(), rules=rules)
    return [(alias.host, alias.priority) for alias in claim.aliases]


@pytest.mark.django_db
def test_aliases_come_in_the_owners_order_and_keep_it_across_a_reauthorization():
    instance = factories.make_service_instance()
    stage = {
        "public": base_models.StagingAlias(id="public", host="lab.example.org", port=443, ssl=True, reach="public"),
        "lan": base_models.StagingAlias(id="lan", host="10.0.0.4", port=443, ssl=True, reach="lan"),
        "loopback": base_models.StagingAlias(id="loopback", host="localhost", port=80, ssl=False, reach="loopback"),
        "old": base_models.StagingAlias(id="old", host="old.example.org", port=443, ssl=True, reach="public"),
    }
    rows = {key: aliases.upsert_instance_alias(instance, staged)[0] for key, staged in stage.items()}

    # By reach, without the owner saying anything: closest first.
    assert _rendered(instance) == [("localhost", 20), ("10.0.0.4", 30), ("lab.example.org", 50), ("old.example.org", 50)]

    # The owner puts the public address first and switches one off.
    models.InstanceAlias.objects.filter(id=rows["public"].id).update(priority=1)
    models.InstanceAlias.objects.filter(id=rows["old"].id).update(enabled=False)
    assert _rendered(instance) == [("lab.example.org", 1), ("localhost", 20), ("10.0.0.4", 30)]

    # The hub authorizes again with the same aliases: the owner's word stays.
    for staged in stage.values():
        aliases.upsert_instance_alias(instance, staged)
    assert _rendered(instance) == [("lab.example.org", 1), ("localhost", 20), ("10.0.0.4", 30)]


@pytest.mark.django_db
def test_the_most_specific_rule_for_a_client_wins():
    organization = factories.make_organization()
    membership = factories.make_membership(organization=organization)
    role = karakter_models.Role.objects.create(organization=organization, identifier="imaging")
    membership.roles.add(role)
    client = factories.make_client(membership=membership)
    other = factories.make_client()
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(instance=instance, host="10.0.0.4", port=443, ssl=True, scope="network")

    def rule(**subject: object) -> models.AliasRule:
        return models.AliasRule.objects.create(alias=alias, **subject)

    disable, ranking = enums.AliasRuleEffectChoices.DISABLE.value, enums.AliasRuleEffectChoices.PRIORITY.value
    rule(role=role, effect=disable)
    assert _rendered(instance, client) == []  # a role it holds disables it...
    assert _rendered(instance, other) == [("10.0.0.4", 30)]  # ...for its holders only

    rule(user=membership.user, effect=ranking, priority=5)
    assert _rendered(instance, client) == [("10.0.0.4", 5)]  # the user beats the role
    rule(app=client.release.app, effect=ranking, priority=7)
    assert _rendered(instance, client) == [("10.0.0.4", 7)]  # the app beats the user
    rule(client=client, effect=disable)
    assert _rendered(instance, client) == []  # the client itself beats them all

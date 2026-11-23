"""karakter 0005 turns the collaborations grants and invites named into lok's own.

Before it, a grant named a collaboration by its service instance and its id there.
The migration makes one ``Collaboration`` per distinct (organization, instance, id),
points every grant and invite entry at it, and leaves every guest a guest of what
they were granted. Run for real: roll karakter back to 0004 on the test database,
write old-shape rows, migrate forward and read them with today's models.
"""

from collections.abc import Iterator

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Model

from fakts.models import ServiceInstance
from karakter import models
from tests import factories

BEFORE = [("karakter", "0004_membership_created_at")]
LONG_ID = "x" * 200


def _executor() -> MigrationExecutor:
    """A fresh executor: its graph and applied set read from the database now."""
    return MigrationExecutor(connection)


@pytest.fixture
def at_0004(transactional_db: None) -> Iterator[None]:
    """The database at karakter 0004 for the test body; back at the leaves afterwards.

    Transactional: 0005 is not atomic (its ALTERs refuse to run after the backfill in
    one transaction), and nothing may be left half-migrated for the tests after it.
    """
    _executor().migrate(BEFORE)
    try:
        yield
    finally:
        executor = _executor()
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_each_old_collaboration_becomes_one_of_its_organization(
    at_0004: None,
) -> None:
    # Users, organizations, hubs and instances look the same before and after: today's
    # factories make them. The two tables 0005 changes are written with 0004's models.
    lab = factories.make_organization()
    i1 = factories.make_service_instance(hub=factories.make_hub(organization=lab))
    i2 = factories.make_service_instance(hub=i1.hub)
    other = factories.make_organization()
    j1 = factories.make_service_instance(hub=factories.make_hub(organization=other))
    visitors = [factories.make_user() for _ in range(3)]

    old = _executor().loader.project_state(BEFORE).apps
    Membership = old.get_model("karakter", "Membership")
    Grant = old.get_model("karakter", "CollaborationGrant")
    Invite = old.get_model("karakter", "Invite")

    invite = Invite.objects.create(
        created_by_id=lab.owner_id,
        created_for_id=lab.pk,
        shared_collaborations=[
            {"instance": str(i1.pk), "collaboration": "12", "roles": ["editor"]},
            {"instance": i2.pk, "collaboration": "7", "roles": []},
        ],
    )
    # Guests: memberships without roles, each with grants (0004's models fire no signals).
    g1 = Membership.objects.create(user_id=visitors[0].pk, organization_id=lab.pk)
    g2 = Membership.objects.create(user_id=visitors[1].pk, organization_id=lab.pk)
    g3 = Membership.objects.create(user_id=visitors[2].pk, organization_id=other.pk)

    def grant(
        membership: Model, instance: ServiceInstance, local: str, roles: list[str]
    ) -> int:
        return Grant.objects.create(
            membership=membership,
            instance_id=instance.pk,
            collaboration_id=local,
            roles=roles,
            granted_by_id=lab.owner_id,
            granted_through_id=invite.pk,
            mesh=True,
        ).pk

    g1_i1_12 = grant(g1, i1, "12", ["editor"])
    g1_i2_12 = grant(g1, i2, "12", [])  # the same id in another instance: another one
    g2_i1_12 = grant(g2, i1, "12", ["viewer"])  # the same one as g1's first
    g2_long = grant(g2, i1, LONG_ID, [])
    g3_j1_12 = grant(g3, j1, "12", [])

    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())

    grants = {
        g.pk: g
        for g in models.CollaborationGrant.objects.select_related("collaboration")
    }
    assert grants[g1_i1_12].collaboration == grants[g2_i1_12].collaboration
    assert grants[g1_i1_12].collaboration != grants[g1_i2_12].collaboration
    assert grants[g3_j1_12].collaboration not in {
        grants[pk].collaboration for pk in (g1_i1_12, g1_i2_12, g2_long)
    }
    for pk in (g1_i1_12, g1_i2_12, g2_i1_12, g2_long):
        assert grants[pk].collaboration.organization_id == lab.pk
    assert grants[g3_j1_12].collaboration.organization_id == other.pk
    assert len(grants[g2_long].collaboration.name) <= 200
    # Roles, granter, invite and mesh carry over.
    assert (grants[g1_i1_12].roles, grants[g2_i1_12].roles) == (["editor"], ["viewer"])
    assert {
        (g.granted_by_id, g.granted_through_id, g.mesh) for g in grants.values()
    } == {(lab.owner_id, invite.pk, True)}

    # The invite names the same collaborations by key; the one no grant named is new.
    entries = models.Invite.objects.get(pk=invite.pk).shared_collaborations
    assert entries[0] == {
        "collaboration": str(grants[g1_i1_12].collaboration.key),
        "roles": ["editor"],
    }
    seven = models.Collaboration.objects.get(key=entries[1]["collaboration"])
    assert seven.organization_id == lab.pk and entries[1]["roles"] == []
    assert models.Collaboration.objects.filter(organization=lab).count() == 4
    assert models.Collaboration.objects.filter(organization=other).count() == 1

    # Every guest stays a guest: scoped, without roles, of what they had.
    for membership_id, count in ((g1.pk, 2), (g2.pk, 2), (g3.pk, 1)):
        membership = models.Membership.objects.get(pk=membership_id)
        assert membership.is_scoped
        assert not membership.roles.exists()
        assert membership.collaboration_grants.count() == count

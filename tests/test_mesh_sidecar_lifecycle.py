"""App and hub mesh sidecars: who may reach them, and how long they live.

A sidecar (the node an app or hub joins with its minted key) is reachable only
from the apps whose clients are bound to that hub, one way, and lives only while
a live client backs it. See ``ionscale.acl`` and ``fakts.services.mesh``.
"""

import time
from types import SimpleNamespace

import pytest
from graphql import GraphQLError

from api.management.mutations.ionscale import CreateIonscaleAuthKeyInput, create_ionscale_auth_key
from api.management.mutations.revoke import RevokeClientSessionsInput, revoke_client_sessions
from authapp.models import OAuth2Token
from fakts import models
from ionscale.acl import build_acl_policy
from ionscale.base_models import Machine
from ionscale.reconcile import reconcile_sidecars
from tests import factories
from tests.test_app_mesh_key import _grant as grant_app
from tests.test_hub_mesh import _grant as grant_hub


@pytest.fixture
def org(settings):
    settings.IONSCALE_MAGIC_DNS_SUFFIX = "mesh.example.test"
    organization = factories.make_organization()
    layer = models.IonscaleLayer.objects.create(
        organization=organization, name="Default", kind="ionscale",
        identifier=organization.slug, tailnet_name=organization.slug,
    )
    admin = factories.make_membership(user=organization.owner, organization=organization)
    member = factories.make_membership(organization=organization)
    hub = factories.make_hub(organization=organization)
    return SimpleNamespace(
        organization=organization, layer=layer, admin=admin, member=member, hub=hub,
        # the shape tests.test_hub_mesh._grant expects
        hub_granter=SimpleNamespace(organization=organization, membership=admin),
    )


def _info(membership):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=membership.user)))


def _app_client(org):
    return models.Client.objects.get(membership=org.member, release__app__identifier="com.example.meshapp", hub=org.hub)


def _seed(ionscale_repo, org, *machines):
    ionscale_repo.machines_by_tailnet[org.layer.tailnet_name] = list(machines)


def _age_out(client):
    """Make a client's refresh chain (and its creation) older than every lifetime."""
    past = int(time.time()) - OAuth2Token.REFRESH_CHAIN_MAX_LIFETIME - 10
    OAuth2Token.objects.filter(client_id=client.client_id).update(issued_at=past, chain_started_at=past)
    models.Client.objects.filter(pk=client.pk).update(created_at="2000-01-01T00:00:00Z")


# --------------------------------------------------------------------------- #
# ACL
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_acl_lets_an_app_sidecar_reach_only_its_hub_one_way(client, ionscale_repo, org):
    grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()

    policy = build_acl_policy(org.organization)

    members, *sidecar_rules = policy["acls"]
    mesh = f"tag:mesh-{org.organization.pk}"
    assert members["src"] == ["autogroup:member", mesh]
    assert members["dst"] == ["autogroup:member:*", f"{mesh}:*"]
    assert sidecar_rules == [{"action": "accept", "src": [enrollment.tag], "dst": [f"tag:hub-{org.hub.pk}:*"]}]
    # Nothing lets a hub (or anyone) initiate towards an app sidecar.
    assert not any(d.startswith("tag:app-") for rule in policy["acls"] for d in rule["dst"])


@pytest.mark.django_db
def test_acl_is_applied_on_accept_and_follows_a_new_hub(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    other_hub = factories.make_hub(organization=org.organization)
    with django_capture_on_commit_callbacks(execute=True):
        grant_app(client, org.member, org.hub)
    tailnet, policy = ionscale_repo.set_acl_policies[-1]
    assert tailnet == org.layer.tailnet_name
    assert [r["dst"] for r in policy["acls"][1:]] == [[f"tag:hub-{org.hub.pk}:*"]]

    with django_capture_on_commit_callbacks(execute=True):
        grant_app(client, org.member, other_hub)
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert sorted(r["dst"][0] for r in policy["acls"][1:]) == sorted([f"tag:hub-{org.hub.pk}:*", f"tag:hub-{other_hub.pk}:*"])


@pytest.mark.django_db
def test_reserved_sidecar_tags_cannot_be_minted_by_hand(ionscale_repo, org):
    with pytest.raises(GraphQLError, match="reserved"):
        create_ionscale_auth_key(_info(org.admin), CreateIonscaleAuthKeyInput(layer_id=str(org.layer.pk), tags=["tag:app-1"]))
    with pytest.raises(GraphQLError, match="reserved"):
        create_ionscale_auth_key(_info(org.admin), CreateIonscaleAuthKeyInput(layer_id=str(org.layer.pk), tags=["tag:hub-7"]))
    assert ionscale_repo.created_auth_keys == []


# --------------------------------------------------------------------------- #
# app sidecar lifetime
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_deleting_the_backing_client_reaps_the_sidecar(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    token = grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    with django_capture_on_commit_callbacks(execute=True):
        _app_client(org).delete()

    assert not models.AppMeshEnrollment.objects.exists()
    assert ionscale_repo.deleted_machines == ["100"]
    assert (org.layer.tailnet_name, token["mesh"]["ionscale_auth_key"]) in ionscale_repo.deleted_auth_keys


@pytest.mark.django_db
def test_reauthorization_keeps_the_sidecar(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    # Re-approval rotates the client (the old one is deleted in the same transaction).
    with django_capture_on_commit_callbacks(execute=True):
        grant_app(client, org.member, org.hub)

    assert models.AppMeshEnrollment.objects.get().pk == enrollment.pk
    assert ionscale_repo.deleted_machines == []


@pytest.mark.django_db
def test_revoking_the_clients_sessions_reaps_the_sidecar(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    with django_capture_on_commit_callbacks(execute=True):
        revoke_client_sessions(_info(org.admin), RevokeClientSessionsInput(client=str(_app_client(org).pk)))

    assert not models.AppMeshEnrollment.objects.exists()
    assert ionscale_repo.deleted_machines == ["100"]


@pytest.mark.django_db
def test_an_expired_refresh_chain_is_reaped_by_the_sweep(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    report = reconcile_sidecars(org.layer)
    assert report.reaped_enrollments == []  # still live

    _age_out(_app_client(org))
    with django_capture_on_commit_callbacks(execute=True):
        report = reconcile_sidecars(org.layer)

    assert report.reaped_enrollments == [enrollment.tag]
    assert not models.AppMeshEnrollment.objects.exists()
    assert "100" in ionscale_repo.deleted_machines
    # The hub entry is gone from the re-applied ACL.
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert len(policy["acls"]) == 1


# --------------------------------------------------------------------------- #
# hub sidecar lifetime
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_deleting_a_hub_deletes_its_identity_client(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    hub, _ = grant_hub(client, org.hub_granter)
    identity = hub.client_id

    with django_capture_on_commit_callbacks(execute=True):
        hub.delete()

    assert not models.Client.objects.filter(pk=identity).exists()


@pytest.mark.django_db
def test_revoking_the_hub_identity_reaps_its_sidecar_but_keeps_the_hub(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    hub, token = grant_hub(client, org.hub_granter)
    _seed(ionscale_repo, org, Machine(id="200", name="hub", tags=[hub.mesh_tag], connected=True))

    with django_capture_on_commit_callbacks(execute=True):
        revoke_client_sessions(_info(org.admin), RevokeClientSessionsInput(client=str(hub.client_id)))

    hub.refresh_from_db()
    assert hub.auth_key is None
    assert ionscale_repo.deleted_machines == ["200"]
    assert (org.layer.tailnet_name, token["mesh"]["ionscale_auth_key"]) in ionscale_repo.deleted_auth_keys


# --------------------------------------------------------------------------- #
# sweep
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_sweep_deletes_orphans_and_migrates_double_tagged_sidecars(client, ionscale_repo, org):
    grant_app(client, org.member, org.hub)
    tag = models.AppMeshEnrollment.objects.get().tag
    mesh = f"tag:mesh-{org.organization.pk}"
    _seed(
        ionscale_repo,
        org,
        Machine(id="1", name="live", tags=[tag], connected=True),
        Machine(id="2", name="orphan-app", tags=["tag:app-999999"], connected=True),
        Machine(id="3", name="orphan-hub", tags=["tag:hub-999999"], connected=True),
        Machine(id="4", name="old-sidecar", tags=[mesh, tag], connected=True),
        Machine(id="5", name="laptop", tags=[mesh], connected=True),
    )

    report = reconcile_sidecars(org.layer, dry_run=True, migrate_tags=True)
    assert sorted(report.deleted_machines) == ["2", "3", "4"]
    assert ionscale_repo.deleted_machines == []

    reconcile_sidecars(org.layer, migrate_tags=True)
    assert sorted(ionscale_repo.deleted_machines) == ["2", "3", "4"]

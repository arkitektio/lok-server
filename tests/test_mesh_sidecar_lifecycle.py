"""App and hub mesh sidecars: who may reach them, and how long they live.

A sidecar (the node an app or hub joins with its minted key) is reachable only
from the apps whose clients are bound to that hub, one way, and lives only while
a live client backs it. See ``ionscale.acl`` and ``fakts.services.mesh``.
"""

import time
from types import SimpleNamespace

import pytest
from django.urls import reverse
from graphql import GraphQLError

from api.management.mutations.ionscale import CreateIonscaleAuthKeyInput, create_ionscale_auth_key
from api.management.mutations.revoke import RevokeClientSessionsInput, revoke_client_sessions
from authapp.models import OAuth2Token
from fakts import models
from ionscale.acl import build_acl_policy
from ionscale.base_models import Machine
from ionscale.reconcile import reconcile_sidecars
from tests import factories
from tests.test_app_mesh_key import _accept as accept_app
from tests.test_app_mesh_key import _grant as grant_app
from tests.test_app_mesh_key import _poll as poll_app
from tests.test_app_mesh_key import _start as start_app
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


# --------------------------------------------------------------------------- #
# rejecting an app
# --------------------------------------------------------------------------- #


def _reject(membership, client_pk):
    return revoke_client_sessions(_info(membership), RevokeClientSessionsInput(client=str(client_pk)))


@pytest.mark.django_db
def test_the_member_who_approved_an_app_may_reject_it(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    token = grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    with django_capture_on_commit_callbacks(execute=True):
        _reject(org.member, _app_client(org).pk)

    assert not models.AppMeshEnrollment.objects.exists()
    assert ionscale_repo.deleted_machines == ["100"]
    assert (org.layer.tailnet_name, token["mesh"]["ionscale_auth_key"]) in ionscale_repo.deleted_auth_keys
    assert not OAuth2Token.objects.filter(client_id=token["client_id"], revoked=False).exists()


@pytest.mark.django_db
def test_another_plain_member_may_not_reject_it(client, ionscale_repo, org):
    grant_app(client, org.member, org.hub)
    bystander = factories.make_membership(organization=org.organization)

    with pytest.raises(GraphQLError):
        _reject(bystander, _app_client(org).pk)

    assert models.AppMeshEnrollment.objects.exists()
    assert OAuth2Token.objects.filter(revoked=False).exists()


@pytest.mark.django_db
def test_rejecting_one_client_removes_the_whole_installation(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    # The same app on the same device, bound to two hubs: two clients, one sidecar.
    other_hub = factories.make_hub(organization=org.organization)
    with django_capture_on_commit_callbacks(execute=True):
        first = grant_app(client, org.member, org.hub)
        second = grant_app(client, org.member, other_hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    with django_capture_on_commit_callbacks(execute=True):
        _reject(org.member, _app_client(org).pk)

    assert not models.AppMeshEnrollment.objects.exists()
    assert ionscale_repo.deleted_machines == ["100"]
    for token in (first, second):
        assert not OAuth2Token.objects.filter(client_id=token["client_id"], revoked=False).exists()
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert len(policy["acls"]) == 1


@pytest.mark.django_db
def test_rejecting_burns_an_approved_code_that_was_not_polled_yet(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    body = start_app(client)
    accept_app(body, org.member, org.hub)
    assert models.AppMeshEnrollment.objects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        _reject(org.member, _app_client(org).pk)

    # The never-polled grace does not keep the sidecar, and the code is dead.
    assert not models.AppMeshEnrollment.objects.exists()
    assert poll_app(client, body).status_code != 200


@pytest.mark.django_db
def test_a_client_revoking_itself_keeps_a_shared_sidecar_but_loses_its_hub(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    other_hub = factories.make_hub(organization=org.organization)
    with django_capture_on_commit_callbacks(execute=True):
        first = grant_app(client, org.member, org.hub)
        grant_app(client, org.member, other_hub)
    enrollment = models.AppMeshEnrollment.objects.get()

    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(
            reverse("revoke"),
            data={"token": first["refresh_token"], "token_type_hint": "refresh_token", "client_id": first["client_id"]},
            secure=True,
        )
    assert resp.status_code == 200, resp.content

    assert models.AppMeshEnrollment.objects.get().pk == enrollment.pk
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert [r["dst"] for r in policy["acls"][1:]] == [[f"tag:hub-{other_hub.pk}:*"]]


@pytest.mark.django_db
def test_a_client_revoking_its_last_session_reaps_the_sidecar(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    token = grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("revoke"),
            data={"token": token["refresh_token"], "token_type_hint": "refresh_token", "client_id": token["client_id"]},
            secure=True,
        )

    assert not models.AppMeshEnrollment.objects.exists()
    assert ionscale_repo.deleted_machines == ["100"]


# --------------------------------------------------------------------------- #
# grace after a session expired
# --------------------------------------------------------------------------- #


def _expire(client, *, days_ago):
    """Let a client's refresh token run out ``days_ago`` days ago (sliding lifetime)."""
    past = int(time.time()) - OAuth2Token.REFRESH_TOKEN_LIFETIME - days_ago * 86400
    OAuth2Token.objects.filter(client_id=client.client_id).update(issued_at=past, chain_started_at=past)
    models.Client.objects.filter(pk=client.pk).update(created_at="2000-01-01T00:00:00Z")


@pytest.mark.django_db
def test_an_expired_session_keeps_the_sidecar_but_loses_the_hub(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    _expire(_app_client(org), days_ago=3)
    with django_capture_on_commit_callbacks(execute=True):
        report = reconcile_sidecars(org.layer)

    assert report.reaped_enrollments == []
    assert ionscale_repo.deleted_machines == []
    assert models.AppMeshEnrollment.objects.get().pk == enrollment.pk
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert len(policy["acls"]) == 1

    # Re-approval within the grace resumes the same sidecar, with its rule.
    with django_capture_on_commit_callbacks(execute=True):
        grant_app(client, org.member, org.hub)
    assert models.AppMeshEnrollment.objects.get().pk == enrollment.pk
    assert ionscale_repo.deleted_machines == []
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert policy["acls"][1]["src"] == [enrollment.tag]


@pytest.mark.django_db
def test_the_sidecar_is_reaped_once_the_grace_is_over(client, ionscale_repo, org, settings, django_capture_on_commit_callbacks):
    grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    _expire(_app_client(org), days_ago=settings.MESH_SIDECAR_GRACE_SECONDS // 86400 + 1)
    with django_capture_on_commit_callbacks(execute=True):
        report = reconcile_sidecars(org.layer)

    assert report.reaped_enrollments == [enrollment.tag]
    assert ionscale_repo.deleted_machines == ["100"]


@pytest.mark.django_db
def test_a_revoked_session_gets_no_grace(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    token = grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    OAuth2Token.objects.filter(client_id=token["client_id"]).update(revoked=True)

    with django_capture_on_commit_callbacks(execute=True):
        report = reconcile_sidecars(org.layer)

    assert report.reaped_enrollments == [enrollment.tag]


@pytest.mark.django_db
def test_a_hub_whose_identity_expired_keeps_its_node_for_the_grace(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    hub, _ = grant_hub(client, org.hub_granter)
    _seed(ionscale_repo, org, Machine(id="200", name="hub", tags=[hub.mesh_tag], connected=True))

    _expire(hub.client, days_ago=3)
    with django_capture_on_commit_callbacks(execute=True):
        report = reconcile_sidecars(org.layer)

    assert report.reaped_hubs == []
    assert ionscale_repo.deleted_machines == []
    hub.refresh_from_db()
    assert hub.auth_key is not None


@pytest.mark.django_db
def test_a_rejected_client_reads_as_revoked(client, ionscale_repo, org, django_capture_on_commit_callbacks):
    # What the client page shows after a reject (ManagementClient.accessState).
    from fakts.services.clients import client_access_state

    grant_app(client, org.member, org.hub)
    app_client = _app_client(org)
    assert client_access_state(app_client) == "active"

    with django_capture_on_commit_callbacks(execute=True):
        _reject(org.member, app_client.pk)

    assert client_access_state(app_client) == "revoked"


# --------------------------------------------------------------------------- #
# removing keys and machines by hand
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_an_admin_revokes_a_hand_made_key_and_removes_a_machine(ionscale_repo, org):
    from api.management.mutations.ionscale import (
        DeleteIonscaleAuthKeyInput,
        DeleteMachineInput,
        delete_ionscale_auth_key,
        delete_machine,
    )

    key = create_ionscale_auth_key(_info(org.admin), CreateIonscaleAuthKeyInput(layer_id=str(org.layer.pk), tags=["tag:lab"]))
    _seed(ionscale_repo, org, Machine(id="7", name="laptop", tags=["tag:lab"], connected=True))

    # A plain member may do neither.
    with pytest.raises(GraphQLError):
        delete_ionscale_auth_key(_info(org.member), DeleteIonscaleAuthKeyInput(id=str(key.pk)))
    with pytest.raises(GraphQLError):
        delete_machine(_info(org.member), DeleteMachineInput(layer_id=str(org.layer.pk), machine_id="7"))

    delete_ionscale_auth_key(_info(org.admin), DeleteIonscaleAuthKeyInput(id=str(key.pk)))
    assert (org.layer.tailnet_name, key.key) in ionscale_repo.deleted_auth_keys
    assert not models.IonscaleAuthKey.objects.filter(pk=key.pk).exists()
    # Revoking the key leaves the machine that joined with it.
    assert ionscale_repo.deleted_machines == []

    delete_machine(_info(org.admin), DeleteMachineInput(layer_id=str(org.layer.pk), machine_id="7"))
    assert ionscale_repo.deleted_machines == ["7"]


@pytest.mark.django_db
def test_sidecar_keys_and_nodes_are_not_removed_by_hand(client, ionscale_repo, org):
    from api.management.mutations.ionscale import (
        DeleteIonscaleAuthKeyInput,
        DeleteMachineInput,
        delete_ionscale_auth_key,
        delete_machine,
    )

    grant_app(client, org.member, org.hub)
    enrollment = models.AppMeshEnrollment.objects.get()
    _seed(ionscale_repo, org, Machine(id="100", name="app", tags=[enrollment.tag], connected=True))

    with pytest.raises(GraphQLError, match="Reject the client"):
        delete_ionscale_auth_key(_info(org.admin), DeleteIonscaleAuthKeyInput(id=str(enrollment.auth_key_id)))
    with pytest.raises(GraphQLError, match="Reject the client"):
        delete_machine(_info(org.admin), DeleteMachineInput(layer_id=str(org.layer.pk), machine_id="100"))
    # A machine of another mesh is "not found", whatever its id.
    with pytest.raises(GraphQLError):
        delete_machine(_info(org.admin), DeleteMachineInput(layer_id=str(org.layer.pk), machine_id="999"))

    assert ionscale_repo.deleted_machines == []
    assert models.AppMeshEnrollment.objects.exists()

"""Replacing an organization's mesh with a fresh, empty tailnet of the same name.

The way out of a mesh nobody can administer any more (a tailnet lock whose key
and disablement secrets are lost). The tailnet is wiped and recreated; the grants
lok keeps (app enrollments, hub keys) survive, so sidecars rejoin by renewing
their key. See ``ionscale.manager.replace_org_mesh``.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone
from graphql import GraphQLError

from api.management.mutations.ionscale import ReplaceIonscaleLayerInput, replace_ionscale_layer
from fakts import models
from fakts.services.hubs import MESH_KEY_RENEWAL_INTERVAL_SECONDS
from ionscale.base_models import Machine
from ionscale.errors import IonscaleError
from ionscale.manager import ensure_org_mesh
from tests import factories
from tests.test_app_mesh_key import _grant as grant_app
from tests.test_hub_mesh import _grant as grant_hub


@pytest.fixture
def org(ionscale_repo):
    ionscale_repo.unique_auth_keys = True
    organization = factories.make_organization()
    layer = ensure_org_mesh(organization)
    admin = factories.make_membership(user=organization.owner, organization=organization)
    member = factories.make_membership(organization=organization)
    hub = factories.make_hub(organization=organization)
    return SimpleNamespace(
        organization=organization, layer=layer, member=member, hub=hub,
        hub_granter=SimpleNamespace(organization=organization, membership=admin),
    )


def _info_for(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _replace(org, user=None, confirm=None):
    return replace_ionscale_layer(
        _info_for(user or org.organization.owner),
        ReplaceIonscaleLayerInput(id=org.layer.pk, confirm=org.layer.tailnet_name if confirm is None else confirm),
    )


def _renew(client, token):
    return client.post(reverse("fakts:meshkey"), HTTP_AUTHORIZATION=f"Bearer {token['access_token']}", secure=True)


def _let_the_keys_age():
    old = timezone.now() - timedelta(seconds=MESH_KEY_RENEWAL_INTERVAL_SECONDS + 1)
    models.IonscaleAuthKey.objects.update(created_at=old)


@pytest.mark.django_db
def test_replace_recreates_the_tailnet_under_the_same_name_and_organization(ionscale_repo, org):
    name = org.layer.tailnet_name
    ionscale_repo.machines_by_tailnet[name] = [Machine(id="1", name="laptop", tags=[], connected=True)]
    acls, dns = len(ionscale_repo.set_acl_policies), len(ionscale_repo.dns_configs)

    layer = _replace(org)

    assert layer.pk == org.layer.pk
    assert ionscale_repo.deleted_tailnets == [(name, True)]
    assert [(t.name, t.organization) for t in ionscale_repo.created_tailnets] == [(name, str(org.organization.pk))] * 2
    assert ionscale_repo.machines_by_tailnet.get(name, []) == []
    # IAM, DNS and ACL are pushed to the new tailnet; without the ACL it would be allow-all.
    assert name in ionscale_repo.policies
    assert len(ionscale_repo.dns_configs) == dns + 1
    assert len(ionscale_repo.set_acl_policies) == acls + 1


@pytest.mark.django_db
def test_sidecars_keep_their_grant_and_renew_into_the_new_tailnet(client, ionscale_repo, org):
    app_token = grant_app(client, org.member, org.hub)
    hub, hub_token = grant_hub(client, org.hub_granter)
    enrollment = models.AppMeshEnrollment.objects.get()
    models.Hub.objects.filter(pk=hub.pk).update(mesh_connected=True, mesh_host="hub.old.mesh")

    _replace(org)

    enrollment.refresh_from_db()
    hub.refresh_from_db()
    assert enrollment.auth_key_id is not None and hub.auth_key_id is not None
    assert hub.mesh_connected is None and hub.mesh_host == ""
    # The ACL still carries the app's sidecar rule.
    assert enrollment.tag in str(ionscale_repo.set_acl_policies[-1])

    _let_the_keys_age()
    for token, tag in ((app_token, enrollment.tag), (hub_token, hub.mesh_tag)):
        resp = _renew(client, token)
        assert resp.status_code == 200, resp.content
        assert ionscale_repo.created_auth_keys[-1]["tags"] == [tag]
        assert ionscale_repo.created_auth_keys[-1]["tailnet"] == org.layer.tailnet_name


@pytest.mark.django_db
def test_replace_drops_keys_nothing_points_at(ionscale_repo, org):
    loose = models.IonscaleAuthKey.objects.create(layer=org.layer, key="tskey-loose", creator=org.organization.owner)

    _replace(org)

    assert not models.IonscaleAuthKey.objects.filter(pk=loose.pk).exists()


@pytest.mark.django_db
def test_a_tailnet_already_gone_is_recreated(ionscale_repo, org):
    ionscale_repo.fail_with["delete_tailnet"] = IonscaleError("not_found", "no such tailnet")
    ionscale_repo.tailnets = []

    _replace(org)

    assert len(ionscale_repo.created_tailnets) == 2


@pytest.mark.django_db
def test_a_failed_delete_changes_nothing(ionscale_repo, org):
    ionscale_repo.fail_with["delete_tailnet"] = IonscaleError("unavailable", "down")

    with pytest.raises(GraphQLError):
        _replace(org)

    assert len(ionscale_repo.created_tailnets) == 1


@pytest.mark.django_db
def test_a_failed_acl_push_is_an_error(ionscale_repo, org):
    ionscale_repo.fail_with["set_acl_policy"] = IonscaleError("unavailable", "down")

    with pytest.raises(GraphQLError):
        _replace(org)


@pytest.mark.django_db
def test_replace_needs_the_name_typed_out(ionscale_repo, org):
    with pytest.raises(GraphQLError):
        _replace(org, confirm="nope")

    assert ionscale_repo.deleted_tailnets == []


@pytest.mark.django_db
def test_replace_rejects_non_admin(ionscale_repo, org):
    with pytest.raises(GraphQLError):
        _replace(org, user=org.member.user)

    assert ionscale_repo.deleted_tailnets == []

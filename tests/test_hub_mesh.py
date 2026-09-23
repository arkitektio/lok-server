"""Hubs on the mesh: the hub counterpart of ``test_app_mesh_key``.

A hub that sets ``request_auth_key`` in its manifest gets a mesh key exactly like
an app: minted at accept, handed out once as ``auth`` on the device-code token
response, rotated on re-authorization, revoked with the hub. Its ``kind: mesh``
aliases resolve to its node's MagicDNS name, and its health callback
(``/f/hubhealth/``) keeps that resolution (and its liveness) current.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from pydantic import ValidationError

from api.management.mutations.hub_device_code import AcceptHubDeviceCodeInput, accept_hub_device_code
from fakts import base_models, models
from fakts.services.hubs import APP_MESH_KEY_EXPIRY_SECONDS
from fakts.services.mesh import resolve_hub_mesh_host
from ionscale.base_models import Machine
from tests import factories

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SUFFIX = "mesh.example.test"


@pytest.fixture(autouse=True)
def _fresh_cache(settings):
    settings.IONSCALE_MAGIC_DNS_SUFFIX = SUFFIX
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def mesh_org():
    organization = factories.make_organization()
    layer = models.IonscaleLayer.objects.create(
        organization=organization,
        name="Default",
        kind="ionscale",
        identifier=organization.slug,
        tailnet_name=organization.slug,
    )
    membership = factories.make_membership(user=organization.owner, organization=organization)
    return SimpleNamespace(organization=organization, layer=layer, membership=membership)


def _manifest(*, request_auth_key=True, aliases=None, clients=None, identifier="meshhub"):
    return {
        "identifier": identifier,
        "request_auth_key": request_auth_key,
        "instances": [
            {
                "identifier": "rekuest",
                "manifest": {"identifier": "live.arkitekt.rekuest", "version": "1.0.0"},
                "aliases": aliases if aliases is not None else [{"id": "mesh", "kind": "mesh", "port": 80, "path": "rekuest"}],
            }
        ],
        "clients": clients or [],
    }


def _grant(client, mesh_org, *, allow_ionscale=True, **manifest):
    body = client.post(
        reverse("hub_authorization"),
        data=json.dumps({"hub": _manifest(**manifest)}),
        content_type="application/json",
    ).json()
    code = models.DeviceCode.objects.get(secret=body["device_code"])
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=mesh_org.membership.user)))
    hub = accept_hub_device_code(
        info,
        AcceptHubDeviceCodeInput(
            device_code=str(code.id),
            code=code.code,
            organization=str(mesh_org.organization.id),
            allow_ionscale=allow_ionscale,
        ),
    )
    resp = client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": body["device_code"], "client_id": body["client_id"]},
        secure=True,
    )
    assert resp.status_code == 200, resp.content
    hub.refresh_from_db()
    return hub, resp.json()


def _refresh(client, token):
    return client.post(
        reverse("token"),
        data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": token["client_id"]},
        secure=True,
    ).json()


# --------------------------------------------------------------------------- #
# key lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_hub_key_rides_the_initial_token_response_once_in_the_app_shape(client, ionscale_repo, mesh_org):
    hub, token = _grant(client, mesh_org)

    assert token["mesh"] == {
        "ionscale_auth_key": ionscale_repo.auth_key,
        "ionscale_coord_url": settings.IONSCALE_COORD_URL,
    }
    identity = {
        "sub": str(mesh_org.membership.user_id),
        "organization": str(mesh_org.organization.pk),
        "hub": str(hub.pk),  # a hub's own login is keyed by itself
    }
    assert {k: token["self"][k] for k in identity} == identity
    minted = ionscale_repo.created_auth_keys[-1]
    assert minted["tags"] == [hub.mesh_tag]
    assert minted["expiry_seconds"] == APP_MESH_KEY_EXPIRY_SECONDS
    assert hub.auth_key.hub == hub

    refreshed = _refresh(client, token)
    assert refreshed["refresh_token"]
    assert "mesh" not in refreshed
    assert refreshed["self"]["jwks_url"] == token["self"]["jwks_url"]
    assert {k: refreshed["self"][k] for k in identity} == identity


@pytest.mark.django_db
@pytest.mark.parametrize("request_auth_key, allow_ionscale", [(False, True), (True, False)])
def test_no_hub_key_unless_requested_and_allowed(client, ionscale_repo, mesh_org, request_auth_key, allow_ionscale):
    hub, token = _grant(client, mesh_org, request_auth_key=request_auth_key, allow_ionscale=allow_ionscale)
    assert "mesh" not in token
    assert hub.auth_key is None
    assert ionscale_repo.created_auth_keys == []


@pytest.mark.django_db
def test_ionscale_failure_still_provisions_the_hub(client, ionscale_repo, mesh_org):
    from ionscale.errors import IonscaleError

    ionscale_repo.fail_with["create_auth_key"] = IonscaleError("unavailable", "down")
    hub, token = _grant(client, mesh_org)
    assert token["access_token"]
    assert "mesh" not in token
    assert hub.instances.count() == 1


@pytest.mark.django_db
def test_reauthorization_rotates_the_key_and_prunes_without_duplicating(client, ionscale_repo, mesh_org):
    ionscale_repo.unique_auth_keys = True
    hub_client = {"identifier": "ui", "manifest": {"identifier": "com.example.hubui", "version": "1.0.0", "scopes": []}}
    hub, first = _grant(client, mesh_org, clients=[hub_client])
    old_identity = hub.client_id
    app_client_ids = set(hub.clients.values_list("client_id", flat=True))
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="100", name="meshhub", tags=[hub.mesh_tag], connected=False),
        Machine(id="300", name="meshhub-1", tags=[hub.mesh_tag], connected=False),
    ]

    again, second = _grant(client, mesh_org, clients=[hub_client])

    assert again.pk == hub.pk
    assert models.Hub.objects.count() == 1
    assert again.instances.count() == 1
    assert models.InstanceAlias.objects.filter(instance__hub=hub).count() == 1
    assert set(again.clients.values_list("client_id", flat=True)) == app_client_ids
    # The re-authorizing server took over the identity; the old one is gone.
    assert again.client_id != old_identity
    assert not models.Client.objects.filter(pk=old_identity).exists()
    # Key rotated: old one revoked, only the live one kept.
    assert first["mesh"]["ionscale_auth_key"] != second["mesh"]["ionscale_auth_key"]
    assert ionscale_repo.deleted_auth_keys == [(mesh_org.layer.tailnet_name, first["mesh"]["ionscale_auth_key"])]
    assert list(models.IonscaleAuthKey.objects.values_list("key", flat=True)) == [second["mesh"]["ionscale_auth_key"]]
    # Stale nodes pruned, the newest kept.
    assert ionscale_repo.deleted_machines == ["100"]


@pytest.mark.django_db
def test_deleting_the_hub_removes_its_key_and_nodes(client, ionscale_repo, mesh_org, django_capture_on_commit_callbacks):
    hub, token = _grant(client, mesh_org)
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="100", name="meshhub", tags=[hub.mesh_tag], connected=True),
        Machine(id="200", name="someone-else", tags=["tag:hub-999999"], connected=True),
    ]

    with django_capture_on_commit_callbacks(execute=True):
        hub.delete()

    assert ionscale_repo.deleted_machines == ["100"]
    assert (mesh_org.layer.tailnet_name, token["mesh"]["ionscale_auth_key"]) in ionscale_repo.deleted_auth_keys


# --------------------------------------------------------------------------- #
# kind: mesh aliases
# --------------------------------------------------------------------------- #


def test_only_mesh_aliases_may_omit_the_host():
    assert base_models.StagingAlias(id="m", kind="mesh", host="ignored.example").host is None
    with pytest.raises(ValidationError):
        base_models.StagingAlias(id="a", kind="absolute")


@pytest.mark.django_db
def test_mesh_alias_resolves_to_the_hub_nodes_magicdns_name(client, ionscale_repo, mesh_org):
    hub, _ = _grant(client, mesh_org)
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="100", name="old", tags=[hub.mesh_tag], connected=False, ipv4="100.64.0.1"),
        Machine(id="200", name="meshhub", tags=[hub.mesh_tag], connected=True, ipv4="100.64.0.2"),
    ]

    alias = models.InstanceAlias.objects.get(instance__hub=hub)
    rendered = alias.to_url(None)
    assert rendered.host == f"meshhub.{mesh_org.layer.tailnet_name}.{SUFFIX}"
    assert rendered.port == 80 and rendered.path == "rekuest"


@pytest.mark.django_db
def test_mesh_alias_falls_back_to_the_ip_without_magicdns(client, ionscale_repo, mesh_org):
    mesh_org.layer.magic_dns_enabled = False
    mesh_org.layer.save()
    hub, _ = _grant(client, mesh_org)
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="200", name="meshhub", tags=[hub.mesh_tag], connected=True, ipv4="100.64.0.2"),
    ]
    assert resolve_hub_mesh_host(hub) == "100.64.0.2"


@pytest.mark.django_db
def test_unresolvable_mesh_alias_is_left_out_of_the_instance_claim(client, ionscale_repo, mesh_org):
    hub, _ = _grant(client, mesh_org)
    instance = hub.instances.get()
    public = models.InstanceAlias.objects.create(instance=instance, host="public.example", port=443, kind="absolute")

    # No node carries the hub's tag: the mesh alias is dropped, the rest render.
    claim = instance.render(None)
    assert [a.id for a in claim.aliases] == [str(public.id)]


# --------------------------------------------------------------------------- #
# health callback
# --------------------------------------------------------------------------- #


def _report(client, access_token, payload):
    return client.post(
        reverse("fakts:hubhealth"),
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )


@pytest.mark.django_db
def test_health_callback_needs_a_hub_token(client, ionscale_repo, mesh_org):
    assert client.post(reverse("fakts:hubhealth"), data="{}", content_type="application/json").status_code == 401

    # A valid token whose client is no (longer a) hub identity.
    hub, token = _grant(client, mesh_org)
    models.Hub.objects.filter(pk=hub.pk).update(client=None)
    assert _report(client, token["access_token"], {"healthy": True}).status_code == 401


@pytest.mark.django_db
def test_health_callback_records_liveness_and_trims_history(client, ionscale_repo, mesh_org, settings):
    settings.HUB_HEALTH_RETENTION = 2
    hub, token = _grant(client, mesh_org)
    instance_key = hub.instances.get().token

    for _ in range(3):
        resp = _report(
            client,
            token["access_token"],
            {"healthy": True, "version": "0.9", "instances": {instance_key: {"healthy": False, "reason": "db"}, "not-mine": {"healthy": True}}},
        )
        assert resp.status_code == 200, resp.content
    assert resp.json()["next_report_in"] == settings.HUB_HEALTH_INTERVAL

    hub.refresh_from_db()
    assert hub.online and hub.last_healthy and hub.version == "0.9"
    snapshots = list(hub.health_snapshots.all())
    assert len(snapshots) == 2
    assert snapshots[0].payload["instances"] == {instance_key: {"healthy": False, "reason": "db"}}

    hub.last_seen_at = timezone.now() - timedelta(seconds=3 * settings.HUB_HEALTH_INTERVAL + 1)
    assert not hub.online


@pytest.mark.django_db
def test_reported_mesh_state_drives_mesh_aliases_without_asking_ionscale(client, ionscale_repo, mesh_org):
    hub, token = _grant(client, mesh_org)
    # ionscale would say otherwise; the hub's own report wins while it is online.
    ionscale_repo.machines_by_tailnet[mesh_org.layer.tailnet_name] = [
        Machine(id="200", name="stale-name", tags=[hub.mesh_tag], connected=True),
    ]

    _report(client, token["access_token"], {"healthy": True, "mesh": {"connected": True, "hostname": "hub.from.report"}})
    hub.refresh_from_db()
    assert resolve_hub_mesh_host(hub) == "hub.from.report"

    _report(client, token["access_token"], {"healthy": True, "mesh": {"connected": False}})
    hub.refresh_from_db()
    assert resolve_hub_mesh_host(hub) is None

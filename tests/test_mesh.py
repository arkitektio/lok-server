"""Opt-in ionscale mesh: singleton provisioning + graceful degradation.

The mesh is a per-organization singleton, provisioned only on explicit opt-in
(`ensure_org_mesh`). Hubs use the org's existing mesh read-only and give
an actionable message when there is none, instead of a bare failure.
"""

from types import SimpleNamespace

import pytest
from graphql import GraphQLError
from django.test import override_settings

from fakts import models as fakts_models
from fakts import enums as fakts_enums
from fakts.services.hubs import enroll_hub_on_mesh
from ionscale.manager import ensure_org_mesh, get_org_mesh
from tests import factories


def _linking(host="go.example", port=443, is_secure=True):
    return SimpleNamespace(request=SimpleNamespace(host=host, port=port, is_secure=is_secure))


def test_alias_render_is_layer_independent():
    """A rendered client alias is self-contained — `to_url` never reads the layer,
    so the client contract does not depend on the (deprecated) layer concept."""
    # ABSOLUTE alias with no layer → uses its own host/port/ssl.
    absolute = fakts_models.InstanceAlias(
        kind=fakts_enums.AliasKindChoices.ABSOLUTE.value,
        host="svc.example", port=8080, ssl=True, path="p", challenge="ht", public=True, layer=None,
    )
    a = absolute.to_url(_linking())
    assert a.host == "svc.example" and a.port == 8080 and a.ssl is True

    # RELATIVE alias with no layer → resolves against the linking request host.
    relative = fakts_models.InstanceAlias(
        kind=fakts_enums.AliasKindChoices.RELATIVE.value,
        host=None, port=None, ssl=True, path="p", challenge="ht", public=False, layer=None,
    )
    r = relative.to_url(_linking(host="coord.example", port=443, is_secure=True))
    assert r.host == "coord.example" and r.port == 443


@pytest.mark.django_db
def test_ensure_org_mesh_is_idempotent_singleton(ionscale_repo):
    org = factories.make_organization()

    mesh1 = ensure_org_mesh(org)
    assert mesh1 is not None
    assert fakts_models.IonscaleLayer.objects.filter(organization=org).count() == 1
    assert len(ionscale_repo.created_tailnets) == 1

    # Second call returns the same mesh; no second tailnet.
    mesh2 = ensure_org_mesh(org)
    assert mesh2.pk == mesh1.pk
    assert fakts_models.IonscaleLayer.objects.filter(organization=org).count() == 1
    assert len(ionscale_repo.created_tailnets) == 1


@pytest.mark.django_db
@override_settings(IONSCALE_REPOSITORY=None, IONSCALE_SERVER_URL=None)
def test_ensure_org_mesh_is_noop_when_ionscale_unconfigured():
    org = factories.make_organization()
    # Degrades gracefully — no mesh, no exception into the org flow.
    assert ensure_org_mesh(org) is None
    assert get_org_mesh(org) is None
    assert fakts_models.IonscaleLayer.objects.filter(organization=org).count() == 0


@pytest.mark.django_db
def test_create_organization_enables_mesh_by_default(ionscale_repo):
    """Creating an organization auto-provisions its mesh (enabled by default)."""
    from types import SimpleNamespace
    from karakter.graphql.mutations.organization import create_organization, CreateOrganizationInput

    user = factories.make_user()
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    org = create_organization(info, CreateOrganizationInput(name="Meshy Org", description="d"))

    assert fakts_models.IonscaleLayer.objects.filter(organization=org).count() == 1
    assert len(ionscale_repo.created_tailnets) == 1


@pytest.mark.django_db
def test_ensure_org_mesh_pushes_default_dns_config(ionscale_repo):
    """Provisioning a mesh pushes the default DNS state (MagicDNS + HTTPS both on)."""
    org = factories.make_organization()

    mesh = ensure_org_mesh(org)
    assert mesh.magic_dns_enabled is True
    assert mesh.https_enabled is True

    assert len(ionscale_repo.dns_configs) == 1
    tailnet, config = ionscale_repo.dns_configs[-1]
    assert tailnet == mesh.tailnet_name
    assert config.magic_dns is True
    assert config.https_certs is True


def _info_for(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


@pytest.mark.django_db
def test_update_mesh_toggles_dns_and_pushes_full_state(ionscale_repo):
    """Toggling HTTPS off re-pushes the full desired DNS state to ionscale."""
    from api.management.mutations.ionscale import (
        update_ionscale_layer,
        UpdateIonscaleLayerInput,
    )

    org = factories.make_organization()
    member = org.owner  # mesh settings are owner/admin-only
    mesh = ensure_org_mesh(org)
    ionscale_repo.dns_configs.clear()

    update_ionscale_layer(
        _info_for(member),
        UpdateIonscaleLayerInput(
            id=mesh.pk, name=None, description=None, https_certs=False
        ),
    )

    mesh.refresh_from_db()
    assert mesh.https_enabled is False
    assert mesh.magic_dns_enabled is True  # untouched
    assert len(ionscale_repo.dns_configs) == 1
    _, config = ionscale_repo.dns_configs[-1]
    assert config.magic_dns is True
    assert config.https_certs is False


@pytest.mark.django_db
def test_update_mesh_https_requires_magic_dns(ionscale_repo):
    """HTTPS certs cannot be enabled without MagicDNS — rejected, no push."""
    from api.management.mutations.ionscale import (
        update_ionscale_layer,
        UpdateIonscaleLayerInput,
    )

    org = factories.make_organization()
    member = org.owner  # mesh settings are owner/admin-only
    mesh = ensure_org_mesh(org)
    ionscale_repo.dns_configs.clear()

    with pytest.raises(GraphQLError, match="MagicDNS"):
        update_ionscale_layer(
            _info_for(member),
            UpdateIonscaleLayerInput(
                id=mesh.pk, name=None, description=None,
                magic_dns=False, https_certs=True,
            ),
        )

    mesh.refresh_from_db()
    # State unchanged and nothing pushed on the rejected toggle.
    assert mesh.magic_dns_enabled is True
    assert mesh.https_enabled is True
    assert len(ionscale_repo.dns_configs) == 0


@pytest.mark.django_db
def test_update_mesh_dns_failure_propagates_and_rolls_back(ionscale_repo):
    """An ionscale push failure on an explicit toggle propagates (so the UI can
    report it) and rolls back the model — desired state never drifts ahead."""
    from api.management.mutations.ionscale import (
        update_ionscale_layer,
        UpdateIonscaleLayerInput,
    )

    org = factories.make_organization()
    member = org.owner  # mesh settings are owner/admin-only
    mesh = ensure_org_mesh(org)

    def boom(tailnet, config):
        raise RuntimeError("ionscale unreachable")

    ionscale_repo.set_dns_config = boom

    with pytest.raises(RuntimeError, match="ionscale unreachable"):
        update_ionscale_layer(
            _info_for(member),
            UpdateIonscaleLayerInput(
                id=mesh.pk, name=None, description=None, https_certs=False
            ),
        )

    mesh.refresh_from_db()
    # Rolled back: still enabled, matching what ionscale actually has.
    assert mesh.https_enabled is True


@pytest.mark.django_db
def test_update_mesh_rejects_non_member(ionscale_repo):
    """A user who is not a member of the owning org cannot flip mesh settings."""
    from api.management.mutations.ionscale import (
        update_ionscale_layer,
        UpdateIonscaleLayerInput,
    )

    org = factories.make_organization()
    mesh = ensure_org_mesh(org)
    outsider = factories.make_user()
    ionscale_repo.dns_configs.clear()

    # Denials are GraphQLErrors so they reach the client as errors, not 500s.
    with pytest.raises(GraphQLError):
        update_ionscale_layer(
            _info_for(outsider),
            UpdateIonscaleLayerInput(
                id=mesh.pk, name=None, description=None, magic_dns=False
            ),
        )
    assert len(ionscale_repo.dns_configs) == 0


@pytest.mark.django_db
def test_delete_mesh_tears_down_tailnet_after_commit(ionscale_repo, commit_callbacks):
    """Disabling the mesh deletes the tailnet (forced: machines go with it), but
    only once the layer's deletion has committed."""
    from api.management.mutations.ionscale import (
        delete_ionscale_layer,
        DeleteIonscaleLayerInput,
    )

    org = factories.make_organization()
    mesh = ensure_org_mesh(org)
    tailnet_name = mesh.tailnet_name

    with commit_callbacks() as callbacks:
        returned = delete_ionscale_layer(_info_for(org.owner), DeleteIonscaleLayerInput(id=mesh.pk))
        # inside the transaction: the row is gone, ionscale untouched
        assert get_org_mesh(org) is None
        assert ionscale_repo.deleted_tailnets == []

    assert str(returned) == str(mesh.pk)
    assert len(callbacks) == 1
    assert ionscale_repo.deleted_tailnets == [(tailnet_name, True)]
    assert all(t.name != tailnet_name for t in ionscale_repo.tailnets)


@pytest.mark.django_db
def test_delete_mesh_tolerates_missing_tailnet(ionscale_repo, commit_callbacks):
    """A tailnet already gone on ionscale is not an error: the layer is still removed."""
    from ionscale.errors import IonscaleError
    from api.management.mutations.ionscale import (
        delete_ionscale_layer,
        DeleteIonscaleLayerInput,
    )

    org = factories.make_organization()
    mesh = ensure_org_mesh(org)
    ionscale_repo.fail_with["delete_tailnet"] = IonscaleError("not_found", "no such tailnet")

    with commit_callbacks():
        delete_ionscale_layer(_info_for(org.owner), DeleteIonscaleLayerInput(id=mesh.pk))

    assert get_org_mesh(org) is None


@pytest.mark.django_db
def test_delete_mesh_rejects_non_admin(ionscale_repo, commit_callbacks):
    from api.management.mutations.ionscale import (
        delete_ionscale_layer,
        DeleteIonscaleLayerInput,
    )

    org = factories.make_organization()
    mesh = ensure_org_mesh(org)
    outsider = factories.make_user()

    with pytest.raises(GraphQLError), commit_callbacks():
        delete_ionscale_layer(_info_for(outsider), DeleteIonscaleLayerInput(id=mesh.pk))

    assert get_org_mesh(org) == mesh
    assert ionscale_repo.deleted_tailnets == []


UPDATE_MESH_DNS = """
    mutation Update($input: UpdateIonscaleLayerInput!) {
        updateIonscaleLayer(input: $input) {
            id
            magicDnsEnabled
            httpsEnabled
        }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_update_mesh_dns_through_graphql_schema(ionscale_repo):
    """Full GraphQL path: camelCase inputs (magicDns/httpsCerts) coerce correctly
    and the resolver pushes DNS state — what the kontrol UI actually calls."""
    from asgiref.sync import sync_to_async

    from api.management.schema import schema as management_schema
    from tests.conftest import build_auth_context
    from tests import factories

    def _setup():
        # Mesh settings are owner/admin-only: act as the organization's owner.
        organization = factories.make_organization()
        membership = factories.make_membership(user=organization.owner, organization=organization)
        request_client = factories.make_client(membership=membership)
        mesh = ensure_org_mesh(membership.organization)
        ionscale_repo.dns_configs.clear()
        ctx = build_auth_context(
            membership.user, membership.organization, request_client
        )
        # The management schema has no auth strawberry-extension; production sets
        # request.user at the view layer, so we set it directly here.
        ctx.request._user = membership.user
        return mesh, ctx

    mesh, ctx = await sync_to_async(_setup)()

    result = await management_schema.execute(
        UPDATE_MESH_DNS,
        context_value=ctx,
        variable_values={"input": {"id": str(mesh.pk), "httpsCerts": False}},
    )

    assert not result.errors, result.errors
    data = result.data["updateIonscaleLayer"]
    assert data["magicDnsEnabled"] is True
    assert data["httpsEnabled"] is False
    assert len(ionscale_repo.dns_configs) == 1
    _, config = ionscale_repo.dns_configs[-1]
    assert config.magic_dns is True and config.https_certs is False


@pytest.mark.django_db
def test_hub_auth_key_needs_opted_in_mesh(ionscale_repo):
    hub = factories.make_hub()
    org = hub.organization

    # No mesh opted in yet -> no key, and no tailnet auto-created.
    assert enroll_hub_on_mesh(org.owner, hub) is None
    assert len(ionscale_repo.created_tailnets) == 0

    # Opt in, then issuing a key works against the singleton mesh.
    ensure_org_mesh(org)
    key = enroll_hub_on_mesh(org.owner, hub)
    assert key.pk is not None
    assert len(ionscale_repo.created_auth_keys) == 1
    assert ionscale_repo.created_auth_keys[-1]["tailnet"] == get_org_mesh(org).tailnet_name


# ---------------------------------------------------------------------------
# Mesh device-code flow ("meshconfigure"): a machine requests to join the org
# mesh, a human member authorizes, and a per-machine pre-auth key is minted.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_start_mesh_device_code_mints_distinct_codes():
    from fakts import base_models
    from fakts.services.device_codes import start_mesh_device_code

    dc = start_mesh_device_code(
        base_models.MeshDeviceCodeStartRequest(requested_machine_name="gpu-01", description="A GPU box")
    )
    assert dc.code and dc.challenge_code
    assert dc.code != dc.challenge_code  # human-visible code is not the poll secret
    assert dc.requested_machine_name == "gpu-01"
    assert dc.auth_key is None and dc.denied is False


@pytest.mark.django_db
def test_create_mesh_auth_key_needs_opted_in_mesh(ionscale_repo):
    from fakts.services.hubs import create_mesh_auth_key

    org = factories.make_organization()

    # No mesh opted in -> actionable message, and no tailnet auto-created.
    with pytest.raises(Exception, match="no mesh"):
        create_mesh_auth_key(org.owner, org)
    assert len(ionscale_repo.created_tailnets) == 0

    # Opt in, then a single-use, persistent, pre-authorized key is minted.
    ensure_org_mesh(org)
    key = create_mesh_auth_key(org.owner, org)
    assert key.pk is not None
    assert key.ephemeral is False
    assert len(ionscale_repo.created_auth_keys) == 1
    recorded = ionscale_repo.created_auth_keys[-1]
    assert recorded["tailnet"] == get_org_mesh(org).tailnet_name
    assert recorded["pre_authorized"] is True
    assert recorded["ephemeral"] is False


@pytest.mark.django_db
def test_accept_mesh_device_code_mints_key_and_sets_name(ionscale_repo):
    from fakts import base_models
    from fakts.services.device_codes import start_mesh_device_code
    from api.management.mutations.mesh_device_code import (
        accept_mesh_device_code,
        AcceptMeshDeviceCodeInput,
    )

    org = factories.make_organization()
    member = org.owner  # minting a mesh key is owner/admin-only
    ensure_org_mesh(org)

    dc = start_mesh_device_code(
        base_models.MeshDeviceCodeStartRequest(requested_machine_name="gpu-01")
    )

    # Human omits machine_name -> falls back to the requested one.
    accept_mesh_device_code(
        _info_for(member),
        AcceptMeshDeviceCodeInput(device_code=str(dc.pk), code=dc.code, organization=str(org.pk)),
    )

    dc.refresh_from_db()
    assert dc.auth_key is not None
    assert dc.machine_name == "gpu-01"
    assert len(ionscale_repo.created_auth_keys) == 1


@pytest.mark.django_db
def test_accept_mesh_device_code_human_overrides_name(ionscale_repo):
    from fakts import base_models
    from fakts.services.device_codes import start_mesh_device_code
    from api.management.mutations.mesh_device_code import (
        accept_mesh_device_code,
        AcceptMeshDeviceCodeInput,
    )

    org = factories.make_organization()
    member = org.owner  # minting a mesh key is owner/admin-only
    ensure_org_mesh(org)

    dc = start_mesh_device_code(
        base_models.MeshDeviceCodeStartRequest(requested_machine_name="gpu-01")
    )
    accept_mesh_device_code(
        _info_for(member),
        AcceptMeshDeviceCodeInput(
            device_code=str(dc.pk), code=dc.code, organization=str(org.pk), machine_name="renamed-box"
        ),
    )

    dc.refresh_from_db()
    assert dc.machine_name == "renamed-box"


@pytest.mark.django_db
def test_accept_mesh_device_code_rejects_non_member(ionscale_repo):
    from fakts import base_models
    from fakts.services.device_codes import start_mesh_device_code
    from api.management.mutations.mesh_device_code import (
        accept_mesh_device_code,
        AcceptMeshDeviceCodeInput,
    )

    org = factories.make_organization()
    outsider = factories.make_user()
    ensure_org_mesh(org)

    dc = start_mesh_device_code(base_models.MeshDeviceCodeStartRequest())

    # Denials are GraphQLErrors so they reach the client as errors, not 500s.
    with pytest.raises(GraphQLError):
        accept_mesh_device_code(
            _info_for(outsider),
            AcceptMeshDeviceCodeInput(device_code=str(dc.pk), code=dc.code, organization=str(org.pk)),
        )
    assert len(ionscale_repo.created_auth_keys) == 0


@pytest.mark.django_db
def test_mesh_challenge_view_poll_lifecycle(ionscale_repo):
    """The poll endpoint returns pending before accept and the key + coord url +
    machine name once granted."""
    import json

    from django.test import RequestFactory

    from fakts import base_models
    from fakts.services.device_codes import start_mesh_device_code
    from fakts.views import MeshChallengeView
    from api.management.mutations.mesh_device_code import (
        accept_mesh_device_code,
        AcceptMeshDeviceCodeInput,
    )

    org = factories.make_organization()
    member = org.owner  # minting a mesh key is owner/admin-only
    ensure_org_mesh(org)
    dc = start_mesh_device_code(
        base_models.MeshDeviceCodeStartRequest(requested_machine_name="gpu-01")
    )

    def _poll():
        rf = RequestFactory()
        req = rf.post(
            "/lok/f/meshchallenge/",
            data=json.dumps({"code": dc.challenge_code}),
            content_type="application/json",
        )
        return json.loads(MeshChallengeView.as_view()(req).content)

    # Before authorization -> pending.
    assert _poll()["status"] == "pending"

    # After the owner accepts -> granted with the minted key.
    accept_mesh_device_code(
        _info_for(member),
        AcceptMeshDeviceCodeInput(device_code=str(dc.pk), code=dc.code, organization=str(org.pk)),
    )
    granted = _poll()
    assert granted["status"] == "granted"
    assert granted["ionscale_auth_key"] == ionscale_repo.auth_key
    assert granted["machine_name"] == "gpu-01"
    assert "ionscale_coord_url" in granted


def test_mesh_device_code_type_never_exposes_the_key():
    """Security: the by-code lookup is preview-friendly, so the minted mesh key must
    not be reachable through ManagementMeshDeviceCode. The machine gets it via the
    secret-gated REST poll instead."""
    from api.management.schema import schema as management_schema

    sdl = management_schema.as_str()
    # Locate the type block and assert it carries no auth-key field.
    start = sdl.index("type ManagementMeshDeviceCode")
    block = sdl[start:sdl.index("}", start)]
    assert "authKey" not in block
    assert "IonscaleAuthKey" not in block


MESH_ACCEPT = """
    mutation Accept($input: AcceptMeshDeviceCodeInput!) {
        acceptMeshDeviceCode(input: $input) {
            id
            machineName
            denied
        }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_accept_mesh_device_code_through_graphql_schema(ionscale_repo):
    """Full GraphQL path: camelCase input coercion (deviceCode/organization/machineName)
    and the resolver mints + links the key — what the kontrol configure page calls."""
    from asgiref.sync import sync_to_async

    from api.management.schema import schema as management_schema
    from fakts import base_models
    from fakts.services.device_codes import start_mesh_device_code
    from tests.conftest import build_auth_context

    def _setup():
        # Minting a mesh key is owner/admin-only: act as the organization's owner.
        organization = factories.make_organization()
        membership = factories.make_membership(user=organization.owner, organization=organization)
        request_client = factories.make_client(membership=membership)
        ensure_org_mesh(membership.organization)
        dc = start_mesh_device_code(
            base_models.MeshDeviceCodeStartRequest(requested_machine_name="gpu-01")
        )
        ctx = build_auth_context(
            membership.user, membership.organization, request_client
        )
        ctx.request._user = membership.user
        return membership, dc, ctx

    membership, dc, ctx = await sync_to_async(_setup)()

    result = await management_schema.execute(
        MESH_ACCEPT,
        context_value=ctx,
        variable_values={
            "input": {"deviceCode": str(dc.pk), "code": dc.code, "organization": str(membership.organization.pk)}
        },
    )

    assert not result.errors, result.errors
    data = result.data["acceptMeshDeviceCode"]
    assert data["machineName"] == "gpu-01"

    # The minted key is linked server-side but deliberately NOT exposed over GraphQL
    # (it is delivered only via the REST poll). Verify the mint via the DB.
    def _check():
        dc.refresh_from_db()
        return dc.auth_key is not None and dc.auth_key.key == ionscale_repo.auth_key

    assert await sync_to_async(_check)()


# ---------------------------------------------------------------------------
# Machine detail resolver: robust lookup via the reliable list parser, graceful
# not-found, and the derived MagicDNS name. Regression cover for the detail page
# that used to error when the fragile `get_machine` parse lost the tailnet.
# ---------------------------------------------------------------------------

MACHINE_DETAIL_QUERY = """
query DetailMachine($id: ID!) {
  machine(id: $id) {
    id
    name
    connected
    ipv4
    tags
    os
    keyExpiry
    authorized
    isExternal
    magicDnsName
  }
}
"""


def _machine_ctx(ionscale_repo):
    """A mesh with an authenticated owner context. Returns (mesh, ctx)."""
    from tests.conftest import build_auth_context

    membership = factories.make_membership()
    request_client = factories.make_client(membership=membership)
    mesh = ensure_org_mesh(membership.organization)
    ctx = build_auth_context(
        membership.user, membership.organization, request_client
    )
    # Management schema has no auth extension; production sets request.user at the
    # view layer, so set it directly here.
    ctx.request._user = membership.user
    return mesh, ctx


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_machine_detail_resolves_via_list_when_get_machine_fails(ionscale_repo):
    """The old page broke when `get_machine` couldn't be parsed. Seed only the LIST
    data (leave `machines[id]` unseeded so the fake's get_machine raises) and assert
    the machine still resolves — with the reliable connected/ipv4 from the list path."""
    from asgiref.sync import sync_to_async
    from ionscale.base_models import Machine
    from api.management.schema import schema as management_schema

    mesh, ctx = await sync_to_async(_machine_ctx)(ionscale_repo)
    ionscale_repo.machines_by_tailnet[mesh.tailnet_name] = [
        Machine(id="m1", name="gpu-01", connected=True, ipv4="100.64.0.1", ipv6="fd7a::1", tags=["tag:gpu"])
    ]
    # Deliberately do NOT seed ionscale_repo.machines["m1"] -> get_machine raises KeyError.

    result = await management_schema.execute(
        MACHINE_DETAIL_QUERY, context_value=ctx, variable_values={"id": "m1"}
    )

    assert not result.errors, result.errors
    machine = result.data["machine"]
    assert machine is not None
    assert machine["id"] == "m1"
    assert machine["name"] == "gpu-01"
    assert machine["connected"] is True  # from the list path, not hardcoded False
    assert machine["ipv4"] == "100.64.0.1"
    assert machine["tags"] == ["tag:gpu"]
    # Detail-only fields are unknown (not sourced) -> null, never a misleading False.
    assert machine["authorized"] is None
    assert machine["isExternal"] is None
    assert machine["os"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_machine_detail_list_wins_over_detail_for_connected(ionscale_repo):
    """Merge trap: the detail parser hardcodes connected=False. The list value must
    win so a live machine never renders 'Disconnected'."""
    from asgiref.sync import sync_to_async
    from ionscale.base_models import Machine, MachineDetail
    from api.management.schema import schema as management_schema

    mesh, ctx = await sync_to_async(_machine_ctx)(ionscale_repo)
    ionscale_repo.machines_by_tailnet[mesh.tailnet_name] = [
        Machine(id="m1", name="gpu-01", connected=True, ipv4="100.64.0.1")
    ]
    # Detail says connected=False (as the real parser always does) but adds os + a known authorized.
    ionscale_repo.machines["m1"] = MachineDetail(
        id="m1", name="gpu-01", connected=False, os="linux", authorized=True
    )

    result = await management_schema.execute(
        MACHINE_DETAIL_QUERY, context_value=ctx, variable_values={"id": "m1"}
    )

    assert not result.errors, result.errors
    machine = result.data["machine"]
    assert machine["connected"] is True  # list wins
    assert machine["os"] == "linux"  # detail-only field is surfaced
    assert machine["authorized"] is True  # a KNOWN detail value comes through


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_machine_detail_unknown_id_returns_null(ionscale_repo):
    """An id present in no layer of the caller resolves to null (not an error)."""
    from asgiref.sync import sync_to_async
    from api.management.schema import schema as management_schema

    mesh, ctx = await sync_to_async(_machine_ctx)(ionscale_repo)
    # No machines seeded for this mesh at all.

    result = await management_schema.execute(
        MACHINE_DETAIL_QUERY, context_value=ctx, variable_values={"id": "does-not-exist"}
    )

    assert not result.errors, result.errors
    assert result.data["machine"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_machine_detail_hidden_from_non_member(ionscale_repo):
    """A machine on another org's mesh is not reachable — the caller's layer scan is
    empty, so it resolves to null."""
    from asgiref.sync import sync_to_async
    from ionscale.base_models import Machine
    from api.management.schema import schema as management_schema

    def _setup():
        # Owner org has the machine...
        owner_mesh, _ = _machine_ctx(ionscale_repo)
        ionscale_repo.machines_by_tailnet[owner_mesh.tailnet_name] = [
            Machine(id="m1", name="gpu-01", connected=True)
        ]
        # ...but a different user (their own org/mesh) makes the request.
        return _machine_ctx(ionscale_repo)

    _outsider_mesh, outsider_ctx = await sync_to_async(_setup)()

    result = await management_schema.execute(
        MACHINE_DETAIL_QUERY, context_value=outsider_ctx, variable_values={"id": "m1"}
    )

    assert not result.errors, result.errors
    assert result.data["machine"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(IONSCALE_MAGIC_DNS_SUFFIX="mesh.test")
async def test_machine_detail_magic_dns_name(ionscale_repo):
    """MagicDNS name is derived from the configured suffix when the mesh has MagicDNS
    on, and null when off."""
    from asgiref.sync import sync_to_async
    from ionscale.base_models import Machine
    from api.management.schema import schema as management_schema

    def _setup(enabled):
        membership = factories.make_membership()
        request_client = factories.make_client(membership=membership)
        mesh = ensure_org_mesh(membership.organization)
        mesh.magic_dns_enabled = enabled
        mesh.save()
        from tests.conftest import build_auth_context
        ctx = build_auth_context(membership.user, membership.organization, request_client)
        ctx.request._user = membership.user
        ionscale_repo.machines_by_tailnet[mesh.tailnet_name] = [Machine(id="m1", name="gpu-01", connected=True)]
        return mesh, ctx

    # MagicDNS enabled -> derived `<name>.<tailnet>.<suffix>`.
    mesh, ctx = await sync_to_async(_setup)(True)
    result = await management_schema.execute(
        MACHINE_DETAIL_QUERY, context_value=ctx, variable_values={"id": "m1"}
    )
    assert not result.errors, result.errors
    assert result.data["machine"]["magicDnsName"] == f"gpu-01.{mesh.tailnet_name}.mesh.test"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(IONSCALE_MAGIC_DNS_SUFFIX="mesh.test")
async def test_machine_detail_magic_dns_name_null_when_disabled(ionscale_repo):
    """MagicDNS off -> no name (it would not resolve)."""
    from asgiref.sync import sync_to_async
    from ionscale.base_models import Machine
    from api.management.schema import schema as management_schema

    def _setup():
        membership = factories.make_membership()
        request_client = factories.make_client(membership=membership)
        mesh = ensure_org_mesh(membership.organization)
        mesh.magic_dns_enabled = False
        mesh.save()
        from tests.conftest import build_auth_context
        ctx = build_auth_context(membership.user, membership.organization, request_client)
        ctx.request._user = membership.user
        ionscale_repo.machines_by_tailnet[mesh.tailnet_name] = [Machine(id="m1", name="gpu-01", connected=True)]
        return mesh, ctx

    _mesh, ctx = await sync_to_async(_setup)()
    result = await management_schema.execute(
        MACHINE_DETAIL_QUERY, context_value=ctx, variable_values={"id": "m1"}
    )
    assert not result.errors, result.errors
    assert result.data["machine"]["magicDnsName"] is None


# ---------------------------------------------------------------------------
# Parser regression: the `ionscale machines list` column layout has TAILNET as
# the 2nd column and NAME as the 3rd. A naive positional parse mis-assigned the
# tailnet to `name` and the name to `ipv4`, which is what broke the detail page.
# Fixture below is real output captured from ionscale 1.9x.
# ---------------------------------------------------------------------------

REAL_MACHINES_LIST_OUTPUT = (
    "ID                  TAILNET          NAME            IPv4          IPv6                                     AUTHORIZED  EPHEMERAL  VERSION                       LAST_SEEN     TAGS        \n"
    "221707112168816643  linz-4wnswa-kkk  jhnnsrs-server  100.98.11.67  fd7a:115c:a1e0:ab12:4843:cd96:6262:b43   true        false      1.96.4-t8cf541dfd-g62bc84ce7  a minute ago  tag:mesh-9  \n"
    "220990015570771973  linz-4wnswa-kkk  pixel-8         100.76.19.36  fd7a:115c:a1e0:ab12:4843:cd96:624c:1324  true        false      1.98.2-taaf7caef1-g983926d2a  4 days ago                \n"
)


def test_parse_machine_list_maps_columns_by_header():
    """Names and IPs land in the right fields, and authorized/tags/ephemeral parse."""
    from ionscale.repo import IonscaleRepository

    repo = IonscaleRepository.__new__(IonscaleRepository)  # no CLI/network needed for the parser
    machines = repo._parse_machine_list_output(REAL_MACHINES_LIST_OUTPUT)

    assert len(machines) == 2
    by_id = {m.id: m for m in machines}

    server = by_id["221707112168816643"]
    assert server.name == "jhnnsrs-server"          # NAME column, not the tailnet
    assert server.tailnet == "linz-4wnswa-kkk"
    assert server.ipv4 == "100.98.11.67"            # IPv4 column, not the name
    assert server.ipv6 == "fd7a:115c:a1e0:ab12:4843:cd96:6262:b43"
    assert server.authorized is True
    assert server.ephemeral is False
    assert server.tags == ["tag:mesh-9"]
    assert server.connected is True                 # "a minute ago" -> live

    pixel = by_id["220990015570771973"]
    assert pixel.name == "pixel-8"
    assert pixel.ipv4 == "100.76.19.36"
    assert pixel.tags == []                          # empty TAGS column
    assert pixel.connected is False                 # "4 days ago" -> not live

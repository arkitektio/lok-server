"""An alias's reach: one word for where its address works, derived from what is stored, and
written as the kind and scope it stands for — so the previous release reads the same rows."""

import pytest

from fakts import base_models
from fakts.services.alias_reach import REACHES, legacy_of, reach_of


def staged(**fields) -> base_models.StagingAlias:
    return base_models.StagingAlias(id="a", port=80, **fields)


@pytest.mark.parametrize(
    ("kind", "scope", "host", "reach"),
    [
        ("absolute", "local", "localhost", "loopback"),
        ("absolute", "local", "127.0.0.1", "loopback"),
        ("absolute", "local", "::1", "loopback"),
        # konstruktor's in-network alias, as older hubs send it.
        ("absolute", "local", "gateway", "internal"),
        ("docker", "local", "gateway", "internal"),
        ("absolute", "network", "10.0.0.4", "lan"),
        ("absolute", "public", "lab.example.org", "public"),
        ("absolute", "ionscale", "hub.tail.ts.net", "mesh"),
        ("mesh", "ionscale", None, "mesh"),
    ],
)
def test_reach_is_read_off_what_older_hubs_send(kind, scope, host, reach):
    assert reach_of(kind, scope, host) == reach
    assert staged(kind=kind, scope=scope, host=host).effective_reach == reach


@pytest.mark.parametrize("reach", REACHES)
@pytest.mark.parametrize("host", ["127.0.0.1", "gateway", None])
def test_a_said_reach_is_stored_as_what_it_reads_back_as(reach, host):
    if host is None and reach != "mesh":
        pytest.skip("only a mesh alias may leave its host to the coordination server")
    if reach == "loopback" and host != "127.0.0.1":
        pytest.skip("refused, see below")
    alias = staged(reach=reach, host=host, kind="absolute", scope="public")
    assert (alias.kind, alias.scope) == legacy_of(reach, alias.host)
    assert alias.effective_reach == reach
    # Validated again, as part of a request: the same alias.
    again = base_models.StagingAlias.model_validate(alias.model_dump())
    assert (again.kind, again.scope, again.effective_reach) == (alias.kind, alias.scope, reach)


def test_a_mesh_alias_without_a_host_is_the_placeholder_lok_fills_in():
    alias = staged(reach="mesh")
    assert alias.kind == "mesh" and alias.host is None


def test_loopback_must_be_this_machine():
    with pytest.raises(ValueError, match="not this machine"):
        staged(reach="loopback", host="lab.local")


def test_an_internal_alias_cannot_be_checked_by_lok():
    with pytest.raises(ValueError, match="internal"):
        staged(reach="internal", host="gateway", lok_checks=True)


def test_lok_checks_is_publics_new_name():
    assert staged(reach="public", host="lab.example.org", lok_checks=True).public is True
    assert staged(reach="public", host="lab.example.org", public=True, lok_checks=False).public is False


def test_an_older_hubs_alias_validates_the_same_twice():
    alias = staged(host="example.com", public=True)
    again = base_models.StagingAlias.model_validate(alias.model_dump())
    assert (again.kind, again.public, again.reach) == ("absolute", True, None)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("alias", "reach", "kind"),
    [
        (dict(reach="lan", host="10.0.0.4"), "lan", "absolute"),
        (dict(reach="internal", host="gateway"), "internal", "docker"),
        (dict(reach="loopback", host="localhost"), "loopback", "absolute"),
        # An older hub, saying only kind and scope.
        (dict(host="lab.example.org", scope="public"), "public", "absolute"),
    ],
)
def test_apps_are_told_the_reach_and_the_kind_older_ones_read(alias, reach, kind):
    from fakts import models
    from fakts.services import hubs
    from tests import factories
    from tests.test_alias_public import _linking_context

    manifest = base_models.HubManifest(
        identifier="com.example.reach",
        instances=[
            base_models.InstanceRequest(
                identifier="inst-1",
                manifest=base_models.ServiceManifest(identifier="com.example.svc", version="1.0.0"),
                aliases=[base_models.StagingAlias(id="a", port=80, challenge="ht", **alias)],
            )
        ],
    )
    hubs.create_hub_from_manifest(manifest, factories.make_organization())
    rendered = models.InstanceAlias.objects.get().to_url(_linking_context())
    assert (rendered.reach, rendered.kind) == (reach, kind)

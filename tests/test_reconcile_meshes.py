"""`manage.py reconcile_meshes` and the `ionscale.reconcile` module behind it."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from fakts import models as fakts_models
from ionscale.base_models import Tailnet, TailnetUser
from ionscale.errors import IonscaleError
from ionscale.reconcile import orphaned_tailnets, reconcile_layer
from karakter.models import Membership, Organization, User


def _mesh(slug: str, *, tailnet_name: str | None = None) -> fakts_models.IonscaleLayer:
    """An organization with one mesh layer. The control plane is *not* seeded:
    each test decides what ionscale knows."""
    owner = User.objects.create(username=f"{slug}-owner")
    organization = Organization.objects.create(slug=slug, owner=owner)
    tailnet_name = tailnet_name or slug
    return fakts_models.IonscaleLayer.objects.create(
        organization=organization,
        name="Default",
        kind="ionscale",
        identifier=tailnet_name,
        tailnet_name=tailnet_name,
    )


def _seed_tailnet(ionscale_repo, layer, name: str | None = None, tailnet_id: str = "1") -> Tailnet:
    tailnet = Tailnet(
        id=tailnet_id,
        name=name or layer.tailnet_name,
        dns_name=name or layer.tailnet_name,
        organization=str(layer.organization.pk),
    )
    ionscale_repo.tailnets.append(tailnet)
    return tailnet


@pytest.mark.django_db
def test_in_sync_mesh_only_repushes_members_and_dns(ionscale_repo):
    layer = _mesh("steady")
    _seed_tailnet(ionscale_repo, layer)

    report = reconcile_layer(layer)

    assert not report.changed
    assert report.synced and report.dns_applied
    assert ionscale_repo.created_tailnets == []
    assert ionscale_repo.updated_tailnets == []
    assert len(ionscale_repo.updated_policies) == 1
    _, policy = ionscale_repo.updated_policies[0]
    assert policy["subs"] == [str(layer.organization.owner.pk)]
    assert [t for t, _ in ionscale_repo.dns_configs] == ["steady"]


@pytest.mark.django_db
def test_missing_tailnet_is_created_and_bound_to_the_organization(ionscale_repo):
    layer = _mesh("fresh")

    report = reconcile_layer(layer)

    assert report.created_tailnet and report.changed
    (created,) = ionscale_repo.created_tailnets
    assert created.name == "fresh"
    assert created.organization == str(layer.organization.pk)
    assert len(ionscale_repo.updated_policies) == 1


@pytest.mark.django_db
def test_dry_run_reports_without_writing(ionscale_repo):
    layer = _mesh("plan-only")
    out = io.StringIO()

    report = reconcile_layer(layer, dry_run=True, out=out)

    assert report.created_tailnet and report.changed
    assert not report.synced and not report.dns_applied
    assert "creating 'plan-only'" in out.getvalue()
    assert ionscale_repo.created_tailnets == []
    assert ionscale_repo.updated_policies == []
    assert ionscale_repo.dns_configs == []


@pytest.mark.django_db
def test_renamed_organization_renames_tailnet_and_layer(ionscale_repo):
    layer = _mesh("new-slug", tailnet_name="old-slug")
    _seed_tailnet(ionscale_repo, layer, name="old-slug")

    report = reconcile_layer(layer)

    assert report.renamed_from == "old-slug"
    assert ionscale_repo.updated_tailnets == [("old-slug", {"name": "new-slug"})]
    layer.refresh_from_db()
    assert layer.tailnet_name == "new-slug"
    assert layer.identifier == "new-slug"
    # members are pushed to the tailnet under its new name
    assert [t for t, _ in ionscale_repo.updated_policies] == ["new-slug"]


@pytest.mark.django_db
def test_rename_collision_keeps_current_name_with_a_warning(ionscale_repo):
    layer = _mesh("taken", tailnet_name="taken-old")
    _seed_tailnet(ionscale_repo, layer, name="taken-old")
    # an unrelated tailnet already owns the wanted name
    ionscale_repo.tailnets.append(Tailnet(id="9", name="taken", dns_name="taken", organization=None))

    report = reconcile_layer(layer)

    assert report.renamed_from == "taken-old"
    assert any("name taken" in w for w in report.warnings)
    layer.refresh_from_db()
    assert layer.tailnet_name == "taken-old"
    assert [t for t, _ in ionscale_repo.updated_policies] == ["taken-old"]


@pytest.mark.django_db
def test_layer_follows_tailnet_name_when_only_lok_drifted(ionscale_repo):
    """ionscale already carries the right name; only the layer is stale."""
    layer = _mesh("correct", tailnet_name="stale")
    _seed_tailnet(ionscale_repo, layer, name="correct")

    reconcile_layer(layer)

    layer.refresh_from_db()
    assert layer.tailnet_name == "correct"
    assert ionscale_repo.updated_tailnets == []


@pytest.mark.django_db
def test_create_race_falls_back_to_lookup(ionscale_repo):
    layer = _mesh("raced")
    tailnet = _seed_tailnet(ionscale_repo, layer)
    # the org lookup misses (an ionscale that lost the binding), the create then
    # collides on the name -> report, don't steal
    calls = {"n": 0}
    real = ionscale_repo.get_tailnet_by_organization

    def flaky(org):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(org)

    ionscale_repo.get_tailnet_by_organization = flaky  # type: ignore[method-assign]

    report = reconcile_layer(layer)

    assert ionscale_repo.created_tailnets == []
    assert any("could not create" in w for w in report.warnings)
    assert report.synced
    assert tailnet in ionscale_repo.tailnets


@pytest.mark.django_db
def test_revoke_orphans_only_touches_non_members(ionscale_repo):
    layer = _mesh("orphans")
    _seed_tailnet(ionscale_repo, layer)
    member = User.objects.create(username="member")
    Membership.objects.create(user=member, organization=layer.organization)
    ionscale_repo.users_by_tailnet["orphans"] = [
        TailnetUser(id="1", name="owner", external_id=str(layer.organization.owner.pk)),
        TailnetUser(id="2", name="member", external_id=str(member.pk)),
        TailnetUser(id="3", name="gone", external_id="99999"),
    ]

    report = reconcile_layer(layer, revoke_orphans=True)

    assert report.revoked == ["99999"]
    assert ionscale_repo.revoked_accounts == [("99999", str(layer.organization.pk))]

    # without the flag nothing is revoked
    ionscale_repo.revoked_accounts.clear()
    reconcile_layer(layer)
    assert ionscale_repo.revoked_accounts == []


@pytest.mark.django_db
def test_revoke_orphans_refuses_to_guess_without_external_ids(ionscale_repo):
    layer = _mesh("blind")
    _seed_tailnet(ionscale_repo, layer)
    ionscale_repo.users_by_tailnet["blind"] = [TailnetUser(id="3", name="who", external_id=None)]

    report = reconcile_layer(layer, revoke_orphans=True)

    assert report.revoked == []
    assert any("external ids" in w for w in report.warnings)
    assert ionscale_repo.revoked_accounts == []


@pytest.mark.django_db
def test_control_plane_errors_propagate_to_the_caller(ionscale_repo):
    layer = _mesh("down")
    ionscale_repo.fail_with["get_tailnet_by_organization"] = IonscaleError("unavailable", "refused")

    with pytest.raises(IonscaleError):
        reconcile_layer(layer)


@pytest.mark.django_db
def test_orphaned_tailnets_are_bound_tailnets_without_a_layer(ionscale_repo):
    layer = _mesh("kept")
    _seed_tailnet(ionscale_repo, layer)
    ionscale_repo.tailnets.append(Tailnet(id="7", name="deleted-org", dns_name="d", organization="424242"))
    ionscale_repo.tailnets.append(Tailnet(id="8", name="unbound", dns_name="u", organization=None))

    orphans = orphaned_tailnets(fakts_models.IonscaleLayer.objects.all())

    assert [t.name for t in orphans] == ["deleted-org"]


# --- the management command --------------------------------------------------


@pytest.mark.django_db
def test_command_reconciles_every_mesh_and_reports_orphans(ionscale_repo):
    a = _mesh("cmd-a")
    _mesh("cmd-b")  # missing on ionscale -> created
    _seed_tailnet(ionscale_repo, a)
    ionscale_repo.tailnets.append(Tailnet(id="7", name="stray", dns_name="s", organization="424242"))
    out = io.StringIO()

    call_command("reconcile_meshes", stdout=out)

    text = out.getvalue()
    assert "[cmd-a] ok" in text
    assert "[cmd-b] changed" in text
    assert "orphan tailnet 'stray'" in text
    assert "2 mesh(es), 1 changed, 0 failed" in text
    assert [t.name for t in ionscale_repo.created_tailnets] == ["cmd-b"]


@pytest.mark.django_db
def test_command_dry_run_writes_nothing(ionscale_repo):
    _mesh("cmd-dry")
    out = io.StringIO()

    call_command("reconcile_meshes", "--dry-run", stdout=out)

    assert "[cmd-dry] would change" in out.getvalue()
    assert ionscale_repo.created_tailnets == []
    assert ionscale_repo.updated_policies == []


@pytest.mark.django_db
def test_command_scopes_to_one_organization(ionscale_repo):
    _mesh("cmd-one")
    other = _mesh("cmd-other")
    out = io.StringIO()

    call_command("reconcile_meshes", "--organization", "cmd-one", stdout=out)
    assert [t.name for t in ionscale_repo.created_tailnets] == ["cmd-one"]

    call_command("reconcile_meshes", "--organization", str(other.organization.pk), stdout=out)
    assert [t.name for t in ionscale_repo.created_tailnets] == ["cmd-one", "cmd-other"]

    with pytest.raises(CommandError):
        call_command("reconcile_meshes", "--organization", "no-such-org")


@pytest.mark.django_db
def test_command_keeps_going_and_exits_nonzero_on_failure(ionscale_repo):
    _mesh("cmd-broken")
    _mesh("cmd-fine")
    ionscale_repo.fail_with["create_tailnet"] = IonscaleError("unavailable", "refused")
    out, err = io.StringIO(), io.StringIO()

    with pytest.raises(CommandError, match="2 mesh"):
        call_command("reconcile_meshes", stdout=out, stderr=err)

    assert "[cmd-broken] failed" in err.getvalue()
    assert "[cmd-fine] failed" in err.getvalue()
    assert "2 mesh(es), 0 changed, 2 failed" in out.getvalue()


@pytest.mark.django_db
def test_command_refuses_when_ionscale_is_not_configured(settings):
    settings.IONSCALE_REPOSITORY = None
    settings.IONSCALE_SERVER_URL = None
    with pytest.raises(CommandError, match="not configured"):
        call_command("reconcile_meshes")

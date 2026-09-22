import logging
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

from api.management.mutations.ionscale import CreateIonscaleLayerInput, create_ionscale_layer
from fakts import models as fakts_models
from ionscale.base_models import DNSConfig, Tailnet
from ionscale.errors import IonscaleError
from ionscale.repo import IonscaleRepository
from karakter.models import Membership, Organization, User


def _info_for(user):
    """Minimal Info stand-in carrying a principal, matching ``tests/test_mesh``."""
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _repo() -> IonscaleRepository:
    # Bypass the binary lookup in __init__ so the arg-building logic can be tested
    # without an ionscale binary present.
    with mock.patch("ionscale.repo.shutil.which", return_value="/usr/bin/ionscale"):
        return IonscaleRepository(server_url="http://ionscale", admin_key="k")


def test_set_dns_config_enable_both():
    """MagicDNS + HTTPS on → both flags present."""
    repo = _repo()
    with mock.patch.object(repo, "_run_command", return_value="ok") as run:
        repo.set_dns_config("tn", DNSConfig(magic_dns=True, https_certs=True))
    assert run.call_args.args[0] == [
        "tailnets", "set-dns", "--tailnet", "tn", "--magic-dns", "--https-certs",
    ]


def test_set_dns_config_disable_by_omission():
    """Disabling = omitting the flag. Relies on set-dns replacing the whole config."""
    repo = _repo()
    with mock.patch.object(repo, "_run_command", return_value="ok") as run:
        repo.set_dns_config("tn", DNSConfig(magic_dns=False, https_certs=False))
    assert run.call_args.args[0] == ["tailnets", "set-dns", "--tailnet", "tn"]


def test_set_dns_config_magic_on_https_off():
    """MagicDNS on, HTTPS off → only --magic-dns."""
    repo = _repo()
    with mock.patch.object(repo, "_run_command", return_value="ok") as run:
        repo.set_dns_config("tn", DNSConfig(magic_dns=True, https_certs=False))
    assert run.call_args.args[0] == [
        "tailnets", "set-dns", "--tailnet", "tn", "--magic-dns",
    ]


def test_cli_repo_reports_missing_service_token_for_http_only_calls():
    """The CLI has no equivalent for the org lookup; the error says what to configure."""
    repo = _repo()
    with pytest.raises(IonscaleError) as excinfo:
        repo.get_tailnet_by_organization("1")
    assert excinfo.value.code == "unimplemented"
    assert "service_token" in excinfo.value.message


def test_cli_repo_revoke_account_builds_command():
    repo = _repo()
    with mock.patch.object(repo, "_run_command", return_value="") as run:
        repo.revoke_account("42", "7")
    assert run.call_args.args[0] == ["users", "revoke-account", "--external-id", "42", "--org", "7"]


# --- signal-driven sync ------------------------------------------------------


def _org_with_mesh(slug: str, owner: User, ionscale_repo, commit_callbacks) -> tuple[Organization, fakts_models.IonscaleLayer]:
    """An organization whose owner is already a member, with one mesh layer whose
    tailnet exists on the (fake) control plane. Recorded calls are cleared so the
    caller only sees what its own actions produce."""
    with commit_callbacks():
        # the org post_save signal makes the owner an admin member
        organization = Organization.objects.create(slug=slug, owner=owner)
    layer = fakts_models.IonscaleLayer.objects.create(
        organization=organization,
        name="Default",
        kind="ionscale",
        identifier=slug,
        tailnet_name=slug,
    )
    ionscale_repo.tailnets.append(Tailnet(id="1", name=slug, dns_name=slug, organization=str(organization.pk)))
    ionscale_repo.updated_policies.clear()
    return organization, layer


@pytest.mark.django_db
def test_membership_changes_resync_ionscale_layers(ionscale_repo, commit_callbacks):
    existing_user = User.objects.create(username="existing-user")
    organization, _ = _org_with_mesh("ionscale-sync-org", existing_user, ionscale_repo, commit_callbacks)

    new_user = User.objects.create(username="new-user")
    with commit_callbacks():
        membership = Membership.objects.create(user=new_user, organization=organization)

    assert len(ionscale_repo.updated_policies) == 1
    tailnet, policy = ionscale_repo.updated_policies[-1]
    assert tailnet == "ionscale-sync-org"
    assert set(policy["subs"]) == {str(existing_user.pk), str(new_user.pk)}

    ionscale_repo.updated_policies.clear()

    with commit_callbacks():
        membership.delete()

    assert len(ionscale_repo.updated_policies) == 1
    tailnet, policy = ionscale_repo.updated_policies[-1]
    assert tailnet == "ionscale-sync-org"
    assert set(policy["subs"]) == {str(existing_user.pk)}


@pytest.mark.django_db
def test_membership_sync_waits_for_commit(ionscale_repo, commit_callbacks):
    """Nothing reaches ionscale from inside the transaction."""
    existing_user = User.objects.create(username="existing-user")
    organization, _ = _org_with_mesh("ionscale-uncommitted", existing_user, ionscale_repo, commit_callbacks)

    Membership.objects.create(user=User.objects.create(username="u2"), organization=organization)

    assert ionscale_repo.updated_policies == []
    assert ionscale_repo.revoked_accounts == []


@pytest.mark.django_db
def test_membership_delete_revokes_access(ionscale_repo, commit_callbacks):
    owner = User.objects.create(username="owner")
    organization, _ = _org_with_mesh("ionscale-revoke-org", owner, ionscale_repo, commit_callbacks)
    member = User.objects.create(username="member")
    with commit_callbacks():
        membership = Membership.objects.create(user=member, organization=organization)
    ionscale_repo.updated_policies.clear()

    with commit_callbacks():
        membership.delete()

    # revoke first, then the subs rewrite
    assert ionscale_repo.revoked_accounts == [(str(member.pk), str(organization.pk))]
    assert len(ionscale_repo.updated_policies) == 1
    _, policy = ionscale_repo.updated_policies[0]
    assert set(policy["subs"]) == {str(owner.pk)}


@pytest.mark.django_db
def test_membership_delete_survives_control_plane_outage(ionscale_repo, commit_callbacks, caplog):
    owner = User.objects.create(username="owner")
    organization, _ = _org_with_mesh("ionscale-outage-org", owner, ionscale_repo, commit_callbacks)
    member = User.objects.create(username="member")
    with commit_callbacks():
        membership = Membership.objects.create(user=member, organization=organization)

    ionscale_repo.fail_with["revoke_account"] = IonscaleError("unavailable", "connection refused")
    ionscale_repo.fail_with["update_policy"] = IonscaleError("unavailable", "connection refused")

    membership_pk = membership.pk
    with caplog.at_level(logging.ERROR, logger="ionscale.sync"), commit_callbacks():
        membership.delete()  # must not raise

    assert not Membership.objects.filter(pk=membership_pk).exists()
    assert "reconcile_meshes" in caplog.text


@pytest.mark.django_db
def test_organization_delete_tears_down_tailnet_without_revoking(ionscale_repo, commit_callbacks):
    owner = User.objects.create(username="owner")
    organization, _ = _org_with_mesh("ionscale-doomed-org", owner, ionscale_repo, commit_callbacks)
    with commit_callbacks():
        Membership.objects.create(user=User.objects.create(username="member"), organization=organization)
    ionscale_repo.updated_policies.clear()

    with commit_callbacks():
        organization.delete()

    assert ionscale_repo.deleted_tailnets == [("ionscale-doomed-org", True)]
    # the cascade deleted two memberships; their revocations are pointless once
    # the whole tailnet is gone
    assert ionscale_repo.revoked_accounts == []
    assert ionscale_repo.updated_policies == []


@pytest.mark.django_db
def test_user_delete_revokes_in_every_organization(ionscale_repo, commit_callbacks):
    owner_a = User.objects.create(username="owner-a")
    owner_b = User.objects.create(username="owner-b")
    org_a, _ = _org_with_mesh("ionscale-org-a", owner_a, ionscale_repo, commit_callbacks)
    org_b, _ = _org_with_mesh("ionscale-org-b", owner_b, ionscale_repo, commit_callbacks)

    with commit_callbacks():
        member = User.objects.create(username="member")
        Membership.objects.create(user=member, organization=org_a)
        Membership.objects.create(user=member, organization=org_b)
    ionscale_repo.updated_policies.clear()
    ionscale_repo.revoked_accounts.clear()
    ionscale_repo.deleted_tailnets.clear()

    member_pk = member.pk
    with commit_callbacks():
        member.delete()

    assert sorted(ionscale_repo.revoked_accounts) == sorted(
        [(str(member_pk), str(org_a.pk)), (str(member_pk), str(org_b.pk))]
    )
    assert {t for t, _ in ionscale_repo.updated_policies} == {"ionscale-org-a", "ionscale-org-b"}


@pytest.mark.django_db
def test_deactivating_user_revokes_everywhere(ionscale_repo, commit_callbacks):
    owner = User.objects.create(username="owner")
    organization, _ = _org_with_mesh("ionscale-deactivate-org", owner, ionscale_repo, commit_callbacks)
    member = User.objects.create(username="member")
    with commit_callbacks():
        Membership.objects.create(user=member, organization=organization)

    with commit_callbacks():
        member.is_active = False
        member.save()

    assert ionscale_repo.revoked_accounts == [(str(member.pk), None)]

    # saving an already inactive user again is not a second revocation
    with commit_callbacks():
        member.save()
    assert len(ionscale_repo.revoked_accounts) == 1


@pytest.mark.django_db
def test_create_ionscale_layer_syncs_existing_members(ionscale_repo, commit_callbacks):
    first_user = User.objects.create(username="first-user")
    second_user = User.objects.create(username="second-user")
    with commit_callbacks():
        # owner is required; the org post_save signal makes ``first_user`` an admin
        # member, so only ``second_user``'s membership is created explicitly.
        organization = Organization.objects.create(slug="ionscale-create-org", owner=first_user)
        Membership.objects.create(user=second_user, organization=organization)
    # no layer existed yet, so the membership commits had nothing to push
    assert ionscale_repo.updated_policies == []

    layer = create_ionscale_layer(
        info=_info_for(first_user),
        input=cast(
            CreateIonscaleLayerInput,
            SimpleNamespace(organization_id=organization.pk, name="Default"),
        ),
    )

    assert len(ionscale_repo.created_tailnets) == 1
    assert ionscale_repo.created_tailnets[0].name == "ionscale-create-org"
    assert ionscale_repo.created_tailnets[0].organization == str(organization.pk)
    assert layer.tailnet_name == "ionscale-create-org"
    assert ionscale_repo.updated_policies == [
        ("ionscale-create-org", {"subs": [str(first_user.pk), str(second_user.pk)]}),
    ]


AUTH_KEYS_LIST_OUTPUT = (
    "ID                  KEY              EPHEMERAL  EXPIRED  EXPIRES_AT           TAGS                   \n"
    "221707112168816001  a1b2c3d4e5f6...  false      false    2026-09-22 20:15:00  tag:mesh-9,tag:app-1  \n"
    "221707112168816002  zzzzzzzzzzzz...  false      true     2026-03-01 10:00:00  tag:mesh-9             \n"
)


def test_create_auth_key_passes_expiry_in_seconds():
    repo = _repo()
    out = "\nGenerated new auth key\nBe sure to copy your new key below. It won't be shown in full again.\n\n  a1b2c3d4e5f6_secret\n"
    with mock.patch.object(repo, "_run_command", return_value=out) as run:
        assert repo.create_auth_key("tn", tags=["tag:mesh-9"], expiry_seconds=900) == "a1b2c3d4e5f6_secret"
    args = run.call_args.args[0]
    assert args[args.index("--expiry") + 1] == "900s"


def test_delete_auth_key_resolves_the_id_by_key_prefix():
    repo = _repo()
    with mock.patch.object(repo, "_run_command", side_effect=[AUTH_KEYS_LIST_OUTPUT, "Auth key deleted."]) as run:
        assert repo.delete_auth_key("tn", "a1b2c3d4e5f6_secret") is True
    assert run.call_args_list[0].args[0] == ["auth-keys", "list", "--tailnet", "tn"]
    assert run.call_args_list[1].args[0] == ["auth-keys", "delete", "--id", "221707112168816001"]


def test_delete_auth_key_of_an_unknown_key_is_a_noop():
    repo = _repo()
    with mock.patch.object(repo, "_run_command", return_value=AUTH_KEYS_LIST_OUTPUT) as run:
        assert repo.delete_auth_key("tn", "nothere_secret") is False
    assert run.call_count == 1


def test_delete_machine():
    repo = _repo()
    with mock.patch.object(repo, "_run_command", return_value="Machine deleted.") as run:
        repo.delete_machine("221707112168816643")
    assert run.call_args.args[0] == ["machines", "delete", "--machine-id", "221707112168816643"]


def test_machine_list_reads_connected_as_live():
    """`machines list` prints LAST_SEEN "Connected" for live nodes."""
    out = (
        "ID                  TAILNET  NAME    IPv4          IPv6     AUTHORIZED  EPHEMERAL  VERSION  LAST_SEEN  TAGS  \n"
        "221707112168816643  tn       laptop  100.98.11.67  fd7a::1  true        false      1.96.4   Connected  tag:app-1  \n"
    )
    (machine,) = _repo()._parse_machine_list_output(out)
    assert machine.connected is True

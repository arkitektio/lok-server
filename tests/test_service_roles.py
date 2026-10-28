"""A service's scopes and roles, as its hub registers them, and the roles lok hands out by itself:
each admin role to the organization's owner and admins, each default role to every full member."""

import pytest

from fakts import base_models
from fakts.services import hubs
from karakter import models
from karakter.roles import ADMIN
from tests import factories

SCOPES = [
    base_models.Scope(key="mikro_read", description="See images."),
    base_models.Scope(key="mikro_write", description="Change images."),
    base_models.Scope(key="mikro_all", description="Do everything in Mikro.", is_all_scope=True),
]
ROLES = [
    base_models.Role(key="mikro_admin", description="Full control of Mikro.", is_admin_role=True),
    base_models.Role(key="mikro_editor", description="Upload and change images.", is_default_role=True),
    base_models.Role(key="mikro_annotator", description="Annotate images."),
]


def _register(organization, scopes=SCOPES, roles=ROLES):
    manifest = base_models.HubManifest(
        identifier="com.example.hub",
        instances=[
            base_models.InstanceRequest(
                identifier="mikro",
                manifest=base_models.ServiceManifest(identifier="live.arkitekt.mikro", version="1.0.0", scopes=scopes, roles=roles),
            )
        ],
    )
    return hubs.create_hub_from_manifest(manifest, organization)


def _roles(membership) -> set[str]:
    return set(membership.roles.values_list("identifier", flat=True))


def _admin_role(organization):
    return models.Role.objects.get(identifier=ADMIN, organization=organization)


@pytest.mark.django_db
def test_the_flags_and_descriptions_are_registered_as_declared():
    organization = factories.make_organization()
    _register(organization)
    assert models.Scope.objects.get(organization=organization, identifier="mikro_all").is_all_scope
    admin = models.Role.objects.get(organization=organization, identifier="mikro_admin")
    assert admin.is_admin_role and not admin.is_default_role
    assert models.Role.objects.get(organization=organization, identifier="mikro_editor").is_default_role

    rewritten = [base_models.Scope(key="mikro_read", description="See images and their files."), *SCOPES[1:]]
    _register(organization, scopes=rewritten)
    assert models.Scope.objects.get(organization=organization, identifier="mikro_read").description == "See images and their files."


@pytest.mark.django_db
def test_what_a_service_stops_declaring_goes_but_lok_keeps_its_own():
    organization = factories.make_organization()
    _register(organization)
    _register(organization, scopes=[SCOPES[0], SCOPES[2]], roles=ROLES[:2])
    assert not models.Scope.objects.filter(organization=organization, identifier="mikro_write").exists()
    assert not models.Role.objects.filter(organization=organization, identifier="mikro_annotator").exists()
    assert models.Role.objects.filter(organization=organization, identifier=ADMIN).exists()
    assert models.Scope.objects.filter(organization=organization, identifier="openid").exists()


@pytest.mark.django_db
def test_a_scope_an_app_holds_stays_until_it_lets_go():
    organization = factories.make_organization()
    _register(organization)
    client = factories.make_client(membership=models.Membership.objects.get(user=organization.owner, organization=organization))
    client.scopes.add(models.Scope.objects.get(organization=organization, identifier="mikro_write"))
    _register(organization, scopes=[SCOPES[0], SCOPES[2]])
    assert models.Scope.objects.filter(organization=organization, identifier="mikro_write").exists()


@pytest.mark.django_db
def test_lok_makes_only_admin_and_guest():
    organization = factories.make_organization()
    assert set(organization.roles.values_list("identifier", flat=True)) == {ADMIN, "guest"}


@pytest.mark.django_db
def test_the_owner_and_the_admins_hold_every_admin_role_and_members_the_default_one(django_capture_on_commit_callbacks):
    organization = factories.make_organization()
    with django_capture_on_commit_callbacks(execute=True):
        admin = factories.make_membership(organization=organization)
        member = factories.make_membership(organization=organization)
    admin.roles.add(_admin_role(organization))
    _register(organization)

    owner = models.Membership.objects.get(user=organization.owner, organization=organization)
    assert {"mikro_admin", "mikro_editor"} <= _roles(owner)
    assert {"mikro_admin", "mikro_editor"} <= _roles(admin)
    assert _roles(member) == {"mikro_editor"}


@pytest.mark.django_db
def test_a_new_member_gets_the_default_role_and_a_guest_none(django_capture_on_commit_callbacks):
    organization = factories.make_organization()
    _register(organization)
    with django_capture_on_commit_callbacks(execute=True):
        member = factories.make_membership(organization=organization)
    assert _roles(member) == {"mikro_editor"}

    with django_capture_on_commit_callbacks(execute=True):
        guest = factories.make_membership(organization=organization)
        models.CollaborationGrant.objects.create(membership=guest, instance=_instance(organization), collaboration_id="12", granted_by=organization.owner)
    assert _roles(guest) == set()


def _instance(organization):
    from fakts import models as fakts_models

    return fakts_models.ServiceInstance.objects.get(organization=organization, instance_id="mikro")


@pytest.mark.django_db
def test_promotion_gives_the_admin_roles_and_demotion_takes_back_only_what_lok_gave(django_capture_on_commit_callbacks):
    organization = factories.make_organization()
    _register(organization)
    with django_capture_on_commit_callbacks(execute=True):
        member = factories.make_membership(organization=organization)
        trusted = factories.make_membership(organization=organization)
    trusted.roles.add(models.Role.objects.get(organization=organization, identifier="mikro_admin"))  # handed by an admin: not lok's to take back

    member.roles.add(_admin_role(organization))
    assert "mikro_admin" in _roles(member)
    member.roles.remove(_admin_role(organization))
    assert _roles(member) == {"mikro_editor"}

    trusted.roles.add(_admin_role(organization))
    trusted.roles.remove(_admin_role(organization))
    assert "mikro_admin" in _roles(trusted)


@pytest.mark.django_db
def test_a_default_role_an_admin_took_stays_taken(django_capture_on_commit_callbacks):
    organization = factories.make_organization()
    _register(organization)
    with django_capture_on_commit_callbacks(execute=True):
        member = factories.make_membership(organization=organization)
    editor = models.Role.objects.get(organization=organization, identifier="mikro_editor")
    member.roles.remove(editor)
    _register(organization)
    assert "mikro_editor" not in _roles(member)

    member.roles.add(editor)  # an admin gives it back
    _register(organization)
    assert "mikro_editor" in _roles(member)
    assert not member.withheld_roles.exists()


@pytest.mark.django_db
def test_a_new_owner_holds_every_admin_role(django_capture_on_commit_callbacks):
    organization = factories.make_organization()
    _register(organization)
    with django_capture_on_commit_callbacks(execute=True):
        heir = factories.make_membership(organization=organization)
    organization.owner = heir.user
    organization.save()
    assert "mikro_admin" in _roles(heir)


def test_a_pin_never_has_to_name_the_oidc_scopes():
    pin = base_models.MandatePin(identifier="plugin_engine", scopes=["kabinet_read"])
    manifest = base_models.Manifest(identifier="plugin_engine", version="1.0.0", scopes=["openid", "kabinet_read"])
    pin.check(manifest)
    with pytest.raises(Exception, match="kabinet_deploy"):
        pin.check(base_models.Manifest(identifier="plugin_engine", version="1.0.0", scopes=["openid", "kabinet_deploy"]))


def test_a_requirement_is_the_service_whatever_key_it_is_listed_under():
    """Orkestrator lists the object store as `datalayer`, the Python clients as `s3`: one service."""
    issuer = base_models.MandatePin(requirements=[{"key": "datalayer", "service": "live.arkitekt.s3"}])
    child = base_models.MandatePin(requirements=[{"key": "s3", "service": "live.arkitekt.s3"}])
    child.assert_within(issuer)
    issuer.check(base_models.Manifest(identifier="app", version="1.0.0", requirements=[base_models.Requirement(key="s3", service="live.arkitekt.s3")]))

    stranger = base_models.MandatePin(requirements=[{"key": "s3", "service": "live.arkitekt.mikro"}])
    with pytest.raises(base_models.MandatePinMismatch, match="exceed the issuer's"):
        stranger.assert_within(issuer)

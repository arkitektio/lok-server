"""Granting collaborations through an invite: guests, with roles that hold only inside them.

A collaboration invite makes whoever accepts it a guest of the organization: a scoped
membership with no roles that reaches only the granted collaborations of its service
instances (``karakter.collaboration_grants``). These tests pin the rules that keep such a
membership from ever acting as a full one (no roles, nothing of the organization
in the management API, never left as a role-less full member when its grants go)
and what a guest's token says: the collaborations of the instances it is minted for, each
with roles that never exceed what the granter holds now.
"""

from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from api.management.schema import schema as management_schema
from authapp.server import SCOPED_ACCESS_TOKEN_EXPIRES_IN, access_token_expires_in
from authapp.token_generators import MyJWTBearerTokenGenerator
from fakts import models as fakts_models
from karakter import models, collaboration_grants
from karakter.invites import InviteError, redeem_invite
from tests import factories
from tests.conftest import build_auth_context

CREATE_INVITE = """
    mutation ($input: CreateInviteInput!) {
        createInvite(input: $input) { id token collaborations { instance collaboration roles } maxUses uses }
    }
"""

ORGANIZATION = """
    query ($id: ID!) {
        organization(id: $id) {
            id name deeplinkApps { protocol }
            users { id }
            memberships { id user { id } isScoped collaborationGrants { instanceId collaborationId roles } }
        }
    }
"""

REVOKE = """
    mutation ($input: RevokeCollaborationGrantInput!) { revokeCollaborationGrant(input: $input) }
"""

MEMBER_ROLES = ("user", "editor", "viewer")


def _generator():
    return MyJWTBearerTokenGenerator(issuer="https://lok.test")


def _role(organization, identifier):
    return models.Role.objects.get_or_create(identifier=identifier, organization=organization)[0]


def _member(organization, roles=MEMBER_ROLES):
    member = factories.make_membership(organization=organization)
    member.roles.add(*[_role(organization, r) for r in roles])
    return member


def _org_with_member(roles=MEMBER_ROLES):
    """An organization with a hub, a service instance on it, and a member holding ``roles``."""
    organization = factories.make_organization()
    hub = factories.make_hub(organization=organization)
    instance = factories.make_service_instance(hub=hub)
    return organization, _member(organization, roles), instance


def _entry(instance, collaboration="12", roles=()):
    return {"instance": str(instance.pk), "collaboration": collaboration, "roles": list(roles)}


def _collaboration_invite(organization, created_by, entries, **kw):
    return models.Invite.objects.create(
        created_by=created_by,
        created_for=organization,
        shared_collaborations=collaboration_grants.normalize_collaborations(entries, organization),
        **kw,
    )


def _client(membership, *instances):
    """A fakts client of ``membership`` whose tokens are minted for ``instances``."""
    client = factories.make_client(membership=membership)
    for n, instance in enumerate(instances):
        fakts_models.ServiceInstanceMapping.objects.create(client=client, instance=instance, key=f"svc{n}")
    return client


def _claims(membership, *instances):
    return _generator().get_extra_claims(_client(membership, *instances), "authorization_code", membership, None)


# --------------------------------------------------------------------------- #
# invite entries
# --------------------------------------------------------------------------- #


def test_entries_normalize_and_the_same_collaboration_twice_holds_both_roles():
    entries = collaboration_grants.normalize_collaborations(
        [
            {"instance": 7, "collaboration": "12", "roles": ["viewer"]},
            {"instance": "7", "collaboration": "12", "roles": ["editor", "viewer"]},
            {"instance": "7", "collaboration": "9"},
            {"instance": "8", "collaboration": "12", "roles": []},
        ]
    )
    assert entries == [
        {"instance": "7", "collaboration": "12", "roles": ["editor", "viewer"]},
        {"instance": "7", "collaboration": "9", "roles": []},
        {"instance": "8", "collaboration": "12", "roles": []},
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "7/12",
        {"collaboration": "12"},
        {"instance": "x", "collaboration": "12"},
        {"instance": True, "collaboration": "12"},
        {"instance": "7"},
        {"instance": "7", "collaboration": "a b"},
        {"instance": "7", "collaboration": "x" * 201},
        {"instance": "7", "collaboration": "12", "roles": "editor"},
        {"instance": "7", "collaboration": "12", "roles": [""]},
        {"instance": "7", "collaboration": "12", "roles": [3]},
    ],
)
def test_malformed_entries_are_refused(bad):
    with pytest.raises(ValidationError):
        collaboration_grants.normalize_collaborations([bad])


def test_an_invite_grants_at_least_one_and_at_most_fifty_collaborations():
    with pytest.raises(ValidationError):
        collaboration_grants.normalize_collaborations([])
    many = [{"instance": "7", "collaboration": str(i)} for i in range(collaboration_grants.MAX_COLLABORATIONS_PER_INVITE + 1)]
    with pytest.raises(ValidationError, match="at most"):
        collaboration_grants.normalize_collaborations(many)


@pytest.mark.django_db
def test_only_the_organizations_own_instances_and_roles_can_be_granted():
    organization, _, instance = _org_with_member()
    foreign = factories.make_service_instance()  # on another organization's hub

    with pytest.raises(ValidationError, match="Not a service instance of this organization"):
        collaboration_grants.normalize_collaborations([{"instance": "999999999", "collaboration": "1"}], organization)
    with pytest.raises(ValidationError, match="Not a service instance of this organization"):
        collaboration_grants.normalize_collaborations([_entry(foreign)], organization)
    with pytest.raises(ValidationError, match="Not a role of this organization: wizard"):
        collaboration_grants.normalize_collaborations([_entry(instance, roles=["editor", "wizard"])], organization)
    # A role of another organization is no role here either.
    _role(foreign.hub.organization, "curator")
    with pytest.raises(ValidationError, match="curator"):
        collaboration_grants.normalize_collaborations([_entry(instance, roles=["curator"])], organization)

    assert collaboration_grants.normalize_collaborations([_entry(instance, roles=["editor"])], organization) == [_entry(instance, roles=["editor"])]


# --------------------------------------------------------------------------- #
# accepting
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_accepting_a_collaboration_invite_makes_a_guest_without_roles():
    organization, member, instance = _org_with_member()
    invite = _collaboration_invite(organization, member.user, [_entry(instance, "12", ["editor"]), _entry(instance, "9")])
    visitor = factories.make_user()

    membership = redeem_invite(invite, visitor)

    assert membership.is_scoped
    assert not membership.roles.exists()
    grants = sorted(membership.collaboration_grants.values_list("instance_id", "collaboration_id", "roles", "granted_by_id", "granted_through_id"))
    assert grants == [
        (instance.pk, "12", ["editor"], member.user_id, invite.pk),
        (instance.pk, "9", [], member.user_id, invite.pk),
    ]
    invite.refresh_from_db()
    assert invite.uses == 1
    assert invite.status == models.Invite.Status.PENDING  # open for the next person


@pytest.mark.django_db
def test_a_collaboration_invite_closes_after_max_uses():
    organization, member, instance = _org_with_member()
    invite = _collaboration_invite(organization, member.user, [_entry(instance)], max_uses=2)

    redeem_invite(invite, factories.make_user())
    redeem_invite(invite, factories.make_user())

    invite.refresh_from_db()
    assert invite.status == models.Invite.Status.ACCEPTED
    with pytest.raises(InviteError):
        redeem_invite(invite, factories.make_user())


@pytest.mark.django_db
def test_a_full_member_accepting_a_collaboration_invite_gets_nothing_and_spends_nothing():
    organization, member, instance = _org_with_member()
    invite = _collaboration_invite(organization, member.user, [_entry(instance)], max_uses=1)
    colleague = _member(organization, ("guest",))

    membership = redeem_invite(invite, colleague.user)

    assert membership == colleague
    assert not membership.is_scoped
    invite.refresh_from_db()
    assert invite.uses == 0
    assert invite.status == models.Invite.Status.PENDING


@pytest.mark.django_db
def test_granting_a_collaboration_again_takes_the_union_of_its_roles():
    organization, karl, instance = _org_with_member()
    anna = _member(organization)
    visitor = factories.make_user()
    redeem_invite(_collaboration_invite(organization, karl.user, [_entry(instance, "12", ["viewer"])]), visitor)
    second = _collaboration_invite(organization, anna.user, [_entry(instance, "12", ["editor"]), _entry(instance, "9")])

    membership = redeem_invite(second, visitor)

    grants = {g.collaboration_id: g for g in membership.collaboration_grants.all()}
    assert grants["12"].roles == ["editor", "viewer"]
    # The grant that grew takes its new granter and invite.
    assert (grants["12"].granted_by_id, grants["12"].granted_through_id) == (anna.user_id, second.pk)
    assert grants["9"].roles == []


@pytest.mark.django_db
def test_re_accepting_what_one_already_has_spends_nothing():
    organization, member, instance = _org_with_member()
    visitor = factories.make_user()
    redeem_invite(_collaboration_invite(organization, member.user, [_entry(instance, "12", ["editor", "viewer"])]), visitor)
    invite = _collaboration_invite(organization, member.user, [_entry(instance, "12", ["viewer"])], max_uses=1)

    membership = redeem_invite(invite, visitor)  # fewer roles never narrow a grant

    invite.refresh_from_db()
    assert invite.uses == 0 and invite.status == models.Invite.Status.PENDING
    assert membership.collaboration_grants.get().roles == ["editor", "viewer"]


@pytest.mark.django_db
def test_an_instance_gone_since_the_invite_was_made_grants_nothing_and_makes_no_member():
    organization, member, instance = _org_with_member()
    invite = _collaboration_invite(organization, member.user, [_entry(instance)])
    instance.delete()
    visitor = factories.make_user()

    with pytest.raises(InviteError, match="Not a service instance"):
        redeem_invite(invite, visitor)

    assert not models.Membership.objects.filter(user=visitor, organization=organization).exists()


@pytest.mark.django_db
def test_an_ordinary_invite_makes_a_guest_a_full_member():
    organization, member, instance = _org_with_member()
    visitor = factories.make_user()
    scoped = redeem_invite(_collaboration_invite(organization, member.user, [_entry(instance)]), visitor)
    full_invite = models.Invite.objects.create(created_by=organization.owner, created_for=organization)

    membership = redeem_invite(full_invite, visitor)

    assert membership.pk == scoped.pk
    assert not membership.is_scoped
    assert not models.CollaborationGrant.objects.filter(membership=membership).exists()


@pytest.mark.django_db
def test_declining_a_collaboration_invite_does_not_close_it_for_others():
    organization, member, instance = _org_with_member()
    invite = _collaboration_invite(organization, member.user, [_entry(instance)])
    invite.decline(factories.make_user())
    invite.refresh_from_db()
    assert invite.status == models.Invite.Status.PENDING


# --------------------------------------------------------------------------- #
# the invariants: no roles, never a role-less full member
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_guest_cannot_be_given_roles_without_being_upgraded():
    organization, member, instance = _org_with_member()
    membership = redeem_invite(_collaboration_invite(organization, member.user, [_entry(instance)]), factories.make_user())
    role = _role(organization, "admin")

    with pytest.raises(ValidationError), transaction.atomic():
        membership.roles.add(role)

    collaboration_grants.upgrade_to_full(membership)
    assert not membership.collaboration_grants.exists()
    membership.roles.add(role)
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert not membership.is_scoped


@pytest.mark.django_db
def test_taking_back_the_last_grant_removes_the_membership():
    organization, member, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(organization, member.user, [_entry(instance, "1"), _entry(instance, "2")]), factories.make_user()
    )
    first, second = list(membership.collaboration_grants.order_by("id"))

    assert collaboration_grants.revoke_grant(first) is False
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert collaboration_grants.revoke_grant(second) is True
    assert not models.Membership.objects.filter(pk=membership.pk).exists()


@pytest.mark.django_db
def test_revoking_an_invite_takes_back_everything_it_granted():
    organization, member, instance = _org_with_member()
    invite = _collaboration_invite(organization, member.user, [_entry(instance)])
    a = redeem_invite(invite, factories.make_user())
    b = redeem_invite(invite, factories.make_user())

    assert collaboration_grants.revoke_invite_grants(invite) == 2
    assert not models.Membership.objects.filter(pk__in=[a.pk, b.pk]).exists()


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_guests_token_lists_the_collaborations_of_its_own_instances_only():
    organization, member, mikro = _org_with_member()
    other = factories.make_service_instance(hub=mikro.hub)
    unmapped = factories.make_service_instance(hub=mikro.hub)
    invite = _collaboration_invite(
        organization,
        member.user,
        [_entry(mikro, "12", ["editor"]), _entry(mikro, "9"), _entry(other, "3", ["viewer"]), _entry(unmapped, "4", ["editor"])],
    )
    membership = redeem_invite(invite, factories.make_user())
    client = _client(membership, mikro, other)

    claims = _generator().get_extra_claims(client, "authorization_code", membership, None)

    assert claims["org"] == str(organization.pk)
    assert claims["roles"] == []
    assert "objects" not in claims
    assert claims["collabs"] == {str(mikro.pk): {"12": ["editor"], "9": []}, str(other.pk): {"3": ["viewer"]}}
    assert list(claims["collabs"]) == sorted(claims["collabs"])
    assert _generator().get_audiences(client, membership, None) == ["lok", *sorted([str(mikro.pk), str(other.pk)])]
    assert access_token_expires_in(client, "authorization_code") <= SCOPED_ACCESS_TOKEN_EXPIRES_IN


@pytest.mark.django_db
def test_a_token_minted_for_no_instance_lists_no_collaborations():
    organization, member, instance = _org_with_member()
    membership = redeem_invite(_collaboration_invite(organization, member.user, [_entry(instance, roles=["editor"])]), factories.make_user())

    assert _claims(membership)["collabs"] == {}  # grants nothing; never "everything"
    assert collaboration_grants.live_collaborations(membership, ["lok", "not-an-instance"]) == {}


@pytest.mark.django_db
def test_a_guests_roles_are_cut_to_what_the_granter_holds_now():
    organization, karl, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(organization, karl.user, [_entry(instance, "12", ["editor", "viewer"])]), factories.make_user()
    )
    assert _claims(membership, instance)["collabs"] == {str(instance.pk): {"12": ["editor", "viewer"]}}

    karl.roles.remove(_role(organization, "editor"))
    assert _claims(membership, instance)["collabs"] == {str(instance.pk): {"12": ["viewer"]}}

    karl.roles.set([_role(organization, "user")])
    # Still a full member: the collaboration stays, view only.
    assert _claims(membership, instance)["collabs"] == {str(instance.pk): {"12": []}}

    # And a role given back to the granter comes back to the guest.
    karl.roles.add(_role(organization, "editor"))
    assert _claims(membership, instance)["collabs"] == {str(instance.pk): {"12": ["editor"]}}


@pytest.mark.django_db
def test_a_granter_who_left_takes_their_grants_along():
    organization, karl, instance = _org_with_member()
    anna = _member(organization)
    visitor = factories.make_user()
    redeem_invite(_collaboration_invite(organization, karl.user, [_entry(instance, "1", ["editor"])]), visitor)
    membership = redeem_invite(_collaboration_invite(organization, anna.user, [_entry(instance, "2", ["viewer"])]), visitor)

    karl.delete()

    assert _claims(membership, instance)["collabs"] == {str(instance.pk): {"2": ["viewer"]}}
    anna.delete()
    assert _claims(membership, instance)["collabs"] == {}  # still a guest, of nothing


@pytest.mark.django_db
def test_a_granter_who_is_only_a_guest_or_inactive_grants_nothing():
    organization, karl, instance = _org_with_member()
    membership = redeem_invite(_collaboration_invite(organization, karl.user, [_entry(instance, "1", ["editor"])]), factories.make_user())

    karl.user.is_active = False
    karl.user.save(update_fields=["is_active"])
    assert _claims(membership, instance)["collabs"] == {}

    karl.user.is_active = True
    karl.user.save(update_fields=["is_active"])
    # Karl's own membership turns into a guest's: he no longer acts for the organization.
    karl.roles.clear()
    collaboration_grants.grant_collaborations(karl, [_entry(instance, "99")])
    assert _claims(membership, instance)["collabs"] == {}


@pytest.mark.django_db
def test_a_grant_whose_granter_is_gone_grants_nothing():
    organization, karl, instance = _org_with_member()
    membership = redeem_invite(_collaboration_invite(organization, karl.user, [_entry(instance)]), factories.make_user())
    membership.collaboration_grants.update(granted_by=None)

    assert _claims(membership, instance)["collabs"] == {}


@pytest.mark.django_db
def test_expired_grants_leave_the_token_but_it_stays_scoped():
    organization, member, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(organization, member.user, [_entry(instance, "1"), _entry(instance, "2", ["editor"])]), factories.make_user()
    )
    membership.collaboration_grants.filter(collaboration_id="1").update(expires_at=timezone.now() - timedelta(minutes=1))
    assert _claims(membership, instance)["collabs"] == {str(instance.pk): {"2": ["editor"]}}

    membership.collaboration_grants.update(expires_at=timezone.now() - timedelta(minutes=1))
    claims = _claims(membership, instance)

    assert claims["roles"] == []
    assert claims["collabs"] == {}  # grants nothing; never "everything"


@pytest.mark.django_db
def test_a_full_members_token_is_unchanged():
    organization, member, instance = _org_with_member(roles=("user",))
    claims = _claims(member, instance)
    assert claims["org"] == str(organization.pk)
    assert "collabs" not in claims and "objects" not in claims
    assert claims["roles"] == ["user"]


# --------------------------------------------------------------------------- #
# the management API
# --------------------------------------------------------------------------- #


def _api_setup():
    organization, member, instance = _org_with_member()
    member_context = build_auth_context(member.user, organization, factories.make_client(membership=member), roles=MEMBER_ROLES)
    guest = _member(organization, ("guest",))
    guest_context = build_auth_context(guest.user, organization, factories.make_client(membership=guest), roles=("guest",))
    visitor = factories.make_user()
    return organization, member, instance, member_context, guest_context, visitor


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_members_grant_collaborations_and_the_visitor_sees_only_the_org_shell():
    organization, member, instance, member_context, guest_context, visitor = await sync_to_async(_api_setup)()
    collaboration = {"instance": str(instance.pk), "collaboration": "12", "roles": ["editor"]}

    created = await management_schema.execute(
        CREATE_INVITE,
        variable_values={"input": {"organization": str(organization.pk), "collaborations": [collaboration]}},
        context_value=member_context,
    )
    assert not created.errors, created.errors
    assert created.data["createInvite"]["collaborations"] == [collaboration]

    # A guest may not grant.
    refused = await management_schema.execute(
        CREATE_INVITE,
        variable_values={"input": {"organization": str(organization.pk), "collaborations": [collaboration]}},
        context_value=guest_context,
    )
    assert refused.errors

    # Nor may anyone grant a role the organization does not have.
    unknown = await management_schema.execute(
        CREATE_INVITE,
        variable_values={"input": {"organization": str(organization.pk), "collaborations": [{**collaboration, "roles": ["wizard"]}]}},
        context_value=member_context,
    )
    assert unknown.errors and "wizard" in unknown.errors[0].message

    # The visitor accepts (before they have a membership, so the context is built after).
    invite = await sync_to_async(models.Invite.objects.get)(pk=created.data["createInvite"]["id"])
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    visitor_context = await sync_to_async(
        lambda: build_auth_context(visitor, organization, factories.make_client(membership=membership), roles=())
    )()

    seen = await management_schema.execute(
        ORGANIZATION, variable_values={"id": str(organization.pk)}, context_value=visitor_context
    )
    assert not seen.errors, seen.errors
    org = seen.data["organization"]
    assert org["name"] == organization.name and org["deeplinkApps"]
    assert org["users"] == []
    assert [m["user"]["id"] for m in org["memberships"]] == [str(visitor.pk)]
    assert org["memberships"][0]["collaborationGrants"] == [{"instanceId": str(instance.pk), "collaborationId": "12", "roles": ["editor"]}]

    # A full member sees the visitor among the memberships, marked as scoped.
    as_member = await management_schema.execute(
        ORGANIZATION, variable_values={"id": str(organization.pk)}, context_value=member_context
    )
    scoped = [m for m in as_member.data["organization"]["memberships"] if m["user"]["id"] == str(visitor.pk)]
    assert scoped and scoped[0]["isScoped"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_whoever_granted_can_take_it_back():
    organization, member, instance, member_context, _, visitor = await sync_to_async(_api_setup)()
    invite = await sync_to_async(_collaboration_invite)(organization, member.user, [_entry(instance)])
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    grant = await sync_to_async(membership.collaboration_grants.get)()

    result = await management_schema.execute(
        REVOKE, variable_values={"input": {"id": str(grant.pk)}}, context_value=member_context
    )
    assert not result.errors, result.errors
    assert not await sync_to_async(models.Membership.objects.filter(pk=membership.pk).exists)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_guest_cannot_take_a_grant_back():
    organization, member, instance, _, guest_context, visitor = await sync_to_async(_api_setup)()
    invite = await sync_to_async(_collaboration_invite)(organization, member.user, [_entry(instance)])
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    grant = await sync_to_async(membership.collaboration_grants.get)()

    result = await management_schema.execute(
        REVOKE, variable_values={"input": {"id": str(grant.pk)}}, context_value=guest_context
    )
    assert result.errors
    assert await sync_to_async(models.CollaborationGrant.objects.filter(pk=grant.pk).exists)()


# --------------------------------------------------------------------------- #
# deletes that cascade through grants
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_deleting_a_guests_membership_runs_its_side_effects_once(monkeypatch):
    import ionscale.sync

    calls = []
    monkeypatch.setattr(ionscale.sync, "schedule_member_removal", lambda *args: calls.append(args))
    organization, member, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(organization, member.user, [_entry(instance, "1"), _entry(instance, "2")]), factories.make_user()
    )

    membership.delete()

    assert calls == [(membership.user_id, organization.pk)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("what", ["user", "organization"])
def test_deleting_the_user_or_organization_of_a_guest_works(what):
    organization, member, instance = _org_with_member()
    visitor = factories.make_user()
    membership = redeem_invite(_collaboration_invite(organization, member.user, [_entry(instance)]), visitor)

    (visitor if what == "user" else organization).delete()

    assert not models.Membership.objects.filter(pk=membership.pk).exists()
    assert not models.CollaborationGrant.objects.filter(membership_id=membership.pk).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("what", ["instance", "hub"])
def test_deleting_the_instance_or_its_hub_removes_a_guest_left_with_nothing(what):
    organization, member, instance = _org_with_member()
    membership = redeem_invite(_collaboration_invite(organization, member.user, [_entry(instance)]), factories.make_user())

    (instance if what == "instance" else instance.hub).delete()

    assert not models.Membership.objects.filter(pk=membership.pk).exists()
    assert models.Membership.objects.filter(pk=member.pk).exists()


# --------------------------------------------------------------------------- #
# the migration away from object grants
# --------------------------------------------------------------------------- #

BEFORE = [("karakter", "0004_object_grants")]
AFTER = [("karakter", "0005_collaboration_grants")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def back_to_latest():
    yield
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_the_migration_drops_object_grants_without_leaving_full_members_behind(back_to_latest):
    apps = _migrate(BEFORE)
    User = apps.get_model("karakter", "User")
    Organization = apps.get_model("karakter", "Organization")
    Membership = apps.get_model("karakter", "Membership")
    Invite = apps.get_model("karakter", "Invite")
    ObjectGrant = apps.get_model("karakter", "ObjectGrant")
    Hub = apps.get_model("fakts", "Hub")

    owner = User.objects.create(username="migrating-owner", email="o@example.com")
    visitor = User.objects.create(username="migrating-visitor", email="v@example.com")
    org = Organization.objects.create(owner=owner, slug="migrating", name="Migrating")
    full = Membership.objects.create(user=owner, organization=org)
    scoped = Membership.objects.create(user=visitor, organization=org)
    hub = Hub.objects.create(name="h", organization=org, identifier="h", creator=owner, token="hub-token")
    object_invite = Invite.objects.create(created_by=owner, created_for=org, shared_objects=[f"{hub.pk}/@mikro/image:1"])
    ordinary = Invite.objects.create(created_by=owner, created_for=org)
    ObjectGrant.objects.create(membership=scoped, hub=hub, structure="@mikro/image", object_id="1", granted_by=owner)

    _migrate(AFTER)

    assert "karakter_objectgrant" not in connection.introspection.table_names()
    assert "karakter_collaborationgrant" in connection.introspection.table_names()
    # The guest is gone rather than left as a role-less full member; the member stays.
    assert not models.Membership.objects.filter(pk=scoped.pk).exists()
    assert models.Membership.objects.filter(pk=full.pk).exists()
    # The object invite cannot be redeemed as an ordinary one; the ordinary one is untouched.
    assert models.Invite.objects.get(pk=object_invite.pk).status == models.Invite.Status.CANCELLED
    assert models.Invite.objects.get(pk=ordinary.pk).status == models.Invite.Status.PENDING

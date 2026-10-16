"""Sharing single objects through an invite: scoped memberships.

An object invite makes whoever accepts it a member of the organization that
reaches only the shared objects (``karakter.object_grants``). These tests pin the
rules that keep such a membership from ever acting as a full one: no roles, no
`org` in its tokens, nothing of the organization in the management API, and no
way to be left as a role-less full member when its grants go.
"""

from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.management.schema import schema as management_schema
from authapp.server import SCOPED_ACCESS_TOKEN_EXPIRES_IN, access_token_expires_in
from authapp.token_generators import MyJWTBearerTokenGenerator
from karakter import models, object_grants
from karakter.invites import InviteError, redeem_invite
from tests import factories
from tests.conftest import build_auth_context

CREATE_INVITE = """
    mutation ($input: CreateInviteInput!) {
        createInvite(input: $input) { id token objects maxUses uses }
    }
"""

ACCEPT_INVITE = """
    mutation ($input: AcceptInviteInput!) {
        acceptInvite(input: $input) { id isScoped roles { identifier } objectGrants { grant } }
    }
"""

ORGANIZATION = """
    query ($id: ID!) {
        organization(id: $id) {
            id name deeplinkApps { protocol }
            users { id }
            memberships { id user { id } isScoped }
        }
    }
"""

REVOKE = """
    mutation ($input: RevokeObjectGrantInput!) { revokeObjectGrant(input: $input) }
"""


def _generator():
    return MyJWTBearerTokenGenerator(issuer="https://lok.test")


def _org_with_member(role="user"):
    organization = factories.make_organization()
    factories.make_hub(organization=organization)
    member = factories.make_membership(organization=organization)
    member.roles.add(models.Role.objects.get_or_create(identifier=role, organization=organization)[0])
    return organization, member


def _hub(organization):
    return str(organization.hubs.order_by("id").first().pk)


def _object_invite(organization, created_by, objects=("@mikro/image:42",), **kw):
    """An object invite; the objects are given without their hub and placed on the organization's hub."""
    hub = _hub(organization)
    return models.Invite.objects.create(
        created_by=created_by,
        created_for=organization,
        shared_objects=object_grants.normalize_grants([f"{hub}/{o}" for o in objects], organization),
        **kw,
    )


def _live(membership):
    """The live grants of the membership's organization hub, as a token lists them."""
    return sorted(object_grants.live_grants_by_hub(membership).get(_hub(membership.organization), []))


# --------------------------------------------------------------------------- #
# grant strings
# --------------------------------------------------------------------------- #


def test_grant_strings_parse_and_print_canonically():
    ref = object_grants.parse_grant("3/@mikro/image:42#rois")
    assert (ref.hub, ref.structure, ref.object_id, ref.reach) == ("3", "@mikro/image", "42", "rois")
    assert str(ref) == "3/@mikro/image:42#rois"
    assert ref.scoped == "@mikro/image:42#rois"
    # `self` is the default and is left out.
    assert str(object_grants.parse_grant("3/@mikro/image:42#self")) == "3/@mikro/image:42"
    assert object_grants.parse_grant("3/@mikro/arraydataset:7").reach == "self"


@pytest.mark.parametrize("bad", ["@mikro/image:1", "x/@mikro/image:1", "3/mikro/image:1", "3/@mikro/image", "3/@mikro/image:", "3/@mikro/image:1#", "3/@Mikro/image:1", "3/@mikro/image:1#ROIs", "3/@mikro/image:a b"])
def test_malformed_grant_strings_are_refused(bad):
    with pytest.raises(ValidationError):
        object_grants.parse_grant(bad)


def test_an_invite_shares_at_least_one_and_at_most_fifty_objects():
    with pytest.raises(ValidationError):
        object_grants.normalize_grants([])
    many = [f"3/@mikro/image:{i}" for i in range(object_grants.MAX_OBJECTS_PER_INVITE + 1)]
    with pytest.raises(ValidationError):
        object_grants.normalize_grants(many)
    # Duplicates collapse, the last reach wins; the same id on another hub is another object.
    assert object_grants.normalize_grants(["3/@mikro/image:1", "3/@mikro/image:1#rois", "4/@mikro/image:1"]) == [
        "3/@mikro/image:1#rois",
        "4/@mikro/image:1",
    ]


@pytest.mark.django_db
def test_only_the_organizations_own_hubs_can_be_shared_from():
    organization, _ = _org_with_member()
    foreign_hub = factories.make_hub()
    with pytest.raises(ValidationError, match="Not a hub of this organization"):
        object_grants.normalize_grants([f"{foreign_hub.pk}/@mikro/image:1"], organization)


# --------------------------------------------------------------------------- #
# accepting
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_accepting_an_object_invite_makes_a_scoped_member_without_roles():
    organization, member = _org_with_member()
    invite = _object_invite(organization, member.user, ["@mikro/image:42#rois", "@mikro/arraydataset:7"])
    visitor = factories.make_user()

    membership = redeem_invite(invite, visitor)

    assert membership.is_scoped
    assert not membership.roles.exists()
    assert _live(membership) == ["@mikro/arraydataset:7", "@mikro/image:42#rois"]
    invite.refresh_from_db()
    assert invite.uses == 1
    assert invite.status == models.Invite.Status.PENDING  # open for the next person


@pytest.mark.django_db
def test_an_object_invite_closes_after_max_uses():
    organization, member = _org_with_member()
    invite = _object_invite(organization, member.user, max_uses=2)

    redeem_invite(invite, factories.make_user())
    redeem_invite(invite, factories.make_user())

    invite.refresh_from_db()
    assert invite.status == models.Invite.Status.ACCEPTED
    with pytest.raises(InviteError):
        redeem_invite(invite, factories.make_user())


@pytest.mark.django_db
def test_a_full_member_accepting_an_object_invite_gets_nothing_and_spends_nothing():
    organization, member = _org_with_member()
    other, _ = _org_with_member()
    invite = _object_invite(organization, member.user, max_uses=1)
    colleague = factories.make_membership(organization=organization)
    colleague.roles.add(models.Role.objects.get_or_create(identifier="guest", organization=organization)[0])

    membership = redeem_invite(invite, colleague.user)

    assert membership == colleague
    assert not membership.is_scoped
    invite.refresh_from_db()
    assert invite.uses == 0
    assert invite.status == models.Invite.Status.PENDING


@pytest.mark.django_db
def test_a_second_object_invite_adds_to_what_is_shared_and_widens_the_reach():
    organization, member = _org_with_member()
    visitor = factories.make_user()
    redeem_invite(_object_invite(organization, member.user, ["@mikro/image:1"]), visitor)
    membership = redeem_invite(_object_invite(organization, member.user, ["@mikro/image:1#rois", "@mikro/image:2"]), visitor)

    assert _live(membership) == ["@mikro/image:1#rois", "@mikro/image:2"]


@pytest.mark.django_db
def test_an_ordinary_invite_makes_a_scoped_member_a_full_one():
    organization, member = _org_with_member()
    visitor = factories.make_user()
    scoped = redeem_invite(_object_invite(organization, member.user), visitor)
    full_invite = models.Invite.objects.create(created_by=organization.owner, created_for=organization)

    membership = redeem_invite(full_invite, visitor)

    assert membership.pk == scoped.pk
    assert not membership.is_scoped


@pytest.mark.django_db
def test_declining_an_object_invite_does_not_close_it_for_others():
    organization, member = _org_with_member()
    invite = _object_invite(organization, member.user)
    invite.decline(factories.make_user())
    invite.refresh_from_db()
    assert invite.status == models.Invite.Status.PENDING


# --------------------------------------------------------------------------- #
# the invariants: no roles, never a role-less full member
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_scoped_member_cannot_be_given_roles_without_being_upgraded():
    organization, member = _org_with_member()
    membership = redeem_invite(_object_invite(organization, member.user), factories.make_user())
    role = models.Role.objects.get_or_create(identifier="admin", organization=organization)[0]

    with pytest.raises(ValidationError), transaction.atomic():
        membership.roles.add(role)

    object_grants.upgrade_to_full(membership)
    membership.roles.add(role)
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert not membership.is_scoped


@pytest.mark.django_db
def test_taking_back_the_last_grant_removes_the_membership():
    organization, member = _org_with_member()
    membership = redeem_invite(_object_invite(organization, member.user, ["@mikro/image:1", "@mikro/image:2"]), factories.make_user())
    first, second = list(membership.object_grants.order_by("id"))

    assert object_grants.revoke_grant(first) is False
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert object_grants.revoke_grant(second) is True
    assert not models.Membership.objects.filter(pk=membership.pk).exists()


@pytest.mark.django_db
def test_revoking_an_invite_takes_back_everything_it_shared():
    organization, member = _org_with_member()
    invite = _object_invite(organization, member.user)
    a = redeem_invite(invite, factories.make_user())
    b = redeem_invite(invite, factories.make_user())

    assert object_grants.revoke_invite_grants(invite) == 2
    assert not models.Membership.objects.filter(pk__in=[a.pk, b.pk]).exists()


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_scoped_members_token_lists_the_objects():
    organization, member = _org_with_member()
    membership = redeem_invite(_object_invite(organization, member.user, ["@mikro/image:42#rois"]), factories.make_user())
    client = factories.make_client(membership=membership)

    claims = _generator().get_extra_claims(client, "authorization_code", membership, None)

    assert claims["org"] == str(organization.pk)
    assert claims["roles"] == []
    assert claims["objects"] == {_hub(organization): ["@mikro/image:42#rois"]}
    assert access_token_expires_in(client, "authorization_code") <= SCOPED_ACCESS_TOKEN_EXPIRES_IN


@pytest.mark.django_db
def test_expired_grants_leave_the_token_but_it_stays_scoped():
    organization, member = _org_with_member()
    membership = redeem_invite(_object_invite(organization, member.user), factories.make_user())
    membership.object_grants.update(expires_at=timezone.now() - timedelta(minutes=1))
    client = factories.make_client(membership=membership)

    claims = _generator().get_extra_claims(client, "authorization_code", membership, None)

    assert claims["objects"] == {}  # reaches nothing; never "everything"


@pytest.mark.django_db
def test_a_full_members_token_is_unchanged():
    organization, member = _org_with_member()
    client = factories.make_client(membership=member)
    claims = _generator().get_extra_claims(client, "authorization_code", member, None)
    assert claims["org"] == str(organization.pk)
    assert "objects" not in claims
    assert claims["roles"] == ["user"]


# --------------------------------------------------------------------------- #
# the management API
# --------------------------------------------------------------------------- #


def _api_setup():
    organization, member = _org_with_member()
    member_context = build_auth_context(member.user, organization, factories.make_client(membership=member), roles=("user",))
    guest = factories.make_membership(organization=organization)
    guest.roles.add(models.Role.objects.get_or_create(identifier="guest", organization=organization)[0])
    guest_context = build_auth_context(guest.user, organization, factories.make_client(membership=guest), roles=("guest",))
    visitor = factories.make_user()
    return organization, member, member_context, guest_context, visitor


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_members_share_objects_and_the_visitor_sees_only_the_org_shell():
    organization, member, member_context, guest_context, visitor = await sync_to_async(_api_setup)()
    hub = await sync_to_async(_hub)(organization)

    created = await management_schema.execute(
        CREATE_INVITE,
        variable_values={"input": {"organization": str(organization.pk), "objects": [f"{hub}/@mikro/image:42#rois"]}},
        context_value=member_context,
    )
    assert not created.errors, created.errors
    assert created.data["createInvite"]["objects"] == [f"{hub}/@mikro/image:42#rois"]

    # A guest may not share.
    refused = await management_schema.execute(
        CREATE_INVITE,
        variable_values={"input": {"organization": str(organization.pk), "objects": [f"{hub}/@mikro/image:1"]}},
        context_value=guest_context,
    )
    assert refused.errors

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

    # A full member sees the visitor among the memberships, marked as scoped.
    as_member = await management_schema.execute(
        ORGANIZATION, variable_values={"id": str(organization.pk)}, context_value=member_context
    )
    scoped = [m for m in as_member.data["organization"]["memberships"] if m["user"]["id"] == str(visitor.pk)]
    assert scoped and scoped[0]["isScoped"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_whoever_shared_can_take_it_back():
    organization, member, member_context, _, visitor = await sync_to_async(_api_setup)()
    invite = await sync_to_async(_object_invite)(organization, member.user)
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    grant = await sync_to_async(membership.object_grants.get)()

    result = await management_schema.execute(
        REVOKE, variable_values={"input": {"id": str(grant.pk)}}, context_value=member_context
    )
    assert not result.errors, result.errors
    assert not await sync_to_async(models.Membership.objects.filter(pk=membership.pk).exists)()


# --------------------------------------------------------------------------- #
# re-accepting, and deletes that cascade through grants
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_re_accepting_what_one_already_has_spends_nothing():
    organization, member = _org_with_member()
    visitor = factories.make_user()
    redeem_invite(_object_invite(organization, member.user, ["@mikro/image:1#rois"]), visitor)
    invite = _object_invite(organization, member.user, ["@mikro/image:1"], max_uses=1)

    membership = redeem_invite(invite, visitor)  # a plain `self` never narrows `rois`

    invite.refresh_from_db()
    assert invite.uses == 0 and invite.status == models.Invite.Status.PENDING
    assert _live(membership) == ["@mikro/image:1#rois"]


@pytest.mark.django_db(transaction=True)
def test_deleting_a_scoped_membership_runs_its_side_effects_once(monkeypatch):
    import ionscale.sync

    calls = []
    monkeypatch.setattr(ionscale.sync, "schedule_member_removal", lambda *args: calls.append(args))
    organization, member = _org_with_member()
    membership = redeem_invite(_object_invite(organization, member.user, ["@mikro/image:1", "@mikro/image:2"]), factories.make_user())

    membership.delete()

    assert calls == [(membership.user_id, organization.pk)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("what", ["user", "organization"])
def test_deleting_the_user_or_organization_of_a_scoped_member_works(what):
    organization, member = _org_with_member()
    visitor = factories.make_user()
    membership = redeem_invite(_object_invite(organization, member.user), visitor)

    (visitor if what == "user" else organization).delete()

    assert not models.Membership.objects.filter(pk=membership.pk).exists()
    assert not models.ObjectGrant.objects.filter(membership_id=membership.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_deleting_the_hub_removes_a_member_left_with_nothing():
    organization, member = _org_with_member()
    membership = redeem_invite(_object_invite(organization, member.user), factories.make_user())

    organization.hubs.get().delete()

    assert not models.Membership.objects.filter(pk=membership.pk).exists()
    assert models.Membership.objects.filter(pk=member.pk).exists()

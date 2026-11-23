"""Granting collaborations through an invite: guests, with roles that hold only inside them.

A collaboration invite makes whoever accepts it a guest of the organization: a scoped
membership with no roles that reaches only the granted collaborations
(``karakter.collaboration_grants``), each one key across the organization's services.
These tests pin the rules that keep such a membership from ever acting as a full one
(no roles, nothing of the organization in the management API, never left as a
role-less full member when its grants go) and what a guest's token says: the keys of
their collaborations, each with roles that never exceed what the granter holds now.
"""

import json
import uuid
from collections.abc import Awaitable, Sequence
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from graphql import GraphQLError
from joserfc import jwt
from joserfc.jwk import RSAKey
from kante.context import HttpContext
from strawberry.types import ExecutionResult

from api.management.mutations.device_code import (
    AcceptDeviceCodeInput,
    accept_device_code,
)
from api.management.schema import schema as management_schema
from api.management.types import ManagementClient
from authapp.server import SCOPED_ACCESS_TOKEN_EXPIRES_IN, access_token_expires_in
from authapp.token_generators import MyJWTBearerTokenGenerator
from fakts import models as fakts_models
from ionscale.acl import build_acl_policy
from ionscale.testing import FakeIonscaleRepository
from karakter import collaboration_grants, models
from karakter.invites import InviteError, redeem_invite
from tests import factories
from tests.conftest import build_auth_context

CREATE_INVITE = """
    mutation ($input: CreateInviteInput!) {
        createInvite(input: $input) { id token collaborations { collaboration name roles } maxUses uses }
    }
"""

ORGANIZATION = """
    query ($id: ID!) {
        organization(id: $id) {
            id name deeplinkApps { protocol }
            users { id }
            memberships { id user { id } isScoped collaborationGrants { collaboration { key name } roles } }
        }
    }
"""

REVOKE = """
    mutation ($input: RevokeCollaborationGrantInput!) { revokeCollaborationGrant(input: $input) }
"""

MEMBER_ROLES = ("user", "editor", "viewer")


def _generator() -> MyJWTBearerTokenGenerator:
    return MyJWTBearerTokenGenerator(issuer="https://lok.test")


def _role(organization: models.Organization, identifier: str) -> models.Role:
    return models.Role.objects.get_or_create(
        identifier=identifier, organization=organization
    )[0]


def _member(
    organization: models.Organization, roles: Sequence[str] = MEMBER_ROLES
) -> models.Membership:
    member = factories.make_membership(organization=organization)
    member.roles.add(*[_role(organization, r) for r in roles])
    return member


def _org_with_member(
    roles: Sequence[str] = MEMBER_ROLES,
) -> tuple[models.Organization, models.Membership, fakts_models.ServiceInstance]:
    """An organization with a hub, a service instance on it, and a member holding ``roles``."""
    organization = factories.make_organization()
    hub = factories.make_hub(organization=organization)
    instance = factories.make_service_instance(hub=hub)
    return organization, _member(organization, roles), instance


def _collaboration(
    organization: models.Organization, name: str = "Plates"
) -> models.Collaboration:
    return models.Collaboration.objects.create(organization=organization, name=name)


def _entry(
    collaboration: models.Collaboration, roles: Sequence[str] = ()
) -> dict[str, str | list[str]]:
    return {"collaboration": str(collaboration.key), "roles": list(roles)}


def _key(collaboration: models.Collaboration) -> str:
    return str(collaboration.key)


def _collaboration_invite(
    organization: models.Organization,
    created_by: models.User,
    entries: list[dict[str, str | list[str]]],
    **kw: Any,
) -> models.Invite:
    return models.Invite.objects.create(
        created_by=created_by,
        created_for=organization,
        shared_collaborations=collaboration_grants.normalize_collaborations(
            entries, organization
        ),
        **kw,
    )


def _client(
    membership: models.Membership, *instances: fakts_models.ServiceInstance
) -> fakts_models.Client:
    """A fakts client of ``membership`` whose tokens are minted for ``instances``."""
    client = factories.make_client(membership=membership)
    for n, instance in enumerate(instances):
        fakts_models.ServiceInstanceMapping.objects.create(
            client=client, instance=instance, key=f"svc{n}"
        )
    return client


def _claims(
    membership: models.Membership, *instances: fakts_models.ServiceInstance
) -> dict[str, object]:
    return _generator().get_extra_claims(
        _client(membership, *instances), "authorization_code", membership, None
    )


# --------------------------------------------------------------------------- #
# invite entries
# --------------------------------------------------------------------------- #

A = "0b4e5c4a-1d2f-4a6b-9c8d-7e6f5a4b3c2d"
B = "9f12e3d4-5c6b-4a7f-8e9d-0c1b2a3f4e5d"


def test_entries_normalize_and_the_same_collaboration_twice_holds_both_roles() -> None:
    entries = collaboration_grants.normalize_collaborations(
        [
            {"collaboration": B, "roles": ["viewer"]},
            {"collaboration": f"  {B.upper()} ", "roles": ["editor", "viewer"]},
            {"collaboration": A},
        ]
    )
    assert entries == [
        {"collaboration": B, "roles": ["editor", "viewer"]},
        {"collaboration": A, "roles": []},
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "7/12",
        {"roles": ["editor"]},
        {"collaboration": "12"},
        {"collaboration": 12},
        {"collaboration": True},
        # the old shape: a collaboration of a service instance
        {"instance": "7", "collaboration": "12"},
        {"collaboration": A, "roles": "editor"},
        {"collaboration": A, "roles": [""]},
        {"collaboration": A, "roles": [3]},
    ],
)
def test_malformed_entries_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        collaboration_grants.normalize_collaborations([bad])


def test_an_invite_grants_at_least_one_and_at_most_fifty_collaborations() -> None:
    with pytest.raises(ValidationError):
        collaboration_grants.normalize_collaborations([])
    many = [
        {"collaboration": str(uuid.uuid4())}
        for _ in range(collaboration_grants.MAX_COLLABORATIONS_PER_INVITE + 1)
    ]
    with pytest.raises(ValidationError, match="at most"):
        collaboration_grants.normalize_collaborations(many)


@pytest.mark.django_db
def test_only_the_organizations_own_collaborations_and_roles_can_be_granted() -> None:
    organization, _, _ = _org_with_member()
    collaboration = _collaboration(organization)
    foreign = _collaboration(factories.make_organization())

    with pytest.raises(
        ValidationError, match="Not a collaboration of this organization"
    ):
        collaboration_grants.normalize_collaborations(
            [{"collaboration": str(uuid.uuid4())}], organization
        )
    with pytest.raises(
        ValidationError, match="Not a collaboration of this organization"
    ):
        collaboration_grants.normalize_collaborations([_entry(foreign)], organization)
    with pytest.raises(
        ValidationError, match="Not a role of this organization: wizard"
    ):
        collaboration_grants.normalize_collaborations(
            [_entry(collaboration, roles=["editor", "wizard"])], organization
        )
    # A role of another organization is no role here either.
    _role(foreign.organization, "curator")
    with pytest.raises(ValidationError, match="curator"):
        collaboration_grants.normalize_collaborations(
            [_entry(collaboration, roles=["curator"])], organization
        )

    assert collaboration_grants.normalize_collaborations(
        [_entry(collaboration, roles=["editor"])], organization
    ) == [_entry(collaboration, roles=["editor"])]


# --------------------------------------------------------------------------- #
# accepting
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_accepting_a_collaboration_invite_makes_a_guest_without_roles() -> None:
    organization, member, _ = _org_with_member()
    plates, notes = _collaboration(organization), _collaboration(organization, "Notes")
    invite = _collaboration_invite(
        organization,
        member.user,
        [_entry(plates, ["editor"]), _entry(notes)],
    )
    visitor = factories.make_user()

    membership = redeem_invite(invite, visitor)

    assert membership.is_scoped
    assert not membership.roles.exists()
    grants = sorted(
        membership.collaboration_grants.values_list(
            "collaboration__key",
            "roles",
            "granted_by_id",
            "granted_through_id",
        ),
        key=str,
    )
    assert grants == sorted(
        [
            (plates.key, ["editor"], member.user_id, invite.pk),
            (notes.key, [], member.user_id, invite.pk),
        ],
        key=str,
    )
    invite.refresh_from_db()
    assert invite.uses == 1
    assert invite.status == models.Invite.Status.PENDING  # open for the next person


@pytest.mark.django_db
def test_a_collaboration_invite_closes_after_max_uses() -> None:
    organization, member, _ = _org_with_member()
    invite = _collaboration_invite(
        organization, member.user, [_entry(_collaboration(organization))], max_uses=2
    )

    redeem_invite(invite, factories.make_user())
    redeem_invite(invite, factories.make_user())

    invite.refresh_from_db()
    assert invite.status == models.Invite.Status.ACCEPTED
    with pytest.raises(InviteError):
        redeem_invite(invite, factories.make_user())


@pytest.mark.django_db
def test_a_full_member_accepting_a_collaboration_invite_gets_nothing_and_spends_nothing() -> (
    None
):
    organization, member, _ = _org_with_member()
    invite = _collaboration_invite(
        organization, member.user, [_entry(_collaboration(organization))], max_uses=1
    )
    colleague = _member(organization, ("guest",))

    membership = redeem_invite(invite, colleague.user)

    assert membership == colleague
    assert not membership.is_scoped
    invite.refresh_from_db()
    assert invite.uses == 0
    assert invite.status == models.Invite.Status.PENDING


@pytest.mark.django_db
def test_granting_a_collaboration_again_takes_the_union_of_its_roles() -> None:
    organization, karl, _ = _org_with_member()
    anna = _member(organization)
    plates, notes = _collaboration(organization), _collaboration(organization, "Notes")
    visitor = factories.make_user()
    redeem_invite(
        _collaboration_invite(organization, karl.user, [_entry(plates, ["viewer"])]),
        visitor,
    )
    second = _collaboration_invite(
        organization,
        anna.user,
        [_entry(plates, ["editor"]), _entry(notes)],
    )

    membership = redeem_invite(second, visitor)

    grants = {g.collaboration: g for g in membership.collaboration_grants.all()}
    assert grants[plates].roles == ["editor", "viewer"]
    # The grant that grew takes its new granter and invite.
    assert (grants[plates].granted_by_id, grants[plates].granted_through_id) == (
        anna.user_id,
        second.pk,
    )
    assert grants[notes].roles == []


@pytest.mark.django_db
def test_re_accepting_what_one_already_has_spends_nothing() -> None:
    organization, member, _ = _org_with_member()
    plates = _collaboration(organization)
    visitor = factories.make_user()
    redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(plates, ["editor", "viewer"])]
        ),
        visitor,
    )
    invite = _collaboration_invite(
        organization, member.user, [_entry(plates, ["viewer"])], max_uses=1
    )

    membership = redeem_invite(invite, visitor)  # fewer roles never narrow a grant

    invite.refresh_from_db()
    assert invite.uses == 0 and invite.status == models.Invite.Status.PENDING
    assert membership.collaboration_grants.get().roles == ["editor", "viewer"]


@pytest.mark.django_db
def test_a_collaboration_gone_since_the_invite_was_made_grants_nothing_and_makes_no_member() -> (
    None
):
    organization, member, _ = _org_with_member()
    plates = _collaboration(organization)
    invite = _collaboration_invite(organization, member.user, [_entry(plates)])
    plates.delete()
    visitor = factories.make_user()

    with pytest.raises(InviteError, match="Not a collaboration"):
        redeem_invite(invite, visitor)

    assert not models.Membership.objects.filter(
        user=visitor, organization=organization
    ).exists()


@pytest.mark.django_db
def test_an_ordinary_invite_makes_a_guest_a_full_member() -> None:
    organization, member, _ = _org_with_member()
    visitor = factories.make_user()
    scoped = redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(_collaboration(organization))]
        ),
        visitor,
    )
    full_invite = models.Invite.objects.create(
        created_by=organization.owner, created_for=organization
    )

    membership = redeem_invite(full_invite, visitor)

    assert membership.pk == scoped.pk
    assert not membership.is_scoped
    assert not models.CollaborationGrant.objects.filter(membership=membership).exists()


@pytest.mark.django_db
def test_declining_a_collaboration_invite_does_not_close_it_for_others() -> None:
    organization, member, _ = _org_with_member()
    invite = _collaboration_invite(
        organization, member.user, [_entry(_collaboration(organization))]
    )
    invite.decline(factories.make_user())
    invite.refresh_from_db()
    assert invite.status == models.Invite.Status.PENDING


# --------------------------------------------------------------------------- #
# the invariants: no roles, never a role-less full member
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_guest_cannot_be_given_roles_without_being_upgraded() -> None:
    organization, member, _ = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(_collaboration(organization))]
        ),
        factories.make_user(),
    )
    role = _role(organization, "admin")

    with pytest.raises(ValidationError), transaction.atomic():
        membership.roles.add(role)

    collaboration_grants.upgrade_to_full(membership)
    assert not membership.collaboration_grants.exists()
    membership.roles.add(role)
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert not membership.is_scoped


@pytest.mark.django_db
def test_taking_back_the_last_grant_removes_the_membership() -> None:
    organization, member, _ = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(
            organization,
            member.user,
            [
                _entry(_collaboration(organization)),
                _entry(_collaboration(organization, "Notes")),
            ],
        ),
        factories.make_user(),
    )
    first, second = list(membership.collaboration_grants.order_by("id"))

    assert collaboration_grants.revoke_grant(first) is False
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert collaboration_grants.revoke_grant(second) is True
    assert not models.Membership.objects.filter(pk=membership.pk).exists()


@pytest.mark.django_db
def test_revoking_an_invite_takes_back_everything_it_granted() -> None:
    organization, member, _ = _org_with_member()
    invite = _collaboration_invite(
        organization, member.user, [_entry(_collaboration(organization))]
    )
    a = redeem_invite(invite, factories.make_user())
    b = redeem_invite(invite, factories.make_user())

    assert collaboration_grants.revoke_invite_grants(invite) == 2
    assert not models.Membership.objects.filter(pk__in=[a.pk, b.pk]).exists()


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_a_guests_token_lists_their_collaborations_by_key_on_every_service() -> None:
    organization, member, mikro = _org_with_member()
    other = factories.make_service_instance(hub=mikro.hub)
    plates, notes = _collaboration(organization), _collaboration(organization, "Notes")
    _collaboration(organization, "Not granted")
    invite = _collaboration_invite(
        organization, member.user, [_entry(plates, ["editor"]), _entry(notes)]
    )
    membership = redeem_invite(invite, factories.make_user())
    client = _client(membership, mikro, other)

    claims = _generator().get_extra_claims(
        client, "authorization_code", membership, None
    )

    assert claims["org"] == str(organization.pk)
    assert claims["hub"] == str(mikro.hub.uid)
    assert claims["roles"] == []
    assert "objects" not in claims
    # One key across the organization's services: no service level in between.
    assert claims["collabs"] == {_key(plates): ["editor"], _key(notes): []}
    assert list(claims["collabs"]) == sorted(claims["collabs"])
    # Whatever services the token is minted for, the same keys.
    assert _claims(membership)["collabs"] == claims["collabs"]
    assert _claims(membership, other)["collabs"] == claims["collabs"]
    assert collaboration_grants.live_collaborations(membership) == claims["collabs"]
    # The audiences are still the services the token is minted for.
    assert _generator().get_audiences(client, membership, None) == [
        "lok",
        *sorted([mikro.instance_id, other.instance_id]),
    ]
    assert (
        access_token_expires_in(client, "authorization_code")
        <= SCOPED_ACCESS_TOKEN_EXPIRES_IN
    )


@pytest.mark.django_db
def test_a_guests_roles_are_cut_to_what_the_granter_holds_now() -> None:
    organization, karl, instance = _org_with_member()
    plates = _collaboration(organization)
    membership = redeem_invite(
        _collaboration_invite(
            organization, karl.user, [_entry(plates, ["editor", "viewer"])]
        ),
        factories.make_user(),
    )
    assert _claims(membership, instance)["collabs"] == {
        _key(plates): ["editor", "viewer"]
    }

    karl.roles.remove(_role(organization, "editor"))
    assert _claims(membership, instance)["collabs"] == {_key(plates): ["viewer"]}

    karl.roles.set([_role(organization, "user")])
    # Still a full member: the collaboration stays, view only.
    assert _claims(membership, instance)["collabs"] == {_key(plates): []}

    # And a role given back to the granter comes back to the guest.
    karl.roles.add(_role(organization, "editor"))
    assert _claims(membership, instance)["collabs"] == {_key(plates): ["editor"]}


@pytest.mark.django_db
def test_a_granter_who_left_takes_their_grants_along() -> None:
    organization, karl, instance = _org_with_member()
    anna = _member(organization)
    plates, notes = _collaboration(organization), _collaboration(organization, "Notes")
    visitor = factories.make_user()
    redeem_invite(
        _collaboration_invite(organization, karl.user, [_entry(plates, ["editor"])]),
        visitor,
    )
    membership = redeem_invite(
        _collaboration_invite(organization, anna.user, [_entry(notes, ["viewer"])]),
        visitor,
    )

    karl.delete()

    assert _claims(membership, instance)["collabs"] == {_key(notes): ["viewer"]}
    anna.delete()
    assert _claims(membership, instance)["collabs"] == {}  # still a guest, of nothing


@pytest.mark.django_db
def test_a_granter_who_is_only_a_guest_or_inactive_grants_nothing() -> None:
    organization, karl, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(
            organization, karl.user, [_entry(_collaboration(organization), ["editor"])]
        ),
        factories.make_user(),
    )

    karl.user.is_active = False
    karl.user.save(update_fields=["is_active"])
    assert _claims(membership, instance)["collabs"] == {}

    karl.user.is_active = True
    karl.user.save(update_fields=["is_active"])
    # Karl's own membership turns into a guest's: he no longer acts for the organization.
    karl.roles.clear()
    collaboration_grants.grant_collaborations(
        karl, [_entry(_collaboration(organization, "Karl's"))]
    )
    assert _claims(membership, instance)["collabs"] == {}


@pytest.mark.django_db
def test_a_grant_whose_granter_is_gone_grants_nothing() -> None:
    organization, karl, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(
            organization, karl.user, [_entry(_collaboration(organization))]
        ),
        factories.make_user(),
    )
    membership.collaboration_grants.update(granted_by=None)

    assert _claims(membership, instance)["collabs"] == {}


@pytest.mark.django_db
def test_expired_grants_leave_the_token_but_it_stays_scoped() -> None:
    organization, member, instance = _org_with_member()
    plates, notes = _collaboration(organization), _collaboration(organization, "Notes")
    membership = redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(plates), _entry(notes, ["editor"])]
        ),
        factories.make_user(),
    )
    membership.collaboration_grants.filter(collaboration=plates).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert _claims(membership, instance)["collabs"] == {_key(notes): ["editor"]}

    membership.collaboration_grants.update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    claims = _claims(membership, instance)

    assert claims["roles"] == []
    assert claims["collabs"] == {}  # grants nothing; never "everything"


@pytest.mark.django_db
def test_a_full_members_token_is_unchanged() -> None:
    organization, member, instance = _org_with_member(roles=("user",))
    claims = _claims(member, instance)
    assert claims["org"] == str(organization.pk)
    assert "collabs" not in claims and "objects" not in claims
    assert claims["roles"] == ["user"]


# --------------------------------------------------------------------------- #
# the management API
# --------------------------------------------------------------------------- #


def _api_setup() -> tuple[
    models.Organization,
    models.Membership,
    models.Collaboration,
    HttpContext,
    HttpContext,
    models.User,
]:
    organization, member, _ = _org_with_member()
    member_context = build_auth_context(
        member.user,
        organization,
        factories.make_client(membership=member),
        roles=MEMBER_ROLES,
    )
    guest = _member(organization, ("guest",))
    guest_context = build_auth_context(
        guest.user,
        organization,
        factories.make_client(membership=guest),
        roles=("guest",),
    )
    visitor = factories.make_user()
    return (
        organization,
        member,
        _collaboration(organization),
        member_context,
        guest_context,
        visitor,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_members_grant_collaborations_and_the_visitor_sees_only_the_org_shell() -> (
    None
):
    (
        organization,
        member,
        plates,
        member_context,
        guest_context,
        visitor,
    ) = await sync_to_async(_api_setup)()
    collaboration = {"collaboration": _key(plates), "roles": ["editor"]}

    created = await management_schema.execute(
        CREATE_INVITE,
        variable_values={
            "input": {
                "organization": str(organization.pk),
                "collaborations": [collaboration],
            }
        },
        context_value=member_context,
    )
    assert not created.errors, created.errors
    assert created.data["createInvite"]["collaborations"] == [
        {**collaboration, "name": "Plates"}
    ]

    # A guest may not grant.
    refused = await management_schema.execute(
        CREATE_INVITE,
        variable_values={
            "input": {
                "organization": str(organization.pk),
                "collaborations": [collaboration],
            }
        },
        context_value=guest_context,
    )
    assert refused.errors

    # Nor may anyone grant a role the organization does not have.
    unknown = await management_schema.execute(
        CREATE_INVITE,
        variable_values={
            "input": {
                "organization": str(organization.pk),
                "collaborations": [{**collaboration, "roles": ["wizard"]}],
            }
        },
        context_value=member_context,
    )
    assert unknown.errors and "wizard" in unknown.errors[0].message

    # Nor a collaboration that is not one of the organization's, nor a key that is no key.
    foreign = await sync_to_async(
        lambda: _collaboration(factories.make_organization())
    )()
    for key, message in [
        (_key(foreign), "Not a collaboration of this organization"),
        ("12", "is not a collaboration key"),
    ]:
        refused_key = await management_schema.execute(
            CREATE_INVITE,
            variable_values={
                "input": {
                    "organization": str(organization.pk),
                    "collaborations": [{"collaboration": key}],
                }
            },
            context_value=member_context,
        )
        assert refused_key.errors and message in refused_key.errors[0].message

    # The visitor accepts (before they have a membership, so the context is built after).
    invite = await sync_to_async(models.Invite.objects.get)(
        pk=created.data["createInvite"]["id"]
    )
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    visitor_context = await sync_to_async(
        lambda: build_auth_context(
            visitor,
            organization,
            factories.make_client(membership=membership),
            roles=(),
        )
    )()

    seen = await management_schema.execute(
        ORGANIZATION,
        variable_values={"id": str(organization.pk)},
        context_value=visitor_context,
    )
    assert not seen.errors, seen.errors
    org = seen.data["organization"]
    assert org["name"] == organization.name and org["deeplinkApps"]
    assert org["users"] == []
    assert [m["user"]["id"] for m in org["memberships"]] == [str(visitor.pk)]
    assert org["memberships"][0]["collaborationGrants"] == [
        {"collaboration": {"key": _key(plates), "name": "Plates"}, "roles": ["editor"]}
    ]

    # A full member sees the visitor among the memberships, marked as scoped.
    as_member = await management_schema.execute(
        ORGANIZATION,
        variable_values={"id": str(organization.pk)},
        context_value=member_context,
    )
    scoped = [
        m
        for m in as_member.data["organization"]["memberships"]
        if m["user"]["id"] == str(visitor.pk)
    ]
    assert scoped and scoped[0]["isScoped"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_whoever_granted_can_take_it_back() -> None:
    organization, member, plates, member_context, _, visitor = await sync_to_async(
        _api_setup
    )()
    invite = await sync_to_async(_collaboration_invite)(
        organization, member.user, [_entry(plates)]
    )
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    grant = await sync_to_async(membership.collaboration_grants.get)()

    result = await management_schema.execute(
        REVOKE,
        variable_values={"input": {"id": str(grant.pk)}},
        context_value=member_context,
    )
    assert not result.errors, result.errors
    assert not await sync_to_async(
        models.Membership.objects.filter(pk=membership.pk).exists
    )()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_another_guest_cannot_take_a_grant_back() -> None:
    organization, member, plates, _, guest_context, visitor = await sync_to_async(
        _api_setup
    )()
    invite = await sync_to_async(_collaboration_invite)(
        organization, member.user, [_entry(plates)]
    )
    membership = await sync_to_async(redeem_invite)(invite, visitor)
    grant = await sync_to_async(membership.collaboration_grants.get)()

    result = await management_schema.execute(
        REVOKE,
        variable_values={"input": {"id": str(grant.pk)}},
        context_value=guest_context,
    )
    assert result.errors
    assert await sync_to_async(
        models.CollaborationGrant.objects.filter(pk=grant.pk).exists
    )()


# --------------------------------------------------------------------------- #
# a guest's apps: logging in, and the mesh
# --------------------------------------------------------------------------- #

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _mesh_org_with_member() -> tuple[
    models.Organization,
    models.Membership,
    fakts_models.ServiceInstance,
    fakts_models.IonscaleLayer,
]:
    """``_org_with_member`` whose organization has a mesh."""
    organization, member, instance = _org_with_member()
    layer = fakts_models.IonscaleLayer.objects.create(
        organization=organization,
        name="Default",
        kind="ionscale",
        identifier=organization.slug,
        tailnet_name=organization.slug,
    )
    return organization, member, instance, layer


def _guest(
    organization: models.Organization,
    member: models.Membership,
    roles: Sequence[str] = ("editor",),
    **invite: Any,
) -> models.Membership:
    """A guest of one new collaboration of ``organization``, holding ``roles`` in it."""
    return redeem_invite(
        _collaboration_invite(
            organization,
            member.user,
            [_entry(_collaboration(organization), roles=roles)],
            **invite,
        ),
        factories.make_user(),
    )


def _start_app(
    client: Client,
    instance: fakts_models.ServiceInstance,
    *,
    request_auth_key: bool = True,
) -> dict[str, Any]:
    manifest = {
        "identifier": "com.example.guestapp",
        "version": "1.0.0",
        "scopes": [],
        "device_id": "guest-laptop",
        "requirements": [
            {"key": "data", "service": instance.release.service.identifier}
        ],
    }
    payload = {
        "manifest": manifest,
        "requested_client_kind": "development",
        "requested_client_role": "agent",
        "request_auth_key": request_auth_key,
    }
    resp = client.post(
        reverse("app_authorization"),
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    return resp.json()


def _accept_app(
    body: dict[str, Any], user: models.User, hub: fakts_models.Hub
) -> ManagementClient:
    device_code = fakts_models.DeviceCode.objects.get(secret=body["device_code"])
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    return accept_device_code(
        info,
        AcceptDeviceCodeInput(
            device_code=str(device_code.id), code=device_code.code, hub=str(hub.pk)
        ),
    )


def _login_app(
    client: Client,
    user: models.User,
    instance: fakts_models.ServiceInstance,
    **start: Any,
) -> dict[str, Any]:
    body = _start_app(client, instance, **start)
    _accept_app(body, user, instance.hub)
    resp = client.post(
        reverse("token"),
        data={
            "grant_type": DEVICE_CODE_GRANT,
            "device_code": body["device_code"],
            "client_id": body["client_id"],
        },
        secure=True,
    )
    assert resp.status_code == 200, resp.content
    return resp.json()


def _decode(access_token: str) -> dict[str, Any]:
    return jwt.decode(
        access_token, RSAKey.import_key(settings.PUBLIC_KEY), algorithms=["RS256"]
    ).claims


@pytest.mark.django_db
def test_a_guest_approves_an_app_whose_token_carries_only_their_collaborations(
    client: Client,
) -> None:
    organization, member, instance = _org_with_member()
    guest = _guest(organization, member)

    token = _login_app(client, guest.user, instance, request_auth_key=False)

    claims = _decode(token["access_token"])
    assert claims["sub"] == str(guest.user_id)
    assert claims["roles"] == []
    granted = guest.collaboration_grants.get().collaboration
    assert claims["collabs"] == {_key(granted): ["editor"]}
    app_client = fakts_models.Client.objects.get(client_id=token["client_id"])
    assert app_client.membership == guest
    assert app_client.mappings.get().instance == instance


ACCEPT_DEVICE_CODE = """
    mutation ($input: AcceptDeviceCodeInput!) {
        acceptDeviceCode(input: $input) { id name release { version } }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_approves_an_app_through_the_management_api(
    client: Client,
) -> None:
    def _setup() -> tuple[
        models.Membership,
        fakts_models.ServiceInstance,
        fakts_models.DeviceCode,
        HttpContext,
    ]:
        organization, member, instance = _org_with_member()
        guest = _guest(organization, member)
        body = _start_app(client, instance, request_auth_key=False)
        device_code = fakts_models.DeviceCode.objects.get(secret=body["device_code"])
        context = build_auth_context(
            guest.user, organization, factories.make_client(membership=guest), roles=()
        )
        return guest, instance, device_code, context

    guest, instance, device_code, context = await sync_to_async(_setup)()

    result = await management_schema.execute(
        ACCEPT_DEVICE_CODE,
        variable_values={
            "input": {
                "deviceCode": str(device_code.id),
                "code": device_code.code,
                "hub": str(instance.hub_id),
            }
        },
        context_value=context,
    )

    assert not result.errors, result.errors
    accepted = result.data["acceptDeviceCode"]
    assert accepted["release"] == {"version": "1.0.0"}
    bound = await sync_to_async(
        lambda: fakts_models.Client.objects.get(pk=accepted["id"]).membership_id
    )()
    assert bound == guest.pk


@pytest.mark.django_db
def test_a_non_member_or_a_lapsed_guest_cannot_approve_an_app(client: Client) -> None:
    organization, member, instance = _org_with_member()
    stranger = factories.make_user()
    with pytest.raises(GraphQLError, match="Not found"):
        _accept_app(_start_app(client, instance), stranger, instance.hub)

    lapsed = _guest(organization, member)
    lapsed.collaboration_grants.update(expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(GraphQLError, match="Not found"):
        _accept_app(_start_app(client, instance), lapsed.user, instance.hub)


@pytest.mark.django_db
def test_a_guest_approves_apps_into_any_hub_of_the_organization_and_none_other(
    client: Client,
) -> None:
    organization, member, _ = _org_with_member()
    other = factories.make_service_instance(
        hub=factories.make_hub(organization=organization)
    )
    elsewhere = factories.make_service_instance()  # another organization's hub
    guest = _guest(organization, member)

    # A collaboration spans the organization's services, so every hub of it.
    accepted = _accept_app(_start_app(client, other), guest.user, other.hub)
    assert fakts_models.Client.objects.get(pk=accepted.pk).membership == guest
    with pytest.raises(GraphQLError, match="Not found"):
        _accept_app(_start_app(client, elsewhere), guest.user, elsewhere.hub)


@pytest.mark.django_db
def test_redeeming_copies_the_invites_mesh_onto_its_grants_and_a_re_grant_ors_it() -> (
    None
):
    organization, member, _ = _org_with_member()
    plates = _collaboration(organization)
    visitor = factories.make_user()
    closed = _collaboration_invite(
        organization, member.user, [_entry(plates)], mesh=False
    )
    membership = redeem_invite(closed, visitor)
    assert membership.collaboration_grants.get().mesh is False

    redeem_invite(
        _collaboration_invite(organization, member.user, [_entry(plates)], mesh=True),
        visitor,
    )
    assert membership.collaboration_grants.get().mesh is True
    # A later invite without the mesh takes nothing away, and spends nothing.
    again = _collaboration_invite(
        organization, member.user, [_entry(plates)], mesh=False
    )
    redeem_invite(again, visitor)
    assert membership.collaboration_grants.get().mesh is True
    assert models.Invite.objects.get(pk=again.pk).uses == 0


@pytest.mark.django_db
@pytest.mark.parametrize("mesh", [True, False])
def test_a_guests_app_gets_a_mesh_key_only_when_the_invite_allowed_it(
    client: Client, ionscale_repo: FakeIonscaleRepository, mesh: bool
) -> None:
    organization, member, instance, layer = _mesh_org_with_member()
    guest = _guest(organization, member, mesh=mesh)

    token = _login_app(client, guest.user, instance)

    if mesh:
        assert token["mesh"]["ionscale_auth_key"] == ionscale_repo.auth_key
        assert fakts_models.AppMeshEnrollment.objects.get().membership == guest
    else:
        assert "mesh" not in token
        assert ionscale_repo.created_auth_keys == []
        assert not fakts_models.AppMeshEnrollment.objects.exists()


@pytest.mark.django_db
def test_a_guests_lapsed_mesh_grant_renews_no_mesh_key(
    client: Client, ionscale_repo: FakeIonscaleRepository
) -> None:
    from fakts.services.hubs import MeshKeyRefused, renew_mesh_key

    organization, member, instance, layer = _mesh_org_with_member()
    guest = _guest(organization, member, mesh=True)
    token = _login_app(client, guest.user, instance)
    app_client = fakts_models.Client.objects.get(client_id=token["client_id"])
    fakts_models.IonscaleAuthKey.objects.update(
        created_at=timezone.now() - timedelta(hours=1)
    )

    guest.collaboration_grants.update(expires_at=timezone.now() - timedelta(minutes=1))

    with pytest.raises(MeshKeyRefused) as refused:
        renew_mesh_key(app_client)
    assert refused.value.reason == "not_enrolled"


@pytest.mark.django_db
def test_the_acl_lets_a_guests_app_reach_only_the_hub_it_is_a_client_of(
    client: Client, ionscale_repo: FakeIonscaleRepository
) -> None:
    organization, member, instance, layer = _mesh_org_with_member()
    other_hub = factories.make_hub(organization=organization)
    guest = _guest(organization, member, mesh=True)
    _login_app(client, guest.user, instance)
    tag = fakts_models.AppMeshEnrollment.objects.get(membership=guest).tag
    # A member's app on another hub, for contrast: its rule is unchanged.
    member_body = _start_app(client, factories.make_service_instance(hub=other_hub))
    _accept_app(member_body, member.user, other_hub)
    member_tag = fakts_models.AppMeshEnrollment.objects.get(membership=member).tag

    members, *sidecars = build_acl_policy(organization)["acls"]

    assert members["src"] == ["autogroup:member", f"tag:mesh-{organization.pk}"]
    assert sorted(map(str, sidecars), key=str) == sorted(
        map(
            str,
            [
                {
                    "action": "accept",
                    "src": [tag],
                    "dst": [f"tag:hub-{instance.hub_id}:*"],
                },
                {
                    "action": "accept",
                    "src": [member_tag],
                    "dst": [f"tag:hub-{other_hub.pk}:*"],
                },
            ],
        )
    )
    # The guest's sidecar is in no other rule: not among the members, not towards any other hub.
    assert [
        rule for rule in build_acl_policy(organization)["acls"] if tag in rule["src"]
    ] == [{"action": "accept", "src": [tag], "dst": [f"tag:hub-{instance.hub_id}:*"]}]


@pytest.mark.django_db
@pytest.mark.parametrize("how", ["expired", "revoked", "mesh_off"])
def test_a_guest_whose_mesh_grant_lapses_drops_out_at_the_next_reconcile(
    client: Client, ionscale_repo: FakeIonscaleRepository, how: str
) -> None:
    from ionscale.reconcile import reconcile_sidecars

    organization, member, instance, layer = _mesh_org_with_member()
    guest = _guest(organization, member, mesh=True)
    # A second, view-only grant without the mesh keeps the guest a guest when the first goes.
    redeem_invite(
        _collaboration_invite(
            organization,
            member.user,
            [_entry(_collaboration(organization, "View only"))],
            mesh=False,
        ),
        guest.user,
    )
    _login_app(client, guest.user, instance)
    tag = fakts_models.AppMeshEnrollment.objects.get(membership=guest).tag

    reconcile_sidecars(layer)
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert any(tag in rule["src"] for rule in policy["acls"])

    grant = guest.collaboration_grants.get(mesh=True)
    if how == "expired":
        grant.expires_at = timezone.now() - timedelta(minutes=1)
        grant.save()
    elif how == "revoked":
        assert collaboration_grants.revoke_grant(grant) is False
    else:
        grant.mesh = False
        grant.save()

    report = reconcile_sidecars(layer)

    assert report.acl_applied
    # The app's session is live, so its enrollment stays; only its rule goes.
    assert tag not in report.reaped_enrollments
    _, policy = ionscale_repo.set_acl_policies[-1]
    assert not any(tag in rule["src"] for rule in policy["acls"])


# --------------------------------------------------------------------------- #
# the token schema: the links a member handed out
# --------------------------------------------------------------------------- #

TOKEN_CREATE_INVITE = """
    mutation ($input: CreateInviteInput!) {
        createInvite(input: $input) { id mesh public collaborations { collaboration roles } maxUses uses inviteUrl }
    }
"""

TOKEN_INVITES = """
    query ($id: ID!) {
        organization(id: $id) { invites { id mesh public maxUses uses inviteUrl collaborations { collaboration roles } } }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_creator_sees_their_collaboration_invites_on_the_token_schema() -> (
    None
):
    from lok_server.schema import schema as token_schema

    (
        organization,
        member,
        plates,
        member_context,
        guest_context,
        _,
    ) = await sync_to_async(_api_setup)()
    collaboration = {"collaboration": _key(plates), "roles": ["editor"]}

    created = await token_schema.execute(
        TOKEN_CREATE_INVITE,
        variable_values={
            "input": {
                "organization": str(organization.pk),
                "collaborations": [collaboration],
                "allowMesh": False,
                "maxUses": 3,
            }
        },
        context_value=member_context,
    )
    assert not created.errors, created.errors
    invite = created.data["createInvite"]
    assert invite["mesh"] is False
    assert invite["collaborations"] == [collaboration]
    assert (invite["maxUses"], invite["uses"]) == (3, 0)
    # One the owner made, which the member (neither owner nor admin) does not see.
    await sync_to_async(_collaboration_invite)(
        organization, organization.owner, [_entry(plates)]
    )

    listed = await token_schema.execute(
        TOKEN_INVITES,
        variable_values={"id": str(organization.pk)},
        context_value=member_context,
    )
    assert not listed.errors, listed.errors
    assert listed.data["organization"]["invites"] == [invite]

    # A member holding only the `guest` role lists none.
    as_guest = await token_schema.execute(
        TOKEN_INVITES,
        variable_values={"id": str(organization.pk)},
        context_value=guest_context,
    )
    assert not as_guest.errors, as_guest.errors
    assert as_guest.data["organization"]["invites"] == []

    allowed = await token_schema.execute(
        TOKEN_CREATE_INVITE,
        variable_values={
            "input": {
                "organization": str(organization.pk),
                "collaborations": [collaboration],
            }
        },
        context_value=member_context,
    )
    assert not allowed.errors, allowed.errors
    assert allowed.data["createInvite"]["mesh"] is True


# --------------------------------------------------------------------------- #
# deletes that cascade through grants
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_deleting_a_guests_membership_runs_its_side_effects_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ionscale.sync

    calls = []
    monkeypatch.setattr(
        ionscale.sync, "schedule_member_removal", lambda *args: calls.append(args)
    )
    organization, member, _ = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(
            organization,
            member.user,
            [
                _entry(_collaboration(organization)),
                _entry(_collaboration(organization, "Notes")),
            ],
        ),
        factories.make_user(),
    )

    membership.delete()

    assert calls == [(membership.user_id, organization.pk)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("what", ["user", "organization"])
def test_deleting_the_user_or_organization_of_a_guest_works(what: str) -> None:
    organization, member, _ = _org_with_member()
    visitor = factories.make_user()
    membership = redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(_collaboration(organization))]
        ),
        visitor,
    )

    (visitor if what == "user" else organization).delete()

    assert not models.Membership.objects.filter(pk=membership.pk).exists()
    assert not models.CollaborationGrant.objects.filter(
        membership_id=membership.pk
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_deleting_a_collaboration_removes_a_guest_left_with_nothing() -> None:
    organization, member, _ = _org_with_member()
    plates, notes = _collaboration(organization), _collaboration(organization, "Notes")
    only_plates = redeem_invite(
        _collaboration_invite(organization, member.user, [_entry(plates)]),
        factories.make_user(),
    )
    both = redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(plates), _entry(notes)]
        ),
        factories.make_user(),
    )

    plates.delete()  # through the ORM: the cascade itself cleans up

    assert not models.Membership.objects.filter(pk=only_plates.pk).exists()
    assert both.collaboration_grants.get().collaboration == notes
    assert models.Membership.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("what", ["instance", "hub"])
def test_deleting_an_instance_or_a_hub_leaves_the_guests_be(what: str) -> None:
    organization, member, instance = _org_with_member()
    membership = redeem_invite(
        _collaboration_invite(
            organization, member.user, [_entry(_collaboration(organization))]
        ),
        factories.make_user(),
    )

    (instance if what == "instance" else instance.hub).delete()

    # A collaboration is the organization's, not a service's: nothing of it went.
    assert models.Membership.objects.filter(pk=membership.pk).exists()
    assert membership.collaboration_grants.count() == 1


HUBS = "query { hubs { id name organization { id } instances { id } clients { id } } }"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_sees_the_organizations_hubs_to_approve_an_app_into(
    client: Client,
) -> None:
    def _setup() -> tuple[models.Membership, list[fakts_models.Hub], HttpContext]:
        organization, member, instance = _org_with_member()
        other_hub = factories.make_hub(organization=organization)
        factories.make_service_instance(hub=other_hub)
        factories.make_service_instance()  # another organization's hub
        guest = _guest(organization, member)
        context = build_auth_context(
            guest.user, organization, factories.make_client(membership=guest), roles=()
        )
        return guest, [instance.hub, other_hub], context

    guest, hubs, context = await sync_to_async(_setup)()

    result = await management_schema.execute(HUBS, context_value=context)

    assert not result.errors, result.errors
    # What each hub is, to pick it; nothing of what runs on it.
    assert sorted(result.data["hubs"], key=lambda h: int(h["id"])) == [
        {
            "id": str(hub.pk),
            "name": hub.name,
            "organization": {"id": str(hub.organization_id)},
            "instances": [],
            "clients": [],
        }
        for hub in sorted(hubs, key=lambda h: h.pk)
    ]

    await sync_to_async(
        lambda: guest.collaboration_grants.update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
    )()
    lapsed = await management_schema.execute(HUBS, context_value=context)
    assert lapsed.data["hubs"] == []


VALIDATE = """
    query ($deviceCode: ID!, $hub: ID!, $code: String!) {
        validateDeviceCode(deviceCode: $deviceCode, hub: $hub, code: $code) { __typename }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_guest_dry_runs_an_app_approval_into_the_organizations_hubs_only(
    client: Client,
) -> None:
    def _setup() -> tuple[
        fakts_models.ServiceInstance,
        fakts_models.ServiceInstance,
        fakts_models.ServiceInstance,
        fakts_models.DeviceCode,
        HttpContext,
    ]:
        organization, member, instance = _org_with_member()
        other = factories.make_service_instance(
            hub=factories.make_hub(organization=organization)
        )
        elsewhere = factories.make_service_instance()  # another organization's hub
        guest = _guest(organization, member)
        body = _start_app(client, instance, request_auth_key=False)
        device_code = fakts_models.DeviceCode.objects.get(secret=body["device_code"])
        context = build_auth_context(
            guest.user, organization, factories.make_client(membership=guest), roles=()
        )
        return instance, other, elsewhere, device_code, context

    instance, other, elsewhere, device_code, context = await sync_to_async(_setup)()

    def run(hub: int) -> Awaitable[ExecutionResult]:
        return management_schema.execute(
            VALIDATE,
            variable_values={
                "deviceCode": str(device_code.id),
                "hub": str(hub),
                "code": device_code.code,
            },
            context_value=context,
        )

    for hub_id in (instance.hub_id, other.hub_id):
        ok = await run(hub_id)
        assert not ok.errors, ok.errors
    refused = await run(elsewhere.hub_id)
    assert refused.errors and "Not found" in refused.errors[0].message

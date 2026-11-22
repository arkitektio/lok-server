"""The Team page's lists, answered by the server: memberships by role, counts,
membership requests by status, the active membership on the context, and when
someone became a member."""

import datetime
import importlib
from typing import Any, TypedDict

import pytest
from asgiref.sync import sync_to_async
from django.apps import apps as django_apps
from django.utils import timezone
from kante.context import HttpContext

from karakter import models
from karakter.managers import create_role
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context


def _context(membership: models.Membership) -> HttpContext:
    return build_auth_context(
        membership.user,
        membership.organization,
        factories.make_client(membership=membership),
    )


async def _run(context: HttpContext, query: str, **variables: object) -> dict[str, Any]:
    result = await schema.execute(
        query, context_value=context, variable_values=variables
    )
    assert not result.errors, result.errors
    assert result.data is not None
    return result.data


class Team(TypedDict):
    owner: models.Membership
    admin: models.Membership
    editor_role: models.Role
    editors: list[models.Membership]
    plain: list[models.Membership]
    organization: models.Organization


def _team() -> Team:
    """An organization with its owner (an admin), one more admin, three editors and two plain members."""
    owner = factories.make_user()
    organization = factories.make_organization(owner=owner)
    owner_membership = factories.make_membership(user=owner, organization=organization)
    admin_role = create_role(organization=organization, identifier="admin")
    editor = create_role(organization=organization, identifier="editor")
    admin = factories.make_membership(organization=organization)
    admin.roles.add(admin_role)
    editors = [factories.make_membership(organization=organization) for _ in range(3)]
    for membership in editors:
        membership.roles.add(editor)
    plain = [factories.make_membership(organization=organization) for _ in range(2)]
    return {
        "owner": owner_membership,
        "admin": admin,
        "editor_role": editor,
        "editors": editors,
        "plain": plain,
        "organization": organization,
    }


MEMBERSHIPS = """
query ($id: ID!, $filters: MembershipFilter, $pagination: OffsetPaginationInput) {
  organization(id: $id) { memberships(filters: $filters, pagination: $pagination, ordering: [{ id: ASC }]) { id } }
}
"""

STATS = """
query ($filters: MembershipFilter) { membershipStats(filters: $filters) { count } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_memberships_filter_by_role_and_admin_and_count_per_role() -> None:
    team = await sync_to_async(_team)()
    context = await sync_to_async(_context)(team["owner"])
    organization = str(team["organization"].pk)
    editor_role = str(team["editor_role"].pk)

    editors = await _run(
        context, MEMBERSHIPS, id=organization, filters={"role": editor_role}
    )
    assert {m["id"] for m in editors["organization"]["memberships"]} == {
        str(m.pk) for m in team["editors"]
    }

    admins = await _run(
        context, MEMBERSHIPS, id=organization, filters={"isAdmin": True}
    )
    assert {m["id"] for m in admins["organization"]["memberships"]} == {
        str(team["owner"].pk),
        str(team["admin"].pk),
    }

    # The list pages on the server: the second page holds what the first did not.
    first = await _run(
        context, MEMBERSHIPS, id=organization, pagination={"limit": 4, "offset": 0}
    )
    second = await _run(
        context, MEMBERSHIPS, id=organization, pagination={"limit": 4, "offset": 4}
    )
    seen = [
        m["id"]
        for m in first["organization"]["memberships"]
        + second["organization"]["memberships"]
    ]
    assert len(seen) == len(set(seen)) == 7

    assert (await _run(context, STATS))["membershipStats"]["count"] == 7
    assert (await _run(context, STATS, filters={"role": editor_role}))[
        "membershipStats"
    ]["count"] == 3
    assert (await _run(context, STATS, filters={"isAdmin": False}))["membershipStats"][
        "count"
    ] == 5


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_membership_stats_count_only_the_active_organization() -> None:
    team = await sync_to_async(_team)()
    await sync_to_async(factories.make_membership)()  # another organization's member
    context = await sync_to_async(_context)(team["owner"])
    assert (await _run(context, STATS))["membershipStats"]["count"] == 7


REQUESTS = """
query ($filters: MembershipRequestFilter, $pagination: OffsetPaginationInput) {
  membershipRequests(filters: $filters, pagination: $pagination, ordering: [{ createdAt: DESC }]) { id status }
}
"""


def _requests(
    organization: models.Organization, pending: int, declined: int
) -> list[models.MembershipRequest]:
    rows = []
    for status, count in [
        (models.MembershipRequest.Status.PENDING, pending),
        (models.MembershipRequest.Status.DECLINED, declined),
    ]:
        for _ in range(count):
            rows.append(
                models.MembershipRequest.objects.create(
                    user=factories.make_user(), organization=organization, status=status
                )
            )
    return rows


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_membership_requests_filter_by_status_and_page_for_admins_only() -> None:
    team = await sync_to_async(_team)()
    await sync_to_async(_requests)(team["organization"], pending=3, declined=5)
    owner = await sync_to_async(_context)(team["owner"])

    pending = await _run(owner, REQUESTS, filters={"status": ["PENDING"]})
    assert [r["status"] for r in pending["membershipRequests"]] == ["pending"] * 3

    answered = await _run(
        owner,
        REQUESTS,
        filters={"status": ["APPROVED", "DECLINED"]},
        pagination={"limit": 2, "offset": 4},
    )
    assert [r["status"] for r in answered["membershipRequests"]] == ["declined"]

    member = await sync_to_async(_context)(team["plain"][0])
    assert (await _run(member, REQUESTS))["membershipRequests"] == []


CONTEXT = """
{ mycontext { membership { id } brandHue brandChroma } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_context_names_the_active_membership_and_the_brand_in_effect() -> None:
    team = await sync_to_async(_team)()
    organization = team["organization"]
    organization.brand_hue = 120.0
    organization.brand_chroma = 0.2
    await sync_to_async(organization.save)()
    member = team["plain"][0]
    member.brand_hue = 300.0
    await sync_to_async(member.save)()

    data = await _run(await sync_to_async(_context)(member), CONTEXT)
    # The member's own hue, the organization's chroma (the member set none).
    assert data["mycontext"] == {
        "membership": {"id": str(member.pk)},
        "brandHue": 300.0,
        "brandChroma": 0.2,
    }


NEWEST = """
query { organization: mycontext { organization { memberships(ordering: [{ createdAt: DESC }], pagination: { limit: 1 }) { id createdAt } } } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_new_membership_has_its_creation_time_and_sorts_by_it() -> None:
    team = await sync_to_async(_team)()
    before = timezone.now() - datetime.timedelta(seconds=5)
    newest = await sync_to_async(factories.make_membership)(
        organization=team["organization"]
    )

    data = await _run(await sync_to_async(_context)(team["owner"]), NEWEST)
    [row] = data["organization"]["organization"]["memberships"]
    assert row["id"] == str(newest.pk)
    assert datetime.datetime.fromisoformat(row["createdAt"]) >= before


BY_USERNAME = """
query ($filters: MembershipFilter) { mycontext { organization { memberships(filters: $filters, ordering: [{ username: ASC }]) { user { username } } } } }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_memberships_sort_by_username_and_filter_by_creation_time() -> None:
    def setup() -> HttpContext:
        team = _team()
        organization = team["organization"]
        for name in ["zoe", "anna", "mike"]:
            factories.make_membership(
                user=factories.make_user(username=name), organization=organization
            )
        old = factories.make_membership(
            user=factories.make_user(username="olde"), organization=organization
        )
        models.Membership.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=60)
        )
        unknown = factories.make_membership(
            user=factories.make_user(username="unknowne"), organization=organization
        )
        models.Membership.objects.filter(pk=unknown.pk).update(created_at=None)
        return _context(team["owner"])

    context = await sync_to_async(setup)()
    names = [
        m["user"]["username"]
        for m in (await _run(context, BY_USERNAME))["mycontext"]["organization"][
            "memberships"
        ]
    ]
    assert names == sorted(names)
    assert {"anna", "mike", "zoe"} <= set(names)

    month_ago = (timezone.now() - datetime.timedelta(days=30)).isoformat()
    recent = [
        m["user"]["username"]
        for m in (
            await _run(context, BY_USERNAME, filters={"createdAfter": month_ago})
        )["mycontext"]["organization"]["memberships"]
    ]
    assert {"anna", "mike", "zoe"} <= set(recent)
    assert "olde" not in recent and "unknowne" not in recent, (
        "an old membership, or one of unknown age, is not new"
    )
    older = [
        m["user"]["username"]
        for m in (
            await _run(context, BY_USERNAME, filters={"createdBefore": month_ago})
        )["mycontext"]["organization"]["memberships"]
    ]
    assert older == ["olde"]


@pytest.mark.django_db
def test_the_backfill_takes_the_time_an_invite_or_request_was_answered() -> None:
    migration = importlib.import_module(
        "karakter.migrations.0004_membership_created_at"
    )
    team = _team()
    organization = team["organization"]
    invited_at = timezone.now() - datetime.timedelta(days=40)
    requested_at = timezone.now() - datetime.timedelta(days=12)

    invite = models.Invite.objects.create(
        created_by=organization.owner, created_for=organization, responded_at=invited_at
    )
    invited = factories.make_membership(organization=organization)
    invited.created_through = invite
    invited.save()
    requested = factories.make_membership(organization=organization)
    models.MembershipRequest.objects.create(
        user=requested.user,
        organization=organization,
        status=models.MembershipRequest.Status.APPROVED,
        responded_at=requested_at,
        created_membership=requested,
    )
    unknown = team["plain"][0]
    # As the rows were before the field existed.
    models.Membership.objects.filter(
        pk__in=[invited.pk, requested.pk, unknown.pk]
    ).update(created_at=None)

    migration.backfill(django_apps, None)

    assert models.Membership.objects.get(pk=invited.pk).created_at == invited_at
    assert models.Membership.objects.get(pk=requested.pk).created_at == requested_at
    assert models.Membership.objects.get(pk=unknown.pk).created_at is None

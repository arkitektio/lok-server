"""Who is an admin (karakter.roles): ``Membership.is_admin`` follows the roles, however they change.

The flag is a cache of "holds the organization's ``admin`` role", read by every owner-or-admin
policy instead of joining roles. These tests change roles every way the code can and check the
flag against the join it replaces.
"""

from io import StringIO
from typing import Any, cast

import pytest
from django.core.management import call_command
from graphql import GraphQLError

from api.management.authz import get_scoped, owner_or_admin_q
from karakter import models
from karakter.roles import ADMIN, sync_all
from tests import factories


def _holds(membership: models.Membership) -> bool:
    """The join the flag caches."""
    return membership.roles.filter(identifier=ADMIN).exists()


def _flag(membership: models.Membership) -> bool:
    return models.Membership.objects.values_list("is_admin", flat=True).get(pk=membership.pk)


@pytest.fixture
def organization():
    return factories.make_organization()


def _role(organization: models.Organization, identifier: str) -> models.Role:
    return models.Role.objects.get_or_create(organization=organization, identifier=identifier)[0]


@pytest.mark.django_db
def test_the_owner_is_an_admin_from_the_start(organization):
    owner = organization.memberships.get(user=organization.owner)
    assert _flag(owner) and _holds(owner)


@pytest.mark.django_db
def test_the_flag_follows_the_membership_side(organization):
    member = factories.make_membership(organization=organization)
    admin, editor = _role(organization, ADMIN), _role(organization, "editor")
    assert not _flag(member)

    member.roles.add(editor)
    assert not _flag(member)
    member.roles.add(admin)
    assert _flag(member)
    member.roles.remove(admin)
    assert not _flag(member)
    member.roles.set([admin, editor])
    assert _flag(member)
    member.roles.clear()
    assert not _flag(member)


@pytest.mark.django_db
def test_the_flag_follows_the_role_side(organization):
    first, second = factories.make_membership(organization=organization), factories.make_membership(organization=organization)
    admin = _role(organization, ADMIN)

    admin.memberships.add(first, second)
    assert _flag(first) and _flag(second)
    admin.memberships.remove(first)
    assert not _flag(first) and _flag(second)
    admin.memberships.clear()
    assert not _flag(second)
    # The owner held the role too, and lost it with the rest.
    assert not _flag(organization.memberships.get(user=organization.owner))


@pytest.mark.django_db
def test_the_flag_follows_a_role_renamed_to_or_from_admin(organization):
    member = factories.make_membership(organization=organization)
    lead = _role(organization, "lead")
    member.roles.add(lead)
    models.Role.objects.filter(organization=organization, identifier=ADMIN).delete()

    lead.identifier = ADMIN
    lead.save()
    assert _flag(member)
    lead.identifier = "lead"
    lead.save()
    assert not _flag(member)


@pytest.mark.django_db
def test_deleting_the_admin_role_takes_the_flag_along(organization):
    member = factories.make_membership(organization=organization)
    member.roles.add(_role(organization, ADMIN))

    models.Role.objects.get(organization=organization, identifier=ADMIN).delete()
    assert not _flag(member)


@pytest.mark.django_db
def test_after_a_mix_of_changes_the_flag_is_the_join(organization):
    members = [factories.make_membership(organization=organization) for _ in range(4)]
    admin, editor = _role(organization, ADMIN), _role(organization, "editor")
    members[0].roles.add(admin, editor)
    admin.memberships.add(members[1], members[2])
    members[2].roles.remove(admin)
    editor.memberships.add(members[3])
    for membership in [*members, organization.memberships.get(user=organization.owner)]:
        assert _flag(membership) == _holds(membership)


@pytest.mark.django_db
def test_owner_or_admin_matches_on_one_membership_row(organization):
    admin_user, other = factories.make_user(), factories.make_user()
    factories.make_membership(user=admin_user, organization=organization).roles.add(_role(organization, ADMIN))
    factories.make_membership(user=other, organization=organization)
    elsewhere = factories.make_organization()
    # `other` administers another organization: that must not make them admin here.
    factories.make_membership(user=other, organization=elsewhere).roles.add(_role(elsewhere, ADMIN))

    def visible(user):
        return set(models.Organization.objects.filter(owner_or_admin_q(user, "memberships__organization")).distinct())

    assert organization in visible(admin_user)
    assert organization in visible(organization.owner)
    assert organization not in visible(other)


@pytest.mark.django_db
def test_syncadmins_repairs_a_flag_written_behind_its_back_and_runs_again(organization):
    member = factories.make_membership(organization=organization)
    member.roles.add(_role(organization, ADMIN))
    models.Membership.objects.filter(pk=member.pk).update(is_admin=False)  # an older release, say

    out = StringIO()
    call_command("syncadmins", stdout=out)
    assert _flag(member) and "1 membership" in out.getvalue()
    assert sync_all() == 0


class _NoPolicy:
    """A type that says nothing about who may see what."""


@pytest.mark.django_db
def test_a_scoped_lookup_refuses_a_type_without_a_policy(organization):
    with pytest.raises(TypeError, match="get_queryset"):
        get_scoped(_NoPolicy, models.Organization.objects.all(), cast(Any, None))  # pyright: ignore[reportArgumentType]  # the point: it has no policy


class _NothingVisible:
    @classmethod
    def get_queryset(cls, queryset, info, /):
        return queryset.none()


@pytest.mark.django_db
def test_a_scoped_lookup_is_held_to_the_types_policy(organization):
    with pytest.raises(GraphQLError):
        get_scoped(_NothingVisible, models.Organization.objects.filter(pk=organization.pk), cast(Any, None))



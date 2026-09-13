"""The signup-time personal organization and the slug helpers behind it.

``create_user_default_organization`` once did ``get_or_create`` on
``{username}-org`` and made the newcomer an admin of whatever it found — a
cross-tenant takeover by registering the right username. The fix (create-only,
collision-free slug via ``suggest_slug``, retry on ``IntegrityError``) had no
test. ``karakter.slugs`` itself was likewise untested. These pin all of it.
"""

from unittest import mock

import pytest
from django.db import IntegrityError

from karakter import managers, slugs
from karakter.models import Membership, Organization
from tests import factories


# --------------------------------------------------------------------------- #
# create_user_default_organization (runs from the User post_save signal)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_signup_never_joins_an_existing_organization():
    victim_owner = factories.make_user()
    victim = factories.make_organization(owner=victim_owner, slug="acme-org")

    newcomer = factories.make_user(username="acme")

    personal = Organization.objects.get(owner=newcomer)
    assert personal.pk != victim.pk
    assert personal.slug == "the-real-acme-org"
    assert not Membership.objects.filter(user=newcomer, organization=victim).exists()
    # The newcomer is admin of their *own* organization only.
    assert Membership.objects.get(user=newcomer, organization=personal).roles.filter(identifier="admin").exists()


@pytest.mark.django_db
def test_username_of_only_symbols_falls_back_to_a_pk_based_slug():
    user = factories.make_user(username="***")

    personal = Organization.objects.get(owner=user)
    assert personal.slug == f"user-{user.pk}-org"


@pytest.mark.django_db
def test_integrity_error_is_retried_against_a_fresh_slug():
    user = factories.make_user(username="racer")
    real_create = Organization.objects.create
    calls = []

    def flaky(**kw):
        calls.append(kw["slug"])
        if len(calls) == 1:
            raise IntegrityError("duplicate key")
        return real_create(**kw)

    with mock.patch.object(Organization.objects, "create", side_effect=flaky):
        org = managers.create_user_default_organization(user)

    assert org is not None
    assert len(calls) == 2
    assert org.owner == user
    assert Membership.objects.get(user=user, organization=org).roles.filter(identifier="admin").exists()


@pytest.mark.django_db
def test_exhausted_retries_return_none_without_raising():
    user = factories.make_user(username="unlucky")
    before = Organization.objects.count()

    with mock.patch.object(Organization.objects, "create", side_effect=IntegrityError("duplicate key")):
        assert managers.create_user_default_organization(user) is None

    assert Organization.objects.count() == before


# --------------------------------------------------------------------------- #
# karakter.slugs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Acme Inc!", "acme-inc"),
        ("My_Weird..Org", "my-weird-org"),
        ("--acme--", "acme"),
        ("ÄCME", "cme"),
        ("***", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_slug_produces_the_canonical_form(raw, expected):
    assert slugs.normalize_slug(raw) == expected
    # Idempotent: normalising a canonical slug changes nothing.
    assert slugs.normalize_slug(expected) == expected


@pytest.mark.parametrize("bad", ["", "-acme-", "Acme", "acme--inc", "acme_inc", "acme inc"])
def test_validate_slug_rejects_malformed_slugs(bad):
    with pytest.raises(ValueError):
        slugs.validate_slug(bad)


@pytest.mark.parametrize("good", ["acme", "acme-inc", "a1-b2-c3", "42"])
def test_validate_slug_accepts_canonical_slugs(good):
    slugs.validate_slug(good)


@pytest.mark.django_db
def test_is_slug_taken_is_case_insensitive():
    factories.make_organization(slug="acme")

    assert slugs.is_slug_taken("acme")
    assert slugs.is_slug_taken("ACME")
    assert not slugs.is_slug_taken("acme-inc")


@pytest.mark.django_db
def test_suggest_slug_walks_the_prefixes_then_numbers():
    factories.make_organization(slug="acme")
    assert slugs.suggest_slug("acme") == "the-real-acme"

    factories.make_organization(slug="the-real-acme")
    assert slugs.suggest_slug("acme") == "the-actual-acme"

    factories.make_organization(slug="the-actual-acme")
    assert slugs.suggest_slug("acme") == "the-one-true-acme"

    factories.make_organization(slug="the-one-true-acme")
    assert slugs.suggest_slug("acme") == "acme-2"

    factories.make_organization(slug="acme-2")
    assert slugs.suggest_slug("acme") == "acme-3"

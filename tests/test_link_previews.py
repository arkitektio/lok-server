"""``linkPreview`` — what kontrol's link pages may show to someone who merely
holds a link, signed in or not.

It is public, so it is strictly opt-in: an organization that did not opt in
looks exactly like one that does not exist, and the sharer is only named when
they opted in, the organization did too, and they really are a member of it.
"""

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from kante.context import HttpContext, TemporalResponse, UniversalRequest

from api.management.schema import schema as management_schema
from karakter import models
from tests import factories
from tests.conftest import build_auth_context

PREVIEW = """
    query ($organization: String!, $user: ID) {
        linkPreview(organization: $organization, user: $user) {
            organization { slug name description }
            inviter { name }
        }
    }
"""

EMPTY = {"linkPreview": {"organization": None, "inviter": None}}


def _anonymous():
    request = UniversalRequest(_extensions={})
    request.set_user(AnonymousUser())
    return HttpContext(request=request, response=TemporalResponse(), headers={}, type="http")


def _setup(org_public: bool, user_public: bool):
    """An organization with a member who shares a link, and an outsider."""
    organization = factories.make_organization(description="We image things.")
    organization.public_link_preview = org_public
    organization.save(update_fields=["public_link_preview"])
    sharer = factories.make_membership(organization=organization)
    models.Profile.objects.update_or_create(
        user=sharer.user, defaults={"name": "Ada Sharer", "public_link_preview": user_public}
    )
    outsider = factories.make_membership()
    models.Profile.objects.update_or_create(
        user=outsider.user, defaults={"name": "Not A Member", "public_link_preview": True}
    )
    return organization, sharer.user, outsider.user


async def _preview(organization, user=None):
    result = await management_schema.execute(
        PREVIEW,
        variable_values={"organization": organization, "user": None if user is None else str(user)},
        context_value=_anonymous(),
    )
    assert not result.errors, result.errors
    return result.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nothing_is_shown_by_default():
    organization, sharer, _ = await sync_to_async(_setup)(org_public=False, user_public=False)

    assert await _preview(organization.slug, sharer.id) == EMPTY


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_public_organization_and_sharer_are_shown_to_an_anonymous_visitor():
    organization, sharer, _ = await sync_to_async(_setup)(org_public=True, user_public=True)

    assert await _preview(organization.slug.upper(), sharer.id) == {
        "linkPreview": {
            "organization": {
                "slug": organization.slug,
                "name": organization.name,
                "description": "We image things.",
            },
            "inviter": {"name": "Ada Sharer"},
        }
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_private_sharer_is_not_named_even_in_a_public_organization():
    organization, sharer, _ = await sync_to_async(_setup)(org_public=True, user_public=False)

    data = await _preview(organization.slug, sharer.id)
    assert data["linkPreview"]["organization"]["slug"] == organization.slug
    assert data["linkPreview"]["inviter"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_public_sharer_is_not_named_for_a_private_organization():
    """Naming them would reveal that they belong to an organization that chose
    not to be seen."""
    organization, sharer, _ = await sync_to_async(_setup)(org_public=False, user_public=True)

    assert await _preview(organization.slug, sharer.id) == EMPTY


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_link_cannot_claim_a_public_user_who_is_not_a_member():
    organization, _, outsider = await sync_to_async(_setup)(org_public=True, user_public=True)

    data = await _preview(organization.slug, outsider.id)
    assert data["linkPreview"]["organization"] is not None
    assert data["linkPreview"]["inviter"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["not-a-number", "999999999", "-1", ""])
async def test_unknown_handles_and_junk_user_ids_answer_empty(user):
    organization, _, _ = await sync_to_async(_setup)(org_public=True, user_public=True)

    assert await _preview("no-such-org", user) == EMPTY
    assert (await _preview(organization.slug, user))["linkPreview"]["inviter"] is None


# --------------------------------------------------------------------------- #
# the two opt-ins
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_owner_opts_the_organization_in_and_a_user_opts_themselves_in():
    def setup():
        organization = factories.make_organization()
        owner = factories.make_membership(user=organization.owner, organization=organization)
        profile, _ = models.Profile.objects.get_or_create(user=organization.owner)
        context = build_auth_context(organization.owner, organization, factories.make_client(membership=owner))
        return organization, profile, context

    organization, profile, context = await sync_to_async(setup)()
    assert (organization.public_link_preview, profile.public_link_preview) == (False, False)

    result = await management_schema.execute(
        "mutation ($input: UpdateOrganizationInput!) { updateOrganization(input: $input) { publicLinkPreview } }",
        variable_values={"input": {"id": str(organization.id), "publicLinkPreview": True}},
        context_value=context,
    )
    assert not result.errors, result.errors
    assert result.data["updateOrganization"]["publicLinkPreview"] is True

    result = await management_schema.execute(
        "mutation ($input: UpdateProfileInput!) { updateProfile(input: $input) { publicLinkPreview } }",
        variable_values={"input": {"id": str(profile.id), "publicLinkPreview": True}},
        context_value=context,
    )
    assert not result.errors, result.errors
    assert result.data["updateProfile"]["publicLinkPreview"] is True

    data = await _preview(organization.slug, organization.owner_id)
    assert data["linkPreview"]["organization"]["slug"] == organization.slug
    assert data["linkPreview"]["inviter"] is not None

import logging

import strawberry
from kante.types import Info

from karakter import models
from api.management import types
from api.management.authz import DENIED, assert_owner_or_admin, get_or_denied
from graphql import GraphQLError
from karakter.authz import resolve_own_media_store

logger = logging.getLogger(__name__)


@strawberry.input
class CreateOrganizationProfileInput:
    organization: strawberry.ID
    name: str


def create_organization_profile(info: Info, input: CreateOrganizationProfileInput) -> types.ManagementOrganizationProfile:
    """Create (or, since a profile row is auto-created for every organization,
    update) the organization's profile."""
    organization = get_or_denied(models.Organization.objects, pk=input.organization)

    assert_owner_or_admin(info, organization)

    # `OrganizationProfile.organization` is a OneToOne and a post_save signal
    # creates the row with the organization, so a plain create() always hit
    # the unique constraint.
    profile, _ = models.OrganizationProfile.objects.update_or_create(
        organization=organization, defaults={"name": input.name}
    )
    return profile


@strawberry.input
class UpdateOrganizationProfileInput:
    id: strawberry.ID
    name: str | None = None
    banner: strawberry.ID | None = None
    avatar: strawberry.ID | None = None


def update_organization_profile(info: Info, input: UpdateOrganizationProfileInput) -> types.ManagementOrganizationProfile:
    profile = get_or_denied(models.OrganizationProfile.objects, pk=input.id)

    assert_owner_or_admin(info, profile.organization)

    if input.name:
        profile.name = input.name
    if input.avatar:
        profile.avatar = resolve_own_media_store(info, input.avatar, models.MediaStore)
    if input.banner:
        profile.banner = resolve_own_media_store(info, input.banner, models.MediaStore)
    profile.save()
    return profile


@strawberry.input
class DeleteOrganizationProfileInput:
    id: strawberry.ID


def delete_organization_profile(info: Info, input: DeleteOrganizationProfileInput) -> strawberry.ID:
    profile = get_or_denied(models.OrganizationProfile.objects, pk=input.id)
    assert_owner_or_admin(info, profile.organization)
    profile.delete()
    return input.id

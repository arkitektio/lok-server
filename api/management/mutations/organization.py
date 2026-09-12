from kante.types import Info
import strawberry
from api.management import types
from karakter import models, managers
from karakter import slugs
import logging
from django.db import IntegrityError
from fakts import models as fakts_models
from fakts.logic import create_hub_from_partner
from graphql import GraphQLError
from api.management.authz import HUB_ADMIN_REQUIRED, get_or_denied, is_owner, is_owner_or_admin
from karakter.authz import resolve_own_media_store

logger = logging.getLogger(__name__)


@strawberry.input
class UpdateOrganizationInput:
    id: strawberry.ID
    name: str | None = None
    description: str | None = None
    avatar: strawberry.ID | None = None
    slug: str | None = None
    brand_hue: float | None = None
    brand_chroma: float | None = None
    require_device_auth: bool | None = None
    access_token_lifetime: int | None = None
    sync_mine: bool = False


def update_organization(info: Info, input: UpdateOrganizationInput) -> types.ManagementOrganization:
    """Update an organization's details including name, description, slug, and avatar.

    Only the organization owner may change these org-wide settings (including the
    default brand hue and chroma).
    """
    organization = get_or_denied(models.Organization.objects, pk=input.id)
    if not is_owner(info.context.request.user, organization):
        raise GraphQLError("You must own the organization to update it.")

    if input.name is not None:
        organization.name = input.name

    if input.description is not None:
        organization.description = input.description

    if input.slug is not None:
        candidate = slugs.normalize_slug(input.slug)
        slugs.validate_slug(candidate)
        if candidate != organization.slug:
            if slugs.is_slug_taken(candidate):
                raise GraphQLError(
                    f"The handle '{candidate}' is already taken. Try '{slugs.suggest_slug(candidate)}'."
                )
        organization.slug = candidate

    if input.avatar is not None:
        organization.avatar = resolve_own_media_store(info, input.avatar, models.MediaStore)

    if input.brand_hue is not None:
        organization.brand_hue = input.brand_hue

    if input.brand_chroma is not None:
        organization.brand_chroma = input.brand_chroma

    if input.require_device_auth is not None:
        organization.require_device_auth = input.require_device_auth

    if input.access_token_lifetime is not None:
        # Imported here, not at module scope: authapp.server builds the whole
        # authorization server (and loads the signing key) at import time, and
        # this module is imported while the GraphQL schema is being assembled.
        from authapp import server

        # Rejected here as well as clamped at issue time (authapp.server
        # .access_token_expires_in): an admin who types 0 or a year should be told
        # so, not silently given something else.
        if not (server.MIN_ACCESS_TOKEN_EXPIRES_IN <= input.access_token_lifetime <= server.MAX_ACCESS_TOKEN_EXPIRES_IN):
            raise GraphQLError(
                f"The access token lifetime must be between {server.MIN_ACCESS_TOKEN_EXPIRES_IN} and "
                f"{server.MAX_ACCESS_TOKEN_EXPIRES_IN} seconds."
            )
        organization.access_token_lifetime = input.access_token_lifetime

    try:
        organization.save()
    except IntegrityError:
        # Closes the check->save race on the slug: another org grabbed it in between.
        raise GraphQLError(
            f"The handle '{organization.slug}' is already taken. Try '{slugs.suggest_slug(organization.slug)}'."
        )

    # sync_mine: also copy the new default hue onto the owner's own membership, so
    # the owner's personal colour matches the org default they just set. Snapshots
    # the value (does not make the membership permanently follow the default).
    if input.sync_mine and (input.brand_hue is not None or input.brand_chroma is not None):
        membership, _ = models.Membership.objects.get_or_create(
            user=info.context.request.user, organization=organization
        )
        synced = []
        if input.brand_hue is not None:
            membership.brand_hue = input.brand_hue
            synced.append("brand_hue")
        if input.brand_chroma is not None:
            membership.brand_chroma = input.brand_chroma
            synced.append("brand_chroma")
        membership.save(update_fields=synced)

    logger.info(f"Updated Organization: {organization.id} with name: {organization.name}")
    return organization


@strawberry.input
class CreateOrganizationInput:
    name: str
    description: str | None = None
    brand_hue: float | None = None
    brand_chroma: float | None = None
    slug: str | None = None


def create_organization(info: Info, input: CreateOrganizationInput) -> types.ManagementOrganization:
    """Create a new organization with the given name, slug, and description.

    When no slug is supplied it is derived cleanly from the name (lowercased,
    hyphenated, no random suffix). The slug is heavily validated and must be
    unique; a taken slug is rejected with a suggested alternative.
    """
    # Prefer an explicit (user-chosen) slug, otherwise derive one from the name.
    candidate = slugs.normalize_slug(input.slug) if input.slug else slugs.slugify_name(input.name)
    slugs.validate_slug(candidate)
    if slugs.is_slug_taken(candidate):
        raise GraphQLError(
            f"The handle '{candidate}' is already taken. Try '{slugs.suggest_slug(candidate)}'."
        )

    try:
        organization = models.Organization.objects.create(
            slug=candidate,
            name=input.name,
            description=input.description,
            brand_hue=input.brand_hue,
            brand_chroma=input.brand_chroma,
            owner=info.context.request.user,
        )
    except IntegrityError:
        # Closes the check->create race: another org grabbed the slug in between.
        raise GraphQLError(
            f"The handle '{candidate}' is already taken. Try '{slugs.suggest_slug(candidate)}'."
        )
    logger.info(f"Created Organization: {organization.id} with name: {organization.name}")
    managers.create_default_roles_for_org(organization)
    managers.add_user_roles(
        user=info.context.request.user,
        organization=organization,
        roles=["admin"],
    )

    # Provision the organization's mesh up front when enabled (IONSCALE_AUTO_CREATE_MESH).
    # ensure_org_mesh is idempotent and degrades gracefully if ionscale isn't
    # configured, so a failed/misconfigured mesh never breaks org creation.
    from django.conf import settings as django_settings

    if getattr(django_settings, "IONSCALE_AUTO_CREATE_MESH", False):
        from ionscale.manager import ensure_org_mesh

        ensure_org_mesh(organization)

    return organization


@strawberry.input
class ChangeOrganizationOwnerInput:
    """Input for transferring ownership of an organization."""

    organization: strawberry.ID = strawberry.field(description="The organization to transfer.")
    new_owner: strawberry.ID = strawberry.field(description="The user who becomes the new owner. Must already be a member.")


def change_organization_owner(info: Info, input: ChangeOrganizationOwnerInput) -> types.ManagementOrganization:
    """Transfer ownership of an organization to another member.

    Only the current owner may do this, and the new owner must already belong to
    the organization — ownership cannot be handed to an unrelated user.
    """
    organization = get_or_denied(models.Organization.objects, id=input.organization)
    if not is_owner(info.context.request.user, organization):
        raise GraphQLError("Only the current owner can transfer ownership of the organization.")

    try:
        new_owner = models.User.objects.get(id=input.new_owner)
    except (models.User.DoesNotExist, ValueError, TypeError):
        raise GraphQLError("The new owner must be a member of the organization.")
    if not organization.memberships.filter(user=new_owner).exists():
        raise GraphQLError("The new owner must be a member of the organization.")

    organization.owner = new_owner
    organization.save()

    logger.info(f"Changed owner of Organization: {organization.id} to User: {new_owner.id}")
    return organization


@strawberry.input
class DeleteOrganizationInput:
    id: strawberry.ID


def delete_organization(info: Info, input: DeleteOrganizationInput) -> strawberry.ID:
    """Delete an organization by its ID."""
    organization = get_or_denied(models.Organization.objects, pk=input.id)
    if not is_owner(info.context.request.user, organization):
        raise GraphQLError("Only the organization owner can delete the organization.")
    organization.delete()
    logger.info(f"Deleted Organization: {organization.id}")
    return input.id


@strawberry.input
class ConnectKommunityPartnerInput:
    partner_id: strawberry.ID
    organization_id: strawberry.ID
    license_signature: str | None = None


def connect_kommunity_partner(info: Info, input: ConnectKommunityPartnerInput) -> types.ManagementHub:
    """Attach a preauthorized kommunity partner hub to an organization."""
    organization = get_or_denied(models.Organization.objects, pk=input.organization_id)
    partner = get_or_denied(fakts_models.KommunityPartner.objects, pk=input.partner_id)
    user = info.context.request.user

    if not is_owner_or_admin(user, organization):
        # Connecting a partner provisions its hub — same bar, same sentence as
        # accepting a hub device code.
        raise GraphQLError(HUB_ADMIN_REQUIRED)
    if partner.partner_kind != "preauthorized":
        raise GraphQLError("Only preauthorized partners can be connected directly.")
    if partner.license_agreement and not (input.license_signature and input.license_signature.strip()):
        raise GraphQLError("You must sign the partner license agreement before continuing.")

    hub = create_hub_from_partner(
        partner=partner,
        organization=organization,
        license_signature=input.license_signature.strip() if input.license_signature else None,
    )
    if hub is None:
        raise GraphQLError("This partner has no preconfigured hub.")

    logger.info(
        "Connected kommunity partner '%s' to organization '%s' as hub '%s'",
        partner.identifier,
        organization.slug,
        hub.identifier,
    )

    return hub

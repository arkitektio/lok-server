import logging

import strawberry
from graphql import GraphQLError
from kante.types import Info

from karakter import models
from api.management import types
from api.management.authz import DENIED, get_or_denied, is_owner, is_owner_or_admin

logger = logging.getLogger(__name__)


@strawberry.input
class UpdateMembershipInput:
    id: strawberry.ID
    roles: list[strawberry.ID] | None = None


def update_membership(info: Info, input: UpdateMembershipInput) -> types.ManagementMembership:
    membership = get_or_denied(models.Membership.objects, pk=input.id)
    organization = membership.organization
    user = info.context.request.user

    # Only the organization's owner or an admin may change a member's roles —
    # otherwise any member could grant themselves (or anyone) admin.
    if not is_owner_or_admin(user, organization):
        raise GraphQLError("You are not allowed to manage memberships for this organization.")

    if input.roles:
        # Roles must belong to the membership's own organization; never let a role
        # id from another organization be attached.
        roles = models.Role.objects.filter(pk__in=input.roles, organization=organization)
        membership.roles.set(roles)
    membership.save()
    return membership


@strawberry.input
class DeleteMembershipInput:
    id: strawberry.ID


def delete_membership(info: Info, input: DeleteMembershipInput) -> strawberry.ID:
    """Remove a member from an organization.

    Permitted for the member themselves (leaving), and for the organization's owner
    or an admin (removing someone) — until now it was self-only, so an organization
    had no way to eject anyone.

    The owner's own membership is the one exception: an admin removing it would
    leave the organization owned by a non-member, and is a step towards taking it
    over. Owners may still leave of their own accord.
    """
    membership = get_or_denied(models.Membership.objects, pk=input.id)
    organization = membership.organization
    user = info.context.request.user

    is_self = membership.user_id == user.id
    if not is_self and not is_owner_or_admin(user, organization):
        raise GraphQLError("You are not allowed to manage memberships for this organization.")

    if not is_self and is_owner(membership.user, organization):
        raise GraphQLError(
            "The organization's owner cannot be removed. Transfer ownership first."
        )

    membership.delete()
    logger.info(
        "Removed user %s from organization %s (by %s)",
        membership.user_id,
        organization.slug,
        user.id,
    )
    return input.id


@strawberry.input
class SetMembershipBrandHueInput:
    organization: strawberry.ID
    # UNSET (field omitted) leaves the stored value alone; an explicit null
    # clears it back to the organization default. The two have to be tellable
    # apart now that hue and chroma share this mutation — otherwise setting one
    # would silently wipe the other.
    brand_hue: float | None = strawberry.UNSET
    brand_chroma: float | None = strawberry.UNSET


def set_membership_brand_hue(
    info: Info, input: SetMembershipBrandHueInput
) -> types.ManagementMembership:
    """Set the requesting user's personal brand hue/chroma for one of their organizations.

    Scoped to the caller's own membership, so a user can only recolor their own
    view of an organization. Pass an explicit null `brand_hue`/`brand_chroma` to
    clear it (falling back to the organization default); omit a field to leave it
    unchanged.
    """
    membership = get_or_denied(
        models.Membership.objects, user=info.context.request.user, organization_id=input.organization
    )

    updated = []
    if input.brand_hue is not strawberry.UNSET:
        membership.brand_hue = input.brand_hue
        updated.append("brand_hue")
    if input.brand_chroma is not strawberry.UNSET:
        membership.brand_chroma = input.brand_chroma
        updated.append("brand_chroma")

    if updated:
        membership.save(update_fields=updated)
    return membership


@strawberry.input
class RequestMembershipInput:
    """Input for asking to become a member of an organization, named by its handle"""

    organization: str = strawberry.field(description="The organization's handle (slug).")
    reason: str | None = None


def request_membership(info: Info, input: RequestMembershipInput) -> bool:
    """Ask to become a member of an organization the caller is not in.

    The management-side door to the same request the main schema's
    `requestMembership` stores (its owner and admins are notified, and approve or
    decline it). It takes the handle rather than the id because that is what
    someone holding a shared link has; an outsider cannot look the id up.

    Always answers `true`, for the same reason as there: a foreign organization
    must look exactly like a missing one, so the answer does not depend on
    whether the handle exists, the caller already belongs, already asked, or was
    recently declined.
    """
    # Imported here: karakter.graphql pulls in the main schema's types, which
    # this module must not need at import time.
    from karakter.graphql.mutations.membership_request import _store_request

    try:
        organization_id = (
            models.Organization.objects.filter(slug=(input.organization or "").strip().lower())
            .values_list("pk", flat=True)
            .first()
        )
        if organization_id is not None:
            _store_request(info.context.request.user, organization_id, input.reason)
    except Exception:
        logger.exception("Could not store a membership request")
    return True


@strawberry.input
class ApproveMembershipRequestInput:
    id: strawberry.ID
    roles: list[str] | None = strawberry.field(
        default=None,
        description="Identifiers of the roles the new member gets. Defaults to `guest`.",
    )


@strawberry.input
class DeclineMembershipRequestInput:
    id: strawberry.ID


def _pending_membership_request(info: Info, id) -> models.MembershipRequest:
    """A pending request the caller may answer: they own or administer its
    organization. Anyone else gets the same denial as a missing id."""
    membership_request = get_or_denied(models.MembershipRequest.objects.select_related("organization"), pk=id)
    if not is_owner_or_admin(info.context.request.user, membership_request.organization):
        raise GraphQLError(DENIED)
    if membership_request.status != models.MembershipRequest.Status.PENDING:
        raise GraphQLError(f"This request has already been {membership_request.status}")
    return membership_request


def approve_membership_request(info: Info, input: ApproveMembershipRequestInput) -> types.ManagementMembership:
    """Approve a pending request to join. Only the organization's owner or admins
    may do this.

    Creates the membership with the given roles, or `guest` when none are named,
    and tells the requester.
    """
    from karakter.graphql.mutations.membership_request import _notify

    membership_request = _pending_membership_request(info, input.id)
    organization = membership_request.organization
    roles = list(models.Role.objects.filter(identifier__in=input.roles or [], organization=organization))
    membership = membership_request.approve(info.context.request.user, roles)

    requester = models.User.objects.prefetch_related("com_channels").get(pk=membership_request.user_id)
    _notify([requester], "You were added", f"You are now a member of {organization.name or organization.slug}.")
    return membership


def decline_membership_request(
    info: Info, input: DeclineMembershipRequestInput
) -> types.ManagementMembershipRequest:
    """Decline a pending request to join. Only the organization's owner or admins
    may do this."""
    membership_request = _pending_membership_request(info, input.id)
    membership_request.decline(info.context.request.user)
    return membership_request

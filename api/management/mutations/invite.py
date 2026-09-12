from kante import Info
import strawberry
from api.management import types
from karakter import models
from django.utils import timezone
from datetime import timedelta
import kante
from graphql import GraphQLError
from api.management.authz import DENIED, get_or_denied, is_owner, is_owner_or_admin


@kante.input
class CreateInviteInput:
    """Input for creating a single-use magic invite link for an organization"""

    organization: strawberry.ID | None
    expires_in_days: int | None = 7
    roles: list[str] | None = None  # Role identifiers to assign
    public: bool = True  # If true, the invite can be previewed before signing in (default)


def create_invite(info: Info, input: CreateInviteInput) -> types.ManagementInvite:
    """
    Create a single-use magic invite link for an organization.

    Returns an invite with a unique token that can be shared.
    The link can only be used once and expires after the specified days.
    If no roles are specified, the 'guest' role will be assigned.
    """
    if input.organization:
        organization = get_or_denied(models.Organization.objects, id=input.organization)
        if not is_owner(info.context.request.user, organization):
            raise GraphQLError("You must own the organization to create an invite")
    else:
        raise GraphQLError("Organization ID must be provided")

    # Calculate expiration date if specified
    expires_at = None
    if input.expires_in_days:
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)

    invite = models.Invite.objects.create(
        created_by=info.context.request.user,
        created_for=organization,
        expires_at=expires_at,
        public=input.public,
    )

    # Assign roles
    if input.roles:
        # Add specified roles
        for role_identifier in input.roles:
            try:
                role = models.Role.objects.get(identifier=role_identifier, organization=organization)
                invite.roles.add(role)
            except models.Role.DoesNotExist:
                pass
    else:
        # Default to guest role if no roles specified
        try:
            guest_role = models.Role.objects.get(identifier="guest", organization=organization)
            invite.roles.add(guest_role)
        except models.Role.DoesNotExist:
            # No guest role exists, invite will have no roles
            pass

    return invite


@kante.input
class AcceptInviteInput:
    """Input for accepting an organization invite"""

    token: str


def accept_invite(info: Info, input: AcceptInviteInput) -> types.ManagementMembership:
    """
    Accept an invite to join an organization.

    Validates the invite token and adds the user to the organization.
    """
    # `Invite.token` is a UUIDField: a non-UUID token used to 500 (ValidationError)
    # instead of reporting an invalid token.
    try:
        invite = get_or_denied(models.Invite.objects, token=input.token)
    except GraphQLError:
        raise GraphQLError("Invalid invite token")

    # Check if invite is still valid
    if not invite.is_valid():
        if invite.status == models.Invite.Status.ACCEPTED:
            raise GraphQLError("This invite has already been accepted")
        elif invite.status == models.Invite.Status.DECLINED:
            raise GraphQLError("This invite has been declined")
        elif invite.status == models.Invite.Status.CANCELLED:
            raise GraphQLError("This invite has been cancelled")
        else:
            raise GraphQLError("This invite has expired")

    user = info.context.request.user
    organization = invite.created_for

    # Check if user is already a member
    existing_membership = models.Membership.objects.filter(
        user=user,
        organization=organization,
    ).first()

    if existing_membership:
        # Mark invite as accepted but don't create duplicate membership
        invite.accept(user)
        return existing_membership

    # Create membership
    membership = models.Membership.objects.create(
        user=user,
        organization=organization,
    )

    # Assign roles from the invite
    invite_roles = invite.roles.all()
    if invite_roles.exists():
        membership.roles.set(invite_roles)
    else:
        # Fallback to guest role if invite has no roles
        try:
            guest_role = models.Role.objects.get(identifier="guest", organization=organization)
            membership.roles.add(guest_role)
        except models.Role.DoesNotExist:
            # No guest role exists, that's okay
            pass

    # Mark invite as accepted
    invite.accept(user)

    return membership


@kante.input
class DeclineInviteInput:
    """Input for declining an organization invite"""

    token: str


def decline_invite(info: Info, input: DeclineInviteInput) -> types.ManagementInvite:
    """
    Decline an invite to join an organization.

    Marks the invite as declined.
    """
    try:
        invite = get_or_denied(models.Invite.objects, token=input.token)
    except GraphQLError:
        raise GraphQLError("Invalid invite token")

    # Check if invite is still pending
    if invite.status != models.Invite.Status.PENDING:
        raise GraphQLError(f"This invite has already been {invite.status}")

    user = info.context.request.user
    invite.decline(user)

    return invite


@kante.input
class CancelInviteInput:
    """Input for cancelling an organization invite"""

    id: strawberry.ID


def cancel_invite(info: Info, input: CancelInviteInput) -> types.ManagementInvite:
    """
    Cancel an invite. The organization's owner or admins may cancel any of its
    invites (an invite is a bearer credential into the organization, so the
    people who govern membership must be able to revoke it, not only whoever
    happened to create it).

    Marks the invite as cancelled.
    """
    invite = get_or_denied(models.Invite.objects.select_related("created_for"), id=input.id)

    if not is_owner_or_admin(info.context.request.user, invite.created_for):
        raise GraphQLError(DENIED)

    # Check if invite is still pending
    if invite.status != models.Invite.Status.PENDING:
        raise GraphQLError(f"Cannot cancel an invite that has been {invite.status}")

    invite.cancel()

    return invite

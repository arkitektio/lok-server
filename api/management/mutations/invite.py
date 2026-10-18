from kante import Info
import strawberry
from api.management import types
from karakter import models
from django.utils import timezone
from datetime import timedelta
import kante
from graphql import GraphQLError
from django.core.exceptions import ValidationError
from api.management.authz import DENIED, get_or_denied, is_owner, is_owner_or_admin
from karakter import collaboration_grants
from karakter.invites import InviteError, redeem_invite


@kante.input
class SharedCollaborationInput:
    """One collaboration of a service instance to grant, with the roles that hold inside it."""

    instance: strawberry.ID = strawberry.field(description="The service instance the collaboration lives in (on a hub of the organization).")
    collaboration: str = strawberry.field(description="The collaboration's id in that service.")
    roles: list[str] = strawberry.field(
        default_factory=list,
        description="Identifiers of the organization's roles that hold for the guest inside the collaboration. Empty: view only.",
    )


def collaboration_entries(collaborations: list[SharedCollaborationInput]) -> list[dict]:
    """The input as the entries ``karakter.collaboration_grants`` validates."""
    return [{"instance": str(s.instance), "collaboration": s.collaboration, "roles": list(s.roles or [])} for s in collaborations]


@kante.input
class CreateInviteInput:
    """Input for creating a single-use magic invite link for an organization"""

    organization: strawberry.ID | None
    expires_in_days: int | None = 7
    roles: list[str] | None = None  # Role identifiers to assign
    public: bool = True  # If true, the invite can be previewed before signing in (default)
    collaborations: list[SharedCollaborationInput] | None = strawberry.field(
        default=None,
        description="Grant only these collaborations of the organization's service instances, each with the roles that hold "
        "inside it, instead of making the holder a member. Whoever accepts becomes a guest that reaches just these.",
    )
    max_uses: int | None = strawberry.field(
        default=None,
        description="For a collaboration invite: how many people may accept it. Null: anyone with the link until it expires.",
    )


def _may_share(user, organization) -> bool:
    """Any full member with a role other than `guest` may grant collaborations of the organization.

    They can already reach all of it, and a guest's roles in a collaboration are
    cut to the granter's own when a token is minted, so granting a part gives
    nothing they could not do themselves.
    """
    if is_owner(user, organization):
        return True
    membership = organization.memberships.filter(user=user, collaboration_grants__isnull=True).first()
    if membership is None:
        return False
    return membership.roles.exclude(identifier="guest").exists()


def create_invite(info: Info, input: CreateInviteInput) -> types.ManagementInvite:
    """
    Create an invite link for an organization.

    An ordinary invite is single-use, makes its holder a member and may only be
    created by the owner. If no roles are specified, the 'guest' role is assigned.

    A collaboration invite (`collaborations`) grants just those collaborations:
    whoever accepts becomes a guest, with no roles in the organization and the
    given roles inside each collaboration. Any member other than a guest may
    create one,
    and it may be accepted by up to `max_uses` people (unlimited when null) until
    it expires.
    """
    if not input.organization:
        raise GraphQLError("Organization ID must be provided")
    organization = get_or_denied(models.Organization.objects, id=input.organization)
    user = info.context.request.user

    shared_collaborations = None
    if input.collaborations is not None:
        if not _may_share(user, organization):
            raise GraphQLError("Only members of the organization can grant its collaborations.")
        try:
            shared_collaborations = collaboration_grants.normalize_collaborations(collaboration_entries(input.collaborations), organization)
        except ValidationError as e:
            raise GraphQLError(" ".join(e.messages))
        if input.roles:
            raise GraphQLError("An invite that grants collaborations grants no roles in the organization.")
        if input.max_uses is not None and input.max_uses < 1:
            raise GraphQLError("max_uses must be at least 1.")
    elif not is_owner(user, organization):
        raise GraphQLError("You must own the organization to create an invite")

    # Calculate expiration date if specified
    expires_at = None
    if input.expires_in_days:
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)

    invite = models.Invite.objects.create(
        created_by=user,
        created_for=organization,
        expires_at=expires_at,
        public=input.public,
        shared_collaborations=shared_collaborations,
        max_uses=input.max_uses if shared_collaborations is not None else None,
    )
    if shared_collaborations is not None:
        return invite

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

    try:
        return redeem_invite(invite, info.context.request.user)
    except InviteError as e:
        raise GraphQLError(str(e))


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
    revoke_shared: bool = strawberry.field(
        default=False,
        description="For a collaboration invite: also take back the collaborations it granted from everyone who accepted it.",
    )


def cancel_invite(info: Info, input: CancelInviteInput) -> types.ManagementInvite:
    """
    Cancel an invite. The organization's owner or admins may cancel any of its
    invites (an invite is a bearer credential into the organization, so the
    people who govern membership must be able to revoke it, not only whoever
    happened to create it).

    Marks the invite as cancelled.
    """
    invite = get_or_denied(models.Invite.objects.select_related("created_for"), id=input.id)
    user = info.context.request.user

    # Whoever granted collaborations may take them back; everything else is for the
    # people who govern membership.
    may_cancel = is_owner_or_admin(user, invite.created_for) or (
        invite.is_collaboration_invite and invite.created_by_id == user.id
    )
    if not may_cancel:
        raise GraphQLError(DENIED)

    if input.revoke_shared and invite.is_collaboration_invite:
        collaboration_grants.revoke_invite_grants(invite)
    elif invite.status != models.Invite.Status.PENDING:
        raise GraphQLError(f"Cannot cancel an invite that has been {invite.status}")

    if invite.status == models.Invite.Status.PENDING:
        invite.cancel()

    return invite

from kante import Info
import strawberry
from graphql import GraphQLError
from karakter import types, models
from karakter.authz import get_or_denied, get_user
from django.utils import timezone
from datetime import timedelta
import kante

from django.core.exceptions import ValidationError
from api.management.authz import assert_owner
from api.management.mutations.invite import SharedCollaborationInput, _may_share, collaboration_entries
from karakter import collaboration_grants
from karakter.invites import InviteError, redeem_invite


@kante.input
class CreateInviteInput:
    """Input for creating a single-use magic invite link for an organization"""

    organization: strawberry.ID | None
    expires_in_days: int | None = 7
    roles: list[str] | None = None  # Role identifiers to assign
    collaborations: list[SharedCollaborationInput] | None = strawberry.field(
        default=None,
        description="Grant only these collaborations of the organization's service instances, each with the roles that hold "
        "inside it, instead of making the holder a member.",
    )
    max_uses: int | None = strawberry.field(
        default=None,
        description="For a collaboration invite: how many people may accept it. Null: anyone with the link until it expires.",
    )


def create_invite(info: Info, input: CreateInviteInput) -> types.Invite:
    """
    Create a single-use magic invite link for an organization.

    Returns an invite with a unique token that can be shared.
    The link can only be used once and expires after the specified days.
    If no roles are specified, the 'guest' role will be assigned.

    Requires the caller to own the organization, matching `createInvite` on the
    management API. An invite token is a bearer credential that grants membership
    and whatever roles it carries, so minting one for an arbitrary organization must
    not be possible just by passing its id.
    """
    if input.organization:
        organization = models.Organization.objects.filter(id=input.organization).first()
    else:
        organization = info.context.request.organization

    shared_collaborations = None
    if input.collaborations is not None:
        # A collaboration invite: any member but a guest may grant what they can reach.
        if organization is None or not _may_share(get_user(info), organization):
            raise GraphQLError("Only members of the organization can grant its collaborations.")
        try:
            shared_collaborations = collaboration_grants.normalize_collaborations(collaboration_entries(input.collaborations), organization)
        except ValidationError as e:
            raise GraphQLError(" ".join(e.messages))
        if input.roles:
            raise GraphQLError("An invite that grants collaborations grants no roles in the organization.")
        if input.max_uses is not None and input.max_uses < 1:
            raise GraphQLError("max_uses must be at least 1.")
    else:
        assert_owner(info, organization)

    # Calculate expiration date if specified
    expires_at = None
    if input.expires_in_days:
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)

    invite = models.Invite.objects.create(
        created_by=info.context.request.user,
        created_for=organization,
        expires_at=expires_at,
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


def accept_invite(info: Info, input: AcceptInviteInput) -> types.Membership:
    """
    Accept an invite to join an organization.

    Validates the invite token and adds the user to the organization.
    """
    # `Invite.token` is a UUIDField: a non-UUID string raised `ValidationError`
    # (a 500) here rather than a clean denial. `get_or_denied` folds that, and a
    # genuinely unknown token, into the uniform denied error.
    invite = get_or_denied(models.Invite.objects, token=input.token)
    try:
        return redeem_invite(invite, get_user(info))
    except InviteError as e:
        raise GraphQLError(str(e))


@kante.input
class DeclineInviteInput:
    """Input for declining an organization invite"""

    token: str


def decline_invite(info: Info, input: DeclineInviteInput) -> types.Invite:
    """
    Decline an invite to join an organization.

    Marks the invite as declined.
    """
    invite = get_or_denied(models.Invite.objects, token=input.token)

    # Check if invite is still pending
    if invite.status != models.Invite.Status.PENDING:
        raise GraphQLError(f"This invite has already been {invite.status}")

    user = get_user(info)
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


def cancel_invite(info: Info, input: CancelInviteInput) -> types.Invite:
    """
    Cancel an invite (only the creator can cancel).

    Marks the invite as cancelled.
    """
    # Scoped to the creator in the lookup itself, so "not yours" and "does not
    # exist" read identically and the id cannot be used as an existence oracle.
    invite = get_or_denied(models.Invite.objects, id=input.id, created_by=get_user(info))

    if input.revoke_shared and invite.is_collaboration_invite:
        collaboration_grants.revoke_invite_grants(invite)
    elif invite.status != models.Invite.Status.PENDING:
        raise GraphQLError(f"Cannot cancel an invite that has been {invite.status}")

    if invite.status == models.Invite.Status.PENDING:
        invite.cancel()

    return invite

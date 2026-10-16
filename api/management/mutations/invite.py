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
from karakter import object_grants
from karakter.invites import InviteError, redeem_invite


@kante.input
class CreateInviteInput:
    """Input for creating a single-use magic invite link for an organization"""

    organization: strawberry.ID | None
    expires_in_days: int | None = 7
    roles: list[str] | None = None  # Role identifiers to assign
    public: bool = True  # If true, the invite can be previewed before signing in (default)
    objects: list[str] | None = strawberry.field(
        default=None,
        description="Share only these objects, as `<hub>/<structure>:<id>[#<reach>]` strings (e.g. `3/@mikro/image:42#rois`), "
        "instead of making the holder a member. Whoever accepts gets a scoped membership that reaches just these.",
    )
    max_uses: int | None = strawberry.field(
        default=None,
        description="For an object invite: how many people may accept it. Null: anyone with the link until it expires.",
    )


def _may_share(user, organization) -> bool:
    """Any full member with a role other than `guest` may share objects of the organization.

    They can already read all of it, so sharing a part grants nothing they could
    not reach themselves.
    """
    if is_owner(user, organization):
        return True
    membership = organization.memberships.filter(user=user, object_grants__isnull=True).first()
    if membership is None:
        return False
    return membership.roles.exclude(identifier="guest").exists()


def create_invite(info: Info, input: CreateInviteInput) -> types.ManagementInvite:
    """
    Create an invite link for an organization.

    An ordinary invite is single-use, makes its holder a member and may only be
    created by the owner. If no roles are specified, the 'guest' role is assigned.

    An object invite (`objects`) shares just those objects: whoever accepts gets a
    scoped membership with no roles. Any member other than a guest may create one,
    and it may be accepted by up to `max_uses` people (unlimited when null) until
    it expires.
    """
    if not input.organization:
        raise GraphQLError("Organization ID must be provided")
    organization = get_or_denied(models.Organization.objects, id=input.organization)
    user = info.context.request.user

    shared_objects = None
    if input.objects is not None:
        if not _may_share(user, organization):
            raise GraphQLError("Only members of the organization can share its objects.")
        try:
            shared_objects = object_grants.normalize_grants(input.objects, organization)
        except ValidationError as e:
            raise GraphQLError(" ".join(e.messages))
        if input.roles:
            raise GraphQLError("An invite that shares objects grants no roles.")
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
        shared_objects=shared_objects,
        max_uses=input.max_uses if shared_objects is not None else None,
    )
    if shared_objects is not None:
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
        description="For an object invite: also take back what it shared from everyone who accepted it.",
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

    # Whoever shared objects may take them back; everything else is for the
    # people who govern membership.
    may_cancel = is_owner_or_admin(user, invite.created_for) or (
        invite.is_object_invite and invite.created_by_id == user.id
    )
    if not may_cancel:
        raise GraphQLError(DENIED)

    if input.revoke_shared and invite.is_object_invite:
        object_grants.revoke_invite_grants(invite)
    elif invite.status != models.Invite.Status.PENDING:
        raise GraphQLError(f"Cannot cancel an invite that has been {invite.status}")

    if invite.status == models.Invite.Status.PENDING:
        invite.cancel()

    return invite

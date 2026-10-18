"""Accepting an invite: the one implementation behind every accept path.

The management schema, the token schema and the server-rendered view all used to
carry their own copy of this; a collaboration invite
(``karakter.collaboration_grants``) made the three diverge for good, so they now
share it.

- An ordinary invite makes its holder a member with the invite's roles (``guest``
  when it names none) and is used up.
- A collaboration invite makes its holder a *scoped* member (a guest) of those
  collaborations, or adds them (and their roles) to the collaborations already
  granted to them. It counts a use and stays open until ``max_uses`` is reached
  or it expires.
- A full member accepting a collaboration invite gets nothing new and spends
  nothing, so opening a shared link as a member never uses it up. (An ordinary
  invite is marked accepted either way, as before.) A scoped member accepting an
  ordinary invite becomes a full member.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from karakter import models, collaboration_grants


class InviteError(Exception):
    """Why an invite cannot be accepted, in words a user can read."""


def check_acceptable(invite: models.Invite) -> None:
    if invite.is_valid():
        return
    if invite.status == models.Invite.Status.ACCEPTED:
        raise InviteError("This invite has already been accepted")
    if invite.status == models.Invite.Status.DECLINED:
        raise InviteError("This invite has been declined")
    if invite.status == models.Invite.Status.CANCELLED:
        raise InviteError("This invite has been cancelled")
    raise InviteError("This invite has expired")


def _assign_invite_roles(membership: models.Membership, invite: models.Invite) -> None:
    invite_roles = invite.roles.all()
    if invite_roles.exists():
        membership.roles.set(invite_roles)
        return
    guest_role = models.Role.objects.filter(identifier="guest", organization=invite.created_for).first()
    if guest_role is not None:
        membership.roles.add(guest_role)


@transaction.atomic
def redeem_invite(invite: models.Invite, user) -> models.Membership:
    """Accept ``invite`` for ``user`` and return their membership. Raises ``InviteError``."""
    invite = models.Invite.objects.select_for_update().get(pk=invite.pk)
    check_acceptable(invite)
    organization = invite.created_for
    membership = models.Membership.objects.filter(user=user, organization=organization).first()

    if invite.is_collaboration_invite:
        if membership is not None and not membership.is_scoped:
            return membership  # a full member: nothing to grant, nothing spent
        if membership is None:
            membership = models.Membership.objects.create(user=user, organization=organization, created_through=invite)
        try:
            changed = collaboration_grants.grant_collaborations(
                membership, invite.shared_collaborations or [], invite=invite, granted_by=invite.created_by
            )
        except ValidationError as e:
            raise InviteError(" ".join(e.messages)) from e
        if not changed:
            return membership  # already had all of it: nothing spent
        invite.uses = F("uses") + 1
        invite.save(update_fields=["uses"])
        invite.refresh_from_db(fields=["uses"])
        if invite.max_uses is not None and invite.uses >= invite.max_uses:
            invite.status = models.Invite.Status.ACCEPTED
            invite.accepted_by = user
            invite.responded_at = timezone.now()
            invite.save(update_fields=["status", "accepted_by", "responded_at"])
        return membership

    if membership is not None and not membership.is_scoped:
        invite.accept(user)
        return membership
    if membership is not None:
        collaboration_grants.upgrade_to_full(membership)
        membership.created_through = invite
        membership.save(update_fields=["created_through"])
    else:
        membership = models.Membership.objects.create(user=user, organization=organization, created_through=invite)
    _assign_invite_roles(membership, invite)
    invite.uses = F("uses") + 1
    invite.save(update_fields=["uses"])
    invite.accept(user)
    return membership

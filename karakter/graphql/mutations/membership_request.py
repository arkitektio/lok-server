import logging
import threading
from datetime import timedelta

import kante
import strawberry
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError
from kante import Info

from api.management.authz import is_owner_or_admin
from karakter import models, types
from karakter.authz import DENIED, get_scoped_or_denied, get_user

logger = logging.getLogger(__name__)

# How many requests one user may have open at a time, across organizations.
MAX_PENDING_REQUESTS = 10
# How long a declined request keeps the same user from asking that organization again.
DECLINE_COOLDOWN = timedelta(days=7)


@kante.input
class RequestMembershipInput:
    """Input for asking to become a member of an organization"""

    organization: strawberry.ID
    reason: str | None = None


def _notify(recipients, title: str, message: str) -> None:
    """Push to memberships (or users) off the request thread.

    Delivery is an HTTP call per device; done inline it would make a stored
    request measurably slower than an ignored one. `recipients` arrive with
    their channels prefetched, so the thread never touches the database.
    """

    def run():
        for recipient in recipients:
            try:
                recipient.notify(title, message)
            except Exception:
                # Muted, or no device: asking still worked.
                pass

    threading.Thread(target=run, daemon=True).start()


def _store_request(user, organization_id, reason) -> None:
    try:
        organization = models.Organization.objects.get(pk=organization_id)
    except (models.Organization.DoesNotExist, ValueError, TypeError):
        return
    if models.Membership.objects.filter(user=user, organization=organization).exists():
        return

    mine = models.MembershipRequest.objects.filter(user=user)
    recent = Q(status=models.MembershipRequest.Status.PENDING) | Q(
        status=models.MembershipRequest.Status.DECLINED,
        responded_at__gte=timezone.now() - DECLINE_COOLDOWN,
    )
    if mine.filter(recent, organization=organization).exists():
        return
    if mine.filter(status=models.MembershipRequest.Status.PENDING).count() >= MAX_PENDING_REQUESTS:
        return

    try:
        models.MembershipRequest.objects.create(
            user=user, organization=organization, reason=(reason or "")[:2000] or None
        )
    except IntegrityError:
        # Lost a race against the same request.
        return

    admins = list(
        models.Membership.objects.filter(
            Q(user=organization.owner) | Q(roles__identifier="admin"), organization=organization
        )
        .distinct()
        .select_related("user")
        .prefetch_related("user__com_channels")
    )
    _notify(admins, "Request to join", f"{user.username} asks to join {organization.name or organization.slug}.")


def request_membership(info: Info, input: RequestMembershipInput) -> bool:
    """Ask to become a member of an organization the caller is not in.

    The one mutation on this schema that deliberately reaches outside the
    organization named by the caller's token: the whole point is that they are
    not a member there yet.

    Always answers `true`. A foreign organization must look exactly like a
    missing one on this schema, so the answer is the same whether the
    organization exists, the caller already belongs to it, already asked, was
    recently declined, or has too many requests open. Only the organization's
    owner and admins ever learn that a request was stored.
    """
    user = get_user(info)
    try:
        _store_request(user, input.organization, input.reason)
    except Exception:
        logger.exception("Could not store a membership request")
    return True


@kante.input
class ApproveMembershipRequestInput:
    """Input for letting a requester into the organization"""

    id: strawberry.ID
    roles: list[str] | None = None  # Role identifiers to assign


@kante.input
class DeclineMembershipRequestInput:
    """Input for declining a request to join the organization"""

    id: strawberry.ID


def _pending_request(info: Info, id) -> models.MembershipRequest:
    """A pending request of the caller's organization, for its owner or admins.

    Scoped in the lookup, and a member who may not answer gets the same denial
    as a missing id.
    """
    membership_request = get_scoped_or_denied(models.MembershipRequest.objects, info, id=id)
    if not is_owner_or_admin(get_user(info), membership_request.organization):
        raise GraphQLError(DENIED)
    if membership_request.status != models.MembershipRequest.Status.PENDING:
        raise GraphQLError(f"This request has already been {membership_request.status}")
    return membership_request


def approve_membership_request(info: Info, input: ApproveMembershipRequestInput) -> types.Membership:
    """Approve a pending request to join. Only the organization's owner or admins
    may do this.

    Creates the membership with the given roles, or `guest` when none are named.
    """
    membership_request = _pending_request(info, input.id)
    roles = list(
        models.Role.objects.filter(identifier__in=input.roles or [], organization=membership_request.organization)
    )
    membership = membership_request.approve(get_user(info), roles)

    organization = membership_request.organization
    requester = models.User.objects.prefetch_related("com_channels").get(pk=membership_request.user_id)
    _notify([requester], "You were added", f"You are now a member of {organization.name or organization.slug}.")
    return membership


def decline_membership_request(info: Info, input: DeclineMembershipRequestInput) -> types.MembershipRequest:
    """Decline a pending request to join. Only the organization's owner or admins
    may do this."""
    membership_request = _pending_request(info, input.id)
    membership_request.decline(get_user(info))
    return membership_request

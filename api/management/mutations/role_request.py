import logging

import strawberry
from kante.types import Info

from karakter import models
from api.management import types
from graphql import GraphQLError
from api.management.authz import get_or_denied, is_owner_or_admin

logger = logging.getLogger(__name__)


@strawberry.input
class RequestRoleInput:
    organization: strawberry.ID
    role: strawberry.ID
    reason: str | None = None


def request_role(info: Info, input: RequestRoleInput) -> types.ManagementRoleRequest:
    """Request an additional role in one of the caller's organizations.

    Scoped to the caller's own membership (same pattern as
    set_membership_brand_hue), so a user can only request roles for organizations
    they already belong to. The role must belong to that organization, the member
    must not already hold it, and there must be no pending request for it yet.
    """
    request = info.context.request
    membership = get_or_denied(
        models.Membership.objects, user=request.user, organization_id=input.organization
    )
    role = get_or_denied(models.Role.objects, pk=input.role)

    if role.organization_id != membership.organization_id:
        raise GraphQLError("That role does not belong to this organization.")
    if membership.roles.filter(pk=role.pk).exists():
        raise GraphQLError("You already have this role.")
    if models.RoleRequest.objects.filter(
        membership=membership, role=role, status=models.RoleRequest.Status.PENDING
    ).exists():
        raise GraphQLError("You already have a pending request for this role.")

    return models.RoleRequest.objects.create(
        membership=membership, role=role, reason=input.reason
    )


@strawberry.input
class ResolveRoleRequestInput:
    id: strawberry.ID


def approve_role_request(info: Info, input: ResolveRoleRequestInput) -> types.ManagementRoleRequest:
    """Approve a pending role request. Only the organization's owner or admins
    may do this.

    Approval adds the requested role to the member's membership.
    """
    role_request = get_or_denied(models.RoleRequest.objects, pk=input.id)
    if not is_owner_or_admin(info.context.request.user, role_request.membership.organization):
        raise GraphQLError("You must own or administer the organization to approve role requests.")
    role_request.approve(info.context.request.user)
    return role_request


def decline_role_request(info: Info, input: ResolveRoleRequestInput) -> types.ManagementRoleRequest:
    """Decline a pending role request. Only the organization's owner or admins
    may do this."""
    role_request = get_or_denied(models.RoleRequest.objects, pk=input.id)
    if not is_owner_or_admin(info.context.request.user, role_request.membership.organization):
        raise GraphQLError("You must own or administer the organization to decline role requests.")
    role_request.decline(info.context.request.user)
    return role_request


def cancel_role_request(info: Info, input: ResolveRoleRequestInput) -> strawberry.ID:
    """Withdraw one's own role request. Only the requesting member may do this."""
    role_request = get_or_denied(models.RoleRequest.objects, pk=input.id)
    if role_request.membership.user_id != info.context.request.user.id:
        raise GraphQLError("You can only cancel your own role requests.")
    role_request.delete()
    return input.id

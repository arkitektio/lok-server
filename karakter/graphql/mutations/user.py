from kante import Info
import strawberry
from karakter import types, models
import logging
import kante
from graphql import GraphQLError

from api.management.authz import DENIED, assert_owner_or_admin

logger = logging.getLogger(__name__)


@kante.input
class AddUserToOrganizationInput:
    user: strawberry.ID
    organization: strawberry.ID
    roles: list[str]


def add_user_to_organization(info: Info, input: AddUserToOrganizationInput) -> types.Membership:
    """Add a user to an organization and set their roles.

    Requires the caller to own the organization or hold its ``admin`` role — this
    mutation sets roles wholesale (including ``admin``), so it is the same
    privileged operation as ``updateMembership`` on the management API and carries
    the same bar. Without that check any authenticated principal could add anyone
    to any organization as an admin.
    """
    organization = models.Organization.objects.filter(pk=input.organization).first()
    assert_owner_or_admin(info, organization)

    # The caller-side bar is right; the *target* side had none. `input.user` was
    # an arbitrary pk and the person named by it never consented — which is what
    # the invite flow exists to model. And "org admin" is not a barrier here:
    # any authenticated user can create an organization and be made its owner
    # and admin in the same call. So this was: create an org, attach every
    # sequential user pk, then read `users { username email }` — a
    # deployment-wide directory harvest.
    #
    # A user may now only be attached directly if they already have a
    # relationship with this organization (an existing membership whose roles
    # are being changed, or a pending/accepted invite). Everyone else has to be
    # invited, which is the consented path.
    target = models.User.objects.filter(pk=input.user).first()
    if target is None:
        raise GraphQLError(DENIED)

    already_related = (
        models.Membership.objects.filter(user=target, organization=organization).exists()
        or models.Invite.objects.filter(
            created_for=organization, accepted_by=target
        ).exists()
        or models.Invite.objects.filter(
            created_for=organization,
            email__iexact=(target.email or "\x00"),
        ).exists()
    )
    if not already_related:
        raise GraphQLError(
            "This user has not been invited to the organization. Create an invite "
            "for them instead — a person cannot be added to an organization "
            "without their consent."
        )

    membership, _ = models.Membership.objects.get_or_create(
        user=target,
        organization=organization,
    )

    roles = list(models.Role.objects.filter(identifier__in=input.roles, organization=organization))
    if len(roles) != len(set(input.roles)):
        # Don't half-apply: an unknown identifier means the caller's intent is
        # unclear, and silently dropping it could leave the member over- or
        # under-privileged relative to what was asked for.
        known = {role.identifier for role in roles}
        unknown = sorted(set(input.roles) - known)
        raise GraphQLError(f"Unknown role(s) for this organization: {', '.join(unknown)}")

    membership.roles.set(roles)
    logger.info(
        "Set roles %s for user %s in organization %s",
        sorted(role.identifier for role in roles),
        input.user,
        organization.slug,
    )
    return membership


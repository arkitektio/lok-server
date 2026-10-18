import strawberry
from graphql import GraphQLError
from kante.types import Info

from api.management.authz import DENIED, get_or_denied, is_owner_or_admin
from karakter import models, collaboration_grants


@strawberry.input
class RevokeCollaborationGrantInput:
    id: strawberry.ID


def revoke_collaboration_grant(info: Info, input: RevokeCollaborationGrantInput) -> strawberry.ID:
    """Take back one granted collaboration.

    The organization's owner and admins may take back any collaboration granted in it,
    whoever granted a collaboration may take it back, and the guest it was granted to
    may give it up. Taking back someone's last collaboration removes them from the
    organization altogether (they were only in it for what was granted).
    """
    grant = get_or_denied(models.CollaborationGrant.objects.select_related("membership__organization"), pk=input.id)
    user = info.context.request.user
    allowed = (
        is_owner_or_admin(user, grant.membership.organization)
        or grant.granted_by_id == user.id
        or grant.membership.user_id == user.id
    )
    if not allowed:
        raise GraphQLError(DENIED)
    grant_id = grant.pk
    collaboration_grants.revoke_grant(grant)
    return strawberry.ID(str(grant_id))

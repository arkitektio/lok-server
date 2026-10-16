import strawberry
from graphql import GraphQLError
from kante.types import Info

from api.management.authz import DENIED, get_or_denied, is_owner_or_admin
from karakter import models, object_grants


@strawberry.input
class RevokeObjectGrantInput:
    id: strawberry.ID


def revoke_object_grant(info: Info, input: RevokeObjectGrantInput) -> strawberry.ID:
    """Take back one shared object.

    The organization's owner and admins may take back anything shared from it,
    whoever shared an object may take it back, and the person it was shared with
    may give it up. Taking back someone's last object removes them from the
    organization altogether (they were only in it for what was shared).
    """
    grant = get_or_denied(models.ObjectGrant.objects.select_related("membership__organization"), pk=input.id)
    user = info.context.request.user
    allowed = (
        is_owner_or_admin(user, grant.membership.organization)
        or grant.granted_by_id == user.id
        or grant.membership.user_id == user.id
    )
    if not allowed:
        raise GraphQLError(DENIED)
    grant_id = grant.pk
    object_grants.revoke_grant(grant)
    return strawberry.ID(str(grant_id))

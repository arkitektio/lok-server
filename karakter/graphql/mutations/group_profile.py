from kante.types import Info
import strawberry
from graphql import GraphQLError
from karakter import types, models
from karakter.authz import get_or_denied, get_user, resolve_own_media_store
import logging

logger = logging.getLogger(__name__)


def _group_the_caller_belongs_to(info: Info, group_id: strawberry.ID) -> models.Group:
    """Fetch a group the caller is a member of, or deny.

    Both resolvers here previously fetched by bare pk with no check at all, so any
    principal could create or overwrite the profile of any group in the
    deployment.
    """
    # `User.groups` is declared with `related_query_name="karakter_user"`;
    # the previous `user=` lookup did not exist and raised on every call.
    return get_or_denied(models.Group.objects, pk=group_id, karakter_user=get_user(info))


@strawberry.input
class CreateGroupProfileInput:
    group: strawberry.ID
    name: str
    avatar: strawberry.ID


def create_group_profile(info: Info, input: CreateGroupProfileInput) -> types.GroupProfile:
    trace = _group_the_caller_belongs_to(info, input.group)
    profile = models.GroupProfile(group=trace, name=input.name, avatar=resolve_own_media_store(info, input.avatar, models.MediaStore))
    profile.save()
    return profile


@strawberry.input
class UpdateGroupProfileInput:
    id: strawberry.ID
    name: str
    avatar: strawberry.ID
    
    
def update_group_profile(info: Info, input: UpdateGroupProfileInput) -> types.GroupProfile:
    profile = get_or_denied(models.GroupProfile.objects, pk=input.id, group__karakter_user=get_user(info))
    profile.name = input.name
    profile.avatar = resolve_own_media_store(info, input.avatar, models.MediaStore)

    logger.info(f'Updated GroupProfile: {profile.id} with name: {profile.name} and avatar: {profile.avatar}')
    profile.save()
    return profile






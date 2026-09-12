from kante.types import Info
import strawberry
from karakter import types, models
from karakter.authz import assert_is_self, get_or_denied, get_user, resolve_own_media_store
import logging

logger = logging.getLogger(__name__)


@strawberry.input
class CreateProfileInput:
    user: strawberry.ID
    name: str


def create_profile(info: Info, input: CreateProfileInput) -> types.Profile:
    """Create the calling user's profile.

    `input.user` is retained for API compatibility but must name the caller — the
    management twin (`api.management.mutations.profile.create_profile`) applies
    the same rule. Previously any principal could create a profile for any user.
    """
    assert_is_self(info, input.user)
    trace = get_or_denied(models.User.objects, pk=input.user)
    profile = models.Profile(user=trace, name=input.name)
    profile.save()
    return profile


@strawberry.input
class UpdateProfileInput:
    id: strawberry.ID
    name: str
    avatar: strawberry.ID


def update_profile(info: Info, input: UpdateProfileInput) -> types.Profile:
    """Update the calling user's own profile."""
    profile = get_or_denied(models.Profile.objects, pk=input.id, user=get_user(info))
    profile.name = input.name
    profile.avatar = resolve_own_media_store(info, input.avatar, models.MediaStore)
    profile.save()
    return profile






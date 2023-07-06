from lord.filters import GroupFilter
from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types
from lord import models
from lord import filters
from oauth2_provider.models import get_application_model, Application
from django.conf import settings
import graphene
from django.db.models import Q


class ChannelDetail(BalderQuery):
    """Get a group"""

    class Arguments:
        id = graphene.ID(description="Unique app name fddor user", required=False)

    def resolve(
        root,
        info,
        name=None,
        id=None,
    ):
        return models.Channel.objects.get(id=id)

    class Meta:
        list = False
        type = types.Channel
        operation = "channel"


class Groups(BalderQuery):
    """Get a list of users"""

    class Meta:
        list = True
        type = types.Channel
        filter = filters.ChannelFilter
        operation = "channels"

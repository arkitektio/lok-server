from lord.filters import GroupFilter
from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types
from lord import models
from oauth2_provider.models import get_application_model, Application
from django.conf import settings
import graphene
from django.db.models import Q
import logging
from django.contrib.auth.models import Group as HerreGroupModel


class GroupDetail(BalderQuery):
    """Get a group"""

    class Arguments:
        id = graphene.ID(description="Unique app name fddor user", required=False)
        name = graphene.String(description="Unique app name fddor user", required=False)

    def resolve(
        root,
        info,
        name=None,
        id=None,
    ):
        if name:
            return HerreGroupModel.objects.get(name=name)
        if id:
            return HerreGroupModel.objects.get(id=id)

    class Meta:
        list = False
        type = types.Group
        operation = "group"


class Groups(BalderQuery):
    """Get a list of users"""

    class Meta:
        list = True
        type = types.Group
        filter = GroupFilter
        operation = "groups"


class MyGroups(BalderQuery):
    """Get a list of users"""

    class Arguments:
        name = graphene.String(description="Unique app name for user")

    def resolve(root, info, *args, name=None, client_id=None):
        return info.context.user.groups.all()

    class Meta:
        list = True
        type = types.Group
        filter = GroupFilter
        operation = "mygroups"

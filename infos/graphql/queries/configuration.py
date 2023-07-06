from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from infos import types, models, filters
from django.conf import settings
import logging
import graphene


class Configuration(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The FaktApp ID")
        name = graphene.String(description="Unique app name for user")

    def resolve(root, info, *args, name=None, id = None):
        if id:
            return models.ConfigurationTemplate.objects.get(id=id)
        if name:
            return models.ConfigurationTemplate.objects.get(name=name)

    class Meta:
        list = False
        type = types.Configuration
        operation = "configuration"



class COnfigurationsQuery(BalderQuery):

    class Meta:
        list = True
        type = types.Configuration
        filter = filters.ConfigurationFilter
        operation = "configurations"
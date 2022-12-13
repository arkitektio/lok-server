from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from infos import types, models
from django.conf import settings
import logging
import graphene


logger = logging.getLogger(__name__)


class PrivateFaktAppQuery(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The FaktApp ID")
        identifier = graphene.String(description="Unique app name for user")
        version = graphene.String(description="Unique app name for user")

    def resolve(root, info, *args, name=None, identifier=None, version=None, id=None):
        if id:
            return models.FaktApplication.objects.get(id=id)
        
        return models.FaktApplication.objects.get(identifier=identifier, version=version, user=info.context.user)

    class Meta:
        list = False
        type = types.FaktApplication
        operation = "privatefaktapp"


class PrivateFaktAppsQuery(BalderQuery):
    def resolve(root, info):
        return models.FaktApplication.objects.filter(application__authorization_grant_type="client-credentials", creator=info.context.user)

    class Meta:
        list = True
        type = types.FaktApplication
        operation = "privatefaktapps"




class PublicFaktAppQuery(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The FaktApp ID")
        identifier = graphene.String(description="Unique app name for user")
        version = graphene.String(description="Unique app name for user")

    def resolve(root, info, *args, name=None, identifier=None, version=None, id = None):
        if id:
            return models.FaktApplication.objects.get(id=id)


        return models.FaktApplication.objects.get(identifier=identifier, version=version)

    class Meta:
        list = False
        type = types.FaktApplication
        operation = "publicfaktapp"


class PublicFaktAppsQuery(BalderQuery):
    def resolve(root, info):
        return models.FaktApplication.objects.filter(application__authorization_grant_type="authorization-code", creator=info.context.user)

    class Meta:
        list = True
        type = types.FaktApplication
        operation = "publicfaktapps"

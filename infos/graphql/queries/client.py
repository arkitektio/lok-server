from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from infos import types, models
from django.conf import settings
import logging
import graphene


logger = logging.getLogger(__name__)


class ClientQuery(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The FaktApp ID")
        client_id = graphene.ID(description="The client id of one associated oauth2 application")
        token = graphene.ID(description="The FaktApp ID")

    def resolve(root, info, *args, token=None, id=None, client_id=None):
        if id:
            return models.Client.objects.get(id=id)
        if client_id:
            return models.Client.objects.get(oauth2_client__client_id=client_id)
        
        return models.Client.objects.get(token=token)

    class Meta:
        list = False
        type = types.Client
        operation = "client"


class MyPrivateClients(BalderQuery):
    def resolve(root, info):
        return models.Client.objects.filter(application__authorization_grant_type="client-credentials", creator=info.context.user)

    class Meta:
        list = True
        type = types.Client
        operation = "myPrivateClients"


class Clients(BalderQuery):

    class Meta:
        list = True
        type = types.Client
        operation = "clients"

class MyPublicApps(BalderQuery):
    def resolve(root, info):
        return models.Client.objects.filter(application__authorization_grant_type="authorization-code", creator=info.context.user)

    class Meta:
        list = True
        type = types.Client
        operation = "myPublicClients"

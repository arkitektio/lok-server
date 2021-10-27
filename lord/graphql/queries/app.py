from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types
from lord import models
from oauth2_provider.models import get_application_model
from django.conf import settings
import graphene



ApplicationModel = get_application_model()


class UserApplicationQuery(BalderQuery):

    class Arguments:
        name = graphene.String(description="Unique app name for user")
        client_id = graphene.ID(description="Unique app name for user")
    
    def resolve(root, info, *args, name = None, client_id=None):
        if client_id: return ApplicationModel.objects.get(client_id=client_id, user=info.context.user)
        if name: return ApplicationModel.objects.get(name=name, user=info.context.user)

    class Meta:
        list = False
        type = types.Application
        operation = "userapp"




class ApplicationsQuery(BalderQuery):

    resolve = lambda root, info: ApplicationModel.objects.all()

    class Meta:
        list = True
        type = types.Application
        operation = "applications"


class ApplicationQuery(BalderQuery):

    class Arguments:
        client_id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, client_id: ApplicationModel.objects.get(client_id=client_id)

    class Meta:
        type = types.Application
        operation = "application"
        
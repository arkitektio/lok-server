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

ApplicationModel = get_application_model()


logger = logging.getLogger(__name__)


class UserApplicationQuery(BalderQuery):
    class Arguments:
        name = graphene.String(description="Unique app name for user")
        client_id = graphene.ID(description="Unique app name for user")

    def resolve(root, info, *args, name=None, client_id=None):
        baseqs = ApplicationModel.objects.filter(
            Q(user=info.context.user) | Q(client_type=Application.CLIENT_PUBLIC)
        )
        if client_id:
            return baseqs.get(client_id=client_id)
        if name:
            return baseqs.get(name=name)

    class Meta:
        list = False
        type = types.Application
        operation = "userapp"


class ApplicationsQuery(BalderQuery):
    def resolve(root, info):
         
        return ApplicationModel.objects.all()

    class Meta:
        list = True
        type = types.Application
        operation = "applications"


class ApplicationsQuery(BalderQuery):
    def resolve(root, info):
         
        return ApplicationModel.objects.filter(user=info.context.user)

    class Meta:
        list = True
        type = types.Application
        operation = "myapplications"


class ApplicationQuery(BalderQuery):
    class Arguments:
        client_id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, client_id: ApplicationModel.objects.get(
        client_id=client_id
    )

    class Meta:
        type = types.Application
        operation = "application"


class ApplicationsQuery(BalderQuery):
    def resolve(root, info):
         
        return ApplicationModel.objects.all()

    class Meta:
        list = True
        type = types.Application
        operation = "clients"


class ApplicationsQuery(BalderQuery):
    def resolve(root, info):
         
        return ApplicationModel.objects.filter(user=info.context.user)

    class Meta:
        list = True
        type = types.Application
        operation = "myclients"


class ApplicationQuery(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The ID to search by", required=True)

    resolve = lambda root, info, id: ApplicationModel.objects.get(
        client_id=id
    )

    class Meta:
        type = types.Application
        operation = "client"

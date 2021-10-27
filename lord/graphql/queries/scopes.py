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


class Scope(graphene.ObjectType):
    value = graphene.String()
    label = graphene.String()
    description = graphene.String()


provider_settings = settings.OAUTH2_PROVIDER

scopelist = [{"value": key, "label": key, "description": value} for key, value in provider_settings["SCOPES"].items()]


class ScopesQuery(BalderQuery):

    def resolve(root, info, *args, **kwargs):
        return scopelist

    class Meta:
        list = True
        type = Scope
        operation = "scopes"

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
    value = graphene.String(required=True)
    label = graphene.String(required=True)
    description = graphene.String()


provider_settings = settings.OAUTH2_PROVIDER


def return_filtered_scopes(search):
    return [
        Scope(value=key, label=value, description=provider_settings["SCOPES"][key])
        for key, value in provider_settings["SCOPES"].items()
        if key.startswith(search)
    ]


scopelist = [
    {"value": key, "label": key, "description": value}
    for key, value in provider_settings["SCOPES"].items()
]


class ScopesQuery(BalderQuery):
    class Arguments:
        search = graphene.String(description="Unique app name for user")

    def resolve(root, info, *args, search=None):
        if not search:

            return scopelist
        return return_filtered_scopes(search)

    class Meta:
        list = True
        type = Scope
        operation = "scopes"


class Scope(BalderQuery):
    class Arguments:
        key = graphene.String(description="Unique app name for user", required=True)

    def resolve(root, key):
        return return_filtered_scopes(key)

    class Meta:
        type = Scope
        operation = "scope"

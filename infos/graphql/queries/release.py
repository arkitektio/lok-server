from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from infos import types, models, filters
from django.conf import settings
import logging
import graphene


class Release(BalderQuery):
    class Arguments:
        id = graphene.ID(description="The FaktApp ID")
        identifier = graphene.String(description="Unique app name for user")
        version = graphene.String(description="Unique app name for user")
        client_id = graphene.ID(
            description="The client id of one associated oauth2 application"
        )

    def resolve(
        root,
        info,
        *args,
        name=None,
        identifier=None,
        version=None,
        id=None,
        client_id=None
    ):
        if id:
            return models.Release.objects.get(id=id)
        if client_id:
            return models.Client.objects.get(client_id=client_id).release

        return models.Release.objects.get(app__identifier=identifier, version=version)

    class Meta:
        list = False
        type = types.Release
        operation = "release"


class ReleaseQuery(BalderQuery):
    class Meta:
        list = True
        type = types.Release
        filter = filters.ReleaseFilter
        operation = "releases"

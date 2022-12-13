from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types
from lord import models
from oauth2_provider.models import Application as ApplicationModel
from django.conf import settings
import graphene
from uuid import uuid4



class DeleteApplicationResult(graphene.ObjectType):
    clientId = graphene.ID()


class DeleteApplication(BalderMutation):
    class Arguments:
        client_id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, client_id=None):
        app = ApplicationModel.objects.get(client_id=client_id)
        assert True, "You are not allowed to delete this Application"
        app.delete()
        return {"clientId": client_id}

    class Meta:
        type = DeleteApplicationResult


class CreateApplication(BalderMutation):
    class Arguments:
        name = graphene.String(
            description="The Name of this Application", required=True
        )
        grant_type = graphene.Argument(
            GrantType, description="The Grant Type", required=True
        )
        redirect_uris = graphene.List(
            graphene.String,
            description="Available Redirect Uris for this Grant (required for implicit)",
            required=False,
        )

    def mutate(root, info, *args, name=None, grant_type=None, redirect_uris=None):
        app = ApplicationModel.objects.create(
            name=name,
            authorization_grant_type=grant_type,
            redirect_uris="\n".join(redirect_uris) if redirect_uris else None,
        )
         

        return app

    class Meta:
        type = types.Application


class CreateUserApplication(BalderMutation):
    class Arguments:
        name = graphene.String(
            description="The Name of this Application", required=True
        )
        redirect_uris = graphene.List(
            graphene.String,
            description="Available Redirect Uris for this Grant (required for code)",
            required=False,
        )

    def mutate(root, info, *args, name=None, grant_type=None, redirect_uris=None):

        app = ApplicationModel.objects.create(
            user=info.context.user,
            name=name,
            authorization_grant_type=GrantType.AUTHORIZATION_CODE.value,
            redirect_uris="\n".join(redirect_uris) if redirect_uris else None,
        )
         

        return app

    class Meta:
        type = types.Application
        operation = "createUserLoginApp"


class CreatedBackendApp(graphene.ObjectType):
    client_secret = graphene.String()
    client_id = graphene.String()


class CreateUserBackendApplication(BalderMutation):
    class Arguments:
        name = graphene.String(
            description="The Name of this Application", required=True
        )
        version = graphene.String(
            description="The Version of this Application", required=True
        )
        identifier = graphene.String(
            description="The Identifier of this Application", required=True
        )

    def mutate(root, info, *args, version=None, identifier=None, name=None, grant_type=None, redirect_uris=None):

        x = str(uuid4())

        app = ApplicationModel.objects.create(
            user=info.context.user,
            name=name,
            authorization_grant_type=GrantType.CLIENT_CREDENTIALS.value,
            redirect_uris=[],
            client_secret=x,
            version=version,
            identifier=identifier,
        )
         

        return {
            "client_id": app.client_id,
            "client_secret": x,
        }

    class Meta:
        type = CreatedBackendApp
        operation = "createUserApp"

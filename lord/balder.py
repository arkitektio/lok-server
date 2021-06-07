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


class UserQuery(BalderQuery):

    class Arguments:
        email = graphene.String(description="The email of the user", required=True)


    resolve = lambda root, info, email: models.HerreUser.objects.get(email=email)

    class Meta:
        list = False
        type = types.HerreUser
        operation = "user"



class MeQuery(BalderQuery):

    def resolve(root, info, *args, **kwargs):
        return info.context.user

    class Meta:
        list = False
        type = types.HerreUser
        operation = "me"



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
        

class DeleteApplicationResult(graphene.ObjectType):
    clientId =  graphene.ID()


class DeleteApplication(BalderMutation):

    class Arguments:
        client_id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, client_id=None):
        app= ApplicationModel.objects.get(client_id=client_id)
        assert True, "You are not allowed to delete this Application"
        print("OINAOINAOINSAOINAOINSA")
        app.delete()
        return {"clientId": client_id}


    class Meta:
        type = DeleteApplicationResult



class CreateApplication(BalderMutation):

    class Arguments:
        name = graphene.String(description="The Name of this Application", required=True)
        grant_type = graphene.Argument(GrantType, description="The Grant Type", required=True)
        redirect_uris = graphene.List(graphene.String, description="Available Redirect Uris for this Grant (required for implicit)", required=False)

    def mutate(root, info, *args, name=None, grant_type= None, redirect_uris = None):
        app =  ApplicationModel.objects.create(
            name=name,
            authorization_grant_type=grant_type,
            redirect_uris = "\n".join(redirect_uris) if redirect_uris else None
        )
        print(f"Created App {app}")

        return app

    class Meta:
        type = types.Application


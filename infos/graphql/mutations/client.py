from balder.types import BalderMutation, BalderQuery
from infos import types, models, scalars
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import Application
import graphene
from infos.utils import create_api_token


class CreatePrivateClient(BalderMutation):
    class Arguments:
        version = graphene.String(
            required=True, description="The Repo of the Docker (Repo on Dockerhub)"
        )
        identifier = graphene.String(
            description="The Name of this Elemet",
            required=True,
        )
        imitate = graphene.ID(
            description="The ID of the User to imitate (only managers can do this)",
            required=False,
        )
        logo_url = graphene.String(
            description="The Logo of this Apps",
            required=False,
        )

        scopes = graphene.List(
            graphene.String,
            description="A list of potential scopes for this app",
            required=True,
        )

    def mutate(root, info, version, identifier, scopes, imitate=None, logo_url=None, **kwargs):
        # TODO: assert scopes can manage apps
        token = create_api_token()


        manifest = models.Manifest(
            identifier=identifier,
            version=version,
            scopes=scopes,
            redirect_uris=[],   
        )

        return models.create_private_client(
            manifest,
            info.context.user,
            info.context.user,
            token=token,
            logo=logo_url,
        )

    class Meta:
        type = types.Client
        operation = "createPrivateClient"


class PublicFaktType(graphene.Enum):
    DEKSTOP = "desktop"
    WEBSITE = "website"


class CreatePublicClient(BalderMutation):
    class Arguments:
        version = graphene.String(
            required=True, description="The Repo of the Docker (Repo on Dockerhub)"
        )
        identifier = graphene.String(
            description="The Name of this Elemet",
            required=True,
        )
        kind = graphene.Argument(
            PublicFaktType, description="The kind of this app", required=True
        )
        redirect_uris = graphene.List(
            graphene.String,
            description="A list of potential redirects for this app",
            required=True,
        )
        logo_url = graphene.String(
            description="The Logo of this Apps",
            required=False,
        )
        scopes = graphene.List(
            graphene.String,
            description="A list of potential scopes for this app",
            required=True,
        )

    def mutate(root, info, version, identifier, kind, redirect_uris, scopes, logo_url):
        # TODO: assert scopes can manage apps
        # TODO: assert permissions to create public apps
        token = create_api_token()

        manifest = models.Manifest(
            identifier=identifier,
            version=version,
            scopes=scopes,
            redirect_uris=[],   
        )

        return models.create_public_client(
            manifest, info.context.user, logo=logo_url, token=token
        )

    class Meta:
        type = types.Client
        operation = "createPublicClient"


class DeleteClientResult(graphene.ObjectType):
    id = graphene.ID()


class DeleteClient(BalderMutation):
    class Arguments:
        id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, id=None):
        app = models.Client.objects.get(id=id)
        assert True, "You are not allowed to delete this Application"
        assert app.creator == info.context.user or info.context.user.is_superuser, "You are not the creator and therefore not allowed to delete this Application"
        app.delete()
        return {"id": id}

    class Meta:
        type = DeleteClientResult

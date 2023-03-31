from balder.types import BalderMutation, BalderQuery
from infos import types, models, scalars
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import Application
import graphene
from infos.utils import create_api_token


class CreatePrivateFakt(BalderMutation):
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
        scopes = graphene.List(
            graphene.String,
            description="A list of potential scopes for this app",
            required=True,
        )

    def mutate(root, info, version, identifier, scopes, imitate=None, **kwargs):
        # TODO: assert scopes can manage apps
        token = create_api_token()
        return models.create_private_fakt(
            identifier,
            version,
            info.context.user,
            info.context.user,
            scopes,
            "",
            token=token,
        )

    class Meta:
        type = types.FaktApplication
        operation = "createPrivateFakt"


class PublicFaktType(graphene.Enum):
    DEKSTOP = "desktop"
    WEBSITE = "website"


class CreatePublicFakt(BalderMutation):
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
        scopes = graphene.List(
            graphene.String,
            description="A list of potential scopes for this app",
            required=True,
        )

    def mutate(root, info, version, identifier, kind, redirect_uris, scopes):
        # TODO: assert scopes can manage apps
        # TODO: assert permissions to create public apps
        return models.create_public_fakt(
            identifier, version, info.context.user, redirect_uris, scopes, kind
        )

    class Meta:
        type = types.FaktApplication
        operation = "createPublicFakt"


class DeletePrivateFaktResult(graphene.ObjectType):
    id = graphene.ID()


class DeletePrivateFakt(BalderMutation):
    class Arguments:
        id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, id=None):
        app = models.PrivateFaktApplication.objects.get(id=id)
        assert True, "You are not allowed to delete this Application"
        app.delete()
        return {"id": id}

    class Meta:
        type = DeletePrivateFaktResult


class DeletePublicFaktResult(graphene.ObjectType):
    id = graphene.ID()


class DeletePublicFakt(BalderMutation):
    class Arguments:
        id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, id=None):
        app = models.PublicFaktApplication.objects.get(id=id)
        assert True, "You are not allowed to delete this Application"
        app.delete()
        return {"id": id}

    class Meta:
        type = DeletePublicFaktResult

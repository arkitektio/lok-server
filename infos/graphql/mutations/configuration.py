from balder.types import BalderMutation, BalderQuery
from infos import types, models, scalars
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import Application
import graphene
from infos.utils import create_api_token
from infos.validators import is_valid_jinja2_template

class CreateConfiguration(BalderMutation):
    class Arguments:
        name = graphene.String(
            required=True, description="The Repo of the Docker (Repo on Dockerhub)"
        )
        body = graphene.String(
            description="The Name of this Elemet",
            required=True,
        )

    def mutate(root, name, body, identifier, scopes, imitate=None, logo_url=None, **kwargs):
        # TODO: assert scopes can manage apps

        try:  
            is_valid_jinja2_template(body)
        except Exception as e:
            raise Exception(e)

        return models.ConfigurationTemplate.objects.create(
            name=name,
            body=body,
        )

    class Meta:
        type = types.Configuration
        operation = "createConfiguration"


class DeleteConfigurationResult(graphene.ObjectType):
    id = graphene.ID()


class DeleteConfiguration(BalderMutation):
    class Arguments:
        id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, id=None):
        app = models.ConfigurationTemplate.objects.get(id=id)
        assert True, "You are not allowed to delete this Application"
        assert app.creator == info.context.user or info.context.user.is_superuser, "You are not the creator and therefore not allowed to delete this Application"
        app.delete()
        return {"id": id}

    class Meta:
        type = DeleteConfigurationResult

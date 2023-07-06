from balder.types import BalderMutation, BalderQuery
from infos import types, models, scalars
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import Application
import graphene
from infos.utils import create_api_token
from infos.validators import is_valid_jinja2_template
from infos.inputs import FilterInput

class CreateLinker(BalderMutation):
    class Arguments:
        name = graphene.String(
            required=True, description="The Repo of the Docker (Repo on Dockerhub)"
        )
        template = graphene.ID(
            required=True, description="The Repo of the Docker (Repo on Dockerhub)"
        )
        filters = graphene.List(
            FilterInput,
            required=True,
            description="The Filters of this Elemet",
        )
        priority = graphene.Int(
            required=True, description="The Priority of this Elemet",
        )

    def mutate(root, name, template, filters, priority,  **kwargs):
        # TODO: assert scopes can manage apps

        x = models.ConfigurationTemplate.objects.get(id=template)

        linker ,_ = models.Linker.objects.update_or_create(
            name=name,
            defaults=dict(template=x,
            priority=priority)
        )

        linker.filters.all().delete()

        for filter in filters:
            filter = models.Filter.objects.create(
                linker=linker,
                **filter
            )

        return linker

    class Meta:
        type = types.Linker
        operation = "createLinker"


class DeleteLinkerResult(graphene.ObjectType):
    id = graphene.ID()


class DeleteLinker(BalderMutation):
    class Arguments:
        id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, id=None):
        app = models.Linker.objects.get(id=id)
        app.delete()
        return {"id": id}

    class Meta:
        type = DeleteLinkerResult

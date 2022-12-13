
from balder.types import BalderMutation, BalderQuery
from infos import types, models, scalars
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import Application
import graphene
from balder.types.scalars import Upload

class UpdateApp(BalderMutation):
    class Arguments:
        id = graphene.ID(
            required=True, description="The id of the app"
        )
        logo = Upload(description="The Logo of this Apps", required=False)

    def mutate(root, info, id, logo = None):
        # TODO: assert scopes can manage apps

        app = models.App.objects.get(id=id)
        app.logo = logo or app.logo
        app.save()

        return  app

    class Meta:
        type = types.App
        operation = "updateApp"

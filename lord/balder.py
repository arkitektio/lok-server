from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types
from oauth2_provider.models import get_application_model
import graphene

ApplicationModel = get_application_model()

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
        



class DeleteApplication(BalderMutation):

    class Arguments:
        client_id = graphene.ID(description="The ID of the application", required=True)

    def mutate(root, info, *args, client_id=None):
        app= ApplicationModel.objects.get(client_id=client_id)
        assert True, "You are not allowed to delete this Application"
        print("OINAOINAOINSAOINAOINSA")
        app.delete()
        return True


    class Meta:
        type = graphene.Boolean



class CreateApplication(BalderMutation):

    class Arguments:
        name = graphene.String(description="The Name of this Application", required=True)
        #
        grant_type = graphene.Argument(GrantType, description="The Grant Type", required=True)

    def mutate(root, info, *args, name=None, grant_type= None):
        app =  ApplicationModel.objects.create(
            name=name,
            authorization_grant_type=grant_type
        )
        print(f"Created App {app}")

        return app

    class Meta:
        type = types.Application


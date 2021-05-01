import graphene
from balder.types import BalderObject
from oauth2_provider.models import get_application_model
from .models import HerreUser as HerreUserModel

class HerreUser(BalderObject):
    roles = graphene.List(graphene.String, description="The associated rules of this ")

    @staticmethod
    def resolve_roles(root, info, *args, **kwargs):
        print(root, info, *args, **kwargs)
        return  [group.name for group in root.groups.all()]

    class Meta:
        model = HerreUserModel


class Application(BalderObject):

    class Meta:
        model = get_application_model()
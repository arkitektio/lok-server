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



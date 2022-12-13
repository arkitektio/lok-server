from lord.enums import GrantType
from balder.types.mutation.base import BalderMutation
from balder.types.query.base import BalderQuery
from balder.types import BalderObject
from lord import types, models, filters
from oauth2_provider.models import get_application_model
from django.conf import settings
import graphene

ApplicationModel = get_application_model()


class UserQuery(BalderQuery):
    class Arguments:
        email = graphene.String(description="The email of the user", required=False)
        id = graphene.ID(description="The email of the user", required=False)

    def resolve(
        root,
        info,
        email=None,
        id=None,
    ):
        if email:
            return models.HerreUser.objects.get(email=email)
        if id:
            return models.HerreUser.objects.get(id=id)

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


class UsersQuery(BalderQuery):

    class Meta:
        list = True
        type = types.HerreUser
        filter = filters.UserFilter
        operation = "users"

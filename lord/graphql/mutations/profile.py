from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lord import models, types
from avatar.models import Avatar as AvatarModel
from balder.scalars import Email


class ChangeMe(BalderMutation):
    class Arguments:
        first_name = graphene.String(required=False)
        last_name = graphene.String(required=False)
        email = Email(required=False)

    def mutate(
        root,
        info,
        first_name=None,
        last_name=None,
        email=None,
    ):
        # do something with your file

        user = info.context.user
        user.first_name = first_name or user.first_name
        user.last_name = last_name or user.last_name
        user.email = email or user.email
        user.save()

        return user

    class Meta:
        type = types.HerreUser
        operation = "changeMe"

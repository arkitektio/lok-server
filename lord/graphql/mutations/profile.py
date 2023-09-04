from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lord import models, types
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
        type = types.User
        operation = "changeMe"


class UpdateUser(BalderMutation):
    class Arguments:
        id = graphene.ID(required=True)
        avatar = Upload(required=False)
        first_name = graphene.String(required=False)
        last_name = graphene.String(required=False)
        active = graphene.Boolean(required=False)
        email = Email(required=False)

    def mutate(
        root,
        info,
        id,
        first_name=None,
        last_name=None,
        avatar=None,
        active=None,
        email=None,
    ):
        # do something with your file

        user = models.HerreUser.objects.get(id=id)
        # TODO: check if user is allowed to change this user
        user.first_name = first_name or user.first_name
        user.last_name = last_name or user.last_name
        user.email = email or user.email
        user.is_active = active if active is not None else user.is_active
        try:
            user.profile.avatar = avatar or user.profile.avatar
        except models.Profile.DoesNotExist:
            user.profile = models.Profile()
            user.profile.avatar = avatar or user.profile.avatar

        user.profile.save()

        user.save()

        return user

    class Meta:
        type = types.User
        operation = "updateUser"

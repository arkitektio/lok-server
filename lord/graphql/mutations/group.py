from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lord import models, types
from django.contrib.auth.models import Group


class UpdateGroup(BalderMutation):
    class Arguments:
        id = graphene.ID(required=True)
        avatar = Upload(required=False)
        name = graphene.String(required=False, description="The name of the group (non unique)")

    def mutate(
        root,
        info,
        id,
        avatar = None,
        name = None,
    ):
        # do something with your file

        group = models.Group.objects.get(id=id)
        #TODO: check if user is allowed to change this user
        try:
            group.profile.avatar = avatar
        except:
            group.profile = models.GroupProfile()
        group.profile.avatar = avatar or group.profile.avatar
        group.profile.name = name or group.profile.name
        group.profile.save()
        group.save()


        return group

    class Meta:
        type = types.Group
        operation = "updateGroup"


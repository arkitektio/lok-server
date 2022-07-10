from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lord import models, types
from django.contrib.auth.models import Group


class UploadGroupAvatar(BalderMutation):
    class Arguments:
        group = graphene.String(required=True)
        file = Upload(required=True)
        primary = graphene.Boolean(required=False)

    def mutate(
        root,
        info,
        group,
        file,
        primary=False,
    ):
        # do something with your file

        filename: str = file.name

        t = models.GroupImage.objects.create(
            image=file, group=Group.objects.get(name=group), primary=primary
        )

        return t

    class Meta:
        type = types.GroupImage

from balder.types.scalars import Upload
from balder.types.mutation import BalderMutation
import graphene
from lord import models, types
from avatar.models import Avatar as AvatarModel


class UploadAvatar(BalderMutation):
    class Arguments:
        file = Upload(required=True)
        primary = graphene.Boolean(required=False)

    def mutate(
        root,
        info,
        file,
        primary=False,
    ):
        # do something with your file

        filename: str = file.name
        print(filename)

        t = AvatarModel.objects.create(
            avatar=file, user=info.context.user, primary=primary
        )

        return t

    class Meta:
        type = types.Avatar


class DeleteAvatarResult(graphene.ObjectType):
    id = graphene.String()


class DeleteAvatar(BalderMutation):
    """Create an experiment (only signed in users)"""

    class Arguments:
        id = graphene.ID(
            description="The ID of the two deletet Representation", required=True
        )

    def mutate(root, info, id, **kwargs):
        om = AvatarModel.objects.get(id=id)
        om.delete()
        return {"id": id}

    class Meta:
        type = DeleteAvatarResult

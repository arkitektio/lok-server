import graphene
from balder.types import BalderObject
from balder.types.query import BalderQuery
from avatar.models import Avatar as AvatarModel
from oauth2_provider.models import get_application_model
from .models import HerreUser as HerreUserModel, GroupImage as GroupImageModel
from lord.filters import UserFilter, GroupFilter
from django.contrib.auth.models import Group as HerreGroupModel


class HerreUser(BalderObject):
    roles = graphene.List(graphene.String, description="The associated rules of this ")
    avatar = graphene.String()

    @staticmethod
    def resolve_roles(root, info, *args, **kwargs):
        print(root, info, *args, **kwargs)
        return [group.name for group in root.groups.all()]

    @staticmethod
    def resolve_avatar(root, info, *args, **kwargs):
        path = AvatarModel.objects.filter(primary=True, user=root).first()
        if path:
            return info.context.build_absolute_uri(path.avatar.url)
        else:
            return None

    class Meta:
        model = HerreUserModel
        fields = [
            "id",
            "username",
            "email",
            "roles",
            "avatar",
            "last_name",
            "first_name",
            "groups",
        ]


class Users(BalderQuery):
    """Get a list of users"""

    class Meta:
        list = True
        type = HerreUser
        filter = UserFilter
        operation = "users"


class Group(BalderObject):
    avatar = graphene.String()

    @staticmethod
    def resolve_avatar(root, info, *args, **kwargs):
        path = root.images.first()
        if path:
            return info.context.build_absolute_uri(path.image.url)
        else:
            return None

    class Meta:
        model = HerreGroupModel


class GroupDetail(BalderQuery):
    """Get a list of groups"""

    class Arguments:
        name = graphene.String(description="Unique app name fddor user", required=True)

    def resolve(
        root,
        info,
        name=None,
    ):
        return HerreGroupModel.objects.get(name=name)

    class Meta:
        list = False
        type = Group
        operation = "group"


class Groups(BalderQuery):
    """Get a list of users"""

    class Meta:
        list = True
        type = Group
        filter = GroupFilter
        operation = "groups"


class MyGroups(BalderQuery):
    """Get a list of users"""

    class Arguments:
        name = graphene.String(description="Unique app name for user")

    def resolve(root, info, *args, name=None, client_id=None):
        return info.context.user.groups.all()

    class Meta:
        list = True
        type = Group
        filter = GroupFilter
        operation = "mygroups"


class Avatar(BalderObject):
    class Meta:
        model = AvatarModel


class GroupImage(BalderObject):
    class Meta:
        model = GroupImageModel


class Application(BalderObject):
    redirect_uris = graphene.List(
        graphene.String, description="The associated Redirect Uris"
    )
    image = graphene.String(description="The Url of the Image")

    @staticmethod
    def resolve_redirect_uris(root, info, *args, **kwargs):
        return (
            [uri for uri in root.redirect_uris.splitlines()]
            if root.redirect_uris
            else None
        )

    @staticmethod
    def resolve_image(root, info, *args, **kwargs):

        app_image = root.image.first()
        if app_image:
            try:
                host = info.context.get_host().split(":")[0]
            except:
                host = {
                    key.decode("utf-8"): item.decode("utf-8")
                    for key, item in info.context["headers"]
                }["host"].split(":")[0]

            port = 8000
            return f"http://{host}:{port}{app_image.image.url}"
        return None

    class Meta:
        model = get_application_model()
        exclude_fields = [
            "client_secret",  # it worked
        ]

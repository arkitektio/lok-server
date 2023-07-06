import graphene
from balder.types import BalderObject
from balder.types.query import BalderQuery
from oauth2_provider.models import get_application_model
from .models import (
    HerreUser as HerreUserModel,
    Profile as ProfileModel,
    GroupProfile as GroupProfileModel,
    Channel as ChannelModel,
)
from lord.filters import UserFilter, GroupFilter
from django.contrib.auth.models import Group as HerreGroupModel


class HerreUser(BalderObject):
    roles = graphene.List(graphene.String, description="The associated rules of this ")

    @staticmethod
    def resolve_roles(root, info, *args, **kwargs):
        return [group.name for group in root.groups.all()]

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
            "is_active",
            "private_applications",
            "profile",
        ]


class Group(BalderObject):
    class Meta:
        model = HerreGroupModel


class Channel(BalderObject):
    class Meta:
        model = ChannelModel


class GroupProfile(BalderObject):
    avatar = graphene.String()

    @staticmethod
    def resolve_avatar(root, info, *args, **kwargs):
        if root.avatar:
            return root.avatar.url

    class Meta:
        model = GroupProfileModel


class Profile(BalderObject):
    avatar = graphene.String()

    @staticmethod
    def resolve_avatar(root, info, *args, **kwargs):
        if root.avatar:
            return root.avatar.url

    class Meta:
        model = ProfileModel


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

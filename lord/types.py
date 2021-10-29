import graphene
from balder.types import BalderObject
from avatar.models import Avatar as AvatarModel
from oauth2_provider.models import get_application_model
from .models import HerreUser as HerreUserModel

class HerreUser(BalderObject):
    roles = graphene.List(graphene.String, description="The associated rules of this ")
    avatar = graphene.String()

    @staticmethod
    def resolve_roles(root, info, *args, **kwargs):
        print(root, info, *args, **kwargs)
        return  [group.name for group in root.groups.all()]

    @staticmethod
    def resolve_avatar(root, info, *args, **kwargs):
        path = AvatarModel.objects.filter(primary=True, user=root).first()
        if path:
            return info.context.build_absolute_uri(path.avatar.url)
        else:
            return None


    class Meta:
        model = HerreUserModel


class Avatar(BalderObject):

    class Meta:
        model = AvatarModel


class Application(BalderObject):
    redirect_uris = graphene.List(graphene.String, description="The associated Redirect Uris")
    image = graphene.String(description="The Url of the Image")

    @staticmethod
    def resolve_redirect_uris(root, info, *args, **kwargs):
        return  [uri for uri in root.redirect_uris.splitlines()] if root.redirect_uris else None

    @staticmethod
    def resolve_image(root, info, *args, **kwargs):
        
        app_image = root.image.first()
        if app_image:
            try:
                host = info.context.get_host().split(":")[0]
            except:
                host = {key.decode("utf-8"): item.decode("utf-8") for key, item in info.context["headers"]}["host"].split(":")[0]
            
            port = 8000
            return f"http://{host}:{port}{app_image.image.url}"
        return None


    class Meta:
        model = get_application_model()
from balder.types import BalderObject
from oauth2_provider.models import get_application_model


class Application(BalderObject):

    class Meta:
        model = get_application_model()
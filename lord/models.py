from django.db import models
from oauth2_provider.models import AbstractAccessToken, AbstractApplication, AbstractRefreshToken, Application, get_application_model
from oauth2_provider.settings import oauth2_settings


from django.contrib.auth.models import AbstractUser

class HerreUser(AbstractUser):
    """ A reflection on the real User"""




class AppImage(models.Model):
    app = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="image")
    image = models.ImageField()
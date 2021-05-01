from django.db import models
from oauth2_provider.models import AbstractAccessToken, AbstractApplication, AbstractRefreshToken, Application
from oauth2_provider.settings import oauth2_settings


from django.contrib.auth.models import AbstractUser

class HerreUser(AbstractUser):
    """ A reflection on the real User"""





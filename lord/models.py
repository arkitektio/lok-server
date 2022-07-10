from django.db import models
from oauth2_provider.settings import oauth2_settings
from django.contrib.auth.models import AbstractUser
from oauth2_provider.models import Application
from django.contrib.auth.models import Group


class HerreUser(AbstractUser):
    """A reflection on the real User"""


class AppImage(models.Model):
    app = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="image")
    image = models.ImageField()


class GroupImage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField()
    primary = models.BooleanField(default=False)

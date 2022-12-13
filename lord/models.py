from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import Group
from lord.storage import PrivateAvatarStorage

class HerreUser(AbstractUser):
    """A reflection on the real User"""

    @property
    def is_faktsadmin(self):
        return self.groups.filter(name="admin").exists()


class Profile(models.Model):
    name = models.CharField(max_length=1000, null=True, blank=True)
    user = models.OneToOneField(HerreUser, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(storage=PrivateAvatarStorage(), null=True, blank=True)


class GroupProfile(models.Model):
    name = models.CharField(max_length=1000, null=True, blank=True)
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(storage=PrivateAvatarStorage(), null=True, blank=True)
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import Group
from lord.storage import PrivateAvatarStorage


class HerreUser(AbstractUser):
    """A reflection on the real User"""

    @property
    def is_faktsadmin(self):
        return self.groups.filter(name="admin").exists()

    @property
    def avatar(self):
        if self.profile:
            return self.profile.avatar.url if self.profile.avatar else None


class Profile(models.Model):
    name = models.CharField(max_length=1000, null=True, blank=True)
    user = models.OneToOneField(
        HerreUser, on_delete=models.CASCADE, related_name="profile"
    )
    avatar = models.ImageField(storage=PrivateAvatarStorage(), null=True, blank=True)


class GroupProfile(models.Model):
    name = models.CharField(max_length=1000, null=True, blank=True)
    group = models.OneToOneField(
        Group, on_delete=models.CASCADE, related_name="profile"
    )
    avatar = models.ImageField(storage=PrivateAvatarStorage(), null=True, blank=True)


class Channel(models.Model):
    name = models.CharField(max_length=1000, null=True, blank=True)
    user = models.ForeignKey(
        HerreUser, on_delete=models.CASCADE, related_name="channels"
    )
    token = models.CharField(max_length=1000, null=True, blank=True, unique=True)

from django.db import models
from oauth2_provider.models import Application
from typing import Dict, Any
from django.contrib.auth import get_user_model

# Create your models here.


class ConfigurationGraph(models.Model):
    name = models.CharField(max_length=400)
    version = models.CharField(max_length=600)
    host = models.CharField(
        max_length=500, help_text="Is this appearing on a selection of hosts?"
    )

    def __str__(self):
        return f"{self.name} ({self.version})"


class ConfigurationElement(models.Model):
    graph = models.ForeignKey(
        ConfigurationGraph, on_delete=models.CASCADE, related_name="elements"
    )
    name = models.CharField(max_length=1000)
    values = models.JSONField()

    def __str__(self):
        return f"{self.name} on {self.graph}"

    def parse(self, template) -> Dict[str, Any]:
        return values


class Configurations(models.Model):
    token = models.UUIDField()
    app = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="configurations"
    )
    graph = models.ForeignKey(
        ConfigurationGraph, on_delete=models.CASCADE, related_name="configurations"
    )


class DeviceCode(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    code = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True)
    name = models.CharField(max_length=100, null=True)
    scopes = models.JSONField(default=list)
    graph = models.ForeignKey(
        ConfigurationGraph, related_name="codes", on_delete=models.CASCADE, null=True
    )


class Member(models.Model):
    name = models.CharField(max_length=7000)

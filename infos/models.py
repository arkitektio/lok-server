from django.db import models
from typing import Dict, Any
from django.contrib.auth import get_user_model
from oauth2_provider.generators import generate_client_id, generate_client_secret
# Create your models here.
from django.core.exceptions import ObjectDoesNotExist
from oauth2_provider.models import Application
from typing import List
from .storage import PrivateMediaStorage
from django.db.models import Q
import uuid


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
        return f"{self.name} ons {self.graph}"

    def parse(self, template) -> Dict[str, Any]:
        return values



class DeviceCode(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    code = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, null=True)
    name = models.CharField(max_length=100, null=True)
    version = models.CharField(max_length=1000, null=True)
    identifier = models.CharField(max_length=1000, null=True)
    scopes = models.JSONField(default=list)
    graph = models.ForeignKey(
        ConfigurationGraph, related_name="codes", on_delete=models.CASCADE, null=True
    )


class Member(models.Model):
    name = models.CharField(max_length=7000)



def validate_semver(version: str):
    return True


class VersionField(models.CharField):
    default_validators = [validate_semver]

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 1000
        super().__init__(*args, **kwargs)


def validate_app_identifier(name: str):
    return True


class IdentifierField(models.CharField):
    default_validators = [validate_app_identifier]

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 1000
        super().__init__(*args, **kwargs)



class App(models.Model):
    identifier = IdentifierField()
    version = VersionField()
    name = models.CharField(max_length=1000)
    logo = models.ImageField(max_length=1000, null=True, blank=True, storage=PrivateMediaStorage())

    class Meta:
        constraints = [
        models.UniqueConstraint(
            fields=["identifier","version"],
            name="Only one per identifier and version",
        )
    ]

    def __str__(self):
        return f"{self.identifier}:{self.version}"

class FaktKindChoices(models.TextChoices):
    WEBSITE = "website", "Website"
    DESKTOP = "desktop", "Dekstop"
    USER = "user", "User"


class FaktApplication(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="fakt_applications", null=True) #TODO: fix this in the future should not be null
    application = models.OneToOneField(Application, on_delete=models.CASCADE)
    kind = models.CharField(max_length=1000, choices=FaktKindChoices.choices, null=True)
    token = models.CharField(default=uuid.uuid4, unique=True, max_length=10000)
    client_id = models.CharField(max_length=1000, unique=True, default=generate_client_id)
    client_secret = models.CharField(max_length=1000, default=generate_client_secret)
    scopes = models.JSONField(default=list)
    logo = models.ImageField(max_length=1000, null=True, blank=True)
    creator = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="managed_applications")
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="fakt_applications", null=True)

    class Meta:
        constraints = [
        models.UniqueConstraint(
            fields=["app","application"],
            condition=Q(kind__in=[FaktKindChoices.WEBSITE.value, FaktKindChoices.DESKTOP.value]),
            name="Only one unique app per identifier and version",
        ),
        models.UniqueConstraint(
            fields=["app","application", "user"],
            condition=Q(kind__in=[FaktKindChoices.USER.value]),
            name="Only one unique app per identifier and version if it is a user app",
        )
    ]

    def __str__(self) -> str:
        return f"{self.app} for {self.application}"




def create_public_fakt(identifier: str, version: str, creator: str, redirect_uris: List[str], scopes: List[str], client_secret=None, client_id=None):
    f, _ = App.objects.get_or_create(identifier=identifier, version=version)
    try:
         
        app = FaktApplication.objects.get(client_id=client_id)
        assert app.client_secret == client_secret, "Client secret does not match. Cannot overwrite"
        app.app = f
        app.creator = creator
        app.scopes = scopes
        app.kind = FaktKindChoices.WEBSITE.value
        app.client_secret = client_secret or app.client_secret
        app.client_id = client_id or app.client_id
        app.save()

        app.application.name = f"@{identifier}:{version}"
        app.application.user = creator
        app.application.client_type = "public"
        app.application.algorithm = Application.RS256_ALGORITHM
        app.application.authorization_grant_type = Application.GRANT_AUTHORIZATION_CODE
        app.application.redirect_uris = " ".join(redirect_uris)
        app.application.client_id = app.client_id
        app.application.client_secret = app.client_secret
        app.application.save()

    except FaktApplication.DoesNotExist:
        client_secret = client_secret or generate_client_secret()
        client_id = client_id or generate_client_id()

        app = Application.objects.create(
            user=creator,
            client_type= "public",
            algorithm=Application.RS256_ALGORITHM,
            name=f"@{identifier}:{version}",
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=" ".join(redirect_uris),
            client_id=client_id,
            client_secret=client_secret,
        )


        return FaktApplication.objects.create(
            app=f, creator=creator,
            kind=FaktKindChoices.WEBSITE.value,
            scopes=scopes,
            client_id=client_id, client_secret=client_secret, application=app

        )


def create_private_fakt(identifier: str, version: str, user: str, creator: str, scopes: List[str], client_secret=None, client_id=None):
    f, _ = App.objects.get_or_create(identifier=identifier, version=version)
    try:
        app = FaktApplication.objects.get(client_id=client_id)
        assert app.client_secret == client_secret, "Client secret does not match. Cannot overwrite"
        app.creator = creator
        app.scopes = scopes
        app.kind = FaktKindChoices.USER.value
        app.client_secret = client_secret or app.client_secret
        app.client_id = client_id or app.client_id
        app.app = f
        app.save()

        app.application.name = f"@{identifier}:{version}"
        app.application.user = creator
        app.application.client_type = "confidential"
        app.application.algorithm = Application.RS256_ALGORITHM
        app.application.authorization_grant_type = Application.GRANT_CLIENT_CREDENTIALS
        app.application.redirect_uris = ""
        app.application.client_id = app.client_id
        app.application.client_secret = app.client_secret
        app.application.save()

        return app

    except FaktApplication.DoesNotExist:
        client_secret = client_secret or generate_client_secret()
        client_id = client_id or generate_client_id()

        app = Application.objects.create(
            user=user,
            client_type="confidential",
            algorithm=Application.RS256_ALGORITHM,
            name=f"@{identifier}:{version}",
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
            redirect_uris="",
            client_id=client_id,
            client_secret=client_secret,
        )

        return FaktApplication.objects.create(
            app=f, creator=creator,
            user=user,
            kind=FaktKindChoices.USER.value,
            scopes=scopes,
            client_id=client_id, client_secret=client_secret, application=app

        )

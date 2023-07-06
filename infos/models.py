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
import json
from django.core.exceptions import ValidationError
from jinja2 import Template, TemplateSyntaxError, TemplateError
import yaml
from pydantic import BaseModel, Field
import re
from django.conf import settings
from typing import Optional

class Manifest(BaseModel):
    identifier: str
    version: str
    scopes: List[str]
    public: bool = False
    redirect_uris: List[str] = []

class LinkingRequest(BaseModel):
    host: str
    port: Optional[str] = None
    is_secure: bool = False

class LinkingClient(BaseModel):
    authorization_grant_type: str
    client_type: str
    client_id: str
    client_secret: str
    name: str
    scopes: List[str]

class LinkingContext(BaseModel):
    deployment_name: str = Field(default=settings.DEPLOYMENT_NAME)
    request: LinkingRequest
    "Everything is a string"
    manifest: Manifest
    client: LinkingClient


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
        return json.parse(template)
    


class ConfigurationTemplate(models.Model):
    """ A template for a configuration"""
    name = models.CharField(max_length=1000, unique=True)
    body = models.TextField()

    def render(self, context) -> Dict[str, Any]:
        return yaml.load(Template(self.body).render(context), Loader=yaml.SafeLoader)


class Linker(models.Model):

    name = models.CharField(max_length=1000, unique=True)
    template = models.ForeignKey(
        ConfigurationTemplate, on_delete=models.CASCADE, related_name="linkers"
    )
    priority = models.IntegerField()

    def parse(self, template) -> Dict[str, Any]:
        return json.parse(template)
    
    def rank(self, context: LinkingContext) -> int:
        for filter in self.filters.all():
            if not filter.matches(context):
                return -1

        return self.priority



class Filter(models.Model):
    FILTER_CHOICES = [
        ("host_regex", "Host matches regex"),
        ("host_is", "Host is"),
        ("host_is_not", "Host is not"),
        ("port_is", "Port is"),
        ("port_is_not", "Port is not"),
        ("version_is", "Version is"),
        ("version_is_not", "Version is not"),
        ("version_regex", "Version matches regex"),
        ("identifier_is", "Identifier is"),
        ("identifier_is_not", "Identifier is not"),
        ("identifier_regex", "Identifier matches regex"),
        ("user_is", "Checks if user is certain id"),
        ("user_is_developer", "Checks if the user is developer"),
    ]
    linker = models.ForeignKey(
        Linker, on_delete=models.CASCADE, related_name="filters"
    )
    method = models.CharField(max_length=1000, choices=FILTER_CHOICES)
    value = models.CharField(max_length=1000)


    def matches(self, context: LinkingContext):
        if self.method == "host_regex":
            return re.match(self.value, context.request.host)
        if self.method == "host_is":
            return context.request.host == self.value
        if self.method == "host_is_not":
            return context.request.host != self.value
        if self.method == "port_is":
            return context.request.port == self.value
        if self.method == "port_is_not":
            return context.request.port != self.value
        if self.method == "version_is":
            return context.manifest.version == self.value
        if self.method == "version_is_not":
            return context.manifest.version != self.value
        if self.method == "version_regex":
            return re.match(self.value, context.manifest.version)
        if self.method == "identifier_is":
            return context.manifest.identifier == self.value
        if self.method == "identifier_is_not":
            return context.manifest.identifier != self.value
        if self.method == "identifier_regex":
            return re.match(self.value, context.manifest.identifier)
        return False




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
    logo = models.ImageField(
        max_length=1000, null=True, blank=True, storage=PrivateMediaStorage()
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
    name = models.CharField(max_length=1000)
    identifier = IdentifierField()
    logo = models.ImageField(
        max_length=1000, null=True, blank=True, storage=PrivateMediaStorage()
    )

class Release(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="releases")
    version = VersionField()
    is_latest = models.BooleanField(default=False)
    is_dev = models.BooleanField(default=False)
    name = models.CharField(max_length=1000)
    logo = models.ImageField(
        max_length=1000, null=True, blank=True, storage=PrivateMediaStorage()
    )

    def is_latest(self):
        return self.app.releases.filter(is_latest=True).count() == 1
    
    def is_dev(self):
        return "dev" in self.version


    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["app", "version"],
                name="Only one per app and version",
            )
        ]

    def __str__(self):
        return f"{self.identifier}:{self.version}"


class ClientType(models.TextChoices):
    WEBSITE = "website", "Website"
    DESKTOP = "desktop", "Dekstop"
    USER = "user", "User"


class Client(models.Model):
    release = models.ForeignKey(
        Release, on_delete=models.CASCADE, related_name="clients", null=True
    ) 
    oauth2_client = models.OneToOneField(Application, on_delete=models.CASCADE, related_name="client")
    kind = models.CharField(max_length=1000, choices=ClientType.choices, null=True)
    token = models.CharField(default=uuid.uuid4, unique=True, max_length=10000) # the api token
    client_id = models.CharField(
        max_length=1000, unique=True, default=generate_client_id
    )
    client_secret = models.CharField(max_length=1000, default=generate_client_secret)
    scopes = models.JSONField(default=list)

    creator = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name="managed_clients"
    )
    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="clients",
        null=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release", "user"],
                name="Only one release per user ()",
            )
        ]


    def __str__(self) -> str:
        return f"{self.kind} Client for {self.release}"


def create_public_client(
    manifest: Manifest,
    creator: str,
    client_secret=None,
    client_id=None,
    token: str = None,
    logo: str=None,
):
    assert manifest.scopes, "No scopes defined"
    assert manifest.identifier, "No identifier defined"
    assert manifest.version, "No version defined"
    assert manifest.redirect_uris, "No redirect_uris defined"
    from .utils import download_logo
    app, _  = App.objects.get_or_create(identifier=manifest.identifier)
    release, _ = Release.objects.get_or_create(app=app, version=manifest.version)

    if logo and not release.logo:
        try:
            logo = download_logo(logo)
            release.logo.save(f"{manifest.identifier}-{manifest.version}.png", logo, save=True)
            release.save()
        except Exception as e:
            print(e)
        
    try:
        app = Client.objects.get(token=token)
        assert (
            app.client_secret == client_secret
        ), "Client secret does not match. Cannot overwrite"
        app.release = release
        app.token = token
        app.creator = creator
        app.scopes = manifest.scopes
        app.kind = ClientType.WEBSITE.value
        app.client_secret = client_secret or app.client_secret
        app.client_id = client_id or app.client_id
        app.save()

        app.oauth2_client.name = f"@{manifest.identifier}:{manifest.version}"
        app.oauth2_client.user = creator
        app.oauth2_client.client_type = "public"
        app.oauth2_client.algorithm = Application.RS256_ALGORITHM
        app.oauth2_client.authorization_grant_type = Application.GRANT_AUTHORIZATION_CODE
        app.oauth2_client.redirect_uris = " ".join(manifest.redirect_uris)
        app.oauth2_client.client_id = app.client_id
        app.oauth2_client.client_secret = app.client_secret
        app.oauth2_client.save()

    except Client.DoesNotExist:
        client_secret = client_secret or generate_client_secret()
        client_id = client_id or generate_client_id()

        app = Application.objects.create(
            user=creator,
            client_type="public",
            algorithm=Application.RS256_ALGORITHM,
            name=f"@{manifest.identifier}:{manifest.version}",
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=" ".join(manifest.redirect_uris),
            client_id=client_id,
            client_secret=client_secret,
        )

        return Client.objects.create(
            release=release,
            creator=creator,
            kind=ClientType.WEBSITE.value,
            token=token,
            scopes=manifest.scopes,
            client_id=client_id,
            client_secret=client_secret,
            oauth2_client=app,
        )


def create_private_client(
    manifest: Manifest,
    user: str,
    creator: str,
    client_secret=None,
    client_id=None,
    token: str = None,
    logo: str=None,
):  
    from .utils import download_logo
    app, _  = App.objects.get_or_create(identifier=manifest.identifier)
    release, _ = Release.objects.get_or_create(app=app, version=manifest.version)

    if logo :
        logo = download_logo(logo)
        release.logo.save(f"{manifest.identifier}-{manifest.version}.png", logo, save=True)
        release.save()

    try:
        app = Client.objects.get(release=release, user=user)
        return app

    except Client.DoesNotExist:
        client_secret = client_secret or generate_client_secret()
        client_id = client_id or generate_client_id()

        app = Application.objects.create(
            user=user,
            client_type="confidential",
            algorithm=Application.RS256_ALGORITHM,
            name=f"@{manifest.identifier}:{manifest.version}",
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
            redirect_uris="",
            client_id=client_id,
            client_secret=client_secret,
        )

        return Client.objects.create(
            release=release,
            creator=creator,
            user=user,
            token=token,
            kind=ClientType.USER.value,
            scopes=manifest.scopes,
            client_id=client_id,
            client_secret=client_secret,
            oauth2_client=app,
        )



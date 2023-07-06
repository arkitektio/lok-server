from dataclasses import dataclass
from typing import Optional
from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import FormView, View
from .http import ConfigureResponseRedirect
from .forms import ConfigureForm, DeviceForm
from .errors import ConfigurationRequestMalformed, ConfigurationError
import logging
import urllib
from django.forms.models import modelform_factory
from oauth2_provider.models import Application, get_application_model
from enum import Enum
import json
from uuid import uuid4
from typing import List
from .models import ConfigurationGraph, DeviceCode, Client, create_private_client, App, Release, ClientType, ConfigurationTemplate
import datetime
from .utils import (
    claim_client,
    get_fitting_graph,
    create_api_token,
    download_logo,
    create_linking_context,
    get_fitting_template_for_context,
    render_template,
)
from django.utils import timesince
from django.shortcuts import redirect
import uuid
logger = logging.getLogger(__name__)


class ConfigureGrant(str, Enum):
    CLAIM = "claim"
    "An app that claims to be a predefined public app"
    USER_REDIRECT = "user_redirect"
    PUBLIC_REDIRECT = "public_redirect"
    "An app that wants a new user token assigned to itself"
    DEVICE_CODE = "device_code"


@dataclass
class ConfigurationRequest:
    grant: ConfigureGrant
    claim: Optional[str] = None  #
    device_code: Optional[str] = None


@dataclass
class Manifest:
    version: str
    identifier: str
    scopes: List[str]
    image: Optional[str] = None


class ConfigureMixin:
    def validate_configure_request(self, request) -> ConfigurationRequest:
        """
        Validate the configure request.
        """
        if request.method != "GET":
            raise ConfigurationRequestMalformed("Only GET requests are allowed")

        claim = request.GET.get("claim", None)
        grant = request.GET.get("grant", None)
        device_code = request.GET.get("device_code", None)

        return ConfigurationRequest(claim=claim, grant=grant, device_code=device_code)


class BaseConfigurationView(LoginRequiredMixin, ConfigureMixin, View):
    """
    Implements a generic endpoint to handle *Authorization Requests* as in :rfc:`4.1.1`. The view
    does not implement any strategy to determine *authorize/do not authorize* logic.
    The endpoint is used in the following flows:

    * Authorization code
    * Implicit grant

    """

    def dispatch(self, request, *args, **kwargs):
        self.oauth2_data = {}
        return super().dispatch(request, *args, **kwargs)

    def error_response(self, error, application, **kwargs):
        """
        Handle errors either by redirecting to redirect_uri with a json in the body containing
        error details or providing an error response
        """
        redirect, error_response = super().error_response(error, **kwargs)

        if redirect:
            return self.redirect(error_response["url"], application)

        status = error_response["error"].status_code
        return self.render_to_response(error_response, status=status)

    def redirect(self, redirect_to):
        return ConfigureResponseRedirect(redirect_to)


class ConfigureView(BaseConfigurationView, FormView):
    """
    Implements an endpoint to handle *Authorization Requests* as in :rfc:`4.1.1` and prompting the
    user with a form to determine if she authorizes the client application to access her data.
    This endpoint is reached two times during the authorization process:
    * first receive a ``GET`` request from user asking authorization for a certain client
    application, a form is served possibly showing some useful info and prompting for
    *authorize/do not authorize*.

    * then receive a ``POST`` request possibly after user authorized the access

    Some informations contained in the ``GET`` request and needed to create a Grant token during
    the ``POST`` request would be lost between the two steps above, so they are temporarily stored in
    hidden fields on the form.
    A possible alternative could be keeping such informations in the session.

    The endpoint is used in the following flows:
    * Authorization code
    * Implicit grant
    """

    template_name = "infos/configure.html"
    form_class = ConfigureForm

    def get_initial(self):
        # TODO: move this scopes conversion from and to string into a utils function

        initial_data = {
            "device_code": self.request.GET.get("device_code", None),
        }

        if self.request.GET.get("device_code", None):
            x = DeviceCode.objects.get(code=self.request.GET.get("device_code"))
            initial_data["scopes"] = x.scopes
        else:
            raise NotImplementedError("Only device code is supported at the moment")

        return initial_data
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['code'] = DeviceCode.objects.get(code=self.request.GET.get("device_code"))
        return context

    def form_valid(self, form):
        device_code = form.cleaned_data["device_code"]

        challenge = DeviceCode.objects.get(
            code=device_code,
        )

        challenge.user = self.request.user
        challenge.save()

        kwargs = {}
        kwargs["success"] = True

        return self.render_to_response(self.get_context_data(**kwargs))


    def get(self, request, *args, **kwargs):
        try:
            configuration = self.validate_configure_request(request)
        except ConfigurationError as error:
            # Application is not available at this time.
            return self.error_response(error, application=None)

        kwargs["grant"] = configuration.grant

        if configuration.grant == ConfigureGrant.CLAIM:
            kwargs["claim"] = get_application_model().objects.get(
                client_id=self.request.GET.get("claim")
            )

        if configuration.grant == ConfigureGrant.DEVICE_CODE:
            kwargs["device_code"] = configuration.device_code
            challenge, _ = DeviceCode.objects.get_or_create(
                code=configuration.device_code,
            )
            kwargs["created_at"] = timesince.timesince(challenge.created_at)

        # following two loc are here only because of https://code.djangoproject.com/ticket/17795
        form = self.get_form(self.get_form_class())
        kwargs["form"] = form
        kwargs["grant"] = configuration.grant

        # Check to see if the user has already granted access and return
        # a successful response depending on "approval_prompt" url parameter

        return self.render_to_response(self.get_context_data(**kwargs))


class DeviceView(LoginRequiredMixin, FormView):
    """
    This is the start view for the device flow.
    It will redirect to the device code view.
    """

    template_name = "infos/device.html"
    form_class = DeviceForm

    def get_initial(self):
        initial_data = {
            "device_code": self.request.GET.get("device_code", None),
        }

        return initial_data

    def form_valid(self, form):
        device_code = form.cleaned_data["device_code"]

        return redirect(f"/f/configure/?grant=device_code&device_code={device_code}")

    def get(self, request, *args, **kwargs):
        # following two loc are here only because of https://code.djangoproject.com/ticket/17795
        form = self.get_form(self.get_form_class())
        kwargs["form"] = form

        return self.render_to_response(self.get_context_data(**kwargs))
    

@method_decorator(csrf_exempt, name="dispatch")
class StartChallengeView(View):
    """
    An endpoint that is challenged in the course of a device code flow.
    """

    def generate_code(self):
        """Generates a random 6-digit alpha-numeric code"""

        return "".join([str(uuid.uuid4())[-1] for _ in range(6)])

    def post(self, request, *args, **kwargs):
        json_data = json.loads(request.body)
        if "manifest" in json_data:
            manifest = json_data["manifest"]
            code = self.generate_code()

            logo = manifest.get("logo", None)
            version = manifest.get("version", None)
            identifier = manifest.get("identifier", None)
            scopes = manifest.get("scopes", None)

            if not version or not identifier or not scopes:
                return JsonResponse(
                    data={
                        "status": "error",
                        "error": "Malformed manifest, required fields: version, identifier, scopes",
                    }
                )

            try:
                logo = download_logo(logo) if logo else None
            except Exception as e:
                logger.error(f"Error downloading logo: {logo}", exc_info=True)
                return JsonResponse(
                    data={
                        "status": "error",
                        "error": "Error downloading logo",
                    }
                )
            
            device_code = DeviceCode.objects.create(code=code, scopes=manifest["scopes"], version=manifest["version"], identifier=manifest["identifier"])
            if logo:
                device_code.logo.save(f"{identifier}{version}.png", logo, save=True)
                device_code.save()

            return JsonResponse(
                data={
                    "status": "granted",
                    "code": code,
                }
            )


        return JsonResponse(
                data={
                    "status": "error",
                    "error": "Missing manifest",
                }
            )





@method_decorator(csrf_exempt, name="dispatch")
class ChallengeView(View):
    """
    An endpoint that is challenged in the course of a device code flow.
    """

    def post(self, request, *args, **kwargs):
        json_data = json.loads(request.body)
        if "code" in json_data:
            try:
                device_code = DeviceCode.objects.get(code=json_data["code"])
            except DeviceCode.DoesNotExist:
                return JsonResponse(
                    data={
                        "status": "waiting",
                        "message": "The user hasn't started the transaction yet",
                    }
                )

            delta = datetime.datetime.now(timezone.utc) - device_code.created_at
            if (delta.seconds // 60) > 12:
                device_code.delete()
                return JsonResponse(
                    data={
                        "status": "expired",
                        "message": "The user has not given an answer in enough time",
                    }
                )

            # scopes will only be set if the user has verified the challenge
            if device_code.user:
                token = create_api_token()

                fakt = create_private_client(
                    Manifest(
                        identifier=device_code.identifier,
                        version=device_code.version,
                        scopes=device_code.scopes,

                    ),
                    device_code.user,
                    device_code.user,
                    token=token,
                )

                return JsonResponse(
                    data={
                        "status": "granted",
                        "token": fakt.token,
                    }
                )

            return JsonResponse(
                data={
                    "status": "pending",
                    "message": "User started the process, but has not verfied the challenge",
                }
            )

        raise Exception("Malformed Request")


@method_decorator(csrf_exempt, name="dispatch")
class RetrieveView(View):
    """
    Implements an endpoint that returns the faktsclaim for a given identifier and version
    if the app was already configured and the app is marked as public

    """

    def post(self, request, *args, **kwargs):
        json_data = json.loads(request.body)
        manifest = json_data["manifest"]
        identifier = manifest["identifier"]
        version = manifest["version"]

        try:
            app = App.objects.get(identifier=identifier)
            release = Release.objects.get(app=app, version=version)
        except Release.DoesNotExist:
            return JsonResponse(
                data={
                    "status": "error",
                    "message": f"Release does not exist {identifier}:{version}",
                }
            )
        except App.DoesNotExist:
            return JsonResponse(
                data={
                    "status": "error",
                    "message": f"App does not exist {identifier}",
                }
            )

        try:
            client = release.clients.first()
            if not client:
                return JsonResponse(
                    data={
                        "status": "error",
                        "message": "There is no client for this app registed on this redirect_uri. Please use a different grant",
                    }
                )

            if not client.kind == ClientType.WEBSITE.value:
                return JsonResponse(
                    data={
                        "status": "error",
                        "message": "Client is not public. Please use a different grant",
                    }
                )

            return JsonResponse(
                data={
                    "status": "granted",
                    "token": client.token,
                }
            )

        except Client.DoesNotExist:
            return JsonResponse(
                data={
                    "status": "error",
                    "message": "There is not associated client on the platform that supports this redirect uri",
                }
            )


@method_decorator(csrf_exempt, name="dispatch")
class ClaimView(View):
    """
    Implements an endpoint to retrieve a faktsclaim given a
    token that was generated by the platform
    """

    def post(self, request, *args, **kwargs):
        try:
            json_data = json.loads(request.body)

            token = json_data["token"]
            try:
                client = Client.objects.get(token=token)

                context = create_linking_context(request, client)

                if "template" in json_data and json_data["template"]:
                    try:
                        template = ConfigurationTemplate.objects.get(name=json_data["template"])
                    except ConfigurationGraph.DoesNotExist:
                        return JsonResponse(
                            data={
                                "status": "error",
                                "message": f'Template {json_data["template"]}does not exist',
                            }
                        )
                else:
                    template = get_fitting_template_for_context(context)

                template = template.render(context)

                return JsonResponse(
                    data={
                        "status": "granted",
                        "config": template,
                    }
                )
            except Client.DoesNotExist:
                return JsonResponse(
                    data={
                        "status": "error",
                        "message": "Application not found",
                    }
                )
            except Exception as e:
                logger.error(e , exc_info=True)
                return JsonResponse(
                    data={
                        "status": "error",
                        "message": "Error creating configuration",
                    }
                )
        except Exception as e:
            logger.error(e)
            return JsonResponse(
                data={
                    "status": "error",
                    "message": "Malformed Request",
                }
            )

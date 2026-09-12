"""
URL configuration for kreature project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import include
from fakts.views import WellKnownFakts
from django.shortcuts import render
from kante.path import dynamicpath
from health_check.views import HealthCheckView
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
from strawberry.django.views import AsyncGraphQLView
from allauth.headless.constants import Client
from api.management.schema import schema
from authapp.views import oauth_authorization_server, open_id_configuration
from lok_server.headless_config import PrivacyConfigView


def fakts_challenge(request):
    """
    Placeholdesr view for the .well-known/fakts-challenge endpoint.
    This should be replaced with the actual logic to handle the challenge.
    """
    return HttpResponse("Fakts Challenge Endpoint", status=200)




# Bootstrap Backend
def index(request):
    # Render that in the index template
    return render(request, "index.html")


def management_schema(request):
    schema_content = schema.as_str().encode("utf-8")

    response = HttpResponse(schema_content, content_type="text/plain")
    response["Content-Length"] = str(len(schema_content))
    return response


hallo = "hallsssoss"

urlpatterns = [
    dynamicpath("", index, name="mainhome"),
    # allow_queries_via_get=False: GET is exempt from Django's CSRF check, so
    # leaving GET queries on lets an unauthenticated caller read data cross-site.
    # The SPA only ever POSTs, so this is safe and closes that bypass.
    # graphql_ide=None: strawberry renders the GraphiQL IDE *before* execution,
    # so it is served ahead of `RequireAuthenticationExtension` — an anonymous
    # GET with a browser Accept header got the full IDE. `allow_queries_via_get`
    # does not suppress it.
    dynamicpath("managementgraphql/", AsyncGraphQLView.as_view(schema=schema, allow_queries_via_get=False, graphql_ide=None)),
    dynamicpath("managementschema/", csrf_exempt(login_required(management_schema)), name="management_schema"),
    dynamicpath("admin/", admin.site.urls),
    dynamicpath("f/", include("fakts.urls", namespace="fakts")),
    dynamicpath("o/", include("authapp.urls")),  # /auth/login/, /auth/logout/
    dynamicpath("ht", csrf_exempt(HealthCheckView.as_view(checks=["health_check.checks.Database"])), name="health_check"),
    dynamicpath("accounts/", include("allauth.urls")),
    dynamicpath("accounts/", include("karakter.urls")),
    # Override allauth's headless /config with a privacy-aware one that also reports
    # `privacy_guards`. Must precede the headless include so it wins by URL ordering.
    dynamicpath("_allauth/browser/v1/config", PrivacyConfigView.as_api_view(client=Client.BROWSER)),
    dynamicpath("_allauth/app/v1/config", PrivacyConfigView.as_api_view(client=Client.APP)),
    dynamicpath("_allauth/", include("allauth.headless.urls")),
    dynamicpath(".well-known/fakts-challenge", fakts_challenge, name="fakts-challenge"),
    dynamicpath(".well-known/fakts", WellKnownFakts.as_view()),
    dynamicpath(".well-known/openid-configuration", open_id_configuration, name="openid_configuration"),
    dynamicpath(".well-known/oauth-authorization-server", oauth_authorization_server, name="oauth_authorization_server"),
]

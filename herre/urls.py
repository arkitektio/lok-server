"""arbeid URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
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
from django.urls.conf import include
from herre.helpers import xpath, xstatic
from lord.views import Application, DownloadApplicationViewSet, Me, Callback, WellKnownFakts
import logging
from django.urls import include, path
from django.conf.urls import url
from django.contrib import admin
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from balder.views import BalderView

from django.conf import settings
from django.conf.urls.static import static

logger = logging.getLogger(__name__)

# Bootstrap Backend
def index(request):
    # Render that in the index template
    return render(request, "index-oslo.html")


"hsssssssssssssss"

urlpatterns = (
    [
        path("", index, name="index"),
        path("auth/", Application.as_view()),  # Testing ground for access token testing
        path("avatar/", include("avatar.urls")),
        path("o/me/", Me.as_view()),  # Testing ground for access token testing
        path("accounts/", include("accounts.urls")),  # new
        path("accounts/", include("django.contrib.auth.urls"), name="auth"),
        path("graphql", BalderView, name="graphql"),
        path("admin/", admin.site.urls),
        path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
        path("f/", include("infos.urls", namespace="infos")),
        path("ht/", include("health_check.urls")),
        path("callback/", Callback.as_view(), name="callback"),
        path(".well-known/fakts", WellKnownFakts.as_view()),
    ]
    + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
)

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
from lord.views import Application, DownloadApplicationViewSet, Me
import logging

from django.conf.urls import url
from django.contrib import admin
from django.shortcuts import render
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt
from graphene_django.views import GraphQLView
from rest_framework import routers, serializers, viewsets
from django.conf.urls.static import static
from django.conf import settings

logger = logging.getLogger(__name__)

# Bootstrap Backend
def index(request):
        # Render that in the index template
    return render(request, "index-oslo.html")

router = routers.DefaultRouter()
router.register(r'app', DownloadApplicationViewSet)

urlpatterns = [
    path('', index, name='index'),
    path('auth/', Application.as_view()), # Testing ground for access token testing
    path('api/', include(router.urls)), # Testing ground for access token testing
    url('avatar/', include('avatar.urls')),
    path('me/', Me.as_view()), # Testing ground for access token testing
    url(r'^accounts/', include('registration.backends.default.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    url(r'^graphql$', csrf_exempt(GraphQLView.as_view(graphiql=True))),
    path('admin/', admin.site.urls),
    path('o/', include('oauth2_provider.urls', namespace='oauth2_provider')),
    url(r'^ht/', include('health_check.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

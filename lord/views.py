import logging
from oauth2_provider.contrib.rest_framework.permissions import IsAuthenticatedOrTokenHasScope, TokenHasScope

from django.http.response import HttpResponse
from oauth2_provider.models import AccessToken, get_application_model
from oauth2_provider.contrib.rest_framework.authentication import OAuth2Authentication
from rest_framework.response import Response
import re
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.exceptions import APIException
from .serializers import ApplicationSerializer, CompleteApplicationSerializer, TokenSerializer, UserSerializer
from django.http import FileResponse, request
from rest_framework import viewsets, renderers
from rest_framework.decorators import action
import yaml

@method_decorator(csrf_exempt, name='dispatch')
class Application(APIView):
    """Token Validation View

    This view is validating an Access Token and returns OK it is true (can be called both we post and get)

    Args:
        View ([type]): [description]
    """
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasScope]
    required_scopes = ["introspection"]

    def get(self, request, format=None):
        serializer = ApplicationSerializer(instance=request.auth.application)
        print(serializer.data)
        return Response(serializer.data)
    
    def post(self, request):
        serializer = TokenSerializer(instance=request.auth)
        print(serializer.data)
        return Response(serializer.data)


class PassthroughRenderer(renderers.BaseRenderer):
    """
        Return data as-is. View should supply a Response.
    """
    media_type = 'application/octet-stream'
    format = None
    charset = None
    render_style = 'binary'

    def render(self, data, media_type=None, renderer_context=None):
        return data

@method_decorator(csrf_exempt, name='dispatch')
class DownloadApplicationViewSet(viewsets.ReadOnlyModelViewSet):
    """Token Validation View

    This view is validating an Access Token and returns OK it is true (can be called both we post and get)

    Args:
        View ([type]): [description]
    """
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasScope]
    queryset = get_application_model().objects.all()
    serializer_class = CompleteApplicationSerializer
    required_scopes = ["introspection"]

    @action(methods=['get'], detail=True, renderer_classes=(PassthroughRenderer,))
    def download(self, *args, **kwargs):
        instance = self.get_object()

        serialized = CompleteApplicationSerializer(instance)
        thefile= yaml.dump({"herre": dict(serialized.data)})

        response = FileResponse(thefile)
        response['Content-Disposition'] = 'attachment; filename=bergen.yaml'

        return response

    
    
    
@method_decorator(csrf_exempt, name='dispatch')
class Me(APIView):
    """Me Viewset (only allows get)
    """
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasScope]
    required_scopes = ["introspection"]

    def get(self, request, format=None):
        if request.user and not request.user.is_anonymous:
            user = request.user
        else:
            user = request.auth.user


        serializer = UserSerializer(instance=user)
        return Response(serializer.data)
    






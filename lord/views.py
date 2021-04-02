import logging
from oauth2_provider.contrib.rest_framework.permissions import IsAuthenticatedOrTokenHasScope, TokenHasScope

from django.http.response import HttpResponse
from oauth2_provider.models import AccessToken
from oauth2_provider.contrib.rest_framework.authentication import OAuth2Authentication
from rest_framework.response import Response
import re
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.exceptions import APIException
from .serializers import TokenSerializer, UserSerializer


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
        print(request.auth)
        return Response(json="Ok")
    
    def post(self, request):
        serializer = TokenSerializer(instance=request.auth)
        print(serializer.data)
        return Response(serializer.data)
        


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
    






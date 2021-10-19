from oauth2_provider.models import get_access_token_model, get_application_model
from rest_framework import serializers
from django.contrib.auth import get_user_model

class TokenSerializer(serializers.ModelSerializer):

    class Meta:
        model = get_access_token_model()
        fields = "__all__"

class ApplicationSerializer(serializers.ModelSerializer):

    class Meta:
        model = get_application_model()
        fields = ("client_id","name")


class CompleteApplicationSerializer(serializers.ModelSerializer):

    class Meta:
        model = get_application_model()
        exclude = ("user","id", "skip_authorization", "created", "updated")



    
class UserSerializer(serializers.ModelSerializer):


    class Meta:
        model = get_user_model()
        fields = ("username","email","first_name","last_name")
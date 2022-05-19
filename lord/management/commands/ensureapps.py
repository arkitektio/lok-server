import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings
from oauth2_provider.models import get_application_model, AbstractApplication


class Command(BaseCommand):
    help = "Creates an Consuming Application user non-interactively if it doesn't exist"

    def add_arguments(self, parser):
        parser.add_argument(
            "--client_id",
            help="The Client ID",
            default="9qGqxUYVcNrQyXgAWAu544YmAlBhmuZvtCqfKHap",
        )
        parser.add_argument(
            "--redirect", help="Redirect URIS", default="http://localhost"
        )
        parser.add_argument(
            "--client_secret",
            help="The Client Secret",
            default="MDmIMd3M5JamGqa7xBfF5oXFVELKclAJ4uG9ZHUrNH9XGDxggzbdYcv8ItJyjq4yiVMKPcK8seEdGrUnjKYNIdjqxUDceYXq9fznEr5F5ynXPu1IghHbFm3MbVScirJq",
        )

    def handle(self, *args, **options):

        AppModel = get_application_model()
        UserModel = get_user_model()
        apps = settings.ENSURED_APPS or []

        for app in apps:
            if not AppModel.objects.filter(client_id=app["CLIENT_ID"]).exists():
                AppModel.objects.create(
                    name=app["NAME"],
                    user=UserModel.objects.get(email=app["TENANT"]),
                    client_type=app["CLIENT_TYPE"],
                    redirect_uris="\n".join(app["REDIRECT_URIS"]),
                    client_id=app["CLIENT_ID"],
                    client_secret=app["CLIENT_SECRET"],
                    authorization_grant_type=app["GRANT_TYPE"],
                    algorithm=AbstractApplication.RS256_ALGORITHM,
                )
                print("Application created")
            else:
                aapp = AppModel.objects.get(client_id=app["CLIENT_ID"])
                aapp.name = app["NAME"]
                aapp.user = UserModel.objects.get(email=app["TENANT"])
                aapp.redirect_uris = "\n".join(app["REDIRECT_URIS"])
                aapp.client_id = app["CLIENT_ID"]
                aapp.client_type = app["CLIENT_TYPE"]
                aapp.client_secret = app["CLIENT_SECRET"]
                aapp.authorization_grant_type = app["GRANT_TYPE"]
                aapp.algorithm = AbstractApplication.RS256_ALGORITHM
                aapp.save()
                print("Application already exsisted. Updating")

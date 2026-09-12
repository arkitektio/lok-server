from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from argparse import ArgumentParser
from karakter.models import Organization, Membership
from fakts.models import DeviceCode, Hub
from fakts.logic import validate_device_code

User = get_user_model()


class Command(BaseCommand):
    help = "Validate a device code for a specific user and organization"

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument("--code", type=str, required=True, help="The device code to validate")
        parser.add_argument("--user", type=str, required=True, help="User ID")
        parser.add_argument("--org", type=str, required=True, help="Organization ID")
        parser.add_argument("--hub", type=str, required=True, help="Hub ID")

    def handle(self, *args, **options):
        device_code = options["code"]
        username = options["user"]
        orgslug = options["org"]
        hubidentifier = options["hub"]

        try:
            code = DeviceCode.objects.get(code=device_code)
        except DeviceCode.DoesNotExist:
            raise CommandError(f"Device code {device_code} does not exist")

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"User with username {username} does not exist")

        try:
            org = Organization.objects.get(slug=orgslug)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization with slug {orgslug} does not exist")

        try:
            hub = Hub.objects.get(organization__slug=orgslug, identifier=hubidentifier)
        except Hub.DoesNotExist:
            raise CommandError(f"Hub with identifier {hubidentifier} in organization {orgslug} does not exist")

        # check membership
        try:
            membership = Membership.objects.get(user=user, organization=org)
        except Membership.DoesNotExist:
            raise CommandError(f"User {user.username} is not a member of organization {org.name}")

        try:
            validate_device_code(device_code=code, user=user, organization=org, hub=hub)
        except Exception as e:
            raise CommandError(f"Failed to validate device code {device_code}: {str(e)}")

        print(f"Device code {code} is now valid for user {user.username} in organization {org.name}")

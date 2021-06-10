from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Creates an admin user non-interactively if it doesn't exist"


    def handle(self, *args, **kwargs):
        superusers = settings.SUPERUSERS

        for superuser in superusers:
            User = get_user_model()
            if not User.objects.filter(username=superuser['USERNAME']).exists():
                User.objects.create_superuser(username=superuser['USERNAME'],
                                            email=superuser['EMAIL'],
                                            password=superuser['PASSWORD'])
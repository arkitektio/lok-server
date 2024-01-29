from email.headerregistry import Group
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Creates an admin user non-interactively if it doesn't exist"

    def handle(self, *args, **kwargs):
        superusers = settings.SUPERUSERS

        #TODO: Implement validaiton of superusers

        for superuser in superusers:
            User = get_user_model()
            if not User.objects.filter(username=str(superuser["USERNAME"])).exists():
                user = User.objects.create_superuser(
                    username=str(superuser["USERNAME"]),
                    email=str(superuser["EMAIL"]),
                    password=str(superuser["PASSWORD"]),
                )

                user.groups.set(
                    [
                        Group.objects.get_or_create(name=groupname)[0]
                        for groupname in set(
                            superuser.get("GROUPS", [])
                            + [group.name for group in user.groups.all()]
                        )
                    ]
                )

                user.save()

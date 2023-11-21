from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = "Creates all lok user non-interactively if it doesn't exist"


    def handle(self, *args, **kwargs):
        lokusers = settings.LOKUSERS

        for lokuser in lokusers:
            User = get_user_model()
            if User.objects.filter(username=str(lokuser['USERNAME'])).exists():
                user = User.objects.get(username=str(lokuser["USERNAME"]))
            else:
                user = User.objects.create_user(username=str(lokuser['USERNAME']),
                                            email=lokuser.get('EMAIL',None),
                                            password=str(lokuser['PASSWORD']),
                                            )

            user.groups.set([Group.objects.get_or_create(name=str(groupname))[0] for groupname in set(lokuser.get("GROUPS",[]) + [group.name for group in user.groups.all()])])
            print(f"User {user} created")
            user.save()
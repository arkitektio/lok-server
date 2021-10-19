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
            if User.objects.filter(email=lokuser['EMAIL']).exists():
                user = User.objects.get(email=lokuser["EMAIL"])
            else:
                user = User.objects.create_user(username=lokuser['USERNAME'],
                                            email=lokuser['EMAIL'],
                                            password=lokuser['PASSWORD'],
                                            )

            user.groups.set([Group.objects.get_or_create(name=groupname)[0] for groupname in set(lokuser.get("GROUPS",[]) + [group.name for group in user.groups.all()])])
import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cvd.securitytxt import render_security_txt


class Command(BaseCommand):
    help = "Print the unsigned /.well-known/security.txt body from the `security:` config, for signing offline."

    def handle(self, *args, **options):
        sec = settings.SECURITY_DISCLOSURE
        if sec is None:
            raise CommandError("No `security:` block in the config.")
        days = (sec.expires - datetime.date.today()).days
        if days <= 0:
            raise CommandError(f"security.expires ({sec.expires}) has passed: set a new date before signing.")
        if days > 366:
            # TR-03183-3 4.2.9b / 4.5.3f: at most a year ahead.
            self.stderr.write(f"warning: security.expires is {days} days ahead; BSI asks for at most a year.")
        self.stdout.write(render_security_txt(sec), ending="")

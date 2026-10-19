from typing import Any

from django.core.management.base import BaseCommand

from karakter.roles import sync_all


class Command(BaseCommand):
    """Recompute ``Membership.is_admin`` from the roles: a setup job, run on every build."""

    help = "Recompute which memberships are admins from their roles (Membership.is_admin). Safe to run again."

    def handle(self, *args: Any, **kwargs: Any) -> None:
        """Repair every stale flag, and say how many there were."""
        fixed = sync_all()
        self.stdout.write(f"{fixed} membership(s) had a stale admin flag" if fixed else "Every admin flag matches the roles")

import strawberry
from kante.types import Info

from fakts import models, types
from karakter.authz import get_scoped_or_denied, get_user


def bug_report(info: Info, id: strawberry.ID) -> types.BugReport:
    """One bug report: any of the organization's for its admins, their own for a member."""
    visible = models.BugReport.visible_to(models.BugReport.objects.all(), get_user(info))
    return get_scoped_or_denied(visible, info, pk=id)

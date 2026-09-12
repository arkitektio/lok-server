import logging

import strawberry
from kante.types import Info

from fakts import models as fakts_models
from api.management import types
from django.utils import timezone

from api.management.authz import assert_member, assert_owner_or_admin, get_or_denied

logger = logging.getLogger(__name__)


@strawberry.input
class ResolveReportInput:
    id: strawberry.ID
    note: str | None = None


def resolve_report(info: Info, input: ResolveReportInput) -> types.ManagementReport:
    """Acknowledge a client report.

    This is triage, not repair: it deliberately leaves `client.functional` alone,
    so the record of what the client actually said is preserved. What it changes
    is attention — resolving a client's *latest* report takes it off the
    dashboard's "apps reporting problems" list until the client reports again.

    Any member of the owning organization may acknowledge, since any member can
    already see the report.
    """
    report = get_or_denied(fakts_models.Report.objects, pk=input.id)
    assert_member(info, report.client.organization)
    report.resolve(info.context.request.user, input.note)
    return report


@strawberry.input
class UnresolveReportInput:
    id: strawberry.ID


def unresolve_report(info: Info, input: UnresolveReportInput) -> types.ManagementReport:
    """Reopen an acknowledged report, putting the client back on the action list
    if this is its latest report."""
    report = get_or_denied(fakts_models.Report.objects, pk=input.id)
    assert_member(info, report.client.organization)
    report.unresolve()
    return report


@strawberry.input
class RequestClientReportInput:
    client: strawberry.ID = strawberry.field(description="The client that should re-report its configuration.")
    request: bool = strawberry.field(
        default=True,
        description="True to ask the client to report, False to withdraw a pending request.",
    )


def request_client_report(info: Info, input: RequestClientReportInput) -> types.ManagementClient:
    """Ask a client to re-report its configuration.

    While a request is pending, every token response for this client carries
    `please_report: true` — so the client picks the request up on its next token
    refresh (hourly by default) without anyone touching the machine it runs on,
    and answers it by POSTing to `/f/report/`. That incoming report clears the
    request again.

    This is an operator action on someone else's running deployment, so it takes
    the owner/admin bar (the same one that guards minting credentials), not the
    plain-member bar that `resolve_report` uses for triage.
    """
    client = get_or_denied(fakts_models.Client.objects, pk=input.client)
    assert_owner_or_admin(info, client.organization)

    if input.request:
        client.report_requested_at = timezone.now()
        client.report_requested_by = info.context.request.user
    else:
        client.report_requested_at = None
        client.report_requested_by = None
    client.save(update_fields=["report_requested_at", "report_requested_by"])

    logger.info(
        "Report %s for client %s by user %s",
        "requested" if input.request else "request withdrawn",
        client.client_id,
        info.context.request.user.id,
    )
    return client

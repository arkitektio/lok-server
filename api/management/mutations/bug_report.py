"""Triage of bug reports from lok's own web app.

Filing one is not here: a report is filed from inside a client, on the main
schema, where the token says which client that is (`fakts.graphql.mutations.bug_report`).
The management API reads them and lets an owner or admin close, forward and delete them.
"""

from urllib.parse import urljoin

import strawberry
from django.urls import reverse
from django.utils import timezone
from graphql import GraphQLError
from kante.types import Info

from api.management import types
from api.management.authz import assert_owner_or_admin, get_or_denied, get_user
from fakts import models as fakts_models


def _clip(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def _for_triage(info: Info, id: strawberry.ID) -> fakts_models.BugReport:
    report = get_or_denied(fakts_models.BugReport.objects, pk=id)
    assert_owner_or_admin(info, report.organization)
    return report


@strawberry.input
class ResolveBugReportInput:
    id: strawberry.ID
    note: str | None = None


def resolve_bug_report(info: Info, input: ResolveBugReportInput) -> types.ManagementBugReport:
    """Close a bug report."""
    report = _for_triage(info, input.id)
    report.resolved_at = timezone.now()
    report.resolved_by = get_user(info)
    report.resolution_note = _clip(input.note, 5000)
    report.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])
    return report


@strawberry.input
class UnresolveBugReportInput:
    id: strawberry.ID


def unresolve_bug_report(info: Info, input: UnresolveBugReportInput) -> types.ManagementBugReport:
    """Reopen a closed bug report."""
    report = _for_triage(info, input.id)
    report.resolved_at = None
    report.resolved_by = None
    report.resolution_note = None
    report.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])
    return report


@strawberry.input
class ForwardBugReportInput:
    id: strawberry.ID
    url: str | None = strawberry.field(default=None, description="The issue the report became, when it is known.")
    forwarded: bool = strawberry.field(default=True, description="False takes the mark back.")


def forward_bug_report(info: Info, input: ForwardBugReportInput) -> types.ManagementBugReport:
    """Note that an admin passed this report on to the app's own issue tracker.

    For the prefilled-link path: the admin opens the issue, and this records
    that it happened, so the next admin does not forward it again. Where lok's
    GitHub App is installed, `fileBugReportIssue` opens the issue itself.
    """
    report = _for_triage(info, input.id)
    url = _clip(input.url, 1000)
    if url and not url.lower().startswith(("https://", "http://")):
        raise GraphQLError("The issue link must be an http(s) address.")
    if input.forwarded:
        report.forwarded_at = timezone.now()
        report.forwarded_by = get_user(info)
        report.forwarded_url = url
        report.save(update_fields=["forwarded_at", "forwarded_by", "forwarded_url"])
    else:
        # Taking the mark back also ends the screenshot's publication and the
        # webhook's link to the issue.
        report.forwarded_at = None
        report.forwarded_by = None
        report.forwarded_url = None
        report.github_repo = None
        report.github_issue_number = None
        report.github_issue_state = None
        report.screenshot_token = None
        report.save(
            update_fields=["forwarded_at", "forwarded_by", "forwarded_url", "github_repo", "github_issue_number", "github_issue_state", "screenshot_token"]
        )
    return report


@strawberry.input(description="Open a GitHub issue from a bug report, through lok's GitHub App. The title and body are what the admin reviewed.")
class FileBugReportIssueInput:
    id: strawberry.ID
    title: str
    body: str
    include_screenshot: bool = strawberry.field(default=False, description="Publish the screenshot with the issue. It becomes readable without signing in, by anyone with the issue.")
    again: bool = strawberry.field(default=False, description="Open another issue although lok already opened one for this report.")


def _absolute(info: Info, path: str) -> str | None:
    from authapp.views import discovery_anchor

    anchor = discovery_anchor()
    if anchor:
        return urljoin(anchor + "/", path.lstrip("/"))
    request = getattr(info.context, "request", None)
    build = getattr(request, "build_absolute_uri", None)
    return build(path) if build else None


def file_bug_report_issue(info: Info, input: FileBugReportIssueInput) -> types.ManagementBugReport:
    """Open the issue in the repository the report's app declared, and record it."""
    from fakts.services import github

    report = _for_triage(info, input.id)
    if report.github_issue_number and not input.again:
        raise GraphQLError(f"lok already opened {report.github_repo}#{report.github_issue_number} for this report.")
    if not (input.body or "").strip():
        raise GraphQLError("The issue needs a body.")

    screenshot_url = None
    if input.include_screenshot:
        if not report.screenshot:
            raise GraphQLError("This report has no screenshot.")
        path = reverse("fakts:bugshot", kwargs={"token": github.publish_screenshot(report)})
        screenshot_url = _absolute(info, path)
        if screenshot_url is None:
            raise GraphQLError("lok does not know its own address, so it cannot link the screenshot.")

    try:
        return github.file_issue(report, get_user(info), input.title, input.body, screenshot_url)
    except github.GithubError as e:
        raise GraphQLError(str(e))


@strawberry.input
class DeleteBugReportInput:
    id: strawberry.ID


def delete_bug_report(info: Info, input: DeleteBugReportInput) -> strawberry.ID:
    """Delete a bug report and its screenshot. An owner's or admin's, or the reporter's own."""
    report = get_or_denied(fakts_models.BugReport.objects, pk=input.id)
    if report.reporter_id != get_user(info).id:
        assert_owner_or_admin(info, report.organization)
    report_id = strawberry.ID(str(report.pk))
    report.delete()
    return report_id

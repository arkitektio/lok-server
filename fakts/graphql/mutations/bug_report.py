import base64
import binascii
import datetime
import json
import logging

import strawberry
from django.utils import timezone
from graphql import GraphQLError
from kante.types import Info
from strawberry.scalars import JSON

from fakts import models as fakts_models
from fakts import types
from karakter import roles
from karakter.authz import DENIED, get_organization, get_scoped_or_denied, get_user

logger = logging.getLogger(__name__)

# A report is text plus one picture. These bound what one caller can put in the
# database; the client sends well under them.
DIAGNOSTICS_MAX_BYTES = 512 * 1024
SCREENSHOT_MAX_BYTES = 1024 * 1024
DESCRIPTION_MAX_CHARS = 20_000
REPORTS_PER_HOUR = 20

# What a screenshot may be, with the bytes each starts with: the declared type is
# checked against the content, since it is echoed back in a data URL.
SCREENSHOT_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


@strawberry.input(description="A bug report from inside a client. Which client, release and organization it belongs to is read from the token, not from here.")
class CreateBugReportInput:
    description: str = strawberry.field(description="What happened, in the reporter's words.")
    title: str | None = strawberry.field(default=None, description="A short name; the first line of the description when omitted.")
    expected: str | None = None
    route: str | None = strawberry.field(default=None, description="The page the reporter was on.")
    app_version: str | None = None
    platform: str | None = None
    diagnostics: JSON | None = strawberry.field(default=None, description="What the client recorded. At most 512 KB as JSON.")
    screenshot: str | None = strawberry.field(default=None, description="A data URL (JPEG, PNG or WebP) of at most 1 MB.")


def _parse_screenshot(data_url: str | None) -> tuple[bytes | None, str | None]:
    if not data_url:
        return None, None
    header, _, payload = data_url.partition(",")
    if not header.startswith("data:") or not header.endswith(";base64") or not payload:
        raise GraphQLError("The screenshot must be a base64 data URL.")
    content_type = header[len("data:") : -len(";base64")].lower()
    signatures = SCREENSHOT_SIGNATURES.get(content_type)
    if signatures is None:
        raise GraphQLError(f"Unsupported screenshot type '{content_type}'. Allowed: {', '.join(sorted(SCREENSHOT_SIGNATURES))}.")
    # Four base64 characters carry three bytes: refuse before decoding.
    if len(payload) > SCREENSHOT_MAX_BYTES * 4 // 3 + 4:
        raise GraphQLError("The screenshot is larger than 1 MB.")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise GraphQLError("The screenshot is not valid base64.")
    if len(raw) > SCREENSHOT_MAX_BYTES:
        raise GraphQLError("The screenshot is larger than 1 MB.")
    if not raw.startswith(signatures):
        raise GraphQLError("The screenshot is not the image type it says it is.")
    return raw, content_type


def _clip(value: str | None, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def create_bug_report(info: Info, input: CreateBugReportInput) -> types.BugReport:
    """File a bug report from the calling client.

    Any full member of the client's organization may file one. The client is the
    one the token was issued to, so a report cannot be filed in another client's
    name; its release is recorded alongside, as it is now.
    """
    user = get_user(info)
    try:
        client = info.context.request.client
    except (ValueError, AttributeError):
        client = None
    if client is None:
        raise GraphQLError("A bug report has to be filed from a client.")
    # The token names one organization, and the client must be of it: a report
    # is filed where the reporter is signed in.
    organization = get_organization(info)
    if client.organization_id != organization.id:
        raise GraphQLError(DENIED)

    description = (input.description or "").strip()
    if not description:
        raise GraphQLError("Say what happened: a bug report needs a description.")
    if len(description) > DESCRIPTION_MAX_CHARS:
        raise GraphQLError("The description is too long.")

    diagnostics = input.diagnostics if input.diagnostics is not None else {}
    if not isinstance(diagnostics, dict):
        raise GraphQLError("The diagnostics must be an object.")
    if len(json.dumps(diagnostics)) > DIAGNOSTICS_MAX_BYTES:
        raise GraphQLError("The diagnostics are larger than 512 KB. Leave a part out and send again.")

    screenshot, content_type = _parse_screenshot(input.screenshot)

    since = timezone.now() - datetime.timedelta(hours=1)
    if fakts_models.BugReport.objects.filter(reporter=user, created_at__gte=since).count() >= REPORTS_PER_HOUR:
        raise GraphQLError("You have filed a lot of reports in the last hour. Try again later.")

    title = _clip(input.title, 200) or description.splitlines()[0][:200]
    report = fakts_models.BugReport.objects.create(
        organization=organization,
        client=client,
        release=client.release,
        reporter=user,
        title=title,
        description=description,
        expected=_clip(input.expected, DESCRIPTION_MAX_CHARS),
        route=_clip(input.route, 500),
        app_version=_clip(input.app_version, 100),
        platform=_clip(input.platform, 300),
        diagnostics=diagnostics,
        screenshot=screenshot,
        screenshot_content_type=content_type,
    )
    logger.info("Bug report %s filed by user %s from client %s", report.pk, user.id, client.client_id)
    return report


def _for_triage(info: Info, id: strawberry.ID) -> fakts_models.BugReport:
    """A report of the caller's organization, for an owner or admin of it. Anything else is the uniform denial."""
    report = get_scoped_or_denied(fakts_models.BugReport.objects, info, pk=id)
    if not roles.is_owner_or_admin(get_user(info), report.organization):
        raise GraphQLError(DENIED)
    return report


@strawberry.input
class ResolveBugReportInput:
    id: strawberry.ID
    note: str | None = None


def resolve_bug_report(info: Info, input: ResolveBugReportInput) -> types.BugReport:
    """Close a bug report. Triage is an admin's: a member sees only their own reports."""
    report = _for_triage(info, input.id)
    report.resolved_at = timezone.now()
    report.resolved_by = get_user(info)
    report.resolution_note = _clip(input.note, 5000)
    report.save(update_fields=["resolved_at", "resolved_by", "resolution_note"])
    return report


@strawberry.input
class UnresolveBugReportInput:
    id: strawberry.ID


def unresolve_bug_report(info: Info, input: UnresolveBugReportInput) -> types.BugReport:
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


def forward_bug_report(info: Info, input: ForwardBugReportInput) -> types.BugReport:
    """Note that an admin passed this report on to the app's own issue tracker.

    lok files nothing anywhere itself: the admin opens the issue (the client
    prefills it from the report), and this records that it happened, so the
    next admin does not forward it again.
    """
    report = _for_triage(info, input.id)
    url = _clip(input.url, 1000)
    if url and not url.lower().startswith(("https://", "http://")):
        raise GraphQLError("The issue link must be an http(s) address.")
    if input.forwarded:
        report.forwarded_at = timezone.now()
        report.forwarded_by = get_user(info)
        report.forwarded_url = url
    else:
        report.forwarded_at = None
        report.forwarded_by = None
        report.forwarded_url = None
    report.save(update_fields=["forwarded_at", "forwarded_by", "forwarded_url"])
    return report


@strawberry.input
class DeleteBugReportInput:
    id: strawberry.ID


def delete_bug_report(info: Info, input: DeleteBugReportInput) -> strawberry.ID:
    """Delete a bug report and its screenshot. An admin's, or the reporter's own."""
    report = get_scoped_or_denied(fakts_models.BugReport.objects, info, pk=input.id)
    user = get_user(info)
    if report.reporter_id != user.id and not roles.is_owner_or_admin(user, report.organization):
        raise GraphQLError(DENIED)
    report_id = strawberry.ID(str(report.pk))
    report.delete()
    return report_id

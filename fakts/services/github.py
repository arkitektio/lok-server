"""Filing bug reports as GitHub issues through lok's GitHub App.

lok holds one GitHub App (``settings.GITHUB_APP``). An app that wants its bug
reports forwarded declares its repository as a ``github`` public source and
installs the App there. An admin then forwards a report with one click: lok
renders the issue, the admin reviews and edits it, and lok opens it with an
installation token. The App's ``issues`` webhook (:func:`apply_issue_event`)
closes and reopens the report when the issue is closed or reopened upstream.

Nothing is ever forwarded on its own: a report carries an organization's
diagnostics (routes, request variables, console output) and the repository is
usually public, so a person reads it first.

Repositories the App is not installed on keep the old path: kontrol opens a
prefilled ``issues/new`` link and the admin pastes the issue link back.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

import httpx
import jwt
from django.conf import settings
from django.utils import timezone

from fakts import models

logger = logging.getLogger(__name__)

# GitHub refuses issue bodies over 65,536 characters.
BODY_LIMIT = 65_000
TITLE_LIMIT = 256
TIMEOUT = 10.0

# Tests replace this with an ``httpx.MockTransport`` that plays GitHub.
transport: httpx.BaseTransport | None = None

_REPO_URL = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/*(?:[/?#].*)?$")


class GithubError(Exception):
    """GitHub refused, or could not be reached. The message is safe to show an admin."""


# --------------------------------------------------------------------------- #
# Which repository


def repo_of(client: models.Client | None) -> str | None:
    """``owner/name`` of the first ``github`` public source of ``client``'s app, or None."""
    if client is None:
        return None
    for source in client.public_sources or []:
        if not isinstance(source, dict) or (source.get("kind") or "").lower() != "github":
            continue
        match = _REPO_URL.match((source.get("url") or "").strip())
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return None


def is_configured() -> bool:
    return getattr(settings, "GITHUB_APP", None) is not None


# --------------------------------------------------------------------------- #
# App authentication


def _http() -> httpx.Client:
    return httpx.Client(
        base_url=settings.GITHUB_APP.api_url.rstrip("/"),
        timeout=TIMEOUT,
        transport=transport,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lok-bug-reports"},
    )


def _app_jwt() -> str:
    now = int(time.time())
    # Backdated a minute for clock drift; GitHub allows at most ten minutes.
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": str(settings.GITHUB_APP.app_id)}
    return jwt.encode(payload, settings.GITHUB_APP.private_key_pem(), algorithm="RS256")


_lock = threading.Lock()
# repo -> (installation id or None, valid until)
_installations: dict[str, tuple[int | None, float]] = {}
# installation id -> (token, valid until)
_tokens: dict[int, tuple[str, float]] = {}

INSTALLATION_TTL = 600.0
NOT_INSTALLED_TTL = 60.0


def clear_caches() -> None:
    with _lock:
        _installations.clear()
        _tokens.clear()


def installation_id(repo: str) -> int | None:
    """The App's installation on ``repo``, or None when it is not installed there."""
    now = time.monotonic()
    with _lock:
        cached = _installations.get(repo)
    if cached and cached[1] > now:
        return cached[0]
    try:
        with _http() as http:
            response = http.get(f"/repos/{repo}/installation", headers={"Authorization": f"Bearer {_app_jwt()}"})
    except httpx.HTTPError as e:
        raise GithubError(f"GitHub could not be reached: {e}") from e
    if response.status_code == 404:
        found = None
    elif response.status_code == 200:
        found = int(response.json()["id"])
    else:
        raise GithubError(f"GitHub refused the App ({response.status_code}).")
    with _lock:
        _installations[repo] = (found, now + (INSTALLATION_TTL if found else NOT_INSTALLED_TTL))
    return found


def _installation_token(installation: int) -> str:
    now = time.monotonic()
    with _lock:
        cached = _tokens.get(installation)
    if cached and cached[1] > now:
        return cached[0]
    try:
        with _http() as http:
            response = http.post(f"/app/installations/{installation}/access_tokens", headers={"Authorization": f"Bearer {_app_jwt()}"})
    except httpx.HTTPError as e:
        raise GithubError(f"GitHub could not be reached: {e}") from e
    if response.status_code != 201:
        raise GithubError(f"GitHub did not hand out an installation token ({response.status_code}).")
    data = response.json()
    ttl = 50 * 60.0
    expires = data.get("expires_at")
    if expires:
        left = (datetime.fromisoformat(expires.replace("Z", "+00:00")) - datetime.now(dt_timezone.utc)).total_seconds()
        ttl = max(0.0, left - 5 * 60)
    with _lock:
        _tokens[installation] = (data["token"], now + ttl)
    return data["token"]


# --------------------------------------------------------------------------- #
# The issue


def _clock(at) -> str:
    if not isinstance(at, (int, float)):
        return ""
    return datetime.fromtimestamp(at / 1000, dt_timezone.utc).strftime("%H:%M:%S")


def _fence(text: str) -> list[str]:
    return ["```", str(text).replace("```", "'''"), "```"]


def _diagnostics(report: models.BugReport) -> dict:
    return report.diagnostics if isinstance(report.diagnostics, dict) else {}


def marker(report: models.BugReport) -> str:
    deployment = getattr(settings, "DEPLOYMENT_NAME", "") or "lok"
    return f"<!-- lok-bug-report:{deployment}:{report.pk} -->"


def issue_title(report: models.BugReport) -> str:
    return report.title[:TITLE_LIMIT]


def issue_body(report: models.BugReport) -> str:
    """The report as an issue body: what was written, where, and the errors behind it.

    The same rendering kontrol used for the prefilled link (``issue.ts``), without
    that link's length limit. The admin edits it before it is sent.
    """
    d = _diagnostics(report)
    lines: list[str] = ["### What happened?", report.description, ""]
    if report.expected:
        lines += ["### What was expected?", report.expected, ""]
    lines += [
        "### Environment",
        f"- App version: `{report.app_version or 'unknown'}`",
        f"- OS: `{report.platform or 'unknown'}`",
        f"- Page: `{report.route or 'unknown'}`",
        f"- Reported: {report.created_at.isoformat() if report.created_at else 'unknown'}",
        "",
    ]
    pinned = d.get("pinned")
    if isinstance(pinned, dict):
        title = f"{pinned['title']}: " if pinned.get("title") else ""
        lines += ["### The error this report is about", f"{title}{pinned.get('message') or ''}"]
        if pinned.get("technical"):
            lines += _fence(pinned["technical"])
        lines.append("")
    errors = [e for e in d.get("errors") or [] if isinstance(e, dict)]
    if errors:
        lines.append("### Recent errors")
        for error in reversed(errors):
            title = f"{error['title']}: " if error.get("title") else ""
            lines.append(f"- {_clock(error.get('at'))} {title}{error.get('message') or ''}")
            if error.get("technical"):
                lines += _fence(error["technical"])
        lines.append("")
    requests = [r for r in d.get("requests") or [] if isinstance(r, dict)]
    if requests:
        lines.append("### Failed requests")
        for request in reversed(requests):
            status = f" HTTP {request['status']}" if request.get("status") is not None else ""
            message = str(request.get("message") or "").split("\n")[0]
            lines.append(
                f"- {_clock(request.get('at'))} {request.get('service') or ''} {request.get('kind') or ''} "
                f"`{request.get('operation') or ''}`{status}: {message}"
            )
        lines.append("")
    steps = [s for s in d.get("steps") or [] if isinstance(s, dict)]
    if steps:
        lines.append("### Steps before the report")
        lines += [f"- {_clock(s.get('at'))} {s.get('kind') or ''} {s.get('label') or ''}" for s in steps]
        lines.append("")
    return "\n".join(lines).strip()


def _finish_body(report: models.BugReport, body: str, screenshot_url: str | None) -> str:
    tail = []
    if screenshot_url:
        tail += ["", "### Screenshot", f"![screenshot]({screenshot_url})"]
    tail += ["", "_Forwarded from a bug report filed in Arkitekt._", marker(report)]
    room = BODY_LIMIT - len("\n".join(tail)) - 64
    body = body.strip()
    if len(body) > room:
        body = body[:room] + "\n\n_Cut to fit; the full report is in lok._"
    return body + "\n".join(tail)


@dataclass
class Availability:
    available: bool
    repo: str | None
    reason: str | None


def availability(report: models.BugReport) -> Availability:
    """Can lok open the issue for this report itself, and if not, why not."""
    repo = repo_of(report.client)
    if repo is None:
        return Availability(False, None, "The app did not declare a GitHub repository.")
    if not is_configured():
        return Availability(False, repo, "This deployment has no GitHub App set up.")
    try:
        installed = installation_id(repo)
    except GithubError as e:
        return Availability(False, repo, str(e))
    if installed is None:
        return Availability(False, repo, f"The GitHub App is not installed on {repo}.")
    return Availability(True, repo, None)


def publish_screenshot(report: models.BugReport) -> str:
    """Make the screenshot readable without signing in, under an unguessable token."""
    if not report.screenshot_token:
        report.screenshot_token = secrets.token_urlsafe(32)
        report.save(update_fields=["screenshot_token"])
    return report.screenshot_token


def file_issue(report: models.BugReport, user, title: str, body: str, screenshot_url: str | None = None) -> models.BugReport:
    """Open the issue in the report's repository and record where it went."""
    repo = repo_of(report.client)
    if repo is None:
        raise GithubError("The app did not declare a GitHub repository.")
    if not is_configured():
        raise GithubError("This deployment has no GitHub App set up.")
    installation = installation_id(repo)
    if installation is None:
        raise GithubError(f"The GitHub App is not installed on {repo}.")
    token = _installation_token(installation)

    title = (title or "").strip()[:TITLE_LIMIT] or issue_title(report)
    payload = {"title": title, "body": _finish_body(report, body, screenshot_url)}
    labels = list(settings.GITHUB_APP.labels or [])
    try:
        with _http() as http:
            headers = {"Authorization": f"Bearer {token}"}
            response = http.post(f"/repos/{repo}/issues", json={**payload, "labels": labels} if labels else payload, headers=headers)
            if response.status_code == 422 and labels:
                # A label the App may not create: open the issue without labels.
                response = http.post(f"/repos/{repo}/issues", json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise GithubError(f"GitHub could not be reached: {e}") from e
    if response.status_code != 201:
        logger.warning("GitHub refused an issue for bug report %s in %s: %s %s", report.pk, repo, response.status_code, response.text[:500])
        raise GithubError(f"GitHub refused the issue ({response.status_code}).")
    issue = response.json()

    report.github_repo = repo
    report.github_issue_number = int(issue["number"])
    report.github_issue_state = issue.get("state") or "open"
    report.forwarded_url = issue.get("html_url")
    report.forwarded_at = timezone.now()
    report.forwarded_by = user
    report.save(update_fields=["github_repo", "github_issue_number", "github_issue_state", "forwarded_url", "forwarded_at", "forwarded_by"])
    logger.info("Bug report %s forwarded to %s#%s", report.pk, repo, report.github_issue_number)
    return report


# --------------------------------------------------------------------------- #
# The webhook


def signature_is_valid(body: bytes, signature: str | None) -> bool:
    if not is_configured() or not signature or not signature.startswith("sha256="):
        return False
    secret = settings.GITHUB_APP.webhook_secret.get_secret_value().encode()
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def apply_issue_event(payload: dict) -> int:
    """Follow an issue closed or reopened on GitHub. Returns how many reports changed."""
    action = payload.get("action")
    if action not in ("closed", "reopened"):
        return 0
    issue = payload.get("issue") or {}
    repo = (payload.get("repository") or {}).get("full_name")
    number = issue.get("number")
    if not repo or not isinstance(number, int):
        return 0
    changed = 0
    for report in models.BugReport.objects.filter(github_repo__iexact=repo, github_issue_number=number):
        report.github_issue_state = "closed" if action == "closed" else "open"
        fields = ["github_issue_state"]
        if action == "closed" and report.resolved_at is None:
            reason = " as not planned" if issue.get("state_reason") == "not_planned" else ""
            report.resolved_at = timezone.now()
            report.resolved_by = None
            report.resolution_note = f"Closed on GitHub{reason}: {issue.get('html_url') or f'{repo}#{number}'}"
            fields += ["resolved_at", "resolved_by", "resolution_note"]
        elif action == "reopened" and report.resolved_at is not None and (report.resolution_note or "").startswith("Closed on GitHub"):
            # Reopen only what the webhook closed: an admin's own resolution stands.
            report.resolved_at = None
            report.resolved_by = None
            report.resolution_note = None
            fields += ["resolved_at", "resolved_by", "resolution_note"]
        report.save(update_fields=fields)
        changed += 1
    return changed

"""Forwarding a bug report to GitHub through lok's GitHub App.

``httpx.MockTransport`` plays GitHub. An admin reads the proposed issue
(`githubForwarding`), opens it (`fileBugReportIssue`), and the App's webhook
closes and reopens the report when the issue is closed or reopened.
"""

import hashlib
import hmac
import json

import httpx
import pytest
from asgiref.sync import sync_to_async
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client as HttpClient
from django.urls import reverse

from fakts import models
from fakts.services import github
from lok_server.configuration import GithubSettings
from tests.test_bug_reports import JPEG_URL, _as_session, _create, _setup

REPO = "arkitektio/example"
SECRET = "hook-secret"

DETAIL = """
    query ($id: ID!) { bugReport(id: $id) { id githubForwarding { available repo reason title body } } }
"""
FILE = """
    mutation ($input: FileBugReportIssueInput!) {
        fileBugReportIssue(input: $input) { id forwardedUrl forwardedAt githubRepo githubIssueNumber githubIssueState screenshotPublished }
    }
"""
UNFORWARD = """
    mutation ($input: ForwardBugReportInput!) { forwardBugReport(input: $input) { id forwardedAt screenshotPublished githubIssueNumber } }
"""


class FakeGithub:
    def __init__(self, installed=True):
        self.installed = installed
        self.issues = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/repos/{REPO}/installation":
            return httpx.Response(200, json={"id": 7}) if self.installed else httpx.Response(404, json={})
        if path == "/app/installations/7/access_tokens":
            return httpx.Response(201, json={"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"})
        if path == f"/repos/{REPO}/issues" and request.method == "POST":
            assert request.headers["Authorization"] == "Bearer ghs_test"
            payload = json.loads(request.content)
            self.issues.append(payload)
            number = len(self.issues)
            return httpx.Response(201, json={"number": number, "state": "open", "html_url": f"https://github.com/{REPO}/issues/{number}"})
        return httpx.Response(404, json={})


@pytest.fixture
def fake_github(settings, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    settings.GITHUB_APP = GithubSettings(app_id=1, private_key=pem, webhook_secret=SECRET)
    fake = FakeGithub()
    monkeypatch.setattr(github, "transport", httpx.MockTransport(fake))
    github.clear_caches()
    yield fake
    github.clear_caches()


def _with_repo(s):
    for client in (s["owner_client"], s["member_client"]):
        client.public_sources = [{"kind": "github", "url": f"https://github.com/{REPO}"}]
        client.save(update_fields=["public_sources"])
    return s


async def _management(query, context, **variables):
    from api.management.schema import schema as management_schema

    return await management_schema.execute(query, variable_values=variables, context_value=context)


async def _ready():
    s = await sync_to_async(lambda: _with_repo(_setup()))()
    report = await _create(s["admin"], screenshot=JPEG_URL, diagnostics={"errors": [{"at": 0, "message": "boom", "technical": "Traceback"}]})
    owner = _as_session(s["admin"], s["organization"].owner)
    return s, report, owner


def test_the_repository_is_read_from_the_declared_source():
    client = models.Client(public_sources=[{"kind": "website", "url": "https://x.org"}, {"kind": "github", "url": "https://github.com/a-b/c.d.git/"}])
    assert github.repo_of(client) == "a-b/c.d"
    assert github.repo_of(models.Client(public_sources=[{"kind": "github", "url": "https://evil.org/a/b"}])) is None
    assert github.repo_of(None) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_admin_reviews_and_opens_the_issue(fake_github):
    s, report, owner = await _ready()

    detail = await _management(DETAIL, owner, id=report["id"])
    assert not detail.errors, detail.errors
    forwarding = detail.data["bugReport"]["githubForwarding"]
    assert forwarding["available"] is True and forwarding["repo"] == REPO
    assert "boom" in forwarding["body"] and "Traceback" in forwarding["body"]

    filed = await _management(FILE, owner, input={"id": report["id"], "title": "Empty page", "body": "Edited by the admin", "includeScreenshot": True})
    assert not filed.errors, filed.errors
    data = filed.data["fileBugReportIssue"]
    assert data["forwardedUrl"] == f"https://github.com/{REPO}/issues/1"
    assert (data["githubRepo"], data["githubIssueNumber"], data["githubIssueState"]) == (REPO, 1, "open")
    assert data["screenshotPublished"] is True

    sent = fake_github.issues[0]
    assert sent["title"] == "Empty page"
    assert sent["body"].startswith("Edited by the admin")
    assert f"lok-bug-report:" in sent["body"]
    assert sent["labels"] == ["bug", "user-reported"]

    # The published screenshot is readable without signing in, under its token only.
    stored = await sync_to_async(models.BugReport.objects.get)(pk=report["id"])
    shot_path = reverse("fakts:bugshot", kwargs={"token": stored.screenshot_token})
    assert shot_path in sent["body"]
    http = HttpClient()
    shot = await sync_to_async(http.get)(shot_path)
    assert shot.status_code == 200 and shot["Content-Type"] == "image/jpeg"
    assert (await sync_to_async(http.get)(reverse("fakts:bugshot", kwargs={"token": "x" * 43}))).status_code == 404

    # Not twice by accident.
    again = await _management(FILE, owner, input={"id": report["id"], "title": "t", "body": "b"})
    assert again.errors

    # Taking the forward back ends the screenshot's publication.
    taken = await _management(UNFORWARD, owner, input={"id": report["id"], "forwarded": False})
    assert not taken.errors, taken.errors
    assert taken.data["forwardBugReport"]["screenshotPublished"] is False
    assert (await sync_to_async(http.get)(shot_path)).status_code == 404


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_screenshot_stays_private_unless_chosen(fake_github):
    s, report, owner = await _ready()
    filed = await _management(FILE, owner, input={"id": report["id"], "title": "t", "body": "b"})
    assert not filed.errors, filed.errors
    assert filed.data["fileBugReportIssue"]["screenshotPublished"] is False
    assert "bugshot" not in fake_github.issues[0]["body"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_without_the_app_installed_it_falls_back(fake_github):
    fake_github.installed = False
    s, report, owner = await _ready()

    detail = await _management(DETAIL, owner, id=report["id"])
    forwarding = detail.data["bugReport"]["githubForwarding"]
    assert forwarding["available"] is False
    assert "not installed" in forwarding["reason"]
    filed = await _management(FILE, owner, input={"id": report["id"], "title": "t", "body": "b"})
    assert filed.errors
    assert fake_github.issues == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_member_cannot_open_an_issue(fake_github):
    s, _, _ = await _ready()
    own = await _create(s["plain"])
    member = _as_session(s["plain"], s["member"].user)
    denied = await _management(FILE, member, input={"id": own["id"], "title": "t", "body": "b"})
    assert denied.errors
    assert fake_github.issues == []


def _deliver(http, payload, event="issues", secret=SECRET):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return http.post(reverse("fakts:github-webhook"), data=body, content_type="application/json", HTTP_X_GITHUB_EVENT=event, HTTP_X_HUB_SIGNATURE_256=signature)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_webhook_follows_the_issue(fake_github):
    s, report, owner = await _ready()
    filed = await _management(FILE, owner, input={"id": report["id"], "title": "t", "body": "b"})
    assert not filed.errors, filed.errors
    http = HttpClient()
    issue = {"number": 1, "html_url": f"https://github.com/{REPO}/issues/1", "state_reason": "completed"}

    forged = await sync_to_async(_deliver)(http, {"action": "closed", "issue": issue, "repository": {"full_name": REPO}}, secret="wrong")
    assert forged.status_code == 401
    stored = await sync_to_async(models.BugReport.objects.get)(pk=report["id"])
    assert stored.resolved_at is None

    closed = await sync_to_async(_deliver)(http, {"action": "closed", "issue": issue, "repository": {"full_name": REPO}})
    assert closed.status_code == 200 and closed.json()["changed"] == 1
    stored = await sync_to_async(models.BugReport.objects.get)(pk=report["id"])
    assert stored.resolved_at is not None and stored.github_issue_state == "closed"
    assert stored.resolution_note.startswith("Closed on GitHub")

    reopened = await sync_to_async(_deliver)(http, {"action": "reopened", "issue": issue, "repository": {"full_name": REPO}})
    assert reopened.status_code == 200
    stored = await sync_to_async(models.BugReport.objects.get)(pk=report["id"])
    assert stored.resolved_at is None and stored.github_issue_state == "open"

    other = await sync_to_async(_deliver)(http, {"action": "closed", "issue": {**issue, "number": 99}, "repository": {"full_name": REPO}})
    assert other.json()["changed"] == 0
    assert (await sync_to_async(_deliver)(http, {"zen": "hi"}, event="ping")).status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_admins_own_resolution_survives_a_reopen(fake_github):
    s, report, owner = await _ready()
    await _management(FILE, owner, input={"id": report["id"], "title": "t", "body": "b"})

    def resolve_by_hand():
        models.BugReport.objects.filter(pk=report["id"]).update(resolution_note="fixed in 6.0.2", resolved_at="2026-11-13T00:00:00Z")

    await sync_to_async(resolve_by_hand)()
    http = HttpClient()
    issue = {"number": 1, "html_url": f"https://github.com/{REPO}/issues/1"}
    await sync_to_async(_deliver)(http, {"action": "reopened", "issue": issue, "repository": {"full_name": REPO}})
    stored = await sync_to_async(models.BugReport.objects.get)(pk=report["id"])
    assert stored.resolved_at is not None and stored.resolution_note == "fixed in 6.0.2"

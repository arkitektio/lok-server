"""Bug reports a person files from inside a client (`createBugReport`).

A report belongs to the client the token was issued to, and remembers that
client's release, so an organization reads its reports per client, release and
app. Admins see every report of the organization; a member sees their own.
"""

import base64

import pytest
from asgiref.sync import sync_to_async

import importlib

from lok_server.schema import schema
from fakts import models
from tests import factories
from tests.conftest import build_auth_context

bug_report_mutations = importlib.import_module("fakts.graphql.mutations.bug_report")

CREATE = """
    mutation ($input: CreateBugReportInput!) {
        createBugReport(input: $input) {
            id title description route appVersion hasScreenshot screenshot diagnostics isResolved
            client { id } release { id } app { id } reporter { id }
        }
    }
"""
LIST = """
    query ($filters: BugReportFilter) { bugReports(filters: $filters) { id } }
"""
RESOLVE = """
    mutation ($input: ResolveBugReportInput!) { resolveBugReport(input: $input) { id isResolved resolutionNote } }
"""
FORWARD = """
    mutation ($input: ForwardBugReportInput!) { forwardBugReport(input: $input) { id forwardedAt forwardedUrl } }
"""
DELETE = """
    mutation ($input: DeleteBugReportInput!) { deleteBugReport(input: $input) }
"""

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
JPEG_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


def _setup():
    organization = factories.make_organization()
    owner = factories.make_membership(user=organization.owner, organization=organization)
    member = factories.make_membership(organization=organization)
    app = factories.make_app(organization=organization)
    release = factories.make_release(app=app)
    other_release = factories.make_release(app=factories.make_app(organization=organization))
    owner_client = factories.make_client(membership=owner, release=release)
    member_client = factories.make_client(membership=member, release=other_release)

    outsider_org = factories.make_organization()
    outsider = factories.make_membership(user=outsider_org.owner, organization=outsider_org)
    outsider_client = factories.make_client(membership=outsider)

    return {
        "organization": organization,
        "release": release,
        "other_release": other_release,
        "app": app,
        "owner_client": owner_client,
        "member_client": member_client,
        "member": member,
        "admin": build_auth_context(organization.owner, organization, owner_client),
        "plain": build_auth_context(member.user, organization, member_client, roles=()),
        "outsider": build_auth_context(outsider_org.owner, outsider_org, outsider_client),
    }


async def _run(query, context, **variables):
    return await schema.execute(query, variable_values=variables, context_value=context)


async def _create(context, **fields):
    result = await _run(CREATE, context, input={"description": "The page is empty\nafter a move", **fields})
    assert not result.errors, result.errors
    return result.data["createBugReport"]


async def _ids(context, filters=None):
    result = await _run(LIST, context, filters=filters)
    assert not result.errors, result.errors
    return {row["id"] for row in result.data["bugReports"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_report_belongs_to_the_calling_client_and_its_release():
    s = await sync_to_async(_setup)()
    report = await _create(
        s["admin"],
        route="/mikro/folders/1",
        appVersion="6.0.1",
        diagnostics={"errors": [{"message": "boom"}]},
        screenshot=JPEG_URL,
    )

    assert report["title"] == "The page is empty"
    assert report["client"]["id"] == str(s["owner_client"].id)
    assert report["release"]["id"] == str(s["release"].id)
    assert report["app"]["id"] == str(s["app"].id)
    assert report["reporter"]["id"] == str(s["organization"].owner.id)
    assert report["diagnostics"] == {"errors": [{"message": "boom"}]}
    assert report["hasScreenshot"] is True
    assert report["screenshot"] == JPEG_URL
    assert report["isResolved"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reports_filter_by_client_release_and_app():
    s = await sync_to_async(_setup)()
    from_owner = await _create(s["admin"])
    from_member = await _create(s["plain"])

    assert await _ids(s["admin"]) == {from_owner["id"], from_member["id"]}
    assert await _ids(s["admin"], {"client": str(s["member_client"].id)}) == {from_member["id"]}
    assert await _ids(s["admin"], {"release": str(s["release"].id)}) == {from_owner["id"]}
    assert await _ids(s["admin"], {"app": str(s["app"].id)}) == {from_owner["id"]}
    assert await _ids(s["admin"], {"reporter": str(s["member"].user.id)}) == {from_member["id"]}
    assert await _ids(s["admin"], {"resolved": True}) == set()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_member_sees_their_own_reports_and_another_organization_sees_none():
    s = await sync_to_async(_setup)()
    from_owner = await _create(s["admin"])
    from_member = await _create(s["plain"])

    assert await _ids(s["plain"]) == {from_member["id"]}
    assert await _ids(s["outsider"]) == set()
    assert from_owner["id"] not in await _ids(s["plain"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_an_admin_resolves_and_forwards():
    s = await sync_to_async(_setup)()
    report = await _create(s["plain"])

    denied = await _run(RESOLVE, s["plain"], input={"id": report["id"]})
    assert denied.errors
    denied = await _run(FORWARD, s["outsider"], input={"id": report["id"]})
    assert denied.errors

    resolved = await _run(RESOLVE, s["admin"], input={"id": report["id"], "note": "fixed in 6.0.2"})
    assert not resolved.errors, resolved.errors
    assert resolved.data["resolveBugReport"] == {"id": report["id"], "isResolved": True, "resolutionNote": "fixed in 6.0.2"}
    assert await _ids(s["admin"], {"resolved": True}) == {report["id"]}

    forwarded = await _run(FORWARD, s["admin"], input={"id": report["id"], "url": "https://github.com/a/b/issues/1"})
    assert not forwarded.errors, forwarded.errors
    assert forwarded.data["forwardBugReport"]["forwardedUrl"] == "https://github.com/a/b/issues/1"
    assert forwarded.data["forwardBugReport"]["forwardedAt"]

    bad = await _run(FORWARD, s["admin"], input={"id": report["id"], "url": "javascript:alert(1)"})
    assert bad.errors


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_reporter_may_delete_their_own_report_but_not_another():
    s = await sync_to_async(_setup)()
    from_owner = await _create(s["admin"])
    from_member = await _create(s["plain"])

    denied = await _run(DELETE, s["plain"], input={"id": from_owner["id"]})
    assert denied.errors
    deleted = await _run(DELETE, s["plain"], input={"id": from_member["id"]})
    assert not deleted.errors, deleted.errors
    assert await _ids(s["admin"]) == {from_owner["id"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_what_a_report_may_carry_is_bounded():
    s = await sync_to_async(_setup)()

    async def refused(**fields):
        result = await _run(CREATE, s["admin"], input={"description": "x", **fields})
        return bool(result.errors)

    assert await refused(description="   ")
    assert await refused(diagnostics={"console": "x" * (bug_report_mutations.DIAGNOSTICS_MAX_BYTES + 1)})
    assert await refused(diagnostics=["not", "an", "object"])
    # Not an image type that is allowed, and one that lies about what it is.
    assert await refused(screenshot="data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode())
    assert await refused(screenshot="data:image/png;base64," + base64.b64encode(JPEG).decode())
    big = b"\xff\xd8\xff" + b"\x00" * (bug_report_mutations.SCREENSHOT_MAX_BYTES + 1)
    assert await refused(screenshot="data:image/jpeg;base64," + base64.b64encode(big).decode())
    assert await sync_to_async(models.BugReport.objects.count)() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_one_person_cannot_flood_the_organization(monkeypatch):
    s = await sync_to_async(_setup)()
    monkeypatch.setattr(bug_report_mutations, "REPORTS_PER_HOUR", 2)

    await _create(s["plain"])
    await _create(s["plain"])
    third = await _run(CREATE, s["plain"], input={"description": "again"})
    assert third.errors
    # Someone else is not held back by it.
    await _create(s["admin"])


# --- The management API (lok's own web app): reads and triages, never files. ---

M_LIST = """
    query ($filters: ManagementBugReportFilter) { bugReports(filters: $filters) { id title client { id } app { id } } }
"""
M_DETAIL = """
    query ($id: ID!) { bugReport(id: $id) { id screenshot diagnostics organization { id } } }
"""
M_RESOLVE = """
    mutation ($input: ResolveBugReportInput!) { resolveBugReport(input: $input) { id isResolved } }
"""


def _as_session(context, user):
    # The management schema has no auth extension; production sets the user at
    # the view layer, so it is set directly here.
    context.request._user = user
    return context


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_management_api_reads_and_triages_what_clients_filed():
    from api.management.schema import schema as management_schema

    s = await sync_to_async(_setup)()
    from_owner = await _create(s["admin"], screenshot=JPEG_URL)
    from_member = await _create(s["plain"])
    owner = _as_session(s["admin"], s["organization"].owner)
    member = _as_session(s["plain"], s["member"].user)

    listed = await management_schema.execute(M_LIST, context_value=owner)
    assert not listed.errors, listed.errors
    assert {row["id"] for row in listed.data["bugReports"]} == {from_owner["id"], from_member["id"]}

    by_app = await management_schema.execute(M_LIST, variable_values={"filters": {"app": str(s["app"].id)}}, context_value=owner)
    assert [row["id"] for row in by_app.data["bugReports"]] == [from_owner["id"]]

    detail = await management_schema.execute(M_DETAIL, variable_values={"id": from_owner["id"]}, context_value=owner)
    assert not detail.errors, detail.errors
    assert detail.data["bugReport"]["screenshot"] == JPEG_URL

    # A plain member reads their own and no one else's, and triages nothing.
    own = await management_schema.execute(M_LIST, context_value=member)
    assert {row["id"] for row in own.data["bugReports"]} == {from_member["id"]}
    hidden = await management_schema.execute(M_DETAIL, variable_values={"id": from_owner["id"]}, context_value=member)
    assert hidden.errors
    denied = await management_schema.execute(M_RESOLVE, variable_values={"input": {"id": from_member["id"]}}, context_value=member)
    assert denied.errors

    resolved = await management_schema.execute(M_RESOLVE, variable_values={"input": {"id": from_member["id"]}}, context_value=owner)
    assert not resolved.errors, resolved.errors
    assert resolved.data["resolveBugReport"]["isResolved"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nul_characters_do_not_fail_a_report():
    # Postgres refuses NUL in text and jsonb; a captured console line carried one.
    s = await sync_to_async(_setup)()
    report = await _create(
        s["admin"],
        description="broken\x00page",
        platform="darwin\x00",
        diagnostics={"console": [{"text": "control: [v\x00"}], "k\x00": ["\x00"]},
    )
    stored = await sync_to_async(models.BugReport.objects.get)(pk=report["id"])
    assert stored.description == "broken\ufffdpage"
    assert stored.platform == "darwin\ufffd"
    assert stored.diagnostics == {"console": [{"text": "control: [v\ufffd"}], "k\ufffd": ["\ufffd"]}

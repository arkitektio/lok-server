"""Client report triage (``resolveReport`` / ``unresolveReport``) and retention.

``request_client_report`` is covered in ``test_please_report_and_lifetime``; the
acknowledge/reopen half and the ``Report`` model helpers behind it were not, nor
were the retention rules in ``fakts.services.clients.report_client``. These pin:
resolving only the *latest* report flips the client's dashboard flag, resolving
never rewrites what the client reported (``functional``), unresolve clears every
triage field, the member-vs-owner/admin permission asymmetry, and that pruning
keeps the last healthy report even outside the retention window.
"""

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.utils import timezone

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from fakts import base_models
from fakts import models as fakts_models
from fakts.services import clients as client_services
from tests import factories
from tests.conftest import build_auth_context
from tests.test_management_tenant_isolation import _org_with_admin_and_member

RESOLVE = """
mutation ($id: ID!, $note: String) {
  resolveReport(input: { id: $id, note: $note }) { id isResolved resolutionNote resolvedBy { id } }
}
"""
UNRESOLVE = "mutation ($id: ID!) { unresolveReport(input: { id: $id }) { id isResolved resolutionNote } }"
REQUEST = "mutation ($client: ID!) { requestClientReport(input: { client: $client }) { id } }"


def _setup():
    s = _org_with_admin_and_member()
    client = factories.make_client(membership=s["member_membership"], functional=False)
    older = fakts_models.Report.objects.create(client=client, functional=False)
    latest = fakts_models.Report.objects.create(client=client, functional=False)
    s.update(client=client, older=older, latest=latest)
    return s


async def _flag(client):
    return await sync_to_async(lambda: fakts_models.Client.objects.get(pk=client.pk).latest_report_resolved)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_resolving_the_latest_report_takes_the_client_off_the_action_list():
    s = await sync_to_async(_setup)()

    result = await management_schema.execute(
        RESOLVE, context_value=s["member"], variable_values={"id": str(s["latest"].id), "note": "restarted it"}
    )

    assert not result.errors, result.errors
    assert result.data["resolveReport"]["isResolved"] is True
    assert result.data["resolveReport"]["resolutionNote"] == "restarted it"
    assert result.data["resolveReport"]["resolvedBy"]["id"] == str(s["member_membership"].user.id)
    assert await _flag(s["client"]) is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_resolving_an_older_report_does_not_flip_the_flag():
    s = await sync_to_async(_setup)()

    result = await management_schema.execute(
        RESOLVE, context_value=s["member"], variable_values={"id": str(s["older"].id), "note": None}
    )

    assert not result.errors, result.errors
    assert result.data["resolveReport"]["isResolved"] is True
    assert await _flag(s["client"]) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_resolving_never_rewrites_what_the_client_reported():
    s = await sync_to_async(_setup)()

    await management_schema.execute(RESOLVE, context_value=s["owner"], variable_values={"id": str(s["latest"].id), "note": None})

    functional = await sync_to_async(lambda: fakts_models.Client.objects.get(pk=s["client"].pk).functional)()
    assert functional is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unresolve_clears_every_triage_field_and_reopens():
    s = await sync_to_async(_setup)()
    await management_schema.execute(
        RESOLVE, context_value=s["member"], variable_values={"id": str(s["latest"].id), "note": "done"}
    )

    result = await management_schema.execute(UNRESOLVE, context_value=s["admin"], variable_values={"id": str(s["latest"].id)})

    assert not result.errors, result.errors
    assert result.data["unresolveReport"]["isResolved"] is False
    assert result.data["unresolveReport"]["resolutionNote"] is None
    report = await sync_to_async(fakts_models.Report.objects.get)(pk=s["latest"].pk)
    assert report.resolved_at is None and report.resolved_by_id is None
    assert await _flag(s["client"]) is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_non_member_gets_the_uniform_denial():
    s = await sync_to_async(_setup)()
    outsider = await sync_to_async(factories.make_membership)()
    context = await sync_to_async(
        lambda: build_auth_context(outsider.user, outsider.organization, factories.make_client(membership=outsider))
    )()

    resolved = await management_schema.execute(RESOLVE, context_value=context, variable_values={"id": str(s["latest"].id), "note": None})
    unresolved = await management_schema.execute(UNRESOLVE, context_value=context, variable_values={"id": str(s["latest"].id)})

    assert resolved.errors and resolved.errors[0].message == DENIED
    assert unresolved.errors and unresolved.errors[0].message == DENIED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_member_may_triage_but_not_request_a_report():
    """Triage takes the plain-member bar; asking a running deployment to re-report
    is an operator action and takes the owner/admin bar."""
    s = await sync_to_async(_setup)()

    resolved = await management_schema.execute(RESOLVE, context_value=s["member"], variable_values={"id": str(s["latest"].id), "note": None})
    requested = await management_schema.execute(REQUEST, context_value=s["member"], variable_values={"client": str(s["client"].id)})

    assert not resolved.errors, resolved.errors
    assert requested.errors and requested.errors[0].message == DENIED


# --------------------------------------------------------------------------- #
# fakts.services.clients.report_client — the write side
# --------------------------------------------------------------------------- #


def _report(client, functional):
    return client_services.report_client(client, base_models.ReportRequest(functional=functional))


@pytest.mark.django_db
def test_a_fresh_report_reopens_triage_and_clears_the_pending_request():
    client = factories.make_client()
    fakts_models.Report.objects.create(client=client, functional=False).resolve(client.user, "triaged")
    client.refresh_from_db()
    assert client.latest_report_resolved is True
    client.report_requested_at = timezone.now()
    client.report_requested_by = client.user
    client.save()

    _report(client, functional=False)

    client.refresh_from_db()
    assert client.latest_report_resolved is False
    assert client.report_requested_at is None and client.report_requested_by is None
    assert client.functional is False


@pytest.mark.django_db
def test_healthy_report_moves_the_last_healthy_pointer():
    client = factories.make_client()

    _report(client, functional=True)
    client.refresh_from_db()
    healthy = client.last_healthy_report
    assert healthy is not None and healthy.functional is True

    _report(client, functional=False)
    client.refresh_from_db()
    assert client.last_healthy_report_id == healthy.id


@pytest.mark.django_db
@override_settings(CLIENT_REPORT_RETENTION=2)
def test_pruning_keeps_the_last_healthy_report_outside_the_window():
    client = factories.make_client()

    _report(client, functional=True)
    client.refresh_from_db()
    healthy_id = client.last_healthy_report_id
    for _ in range(3):
        _report(client, functional=False)

    ids = list(fakts_models.Report.objects.filter(client=client).order_by("id").values_list("id", flat=True))
    # Two most recent (the retention window) plus the older healthy one.
    assert len(ids) == 3
    assert healthy_id in ids
    assert ids[-2:] == sorted(ids)[-2:]

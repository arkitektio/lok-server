"""Two operator-facing controls that ride on the token endpoint:

* ``please_report`` — an admin flags a client (``Client.report_requested_at``);
  every subsequent token response for that client carries ``please_report: true``
  until the client answers with a report at ``/f/report/``. A client that
  refreshes hourly therefore picks the request up without anyone touching the
  machine it runs on.
* ``Organization.access_token_lifetime`` — an org-level override of the one-hour
  default, clamped into the server's allowed range at issue time.
"""

import json

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from joserfc import jwt
from joserfc.jwk import RSAKey

from asgiref.sync import sync_to_async

from api.management.authz import DENIED
from api.management.schema import schema as management_schema
from authapp import server
from fakts import base_models, models
from karakter import models as karakter_models
from fakts.services.clients import report_client
from fakts.services.device_codes import validate_device_code
from tests import factories
from tests.conftest import build_auth_context

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _decode(access_token: str) -> dict:
    key = RSAKey.import_key(settings.PUBLIC_KEY)
    return jwt.decode(access_token, key, algorithms=["RS256"]).claims


def _start(client):
    payload = {
        "manifest": {"identifier": "com.example.report", "version": "1.0.0", "scopes": [], "requirements": []},
        "requested_client_kind": "development",
        "requested_client_role": "agent",
    }
    return client.post(reverse("app_authorization"), data=json.dumps(payload), content_type="application/json").json()


def _granted(client):
    """Run the device-code flow to completion; return (body, token, fakts_client)."""
    body = _start(client)
    device_code = models.DeviceCode.objects.get(secret=body["device_code"])
    hub = factories.make_hub()
    user = factories.make_user()
    factories.make_membership(user=user, organization=hub.organization)
    device_code = validate_device_code(device_code, user=user, organization=hub.organization, hub=hub)

    token = client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": body["device_code"], "client_id": body["client_id"]},
        secure=True,
    ).json()
    return body, token, models.Client.objects.get(client_id=body["client_id"])


def _refresh(client, client_id, refresh_token):
    return client.post(
        reverse("token"),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
        secure=True,
    )


# --------------------------------------------------------------------------- #
# please_report
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_refresh_carries_please_report_only_while_requested(client):
    body, token, fakts_client = _granted(client)

    # Nothing pending: the key is absent entirely, so unaffected clients see the
    # exact response shape they saw before.
    unflagged = _refresh(client, body["client_id"], token["refresh_token"]).json()
    assert "please_report" not in unflagged

    models.Client.objects.filter(pk=fakts_client.pk).update(report_requested_at=timezone.now())

    flagged = _refresh(client, body["client_id"], unflagged["refresh_token"]).json()
    assert flagged["please_report"] is True
    # It is a flag on top of the normal response, not a replacement for it.
    assert flagged["access_token"]
    assert flagged["client_id"] == body["client_id"]


@pytest.mark.django_db
def test_please_report_survives_a_failing_envelope_render(client, monkeypatch):
    """A client whose envelope cannot be rendered is exactly the client an
    operator most wants a report from, so the flag must be set before the
    rendering guard swallows the failure."""
    body, token, fakts_client = _granted(client)
    models.Client.objects.filter(pk=fakts_client.pk).update(report_requested_at=timezone.now())

    def boom(*args, **kwargs):
        raise RuntimeError("no instances for you")

    monkeypatch.setattr("fakts.services.rendering.render_envelope", boom)

    response = _refresh(client, body["client_id"], token["refresh_token"])
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed["please_report"] is True
    assert "instances" not in refreshed  # the render did fail


@pytest.mark.django_db
def test_device_code_grant_carries_please_report(client):
    """A re-registering client that is already flagged learns about it on its
    very first token, not only on a refresh."""
    body = _start(client)
    device_code = models.DeviceCode.objects.get(secret=body["device_code"])
    hub = factories.make_hub()
    user = factories.make_user()
    factories.make_membership(user=user, organization=hub.organization)
    validate_device_code(device_code, user=user, organization=hub.organization, hub=hub)
    models.Client.objects.filter(client_id=body["client_id"]).update(report_requested_at=timezone.now())

    token = client.post(
        reverse("token"),
        data={"grant_type": DEVICE_CODE_GRANT, "device_code": body["device_code"], "client_id": body["client_id"]},
        secure=True,
    ).json()
    assert token["please_report"] is True


@pytest.mark.django_db
def test_reporting_clears_the_request(client):
    body, token, fakts_client = _granted(client)
    models.Client.objects.filter(pk=fakts_client.pk).update(report_requested_at=timezone.now())

    report_client(
        models.Client.objects.get(pk=fakts_client.pk),
        base_models.ReportRequest(functional=True, alias_reports={}),
    )

    fakts_client.refresh_from_db()
    assert fakts_client.report_requested_at is None
    assert fakts_client.report_requested_by is None
    assert fakts_client.please_report is False
    assert "please_report" not in _refresh(client, body["client_id"], token["refresh_token"]).json()


# --------------------------------------------------------------------------- #
# organization access-token lifetime
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_org_lifetime_applies_to_both_expires_in_and_the_jwt_exp(client):
    body, token, fakts_client = _granted(client)
    assert token["expires_in"] == server.ACCESS_TOKEN_EXPIRES_IN

    organization = fakts_client.organization
    organization.access_token_lifetime = 7200
    organization.save()

    refreshed = _refresh(client, body["client_id"], token["refresh_token"]).json()
    assert refreshed["expires_in"] == 7200
    # authlib computes `exp` and `expires_in` in two separate calls; they must agree.
    claims = _decode(refreshed["access_token"])
    assert claims["exp"] - claims["iat"] == 7200


@pytest.mark.django_db
def test_org_lifetime_is_clamped_at_issue_time(client):
    """A value written straight into the database (or stored before the cap
    existed) must not outlive the cap."""
    body, token, fakts_client = _granted(client)
    organization = fakts_client.organization
    organization.access_token_lifetime = 60 * 60 * 24 * 365
    organization.save()

    refreshed = _refresh(client, body["client_id"], token["refresh_token"]).json()
    assert refreshed["expires_in"] == server.MAX_ACCESS_TOKEN_EXPIRES_IN

    organization.access_token_lifetime = 30
    organization.save()
    again = _refresh(client, body["client_id"], refreshed["refresh_token"]).json()
    assert again["expires_in"] == server.MIN_ACCESS_TOKEN_EXPIRES_IN


class _OrglessClient:
    """A relying party: no organization to ask (and none of the attributes the
    generator would find on a fakts client)."""

    organization = None


def test_orgless_client_falls_back_to_the_default():
    assert server.access_token_expires_in(_OrglessClient(), "authorization_code") == server.ACCESS_TOKEN_EXPIRES_IN
    assert server.access_token_expires_in(object(), "authorization_code") == server.ACCESS_TOKEN_EXPIRES_IN


# --------------------------------------------------------------------------- #
# the management mutations behind both controls
# --------------------------------------------------------------------------- #

REQUEST_REPORT = """
    mutation ($input: RequestClientReportInput!) {
        requestClientReport(input: $input) { id pleaseReport reportRequestedAt }
    }
"""

UPDATE_ORGANIZATION = """
    mutation ($input: UpdateOrganizationInput!) {
        updateOrganization(input: $input) { id accessTokenLifetime }
    }
"""


def _flagged_setup():
    """An organization, its owner's context, and a client in it."""
    organization = factories.make_organization()
    owner_membership = factories.make_membership(user=organization.owner, organization=organization)
    target = factories.make_client(membership=owner_membership)
    context = build_auth_context(organization.owner, organization, factories.make_client(membership=owner_membership))
    return organization, context, target


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_admin_can_request_and_withdraw_a_client_report():
    organization, context, target = await sync_to_async(_flagged_setup)()

    result = await management_schema.execute(
        REQUEST_REPORT, variable_values={"input": {"client": str(target.id)}}, context_value=context
    )
    assert not result.errors, result.errors
    assert result.data["requestClientReport"]["pleaseReport"] is True

    refreshed = await sync_to_async(models.Client.objects.get)(pk=target.pk)
    assert refreshed.report_requested_by_id == organization.owner_id

    result = await management_schema.execute(
        REQUEST_REPORT,
        variable_values={"input": {"client": str(target.id), "request": False}},
        context_value=context,
    )
    assert not result.errors, result.errors
    assert result.data["requestClientReport"]["pleaseReport"] is False
    assert result.data["requestClientReport"]["reportRequestedAt"] is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_plain_member_cannot_request_a_client_report():
    """Flagging is an operator action on a running deployment, so it takes the
    owner/admin bar — not the plain-member bar that report triage uses."""

    def setup():
        organization, _, target = _flagged_setup()
        member = factories.make_membership(organization=organization)
        return target, build_auth_context(
            member.user, organization, factories.make_client(membership=member), roles=()
        )

    target, member_context = await sync_to_async(setup)()

    result = await management_schema.execute(
        REQUEST_REPORT, variable_values={"input": {"client": str(target.id)}}, context_value=member_context
    )
    assert result.errors
    assert result.errors[0].message == DENIED

    unchanged = await sync_to_async(models.Client.objects.get)(pk=target.pk)
    assert unchanged.report_requested_at is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_sets_the_org_access_token_lifetime_within_the_allowed_range():
    organization, context, _ = await sync_to_async(_flagged_setup)()

    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={"input": {"id": str(organization.id), "accessTokenLifetime": 7200}},
        context_value=context,
    )
    assert not result.errors, result.errors
    assert result.data["updateOrganization"]["accessTokenLifetime"] == 7200

    # Out of range is refused with a sentence, not silently clamped: an admin who
    # types a year should be told the cap exists.
    result = await management_schema.execute(
        UPDATE_ORGANIZATION,
        variable_values={"input": {"id": str(organization.id), "accessTokenLifetime": 60 * 60 * 24 * 365}},
        context_value=context,
    )
    assert result.errors
    assert "between" in result.errors[0].message

    stored = await sync_to_async(karakter_models.Organization.objects.get)(pk=organization.pk)
    assert stored.access_token_lifetime == 7200

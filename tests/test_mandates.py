"""Mandates: a grantor lets an agent app provision clients of a subject app as them.

The agent (a deployer) is an ordinary client; the subject (the app it starts) ends
up acting as the *grantor*, not as the agent's operator, and dies with the mandate.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.test import Client as HttpClient
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import RSAKey

from fakts import models
from karakter.models import Scope
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

REDEEM_GRANT = "urn:fakts:grant-type:redeem"
AGENT = "live.arkitekt.deployer"
SUBJECT = {
    "identifier": "com.example.subject",
    "version": "1.2.0",
    "scopes": ["read"],
    "requirements": [{"key": "rekuest", "service": "live.arkitekt.rekuest", "optional": True}],
}


def _world():
    """A grantor (on orkestrator, composing against a hub) and an agent operator's deployer client."""
    grantor = factories.make_membership()
    org = grantor.organization
    hub = factories.make_hub(organization=org)
    Scope.objects.get_or_create(identifier="read", organization=org)
    ui_client = factories.make_client(membership=grantor, hub=hub)

    operator = factories.make_membership(organization=org)
    agent_release = factories.make_release(app=factories.make_app(organization=org, identifier=AGENT))
    agent_client = factories.make_client(membership=operator, release=agent_release, hub=hub)
    return grantor, hub, ui_client, operator, agent_client


def _ctx(membership, client):
    return build_auth_context(membership.user, membership.organization, client)


CREATE = """
mutation ($input: CreateMandateInput!) {
  createMandate(input: $input) { id agentIdentifier subjectManifest attestation maxClients isLive grantor { id } }
}
"""
PROVISION = """
mutation ($input: ProvisionInput!) { provision(input: $input) { id token pinnedManifest mandate { id } } }
"""
REVOKE = """
mutation ($input: RevokeMandateInput!) { revokeMandate(input: $input) { id revokedAt isLive } }
"""
RELEASE = """
mutation ($input: ReleaseMandateClientInput!) { releaseMandateClient(input: $input) }
"""
TOKEN = """
query ($id: ID!) { mandateToken(id: $id) { id client { clientId } } }
"""
MANDATES = """
query { mandates { id agentIdentifier } }
"""


async def _run(query, ctx, **variables):
    return await schema.execute(query, context_value=ctx, variable_values=variables)


async def _create(grantor, ui_client, **extra):
    result = await _run(CREATE, _ctx(grantor, ui_client), input={"agent": AGENT, "manifest": SUBJECT, **extra})
    assert not result.errors, result.errors
    return result.data["createMandate"]


async def _provision(operator, agent_client, mandate_id, device_id="host-a:pod-1"):
    return await _run(PROVISION, _ctx(operator, agent_client), input={"mandate": mandate_id, "deviceId": device_id})


def _redeem(http, token, **overrides):
    manifest = {**SUBJECT, "deviceId": "host-a:pod-1", **overrides}
    manifest["device_id"] = manifest.pop("deviceId")
    return http.post(
        reverse("token"),
        data={"grant_type": REDEEM_GRANT, "redeem_token": token, "manifest": json.dumps(manifest)},
        secure=True,
    )


def _claims(access_token):
    return jwt.decode(access_token, RSAKey.import_key(settings.PUBLIC_KEY), algorithms=["RS256"]).claims


# --- create -----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_pins_the_subject_without_a_device():
    grantor, hub, ui_client, *_ = await sync_to_async(_world)()
    data = await _create(grantor, ui_client, attestation="sha256:abc", maxClients=2)

    assert data["agentIdentifier"] == AGENT
    assert data["attestation"] == "sha256:abc"
    assert data["isLive"] is True
    assert data["grantor"]["id"] == str(grantor.user_id)
    assert data["subjectManifest"]["identifier"] == "com.example.subject"
    assert data["subjectManifest"]["scopes"] == ["read"]
    assert "device_id" not in data["subjectManifest"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_refuses_unknown_scopes():
    grantor, hub, ui_client, *_ = await sync_to_async(_world)()
    result = await _run(
        CREATE, _ctx(grantor, ui_client), input={"agent": AGENT, "manifest": {**SUBJECT, "scopes": ["write:everything"]}}
    )
    assert result.errors and "write:everything" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_mandated_client_cannot_grant_further_mandates():
    grantor, hub, ui_client, *_ = await sync_to_async(_world)()
    data = await _create(grantor, ui_client)

    def _mandated_client():
        return factories.make_client(membership=grantor, hub=hub, mandate_id=data["id"])

    chained = await sync_to_async(_mandated_client)()
    result = await _run(CREATE, _ctx(grantor, chained), input={"agent": AGENT, "manifest": SUBJECT})
    assert result.errors and "cannot grant" in result.errors[0].message


# --- provision → redeem -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_provisioned_client_acts_as_the_grantor():
    grantor, hub, ui_client, operator, agent_client = await sync_to_async(_world)()
    mandate = await _create(grantor, ui_client)

    result = await _provision(operator, agent_client, mandate["id"])
    assert not result.errors, result.errors
    token = result.data["provision"]
    assert token["pinnedManifest"]["device_id"] == "host-a:pod-1"
    assert token["mandate"]["id"] == mandate["id"]

    resp = await sync_to_async(_redeem)(HttpClient(), token["token"])
    assert resp.status_code == 200, resp.json()
    claims = _claims(resp.json()["access_token"])
    assert claims["sub"] == str(grantor.user_id)
    assert claims["sub"] != str(operator.user_id)
    assert claims["act"] == {"client_app": AGENT, "mandate": mandate["id"]}

    client = await models.Client.objects.aget(client_id=resp.json()["client_id"])
    assert client.mandate_id == int(mandate["id"])

    # The agent can see which client its token produced.
    seen = await _run(TOKEN, _ctx(operator, agent_client), id=token["id"])
    assert not seen.errors, seen.errors
    assert seen.data["mandateToken"]["client"]["clientId"] == client.client_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_the_named_agent_app_may_provision():
    grantor, hub, ui_client, operator, agent_client = await sync_to_async(_world)()
    mandate = await _create(grantor, ui_client)

    # The grantor's own orkestrator client is not the agent.
    result = await _provision(grantor, ui_client, mandate["id"])
    assert result.errors and "Not found" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_device_pin_is_enforced():
    grantor, hub, ui_client, operator, agent_client = await sync_to_async(_world)()
    mandate = await _create(grantor, ui_client, agentDeviceId="some-other-host")

    result = await _provision(operator, agent_client, mandate["id"])
    assert result.errors and "Not found" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_max_clients_counts_outstanding_tokens_and_release_frees_a_slot():
    grantor, hub, ui_client, operator, agent_client = await sync_to_async(_world)()
    mandate = await _create(grantor, ui_client, maxClients=1)

    first = await _provision(operator, agent_client, mandate["id"], "host-a:pod-1")
    assert not first.errors, first.errors
    second = await _provision(operator, agent_client, mandate["id"], "host-a:pod-2")
    assert second.errors and "no client slots" in second.errors[0].message

    resp = await sync_to_async(_redeem)(HttpClient(), first.data["provision"]["token"])
    assert resp.status_code == 200, resp.json()

    released = await _run(RELEASE, _ctx(operator, agent_client), input={"clientId": resp.json()["client_id"]})
    assert not released.errors, released.errors

    third = await _provision(operator, agent_client, mandate["id"], "host-a:pod-3")
    assert not third.errors, third.errors


@pytest.mark.django_db
def test_redeem_cannot_exceed_the_mandate_ceiling(client):
    grantor, hub, ui_client, operator, agent_client = _world()
    from fakts.base_models import Manifest
    from fakts.services import mandates

    mandate = mandates.create_mandate(grantor=grantor, hub=hub, agent_identifier=AGENT, subject=Manifest(**SUBJECT))
    token = mandates.provision(mandate_id=mandate.pk, agent=agent_client, device_id="host-a:pod-1")

    Scope.objects.get_or_create(identifier="admin:all", organization=grantor.organization)
    resp = _redeem(client, token.token, scopes=["read", "admin:all"])
    assert resp.status_code == 400
    assert "admin:all" in resp.json()["error_description"]

    resp = _redeem(client, token.token, deviceId="host-a:pod-9")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_two_instances_on_one_host_do_not_rotate_each_other(client):
    grantor, hub, ui_client, operator, agent_client = _world()
    from fakts.base_models import Manifest
    from fakts.services import mandates

    mandate = mandates.create_mandate(grantor=grantor, hub=hub, agent_identifier=AGENT, subject=Manifest(**SUBJECT))
    a = mandates.provision(mandate_id=mandate.pk, agent=agent_client, device_id="host-a:pod-1")
    b = mandates.provision(mandate_id=mandate.pk, agent=agent_client, device_id="host-a:pod-2")

    assert _redeem(client, a.token, deviceId="host-a:pod-1").status_code == 200
    assert _redeem(client, b.token, deviceId="host-a:pod-2").status_code == 200
    assert mandate.clients.count() == 2


# --- revoke -----------------------------------------------------------------------


@pytest.mark.django_db
def test_revoke_deletes_clients_and_kills_refresh_and_pending_tokens(client):
    grantor, hub, ui_client, operator, agent_client = _world()
    from fakts.base_models import Manifest
    from fakts.services import mandates

    mandate = mandates.create_mandate(grantor=grantor, hub=hub, agent_identifier=AGENT, subject=Manifest(**SUBJECT))
    used = mandates.provision(mandate_id=mandate.pk, agent=agent_client, device_id="host-a:pod-1")
    pending = mandates.provision(mandate_id=mandate.pk, agent=agent_client, device_id="host-a:pod-2")
    body = _redeem(client, used.token).json()

    mandates.revoke_mandate(mandate)

    assert not models.Client.objects.filter(client_id=body["client_id"]).exists()
    refresh = client.post(
        reverse("token"),
        data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": body["client_id"]},
        secure=True,
    )
    assert refresh.status_code in (400, 401)
    assert _redeem(client, pending.token, deviceId="host-a:pod-2").status_code == 400

    with pytest.raises(mandates.MandateNotLive):
        mandates.provision(mandate_id=mandate.pk, agent=agent_client, device_id="host-a:pod-3")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_grantor_or_admin_may_revoke_and_agent_sees_its_mandates():
    grantor, hub, ui_client, operator, agent_client = await sync_to_async(_world)()
    mandate = await _create(grantor, ui_client)

    # The agent's operator can list the mandate (as the agent app) but not revoke it.
    listed = await _run(MANDATES, _ctx(operator, agent_client))
    assert not listed.errors, listed.errors
    assert [m["id"] for m in listed.data["mandates"]] == [mandate["id"]]

    denied = await _run(REVOKE, _ctx(operator, agent_client), input={"id": mandate["id"]})
    assert denied.errors and "Not found" in denied.errors[0].message

    revoked = await _run(REVOKE, _ctx(grantor, ui_client), input={"id": mandate["id"]})
    assert not revoked.errors, revoked.errors
    assert revoked.data["revokeMandate"]["isLive"] is False

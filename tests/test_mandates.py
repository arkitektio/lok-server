"""Who may hand out mandates, and how a mandate dies with whoever handed it out.

An app may issue a key, or an agent mandate, acting as its own user, capped at what it
holds itself. The agent a mandate names (a deployer) issues one keyed child per
instance it runs; that child logs in as the *grantor*, not as the agent's operator.
Every mandate dies with its issuer: revoking the parent, rejecting or deleting the
issuing app, deleting the hub or the grantor.
"""

import json
import threading
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connection
from django.test import Client as HttpClient
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import RSAKey

from fakts import models
from fakts.base_models import MandatePin, Manifest
from fakts.services import mandates
from fakts.services.clients import bind_client, create_public_client
from karakter.models import Scope
from lok_server.schema import schema
from tests import factories
from tests.conftest import build_auth_context

GRANT = "urn:fakts:grant-type:mandate-key"
AGENT = "live.arkitekt.deployer"
REQUIREMENT = {"key": "rekuest", "service": "live.arkitekt.rekuest", "optional": True}
SUBJECT = {"identifier": "com.example.subject", "version": "1.2.0", "scopes": ["read"], "requirements": [REQUIREMENT]}


def _world():
    """A grantor on an app holding `read` and `rekuest` (orkestrator), and an operator's deployer client."""
    grantor = factories.make_membership()
    org = grantor.organization
    hub = factories.make_hub(organization=org)
    read, _ = Scope.objects.get_or_create(identifier="read", organization=org)
    ui_client = factories.make_client(
        membership=grantor,
        hub=hub,
        release=factories.make_release(app=factories.make_app(organization=org, identifier="live.arkitekt.orkestrator")),
        manifest={"identifier": "live.arkitekt.orkestrator", "version": "1.0.0", "scopes": ["read"], "requirements": [REQUIREMENT]},
    )
    ui_client.scopes.add(read)

    operator = factories.make_membership(organization=org)
    agent_release = factories.make_release(app=factories.make_app(organization=org, identifier=AGENT))
    agent_client = factories.make_client(membership=operator, release=agent_release, hub=hub)
    return SimpleNamespace(grantor=grantor, hub=hub, ui_client=ui_client, operator=operator, agent_client=agent_client, org=org)


def _ctx(membership, client):
    return build_auth_context(membership.user, membership.organization, client)


CREATE = """
mutation ($input: CreateMandateInput!) {
  createMandate(input: $input) { id claimant origin agentIdentifier attestation maxClients isLive grantor { id } subject { identifier version deviceId scopes requirements { key service } } }
}
"""
ISSUE_KEY = """
mutation ($input: IssueMandateKeyInput!) { issueMandateKey(input: $input) { key mandate { id claimant origin keyPrefix subject { identifier version deviceId } } } }
"""
ISSUE_CHILD = """
mutation ($input: IssueChildMandateKeyInput!) { issueChildMandateKey(input: $input) { key mandate { id origin parent { id } subject { deviceId } } } }
"""
REVOKE = """
mutation ($input: RevokeMandateInput!) { revokeMandate(input: $input) { id revokedAt revocationReason isLive } }
"""
MANDATES = """
query { mandates { id agentIdentifier origin } }
"""


async def _run(query, ctx, **variables):
    return await schema.execute(query, context_value=ctx, variable_values=variables)


async def _create(w, **extra):
    result = await _run(CREATE, _ctx(w.grantor, w.ui_client), input={"agent": AGENT, "manifest": SUBJECT, **extra})
    assert not result.errors, result.errors
    return result.data["createMandate"]


async def _child(w, mandate_id, device_id="host-a:pod-1"):
    return await _run(ISSUE_CHILD, _ctx(w.operator, w.agent_client), input={"mandate": mandate_id, "deviceId": device_id})


def _login(http, key, **overrides):
    manifest = {**SUBJECT, "device_id": "host-a:pod-1", **overrides}
    return http.post(reverse("token"), data={"grant_type": GRANT, "key": key, "manifest": json.dumps(manifest)}, secure=True)


def _claims(access_token):
    return jwt.decode(access_token, RSAKey.import_key(settings.PUBLIC_KEY), algorithms=["RS256"]).claims


def _agent_mandate(w, **kw) -> models.Mandate:
    return mandates.issue_mandate(
        issuer=mandates.ClientIssuer(client=w.ui_client),
        grantor=w.grantor,
        hub=w.hub,
        pin=MandatePin.from_manifest(Manifest(**SUBJECT)),
        claimant=mandates.AgentClaimant(identifier=AGENT),
        **kw,
    ).mandate


def _issue_child(w, parent, device_id="host-a:pod-1", **kw) -> str:
    issued = mandates.issue_mandate(
        issuer=mandates.AgentIssuer(parent=parent, client=w.agent_client),
        grantor=parent.membership,
        hub=parent.hub,
        pin=parent.pin.narrowed_to_device(device_id),
        claimant=mandates.KeyClaimant(),
        **kw,
    )
    return issued.key


# --- an app grants an agent mandate ----------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_pins_the_subject_without_a_device():
    w = await sync_to_async(_world)()
    data = await _create(w, attestation="sha256:abc", maxClients=2)

    assert (data["claimant"], data["origin"], data["agentIdentifier"]) == ("AGENT", "CLIENT", AGENT)
    assert data["subject"] == {
        "identifier": "com.example.subject",
        "version": "1.2.0",
        "deviceId": None,
        "scopes": ["read"],
        "requirements": [{"key": "rekuest", "service": "live.arkitekt.rekuest"}],
    }
    assert data["attestation"] == "sha256:abc"
    assert data["isLive"] is True
    assert data["grantor"]["id"] == str(w.grantor.user_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_refuses_unknown_scopes():
    w = await sync_to_async(_world)()
    result = await _run(CREATE, _ctx(w.grantor, w.ui_client), input={"agent": AGENT, "manifest": {**SUBJECT, "scopes": ["nope"]}})
    assert result.errors and "nope" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_app_cannot_grant_more_than_it_holds():
    w = await sync_to_async(_world)()
    await Scope.objects.aget_or_create(identifier="admin:all", organization=w.org)

    more_scopes = await _run(CREATE, _ctx(w.grantor, w.ui_client), input={"agent": AGENT, "manifest": {**SUBJECT, "scopes": ["read", "admin:all"]}})
    assert more_scopes.errors and "admin:all" in more_scopes.errors[0].message

    extra = {"key": "mikro", "service": "live.arkitekt.mikro"}
    more_requirements = await _run(
        CREATE, _ctx(w.grantor, w.ui_client), input={"agent": AGENT, "manifest": {**SUBJECT, "requirements": [REQUIREMENT, extra]}}
    )
    assert more_requirements.errors and "mikro=live.arkitekt.mikro" in more_requirements.errors[0].message


@pytest.mark.django_db
def test_a_client_logged_in_with_a_key_cannot_issue_mandates(client):
    w = _world()
    parent = _agent_mandate(w)
    body = _login(client, _issue_child(w, parent)).json()
    keyed = models.Client.objects.get(client_id=body["client_id"])

    with pytest.raises(mandates.MandateChaining):
        mandates.issue_mandate(
            issuer=mandates.ClientIssuer(client=keyed),
            grantor=keyed.membership,
            hub=w.hub,
            pin=MandatePin.from_manifest(Manifest(**SUBJECT)),
            claimant=mandates.KeyClaimant(),
        )


# --- an app issues a key as itself -----------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_app_issues_a_capped_key_that_dies_with_it():
    w = await sync_to_async(_world)()
    result = await _run(
        ISSUE_KEY, _ctx(w.grantor, w.ui_client), input={"manifest": {**SUBJECT, "deviceId": "box"}, "expiresInDays": 7}
    )
    assert not result.errors, result.errors
    issued = result.data["issueMandateKey"]
    assert issued["key"].startswith(issued["mandate"]["keyPrefix"])
    assert (issued["mandate"]["claimant"], issued["mandate"]["origin"]) == ("KEY", "CLIENT")
    assert issued["mandate"]["subject"] == {"identifier": "com.example.subject", "version": "1.2.0", "deviceId": "box"}

    resp = await sync_to_async(_login)(HttpClient(), issued["key"], device_id="box")
    assert resp.status_code == 200, resp.json()
    assert _claims(resp.json()["access_token"])["act"] == {"client_app": "live.arkitekt.orkestrator", "mandate": issued["mandate"]["id"]}

    await sync_to_async(w.ui_client.delete)()
    assert not await models.Mandate.objects.filter(pk=issued["mandate"]["id"]).aexists()
    assert (await sync_to_async(_login)(HttpClient(), issued["key"], device_id="box")).status_code == 400


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 31])
async def test_an_app_key_must_expire_within_a_month(days):
    w = await sync_to_async(_world)()
    result = await _run(ISSUE_KEY, _ctx(w.grantor, w.ui_client), input={"manifest": SUBJECT, "expiresInDays": days})
    assert result.errors and "expiresInDays" in result.errors[0].message


# --- the agent issues keyed children ---------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_child_key_logs_in_as_the_grantor():
    w = await sync_to_async(_world)()
    mandate = await _create(w)

    result = await _child(w, mandate["id"])
    assert not result.errors, result.errors
    child = result.data["issueChildMandateKey"]
    assert child["mandate"]["subject"]["deviceId"] == "host-a:pod-1"
    assert child["mandate"]["parent"]["id"] == mandate["id"]
    assert child["mandate"]["origin"] == "AGENT"

    resp = await sync_to_async(_login)(HttpClient(), child["key"])
    assert resp.status_code == 200, resp.json()
    claims = _claims(resp.json()["access_token"])
    assert claims["sub"] == str(w.grantor.user_id)
    assert claims["sub"] != str(w.operator.user_id)
    assert claims["act"] == {"client_app": AGENT, "mandate": mandate["id"]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_the_named_agent_app_may_issue_children():
    w = await sync_to_async(_world)()
    mandate = await _create(w)

    # The grantor's own orkestrator client is not the agent.
    result = await _run(ISSUE_CHILD, _ctx(w.grantor, w.ui_client), input={"mandate": mandate["id"], "deviceId": "x"})
    assert result.errors and "Not found" in result.errors[0].message


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_agent_device_pin_is_enforced():
    w = await sync_to_async(_world)()
    mandate = await _create(w, agentDeviceId="some-other-host")

    result = await _child(w, mandate["id"])
    assert result.errors and "Not found" in result.errors[0].message


@pytest.mark.django_db
def test_a_key_backed_agent_may_issue_children():
    """The plugin engine logs in with a hub mandate key and deploys apps as the approver."""
    w = _world()
    engine_parent, engine_key = factories.make_mandate_key(hub=w.hub, membership=w.operator, pin={"identifier": AGENT})
    http = HttpClient()
    body = http.post(
        reverse("token"),
        data={"grant_type": GRANT, "key": engine_key, "manifest": json.dumps({"identifier": AGENT, "version": "1.0.0", "device_id": "engine"})},
        secure=True,
    ).json()
    engine = models.Client.objects.get(client_id=body["client_id"])
    parent = _agent_mandate(w)

    key = mandates.issue_mandate(
        issuer=mandates.AgentIssuer(parent=parent, client=engine),
        grantor=parent.membership,
        hub=parent.hub,
        pin=parent.pin.narrowed_to_device("pod-1"),
        claimant=mandates.KeyClaimant(),
    ).key

    assert _login(http, key, device_id="pod-1").status_code == 200


@pytest.mark.django_db
def test_a_child_cannot_widen_the_pin_outlive_the_parent_or_name_an_agent():
    w = _world()
    from django.utils import timezone
    from datetime import timedelta

    parent = _agent_mandate(w, expires_at=timezone.now() + timedelta(days=3))
    issuer = mandates.AgentIssuer(parent=parent, client=w.agent_client)

    with pytest.raises(mandates.MandateExceedsIssuer):
        mandates.issue_mandate(issuer=issuer, grantor=w.grantor, hub=w.hub, pin=MandatePin(**{**parent.pin.model_dump(), "version": None, "device_id": "d"}), claimant=mandates.KeyClaimant(), expires_at=parent.expires_at)
    with pytest.raises(mandates.MandateError, match="outlive"):
        mandates.issue_mandate(issuer=issuer, grantor=w.grantor, hub=w.hub, pin=parent.pin.narrowed_to_device("d"), claimant=mandates.KeyClaimant(), expires_at=parent.expires_at + timedelta(days=1))
    with pytest.raises(mandates.MandateError, match="keyed mandates only"):
        mandates.issue_mandate(issuer=issuer, grantor=w.grantor, hub=w.hub, pin=parent.pin.narrowed_to_device("d"), claimant=mandates.AgentClaimant(identifier="x"), expires_at=parent.expires_at)


@pytest.mark.django_db
def test_a_child_cannot_exceed_the_mandate_ceiling_at_login(client):
    w = _world()
    key = _issue_child(w, _agent_mandate(w))
    Scope.objects.get_or_create(identifier="admin:all", organization=w.org)

    resp = _login(client, key, scopes=["read", "admin:all"])
    assert resp.status_code == 400
    assert "admin:all" in resp.json()["error_description"]
    assert _login(client, key, device_id="host-a:pod-9").status_code == 400


@pytest.mark.django_db
def test_two_instances_on_one_host_do_not_rotate_each_other(client):
    w = _world()
    parent = _agent_mandate(w)
    a = _issue_child(w, parent, "host-a:pod-1")
    b = _issue_child(w, parent, "host-a:pod-2")

    assert _login(client, a, device_id="host-a:pod-1").status_code == 200
    assert _login(client, b, device_id="host-a:pod-2").status_code == 200
    assert models.Client.objects.filter(mandate__parent=parent).count() == 2


@pytest.mark.django_db(transaction=True)
def test_max_clients_counts_live_children_under_concurrency():
    w = _world()
    parent = _agent_mandate(w, max_clients=2)
    barrier = threading.Barrier(5)
    outcomes: list[str] = []

    def issue(i: int) -> None:
        try:
            barrier.wait()
            _issue_child(w, parent, f"pod-{i}")
            outcomes.append("ok")
        except mandates.MandateExhausted:
            outcomes.append("exhausted")
        finally:
            connection.close()

    threads = [threading.Thread(target=issue, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["exhausted"] * 3 + ["ok"] * 2
    # Revoking one child frees its slot.
    mandates.revoke_mandate(parent.children.first())
    _issue_child(w, parent, "pod-new")


# --- death -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_revoking_the_parent_revokes_every_child_and_deletes_their_clients(client):
    w = _world()
    parent = _agent_mandate(w)
    used = _issue_child(w, parent, "host-a:pod-1")
    pending = _issue_child(w, parent, "host-a:pod-2")
    body = _login(client, used).json()

    mandates.revoke_mandate(parent)

    assert not models.Client.objects.filter(client_id=body["client_id"]).exists()
    assert set(parent.children.values_list("revocation_reason", flat=True)) == {"parent_revoked"}
    assert _login(client, used).status_code == 400
    assert _login(client, pending, device_id="host-a:pod-2").status_code == 400
    with pytest.raises(mandates.MandateNotLive):
        _issue_child(w, parent, "host-a:pod-3")


@pytest.mark.django_db
def test_rejecting_the_issuing_app_revokes_what_it_issued(client):
    from fakts.services.mesh import reject_app_installation

    w = _world()
    parent = _agent_mandate(w)
    key = _issue_child(w, parent)
    assert _login(client, key).status_code == 200

    reject_app_installation(w.ui_client)

    parent.refresh_from_db()
    assert parent.revocation_reason == "issuer_revoked"
    assert _login(client, key).status_code == 400


@pytest.mark.django_db
def test_rejecting_the_agent_kills_every_app_it_deployed(client):
    from fakts.services.mesh import reject_app_installation

    w = _world()
    parent = _agent_mandate(w)
    key = _issue_child(w, parent)
    assert _login(client, key).status_code == 200

    reject_app_installation(w.agent_client)

    assert _login(client, key).status_code == 400
    parent.refresh_from_db()
    assert parent.revoked_at is None  # the approval itself stands; a new agent may deploy again


@pytest.mark.django_db
def test_logging_out_the_issuing_app_keeps_what_it_issued(client):
    w = _world()
    parent = _agent_mandate(w)
    key = _issue_child(w, parent)

    from authapp.models import OAuth2Token

    OAuth2Token.objects.filter(client_id=w.ui_client.client_id).update(revoked=True)  # what /o/revoke/ does

    assert _login(client, key).status_code == 200


@pytest.mark.django_db
def test_reapproving_the_issuing_app_keeps_what_it_issued(client):
    """bind_client replaces the issuer's row on re-approval; its mandates move to the new row."""
    w = _world()
    parent = _agent_mandate(w)
    key = _issue_child(w, parent)
    manifest = Manifest(**w.ui_client.manifest)
    old_pk = w.ui_client.pk

    fresh = create_public_client()
    bind_client(fresh, manifest, w.grantor, hub=w.hub)

    assert not models.Client.objects.filter(pk=old_pk).exists()
    parent.refresh_from_db()
    assert parent.issuing_client_id == fresh.pk
    assert _login(client, key).status_code == 200

    from fakts.services.mesh import reject_app_installation

    reject_app_installation(fresh)
    assert _login(client, key).status_code == 400


@pytest.mark.django_db
def test_deleting_the_hub_deletes_its_mandates(client):
    w = _world()
    key = _issue_child(w, _agent_mandate(w))

    w.hub.delete()

    assert not models.Mandate.objects.exists()
    assert _login(client, key).status_code == 400


# --- visibility --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_only_grantor_admin_or_issuing_app_may_revoke_and_agent_sees_its_mandates():
    w = await sync_to_async(_world)()
    mandate = await _create(w)

    # The agent sees the mandate that names it.
    seen = await _run(MANDATES, _ctx(w.operator, w.agent_client))
    assert [m["id"] for m in seen.data["mandates"]] == [mandate["id"]]

    # Another member's unrelated app may not revoke it.
    stranger = await sync_to_async(factories.make_membership)(organization=w.org)
    stranger_client = await sync_to_async(factories.make_client)(membership=stranger, hub=w.hub)
    denied = await _run(REVOKE, _ctx(stranger, stranger_client), input={"id": mandate["id"]})
    assert denied.errors

    revoked = await _run(REVOKE, _ctx(w.grantor, w.ui_client), input={"id": mandate["id"]})
    assert not revoked.errors, revoked.errors
    assert revoked.data["revokeMandate"]["isLive"] is False
    assert revoked.data["revokeMandate"]["revocationReason"] == "REVOKED"

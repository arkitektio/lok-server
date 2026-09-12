"""IonscaleHttpRepository against a scripted connect+JSON server.

No Django, no database: ``httpx.MockTransport`` plays ionscale, so these pin the
wire contract -- request path/headers/body shape, connect's lowerCamelCase and
stringified-uint64 responses, and the error-code mapping -- without a server.
"""

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from ionscale.base_models import DNSConfig, DNSRecord, TailnetCreate
from ionscale.errors import IonscaleError
from ionscale.http_repo import IonscaleHttpRepository

SERVICE = "/ionscale.v1.IonscaleService/"
TOKEN = "svc_" + "x" * 40


def _json(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _connect_error(code: str, message: str = "") -> httpx.Response:
    # connect maps codes to HTTP statuses; the body is what clients should read
    statuses = {"not_found": 404, "already_exists": 409, "permission_denied": 403, "unimplemented": 501}
    return _json(statuses.get(code, 500), {"code": code, "message": message})


class FakeIonscale:
    """Routes ``POST /ionscale.v1.IonscaleService/<Method>`` to handlers and
    records every request for assertions."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.handlers: Dict[str, Callable[[Dict[str, Any]], httpx.Response]] = {}

    def on(self, method: str, handler: Callable[[Dict[str, Any]], httpx.Response]) -> None:
        self.handlers[method] = handler

    def reply(self, method: str, payload: Any, status: int = 200) -> None:
        self.on(method, lambda _body: _json(status, payload))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "POST"
        assert request.url.path.startswith(SERVICE), request.url.path
        method = request.url.path[len(SERVICE):]
        handler = self.handlers.get(method)
        if handler is None:
            return _connect_error("unimplemented", f"no handler for {method}")
        return handler(json.loads(request.content or b"{}"))

    def bodies(self, method: Optional[str] = None) -> List[Dict[str, Any]]:
        return [
            json.loads(r.content or b"{}")
            for r in self.requests
            if method is None or r.url.path == SERVICE + method
        ]

    def calls(self) -> List[str]:
        return [r.url.path[len(SERVICE):] for r in self.requests]


@pytest.fixture
def server() -> FakeIonscale:
    return FakeIonscale()


@pytest.fixture
def repo(server) -> IonscaleHttpRepository:
    return IonscaleHttpRepository(
        "https://mesh.example.org/", TOKEN, transport=httpx.MockTransport(server)
    )


TAILNET_WIRE = {
    "id": "8543723985234",  # uint64 -> string on the wire
    "name": "acme",
    "organization": "17",
    "dnsConfig": {"magicDns": True, "magicDnsSuffix": "acme.mesh.example.org"},
}


def test_requires_a_token():
    with pytest.raises(ValueError):
        IonscaleHttpRepository("https://mesh", "")


def test_request_shape_path_headers_and_body(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})

    repo.list_tailnets()

    (request,) = server.requests
    assert request.url == "https://mesh.example.org/ionscale.v1.IonscaleService/ListTailnets"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {}


def test_parses_camel_case_and_string_ids(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})

    (tailnet,) = repo.list_tailnets()

    assert tailnet.id == "8543723985234"
    assert tailnet.name == "acme"
    assert tailnet.organization == "17"
    assert tailnet.dns_name == "acme.mesh.example.org"


def test_tailnet_keyed_calls_resolve_the_id_once(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply("GetIAMPolicy", {"policy": json.dumps({"subs": ["1", "2"]})})

    assert repo.get_policy("acme") == {"subs": ["1", "2"]}
    assert repo.get_policy("acme") == {"subs": ["1", "2"]}

    assert server.calls() == ["ListTailnets", "GetIAMPolicy", "GetIAMPolicy"]
    assert server.bodies("GetIAMPolicy") == [{"tailnet_id": "8543723985234"}] * 2


def test_empty_policy_is_an_empty_dict(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply("GetIAMPolicy", {})
    assert repo.get_policy("acme") == {}


def test_unknown_tailnet_name_is_not_found_without_a_call(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})

    with pytest.raises(IonscaleError) as excinfo:
        repo.get_policy("nope")

    assert excinfo.value.code == "not_found"
    assert server.calls() == ["ListTailnets"]


def test_stale_cached_id_is_refreshed_once(server, repo):
    """The tailnet was deleted and recreated under the same name: the first
    call fails with not_found, the id is re-resolved, the call is retried."""
    ids = iter(["1", "2"])
    server.on("ListTailnets", lambda _b: _json(200, {"tailnet": [{**TAILNET_WIRE, "id": next(ids)}]}))
    server.on(
        "SetIAMPolicy",
        lambda body: _json(200, {}) if body["tailnet_id"] == "2" else _connect_error("not_found", "tailnet not found"),
    )

    repo.list_tailnets()  # primes the cache with id 1
    assert repo.update_policy("acme", {"subs": []}) == "ok"

    assert server.calls() == ["ListTailnets", "SetIAMPolicy", "ListTailnets", "SetIAMPolicy"]
    assert server.bodies("SetIAMPolicy") == [
        {"tailnet_id": "1", "policy": '{"subs": []}'},
        {"tailnet_id": "2", "policy": '{"subs": []}'},
    ]


def test_fresh_id_not_found_is_not_retried(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.on("SetIAMPolicy", lambda _b: _connect_error("not_found", "gone"))

    with pytest.raises(IonscaleError) as excinfo:
        repo.update_policy("acme", {"subs": []})

    assert excinfo.value.code == "not_found"
    assert server.calls() == ["ListTailnets", "SetIAMPolicy"]


def test_connect_error_code_wins_over_http_status(server, repo):
    server.on("CreateTailnet", lambda _b: _connect_error("already_exists", "tailnet already exists with id 4"))

    with pytest.raises(IonscaleError) as excinfo:
        repo.create_tailnet(TailnetCreate(name="acme", organization="17"))

    err = excinfo.value
    assert err.code == "already_exists"
    assert err.message == "tailnet already exists with id 4"
    assert err.http_status == 409
    assert err.is_("already_exists", "not_found")
    assert server.bodies("CreateTailnet") == [{"name": "acme", "organization": "17"}]


def test_non_connect_error_maps_http_status(server, repo):
    server.on("ListTailnets", lambda _b: httpx.Response(503, text="<html>bad gateway</html>"))

    with pytest.raises(IonscaleError) as excinfo:
        repo.list_tailnets()

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.http_status == 503


def test_transport_failure_is_unavailable():
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    repo = IonscaleHttpRepository("https://mesh", TOKEN, transport=httpx.MockTransport(boom))

    with pytest.raises(IonscaleError) as excinfo:
        repo.list_tailnets()

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.http_status is None


def test_create_tailnet_caches_the_new_id(server, repo):
    server.reply("CreateTailnet", {"tailnet": TAILNET_WIRE})
    server.reply("SetIAMPolicy", {})

    created = repo.create_tailnet(TailnetCreate(name="acme", organization="17"))
    repo.update_policy("acme", {"subs": ["1"]})

    assert created.id == "8543723985234"
    assert server.calls() == ["CreateTailnet", "SetIAMPolicy"]  # no ListTailnets needed


def test_get_tailnet_by_organization(server, repo):
    server.reply("GetTailnetByOrganization", {"tailnet": TAILNET_WIRE})

    tailnet = repo.get_tailnet_by_organization(17)

    assert tailnet is not None and tailnet.name == "acme"
    assert server.bodies() == [{"organization": "17"}]


def test_get_tailnet_by_organization_missing_is_none(server, repo):
    server.on("GetTailnetByOrganization", lambda _b: _connect_error("not_found", "no tailnet"))
    assert repo.get_tailnet_by_organization("17") is None


def test_get_tailnet_by_organization_falls_back_to_list_filter(server, repo):
    """Older ionscale without the RPC: filter ListTailnets by organization."""
    server.on("GetTailnetByOrganization", lambda _b: _connect_error("unimplemented"))
    server.on(
        "ListTailnets",
        lambda body: _json(200, {"tailnet": [TAILNET_WIRE] if body.get("organization") == "17" else []}),
    )

    assert repo.get_tailnet_by_organization("17").name == "acme"
    assert repo.get_tailnet_by_organization("18") is None


def test_update_tailnet_is_partial_and_renames(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.on(
        "UpdateTailnet",
        lambda body: _json(200, {"tailnet": {**TAILNET_WIRE, "name": body.get("name", "acme")}}),
    )
    server.reply("GetIAMPolicy", {"policy": "{}"})

    updated = repo.update_tailnet("acme", name="acme-corp", ssh_enabled=True, machine_authorization_enabled=None)

    assert updated.name == "acme-corp"
    assert server.bodies("UpdateTailnet") == [
        {"tailnet_id": "8543723985234", "ssh_enabled": True, "name": "acme-corp"}
    ]
    # the cache follows the rename: the new name resolves without a lookup, the
    # old one is forgotten and has to be looked up again
    repo.get_policy("acme-corp")
    assert server.calls()[-2:] == ["UpdateTailnet", "GetIAMPolicy"]
    assert "acme" not in repo._tailnet_ids
    repo.get_policy("acme")
    assert server.calls()[-2:] == ["ListTailnets", "GetIAMPolicy"]


def test_delete_tailnet_forwards_force_and_forgets_the_id(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply("DeleteTailnet", {})

    repo.delete_tailnet("acme", force=True)

    assert server.bodies("DeleteTailnet") == [{"tailnet_id": "8543723985234", "force": True}]
    assert "acme" not in repo._tailnet_ids


def test_set_dns_config_sends_full_config_with_extra_records(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply("SetDNSConfig", {"config": {}})

    repo.set_dns_config(
        "acme",
        DNSConfig(
            magic_dns=True,
            https_certs=True,
            nameservers=["1.1.1.1"],
            extra_records=[DNSRecord(name="hub.acme.mesh.example.org", value="100.64.0.7")],
        ),
    )

    assert server.bodies("SetDNSConfig") == [
        {
            "tailnet_id": "8543723985234",
            "config": {
                "magic_dns": True,
                "https_certs": True,
                "override_local_dns": False,
                "nameservers": ["1.1.1.1"],
                "search_domains": [],
                "extra_records": [{"name": "hub.acme.mesh.example.org", "type": "", "value": "100.64.0.7"}],
            },
        }
    ]


def test_create_auth_key_returns_value_and_encodes_expiry(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply("CreateAuthKey", {"value": "tskey-abc", "authKey": {"id": "3"}})

    value = repo.create_auth_key("acme", ephemeral=True, pre_authorized=True, tags=["tag:hub"], expiry_seconds=600)

    assert value == "tskey-abc"
    assert server.bodies("CreateAuthKey") == [
        {"tailnet_id": "8543723985234", "ephemeral": True, "pre_authorized": True, "tags": ["tag:hub"], "expiry": "600s"}
    ]


def test_list_machines_and_get_machine(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    wire = {
        "id": "99",
        "name": "hub",
        "ipv4": "100.64.0.7",
        "ipv6": "fd7a::7",
        "ephemeral": False,
        "connected": True,
        "lastSeen": "2026-09-01T10:00:00Z",
        "tags": ["tag:hub"],
        "authorized": True,
        "tailnet": {"id": "8543723985234", "name": "acme"},
        "os": "linux",
        "keyExpiryDisabled": False,
        "expiresAt": "2027-02-28T10:00:00Z",
    }
    server.reply("ListMachines", {"machines": [wire]})
    server.reply("GetMachine", {"machine": wire})

    (machine,) = repo.list_machines("acme")
    detail = repo.get_machine(99)

    assert machine.id == "99" and machine.tailnet == "acme" and machine.connected
    assert machine.last_seen == datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    assert detail.os == "linux" and detail.key_expiry == datetime(2027, 2, 28, 10, 0, tzinfo=timezone.utc)
    assert server.bodies("GetMachine") == [{"machine_id": "99"}]


def test_list_users_carries_external_ids(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply(
        "ListUsers",
        {"users": [{"id": "5", "name": "jane", "role": "member", "externalId": "42"}, {"id": "6", "name": "svc"}]},
    )

    users = repo.list_users("acme")

    assert [(u.name, u.role, u.external_id) for u in users] == [("jane", "member", "42"), ("svc", "", None)]


def test_revoke_account_returns_affected_tailnets(server, repo):
    server.reply("RevokeAccount", {"tailnetIds": ["8543723985234"]})

    assert repo.revoke_account(42, organization=17) == ["8543723985234"]
    assert server.bodies() == [{"external_id": "42", "organization": "17"}]


def test_revoke_account_everywhere_omits_organization(server, repo):
    server.reply("RevokeAccount", {})
    assert repo.revoke_account("42") == []
    assert server.bodies() == [{"external_id": "42"}]


def test_revoke_account_treats_not_found_as_noop(server, repo):
    server.on("RevokeAccount", lambda _b: _connect_error("not_found", "account not found"))
    assert repo.revoke_account("42", "17") == []


def test_revoke_account_other_errors_propagate(server, repo):
    server.on("RevokeAccount", lambda _b: _connect_error("permission_denied", "system admin required"))
    with pytest.raises(IonscaleError) as excinfo:
        repo.revoke_account("42", "17")
    assert excinfo.value.code == "permission_denied"


def test_tailnet_lock_status(server, repo):
    server.reply("ListTailnets", {"tailnet": [TAILNET_WIRE]})
    server.reply(
        "GetTailnetLockStatus",
        {
            "capabilityEnabled": True,
            "authorityActive": True,
            "head": "abc",
            "nodes": [{"machineId": "99", "name": "hub", "signed": True}],
        },
    )
    server.reply("EnableTailnetLock", {})

    status = repo.get_tailnet_lock_status("acme")
    repo.enable_tailnet_lock("acme")

    assert status.capability_enabled and status.authority_active and not status.authority_disabled
    assert status.head == "abc"
    assert status.nodes[0].machine_id == "99" and status.nodes[0].signed
    assert server.bodies("EnableTailnetLock") == [{"tailnet_id": "8543723985234"}]

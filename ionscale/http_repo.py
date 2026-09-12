"""HTTP implementation of :class:`ionscale.repo.IonscaleRepo`.

ionscale exposes its gRPC service over connect (https://connectrpc.com): every
RPC is ``POST /ionscale.v1.IonscaleService/<Method>`` with a JSON body, so no
generated client or binary is needed. Authentication is a static service token
(``auth.service_tokens`` on the ionscale side) sent as a bearer.

Two connect/protobuf-JSON quirks are handled here so the rest of the app never
sees them: responses use lowerCamelCase keys (requests accept either), and
``uint64`` ids are serialized as *strings*.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

from .base_models import (
    DNSConfig,
    Machine,
    MachineDetail,
    NodeLockState,
    Tailnet,
    TailnetCreate,
    TailnetLockStatus,
    TailnetUser,
)
from .errors import IonscaleError

logger = logging.getLogger(__name__)

_SERVICE_PATH = "/ionscale.v1.IonscaleService/"


def _get(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Read a field by its camelCase *or* snake_case name (connect emits camel)."""
    for key in keys:
        if key in data:
            return data[key]
    return default


def _snake_to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _lenient(data: Dict[str, Any], snake: str, default: Any = None) -> Any:
    return _get(data, _snake_to_camel(snake), snake, default=default)


class IonscaleHttpRepository:
    """Talks to ionscale over connect+JSON with a static service token."""

    def __init__(
        self,
        server_url: str,
        service_token: str,
        *,
        timeout: float = 10.0,
        verify: bool = True,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not service_token:
            raise ValueError("an ionscale service token is required")
        self.server_url = server_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.server_url,
            timeout=timeout,
            verify=verify,
            transport=transport,
            headers={
                "Authorization": f"Bearer {service_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # tailnet name -> id. The protocol is keyed by name (that is what the
        # layers store), the RPCs by id. Evicted on not_found, rename, delete.
        self._tailnet_ids: Dict[str, str] = {}

    # ------------------------------------------------------------------ transport

    def _call(self, method: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = _SERVICE_PATH + method
        logger.debug("ionscale %s %s", method, body)
        try:
            response = self._client.post(url, content=json.dumps(body or {}))
        except httpx.HTTPError as exc:
            # connection refused, DNS, timeouts, TLS -- ionscale never answered
            raise IonscaleError("unavailable", f"{method}: {exc}") from exc

        if response.is_success:
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise IonscaleError("internal", f"{method}: malformed response body") from exc

        code, message = self._parse_error(response)
        raise IonscaleError(code, message, http_status=response.status_code)

    @staticmethod
    def _parse_error(response: httpx.Response) -> tuple[str, str]:
        """connect errors are ``{"code": "...", "message": "..."}``; fall back to
        the HTTP status when a proxy answered instead."""
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict) and payload.get("code"):
            return str(payload["code"]), str(payload.get("message") or "")
        by_status = {
            401: "unauthenticated",
            403: "permission_denied",
            404: "not_found",
            409: "already_exists",
            429: "resource_exhausted",
            502: "unavailable",
            503: "unavailable",
            504: "deadline_exceeded",
        }
        return by_status.get(response.status_code, "unknown"), response.text[:200]

    # ---------------------------------------------------------------- tailnets

    @staticmethod
    def _to_tailnet(data: Dict[str, Any]) -> Tailnet:
        dns = _lenient(data, "dns_config") or {}
        return Tailnet(
            id=str(data["id"]),
            name=data["name"],
            dns_name=_lenient(dns, "magic_dns_suffix"),
            organization=_lenient(data, "organization") or None,
        )

    def _tailnet_id(self, tailnet: str) -> str:
        cached = self._tailnet_ids.get(tailnet)
        if cached:
            return cached
        for t in self.list_tailnets():
            self._tailnet_ids[t.name] = t.id
        try:
            return self._tailnet_ids[tailnet]
        except KeyError:
            raise IonscaleError("not_found", f"tailnet {tailnet!r} not found") from None

    def _forget(self, tailnet: str) -> None:
        self._tailnet_ids.pop(tailnet, None)

    def _tailnet_call(self, method: str, tailnet: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call a per-tailnet RPC, retrying once with a fresh id lookup if the
        cached id turned out stale (tailnet deleted and recreated)."""
        payload = dict(body or {})
        was_cached = tailnet in self._tailnet_ids
        payload["tailnet_id"] = self._tailnet_id(tailnet)
        try:
            return self._call(method, payload)
        except IonscaleError as exc:
            if exc.code != "not_found" or not was_cached:
                raise
            self._forget(tailnet)
            payload["tailnet_id"] = self._tailnet_id(tailnet)
            return self._call(method, payload)

    def list_tailnets(self) -> List[Tailnet]:
        data = self._call("ListTailnets")
        tailnets = [self._to_tailnet(t) for t in _get(data, "tailnet", default=[]) or []]
        for t in tailnets:
            self._tailnet_ids[t.name] = t.id
        return tailnets

    def create_tailnet(self, tailnet_input: TailnetCreate) -> Tailnet:
        body: Dict[str, Any] = {"name": tailnet_input.name}
        if tailnet_input.organization:
            body["organization"] = tailnet_input.organization
        data = self._call("CreateTailnet", body)
        tailnet = self._to_tailnet(data["tailnet"])
        self._tailnet_ids[tailnet.name] = tailnet.id
        return tailnet

    def get_tailnet_by_organization(self, organization: str) -> Optional[Tailnet]:
        """The tailnet bound to an organization, or ``None``.

        Falls back to ``ListTailnets{organization}`` against an ionscale that
        predates ``GetTailnetByOrganization``.
        """
        try:
            data = self._call("GetTailnetByOrganization", {"organization": str(organization)})
        except IonscaleError as exc:
            if exc.code == "not_found":
                return None
            if exc.code != "unimplemented":
                raise
            data = self._call("ListTailnets", {"organization": str(organization)})
            found = _get(data, "tailnet", default=[]) or []
            if not found:
                return None
            tailnet = self._to_tailnet(found[0])
            self._tailnet_ids[tailnet.name] = tailnet.id
            return tailnet
        tailnet = self._to_tailnet(data["tailnet"])
        self._tailnet_ids[tailnet.name] = tailnet.id
        return tailnet

    def update_tailnet(self, tailnet: str, *, name: Optional[str] = None, **flags: bool) -> Tailnet:
        """Partial update. Only the given booleans (``ssh_enabled``,
        ``machine_authorization_enabled``, ...) are touched; ``name`` renames."""
        body: Dict[str, Any] = {k: bool(v) for k, v in flags.items() if v is not None}
        if name:
            body["name"] = name
        data = self._tailnet_call("UpdateTailnet", tailnet, body)
        updated = self._to_tailnet(data["tailnet"])
        if name and name != tailnet:
            self._forget(tailnet)
        self._tailnet_ids[updated.name] = updated.id
        return updated

    def delete_tailnet(self, tailnet: str, force: bool = False) -> None:
        try:
            self._tailnet_call("DeleteTailnet", tailnet, {"force": force})
        finally:
            self._forget(tailnet)

    # ---------------------------------------------------------------- machines

    @staticmethod
    def _to_machine(data: Dict[str, Any], detail: bool = False) -> Machine:
        tailnet = _lenient(data, "tailnet") or {}
        fields: Dict[str, Any] = dict(
            id=str(data["id"]),
            name=data.get("name", ""),
            tailnet=tailnet.get("name") if isinstance(tailnet, dict) else None,
            ipv4=data.get("ipv4") or None,
            ipv6=data.get("ipv6") or None,
            ephemeral=bool(data.get("ephemeral", False)),
            connected=bool(data.get("connected", False)),
            last_seen=_lenient(data, "last_seen"),
            tags=list(data.get("tags") or []),
            authorized=bool(data.get("authorized", False)),
        )
        if not detail:
            return Machine(**fields)
        return MachineDetail(
            **fields,
            os=data.get("os") or None,
            key_expiry=None if _lenient(data, "key_expiry_disabled") else _lenient(data, "expires_at"),
            is_external=None,
            fqdn=None,
        )

    def list_machines(self, tailnet: str) -> List[Machine]:
        data = self._tailnet_call("ListMachines", tailnet)
        return [self._to_machine(m) for m in data.get("machines") or []]

    def get_machine(self, machine_id: str) -> MachineDetail:
        data = self._call("GetMachine", {"machine_id": str(machine_id)})
        return self._to_machine(data["machine"], detail=True)  # type: ignore[return-value]

    # ------------------------------------------------------------------ policy

    def get_policy(self, tailnet: str) -> Dict[str, Any]:
        data = self._tailnet_call("GetIAMPolicy", tailnet)
        raw = data.get("policy") or ""
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IonscaleError("internal", f"Could not parse IAM policy: {exc}") from exc

    def update_policy(self, tailnet: str, policy: Union[Dict[str, Any], str, Path]) -> str:
        if isinstance(policy, dict):
            text = json.dumps(policy)
        elif isinstance(policy, Path) or (isinstance(policy, str) and Path(policy).is_file()):
            text = Path(policy).read_text()
        elif isinstance(policy, str):
            text = policy
        else:
            raise ValueError("policy must be a dict, JSON string, or file path")
        self._tailnet_call("SetIAMPolicy", tailnet, {"policy": text})
        return "ok"

    # --------------------------------------------------------------------- dns

    def set_dns_config(self, tailnet: str, config: DNSConfig) -> str:
        body = {
            "config": {
                "magic_dns": config.magic_dns,
                "https_certs": config.https_certs,
                "override_local_dns": config.override_local_dns,
                "nameservers": list(config.nameservers),
                "search_domains": list(config.search_domains),
                "extra_records": [
                    {"name": r.name, "type": r.type, "value": r.value} for r in config.extra_records
                ],
            }
        }
        self._tailnet_call("SetDNSConfig", tailnet, body)
        return "ok"

    # --------------------------------------------------------------- auth keys

    def create_auth_key(
        self,
        tailnet: str,
        ephemeral: bool = False,
        pre_authorized: bool = True,
        tags: List[str] = None,
        expiry_seconds: Optional[int] = None,
    ) -> str:
        body: Dict[str, Any] = {
            "ephemeral": ephemeral,
            "pre_authorized": pre_authorized,
            "tags": list(tags or []),
        }
        if expiry_seconds:
            # google.protobuf.Duration is "<seconds>s" in JSON
            body["expiry"] = f"{int(expiry_seconds)}s"
        data = self._tailnet_call("CreateAuthKey", tailnet, body)
        value = data.get("value")
        if not value:
            raise IonscaleError("internal", "CreateAuthKey returned no key")
        return value

    # ------------------------------------------------------------ tailnet lock

    def get_tailnet_lock_status(self, tailnet: str) -> TailnetLockStatus:
        data = self._tailnet_call("GetTailnetLockStatus", tailnet)
        return TailnetLockStatus(
            capability_enabled=bool(_lenient(data, "capability_enabled", False)),
            authority_active=bool(_lenient(data, "authority_active", False)),
            authority_disabled=bool(_lenient(data, "authority_disabled", False)),
            head=data.get("head") or "",
            nodes=[
                NodeLockState(
                    machine_id=str(_lenient(n, "machine_id", "")),
                    name=n.get("name") or "",
                    signed=bool(n.get("signed", False)),
                )
                for n in (data.get("nodes") or [])
            ],
        )

    def enable_tailnet_lock(self, tailnet: str) -> None:
        self._tailnet_call("EnableTailnetLock", tailnet)

    def disable_tailnet_lock(self, tailnet: str) -> None:
        self._tailnet_call("DisableTailnetLock", tailnet)

    # ------------------------------------------------------------------- users

    def list_users(self, tailnet: str) -> List[TailnetUser]:
        data = self._tailnet_call("ListUsers", tailnet)
        return [
            TailnetUser(
                id=str(u["id"]),
                name=u.get("name", ""),
                role=u.get("role", ""),
                external_id=_lenient(u, "external_id") or None,
            )
            for u in data.get("users") or []
        ]

    def revoke_account(self, external_id: str, organization: Optional[str] = None) -> List[str]:
        """Cut an identity's access: deletes its users, machines and keys in the
        organization's tailnets (or everywhere when ``organization`` is empty)
        and pushes the change to peers. Idempotent on the server; an ionscale
        that still answers ``not_found`` for unknown accounts is treated the
        same way. Returns the affected tailnet ids."""
        body: Dict[str, Any] = {"external_id": str(external_id)}
        if organization:
            body["organization"] = str(organization)
        try:
            data = self._call("RevokeAccount", body)
        except IonscaleError as exc:
            if exc.code == "not_found":
                return []
            raise
        return [str(t) for t in _lenient(data, "tailnet_ids", []) or []]

    def close(self) -> None:
        self._client.close()

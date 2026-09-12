"""In-memory ionscale repository for tests.

``FakeIonscaleRepository`` satisfies the :class:`ionscale.repo.IonscaleRepo`
protocol without touching ionscale over the network. It records the
calls made against it so tests can assert on them, and lets tests pre-seed the
data returned by read methods.

Install it either globally (via the ``IONSCALE_REPOSITORY`` setting, which the
test settings already point here) or per-test with
``ionscale.repo.set_ionscale_repo(FakeIonscaleRepository())``.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .base_models import DNSConfig, Machine, MachineDetail, Tailnet, TailnetCreate, TailnetLockStatus, TailnetUser
from .errors import IonscaleError


class FakeIonscaleRepository:
    """Records calls and returns canned data; no subprocess, no binary."""

    def __init__(self) -> None:
        # Recorded calls — assert against these in tests.
        self.created_tailnets: List[TailnetCreate] = []
        self.updated_policies: List[tuple[str, Union[Dict[str, Any], str, Path]]] = []
        self.created_auth_keys: List[Dict[str, Any]] = []
        self.dns_configs: List[tuple[str, DNSConfig]] = []
        self.policies: Dict[str, Dict[str, Any]] = {}
        # Canned read data — seed these in tests as needed.
        self.machines_by_tailnet: Dict[str, List[Machine]] = {}
        self.machines: Dict[str, MachineDetail] = {}
        self.tailnets: List[Tailnet] = []
        self.auth_key: str = "tskey-fake-0000000000"
        # Tailnet lock. Default is the state a freshly provisioned tailnet is
        # actually in: no capability, no authority.
        self.lock_status: Dict[str, TailnetLockStatus] = {}
        self.enabled_tailnet_locks: List[str] = []
        self.disabled_tailnet_locks: List[str] = []
        # Lifecycle / revocation, recorded for assertions.
        self.revoked_accounts: List[tuple[str, Optional[str]]] = []
        self.deleted_tailnets: List[tuple[str, bool]] = []
        self.updated_tailnets: List[tuple[str, Dict[str, Any]]] = []
        self.users_by_tailnet: Dict[str, List[TailnetUser]] = {}
        # Failure injection: ``fail_with[method] = IonscaleError(...)`` (or any
        # exception, or a callable returning one) makes that method raise.
        self.fail_with: Dict[str, Union[BaseException, Callable[[], BaseException]]] = {}

    def _maybe_fail(self, method: str) -> None:
        failure = self.fail_with.get(method)
        if failure is None:
            return
        raise failure() if callable(failure) else failure

    def list_tailnets(self) -> List[Tailnet]:
        self._maybe_fail("list_tailnets")
        return list(self.tailnets)

    def get_tailnet_by_organization(self, organization: str) -> Optional[Tailnet]:
        self._maybe_fail("get_tailnet_by_organization")
        for t in self.tailnets:
            if t.organization == str(organization):
                return t
        return None

    def update_tailnet(self, tailnet: str, *, name: Optional[str] = None, **flags: bool) -> Tailnet:
        self._maybe_fail("update_tailnet")
        current = next((t for t in self.tailnets if t.name == tailnet), None)
        if current is None:
            raise IonscaleError("not_found", f"tailnet {tailnet!r} not found")
        if name and name != tailnet and any(t.name == name for t in self.tailnets):
            raise IonscaleError("already_exists", f"tailnet {name!r} already exists")
        changes: Dict[str, Any] = {k: v for k, v in flags.items() if v is not None}
        if name:
            changes["name"] = name
        self.updated_tailnets.append((tailnet, changes))
        if name:
            current.name = name
            current.dns_name = name
        return current

    def delete_tailnet(self, tailnet: str, force: bool = False) -> None:
        self._maybe_fail("delete_tailnet")
        self.deleted_tailnets.append((tailnet, force))
        self.tailnets = [t for t in self.tailnets if t.name != tailnet]
        self.policies.pop(tailnet, None)
        self.users_by_tailnet.pop(tailnet, None)

    def list_users(self, tailnet: str) -> List[TailnetUser]:
        self._maybe_fail("list_users")
        return list(self.users_by_tailnet.get(tailnet, []))

    def revoke_account(self, external_id: str, organization: Optional[str] = None) -> List[str]:
        self._maybe_fail("revoke_account")
        self.revoked_accounts.append((str(external_id), str(organization) if organization else None))
        affected: List[str] = []
        for tailnet_name, users in self.users_by_tailnet.items():
            tailnet = next((t for t in self.tailnets if t.name == tailnet_name), None)
            if organization and (tailnet is None or tailnet.organization != str(organization)):
                continue
            remaining = [u for u in users if u.external_id != str(external_id)]
            if len(remaining) != len(users):
                self.users_by_tailnet[tailnet_name] = remaining
                affected.append(tailnet.id if tailnet else tailnet_name)
        return affected

    def list_machines(self, tailnet: str) -> List[Machine]:
        return list(self.machines_by_tailnet.get(tailnet, []))

    def get_machine(self, machine_id: str) -> MachineDetail:
        return self.machines[str(machine_id)]

    def create_tailnet(self, tailnet_input: TailnetCreate) -> Tailnet:
        self._maybe_fail("create_tailnet")
        if any(t.name == tailnet_input.name for t in self.tailnets):
            raise IonscaleError("already_exists", f"tailnet {tailnet_input.name!r} already exists")
        if tailnet_input.organization and any(t.organization == tailnet_input.organization for t in self.tailnets):
            raise IonscaleError("already_exists", f"organization {tailnet_input.organization!r} already has a tailnet")
        self.created_tailnets.append(tailnet_input)
        tailnet = Tailnet(
            id=str(len(self.created_tailnets)),
            name=tailnet_input.name,
            dns_name=tailnet_input.name,
            organization=tailnet_input.organization,
        )
        # created_tailnets keeps the input, so tests can assert the organization binding.
        self.tailnets.append(tailnet)
        return tailnet

    def update_policy(self, tailnet: str, policy: Union[Dict[str, Any], str, Path]) -> str:
        self._maybe_fail("update_policy")
        self.updated_policies.append((tailnet, policy))
        if isinstance(policy, dict):
            self.policies[tailnet] = policy
        return "ok"

    def get_policy(self, tailnet: str) -> Dict[str, Any]:
        self._maybe_fail("get_policy")
        return dict(self.policies.get(tailnet, {}))

    def set_dns_config(self, tailnet: str, config: DNSConfig) -> str:
        self._maybe_fail("set_dns_config")
        self.dns_configs.append((tailnet, config))
        return "ok"

    def create_auth_key(self, tailnet: str, ephemeral: bool = False, pre_authorized: bool = True, tags: List[str] = None) -> str:
        self.created_auth_keys.append(
            {"tailnet": tailnet, "ephemeral": ephemeral, "pre_authorized": pre_authorized, "tags": tags or []}
        )
        return self.auth_key

    def get_tailnet_lock_status(self, tailnet: str) -> TailnetLockStatus:
        return self.lock_status.get(tailnet, TailnetLockStatus())

    def enable_tailnet_lock(self, tailnet: str) -> None:
        self.enabled_tailnet_locks.append(tailnet)
        status = self.lock_status.setdefault(tailnet, TailnetLockStatus())
        # Only the capability flips: the key authority is established by a
        # client running `tailscale lock init`, which the control plane -- and
        # therefore this fake -- cannot do.
        status.capability_enabled = True

    def disable_tailnet_lock(self, tailnet: str) -> None:
        status = self.lock_status.setdefault(tailnet, TailnetLockStatus())
        if status.authority_active:
            raise RuntimeError("the tailnet's key authority is active")
        self.disabled_tailnet_locks.append(tailnet)
        status.capability_enabled = False

import subprocess
import os
import shutil
import re
import json
import logging
from typing import List, Dict, Any, Union, Optional, Protocol, runtime_checkable
from pathlib import Path
from .base_models import Tailnet, TailnetCreate, Machine, MachineDetail, DNSConfig, TailnetLockStatus, NodeLockState, TailnetUser
from .errors import IonscaleError
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@runtime_checkable
class IonscaleRepo(Protocol):
    """The behaviour the rest of the app depends on.

    :class:`ionscale.http_repo.IonscaleHttpRepository` (connect+JSON, the
    default), :class:`IonscaleRepository` (legacy CLI-backed) and the in-memory
    ``FakeIonscaleRepository`` used in tests satisfy this protocol, so consumers
    can depend on the interface instead of a concrete class. Tailnets are
    addressed by *name* (what ``IonscaleLayer.tailnet_name`` stores); failures
    surface as :class:`ionscale.errors.IonscaleError`.
    """

    def list_tailnets(self) -> List[Tailnet]: ...
    def list_machines(self, tailnet: str) -> List[Machine]: ...
    def get_machine(self, machine_id: str) -> MachineDetail: ...
    def create_tailnet(self, tailnet_input: TailnetCreate) -> Tailnet: ...
    def get_tailnet_by_organization(self, organization: str) -> Optional[Tailnet]: ...
    def update_tailnet(self, tailnet: str, *, name: Optional[str] = ..., **flags: bool) -> Tailnet: ...
    def delete_tailnet(self, tailnet: str, force: bool = ...) -> None: ...
    def get_policy(self, tailnet: str) -> Dict[str, Any]: ...
    def update_policy(self, tailnet: str, policy: Union[Dict[str, Any], str, Path]) -> str: ...
    def set_dns_config(self, tailnet: str, config: DNSConfig) -> str: ...
    def create_auth_key(self, tailnet: str, ephemeral: bool = ..., pre_authorized: bool = ..., tags: List[str] = ...) -> str: ...
    def get_tailnet_lock_status(self, tailnet: str) -> TailnetLockStatus: ...
    def enable_tailnet_lock(self, tailnet: str) -> None: ...
    def disable_tailnet_lock(self, tailnet: str) -> None: ...
    def list_users(self, tailnet: str) -> List[TailnetUser]: ...
    def revoke_account(self, external_id: str, organization: Optional[str] = ...) -> List[str]: ...


class IonscaleRepository:
    """Legacy CLI-backed repository: shells out to the ``ionscale`` binary and
    parses its table output. Prefer :class:`ionscale.http_repo.IonscaleHttpRepository`
    (configured via ``ionscale.service_token``); this stays for deployments that
    still hand lok a system admin key and a binary."""

    def __init__(self, server_url: str, admin_key: str, binary_path: str = "ionscale"):
        """
        Initializes the repository.
        :param server_url: The full URL of your Ionscale instance (e.g. https://vpn.corp.com)
        :param admin_key: The System Admin Key
        :param binary_path: Path to ionscale binary (default: lookup in PATH)
        """
        self.server_url = server_url
        self.admin_key = admin_key

        # Verify binary exists
        self.binary = shutil.which(binary_path)
        if not self.binary:
            raise FileNotFoundError(f"Ionscale binary not found at: {binary_path}")

    # Values that reach argv must not be able to masquerade as flags. The
    # subprocess runs with IONSCALE_SYSTEM_ADMIN_KEY in its environment, so even
    # a bounded argument-injection is worth closing. (This is not shell
    # injection — args are a list and shell=False — but a value like "--foo"
    # still lands in argv as a flag-shaped token.)
    _SAFE_ARG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]*$")

    @classmethod
    def _check_arg(cls, value: str, what: str) -> str:
        text = str(value)
        if not cls._SAFE_ARG.match(text):
            raise ValueError(f"Invalid {what}: {value!r}")
        return text

    def _run_command(self, args: List[str], command_type: str = "tailnet") -> str:
        """
        Executes the ionscale CLI command securely.
        :param args: Command arguments
        :param command_type: The command type (e.g. 'tailnet', 'iam')
        """
        # We inject the key as an env var to avoid it showing up in process lists,
        # assuming the CLI can read it or we pass it via stdin.
        # Ionscale CLI requires the flag, so we pass it in the args but execute carefully.

        base_cmd = [self.binary,  *args]
        logger.debug("Running ionscale CLI: %s", " ".join(base_cmd))  # safe: the key travels in env, not args

        try:
            result = subprocess.run(
                base_cmd,
                capture_output=True,
                text=True,
                check=True,
                # We can inject extra env vars if needed
                env={
                    "IONSCALE_SYSTEM_ADMIN_KEY": self.admin_key,
                    "IONSCALE_ADDR": self.server_url,
                },
            )
            logger.debug("ionscale CLI output: %s", result.stdout)
            return result.stdout.strip()

        except subprocess.CalledProcessError as e:
            # Clean the error message to avoid leaking keys if they appear in stderr
            clean_error = e.stderr.replace(self.admin_key, "***")
            raise IonscaleError(_cli_error_code(clean_error), f"Ionscale CLI Error: {clean_error}")

    def list_tailnets(self) -> List[Tailnet]:
        """
        Runs `ionscale tailnet list` and parses the output.
        """
        output = self._run_command(["tailnet", "list"])
        return self._parse_list_output(output)

    def list_machines(self, tailnet: str) -> List[Machine]:
        """
        Runs `ionscale machines list --tailnet <tailnet>` and parses the output.
        """
        output = self._run_command(["machines", "list", "--tailnet", tailnet])
        return self._parse_machine_list_output(output)

    def get_machine(self, machine_id: str) -> MachineDetail:
        """
        Runs `ionscale machines get --machine-id <machine_id>` and parses the output.
        """
        output = self._run_command(
            ["machines", "get", "--machine-id", self._check_arg(machine_id, "machine id")]
        )
        return self._parse_machine_detail_output(output)

    def create_tailnet(self, tailnet_input: TailnetCreate) -> Tailnet:
        """
        Runs `ionscale tailnet create` and returns the created object.
        """
        # Ionscale create usually returns "Tailnet created: {id}" or similar
        args = ["tailnet", "create", "--name", tailnet_input.name]
        if tailnet_input.organization:
            # Create-time only: ionscale has no RPC that rebinds an existing
            # tailnet, so getting this wrong means a manual DB fix.
            args += ["--org", self._check_arg(tailnet_input.organization, "organization")]
        self._run_command(args)

        # Since create command output might be sparse, we fetch the specific tailnet
        # to return a full object. This is a "read-your-writes" pattern.
        # Alternatively, you can just return a basic object.

        # Optimization: Just return the known data if you want speed
        return Tailnet(
            id="unknown",  # ID is generated server side, would need lookup
            name=tailnet_input.name,
            dns_name=f"{tailnet_input.name}.{self.server_url.split('://')[1]}",
        )

    def set_dns_config(self, tailnet: str, config: DNSConfig) -> str:
        """
        Runs `ionscale tailnets set-dns` to (re)set the tailnet's DNS config.

        set-dns replaces the entire config on each call (presence-based flags), so
        the passed `config` must describe the full desired state — any options not
        represented here are cleared on the server.
        """
        args = ["tailnets", "set-dns", "--tailnet", tailnet]

        if config.magic_dns:
            args.append("--magic-dns")
        if config.https_certs:
            args.append("--https-certs")
        if config.override_local_dns:
            args.append("--override-local-dns")
        for ns in config.nameservers:
            args.extend(["--nameserver", ns])
        for domain in config.search_domains:
            args.extend(["--search-domain", domain])

        return self._run_command(args)

    def update_policy(self, tailnet: str, policy: Union[Dict[str, Any], str, Path]) -> str:
        """
        Updates the policy for a tailnet.

        :param tailnet: The name of the tailnet
        :param policy: Policy data as a dict, JSON string, or path to a JSON file
        :return: CLI output message

        Example:
            # Using a dictionary
            repo.update_policy("my-tailnet", {"acls": [...]})

            # Using a file path
            repo.update_policy("my-tailnet", "/path/to/policy.json")

            # Using a JSON string
            repo.update_policy("my-tailnet", '{"acls": [...]}')
        """
        import tempfile

        # Determine if we need to create a temporary file
        if isinstance(policy, dict):
            # Convert dict to JSON and write to temp file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(policy, f, indent=2)
                temp_file = f.name

            try:
                output = self._run_command(["tailnets", "set-iam-policy", "--tailnet", tailnet, "--file", temp_file], command_type="iam")
                
                return output
            finally:
                # Clean up temp file
                os.unlink(temp_file)

        elif isinstance(policy, (str, Path)):
            # Check if it's a file path
            policy_path = Path(policy)
            if policy_path.exists():
                # It's a file path
                output = self._run_command(["tailnets", "set-iam-policy", "--tailnet", tailnet, "--file", str(policy_path)], command_type="iam")
                return output
            else:
                # Assume it's a JSON string, write to temp file
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                    f.write(policy)
                    temp_file = f.name

                try:
                    output = self._run_command(["update-policy", "--tailnet", tailnet, "--file", temp_file], command_type="iam")
                    return output
                finally:
                    # Clean up temp file
                    os.unlink(temp_file)
        else:
            raise ValueError("policy must be a dict, JSON string, or file path")

    def _parse_list_output(self, cli_output: str) -> List[Tailnet]:
        """
        Parses the ASCII table output from Ionscale into Pydantic models.

        Expected CLI Output format (example):
        ID    NAME        DNS_NAME
        1     marketing   marketing.vpn.com
        2     dev         dev.vpn.com
        """
        lines = cli_output.splitlines()
        tailnets = []

        # Skip header line (ID, NAME, etc)
        if not lines:
            return []

        # Flexible parsing: Split by whitespace
        # You might need to adjust this based on exact CLI version output
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 2:
                # Basic mapping based on typical column order
                # Adjust index based on your actual CLI output columns
                t_id = parts[0]
                name = parts[1]

                # Check if dns_name exists in columns
                dns_name = parts[2] if len(parts) > 2 else None

                tailnets.append(Tailnet(id=t_id, name=name, dns_name=dns_name))

        return tailnets

    def _parse_machine_list_output(self, cli_output: str) -> List[Machine]:
        """
        Parses the aligned ASCII table from `ionscale machines list` into Machine models.

        The real column layout is (verified against ionscale 1.9x)::

            ID  TAILNET  NAME  IPv4  IPv6  AUTHORIZED  EPHEMERAL  VERSION  LAST_SEEN  TAGS

        i.e. NAME is the *third* column, not the second — a naive positional parse mis-assigns
        the tailnet to `name` and the name to `ipv4`. We therefore map columns by their header
        label and split on runs of 2+ spaces (LAST_SEEN is a human phrase like "a minute ago"
        and TAGS may be empty, so single-space splitting is unreliable).
        """
        lines = [ln for ln in cli_output.splitlines() if ln.strip()]
        if not lines:
            return []

        # Build header-label -> column-index from the first row.
        header_cols = re.split(r"\s{2,}", lines[0].strip())
        idx = {col.strip().lower(): i for i, col in enumerate(header_cols)}

        def _col(parts: List[str], label: str) -> Optional[str]:
            i = idx.get(label)
            if i is None or i >= len(parts):
                return None
            value = parts[i].strip()
            return value or None

        machines: List[Machine] = []
        for line in lines[1:]:
            # NOTE: this assumes only *trailing* columns (TAGS) can be empty. If a middle
            # column is ever blank (e.g. a machine with no IPv6 yet), the 2+-space split
            # shifts later fields left. If that surfaces, switch to header char-offset slicing.
            parts = re.split(r"\s{2,}", line.strip())
            m_id = _col(parts, "id")
            if not m_id:
                continue

            # `machines list` has no explicit online column; approximate "connected" from
            # LAST_SEEN recency (ionscale prints "a minute ago" / "now" for live nodes).
            last_seen = (_col(parts, "last_seen") or "").lower()
            connected = any(token in last_seen for token in ("now", "second", "minute"))

            tags_raw = _col(parts, "tags") or ""
            tags = [t for t in re.split(r"[,\s]+", tags_raw) if t]

            authorized_raw = _col(parts, "authorized")
            authorized = authorized_raw.lower() == "true" if authorized_raw is not None else None

            machines.append(Machine(
                id=m_id,
                name=_col(parts, "name") or "",
                tailnet=_col(parts, "tailnet"),
                ipv4=_col(parts, "ipv4"),
                ipv6=_col(parts, "ipv6"),
                ephemeral=(_col(parts, "ephemeral") or "false").lower() == "true",
                connected=connected,
                tags=tags,
                authorized=authorized,
            ))

        return machines

    def _parse_machine_detail_output(self, cli_output: str) -> MachineDetail:
        """
        Parses the output from `ionscale machines get` into a MachineDetail model.
        Supports both "Key: Value" and column-aligned formats.
        """
        lines = cli_output.splitlines()
        data = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Try splitting by 2 or more spaces first (column format)
            parts = re.split(r'\s{2,}', line)
            if len(parts) >= 2:
                key = parts[0].strip().lower().replace(" ", "_")
                value = parts[1].strip()
                data[key] = value
                continue
                
            # Fallback to colon separation
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip().lower().replace(" ", "_")] = value.strip()

        return MachineDetail(
            id=data.get("id", ""),
            name=data.get("machine_name", data.get("name", "")),
            tailnet=data.get("tailnet"),
            ipv4=data.get("tailscale_ipv4", data.get("ipv4")),
            ipv6=data.get("tailscale_ipv6", data.get("ipv6")),
            connected=False, # Not present in detail view usually
            ephemeral=data.get("ephemeral", "false").lower() == "true",
            last_seen=None, # "a few seconds ago" is not easily parsable to datetime without logic
            os=data.get("os"),
            key_expiry=None, # "in 6 months" — not yet parsed to a datetime
            authorized=None, # Not sourced from the CLI yet -> unknown, not a hard False
            is_external=None, # Not sourced from the CLI yet -> unknown, not a hard False
            # The MagicDNS name, if the CLI exposes it. Preferred over deriving it from
            # name+suffix on the GraphQL layer. Key name varies, so probe a few likely labels.
            fqdn=data.get("fqdn", data.get("dns_name", data.get("magic_dns_name"))),
        )
    
    def create_auth_key(self, tailnet: str, ephemeral: bool = False, pre_authorized: bool = True, tags: List[str] = None) -> str:
        """
        Runs `ionscale auth-keys create` and returns the key.
        """
        args = ["auth-keys", "create", "--tailnet", tailnet]
        
        if ephemeral:
            args.append("--ephemeral")
        
        if pre_authorized:
            args.append("--pre-authorized")
            
        if tags:
            for tag in tags:
                args.extend(["--tag", self._check_arg(tag, "tag")])

        output = self._run_command(args, command_type="auth-keys")
        
        match = re.search(r"Generated new auth key.*?again\.\s+(\S+)", output, re.DOTALL)
        if match:
            return match.group(1).strip()
        else:
            raise RuntimeError("Failed to parse auth key from output")
        

    def get_policy(self, tailnet: str) -> Dict[str, Any]:
        """Runs `ionscale tailnets get-iam-policy` and returns the parsed policy.

        The CLI already emits plain JSON here, no flag needed. Used to preserve
        the parts of the policy lok does not own (see manager.sync).
        """
        output = self._run_command(
            ["tailnets", "get-iam-policy", "--tailnet", self._check_arg(tailnet, "tailnet")]
        )
        if not output.strip():
            return {}
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not parse IAM policy: {exc}")

    def get_tailnet_lock_status(self, tailnet: str) -> TailnetLockStatus:
        """Runs `ionscale tailnets tailnet-lock-status --json`.

        Uses the CLI's JSON output rather than its table: this status drives a
        UI, and the table parsers elsewhere in this file are brittle enough
        without adding another.
        """
        output = self._run_command(
            ["tailnets", "tailnet-lock-status", "--tailnet", self._check_arg(tailnet, "tailnet"), "--json"]
        )
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not parse tailnet lock status: {exc}")

        return TailnetLockStatus(
            capability_enabled=bool(data.get("capability_enabled", False)),
            authority_active=bool(data.get("authority_active", False)),
            authority_disabled=bool(data.get("authority_disabled", False)),
            head=data.get("head") or "",
            nodes=[
                NodeLockState(
                    machine_id=str(n.get("machine_id", "")),
                    name=n.get("name") or "",
                    signed=bool(n.get("signed", False)),
                )
                for n in (data.get("nodes") or [])
            ],
        )

    def enable_tailnet_lock(self, tailnet: str) -> None:
        """Grants the tailnet-lock capability. Does NOT create a key authority --
        only `tailscale lock init` on a client can do that."""
        self._run_command(
            ["tailnets", "enable-tailnet-lock", "--tailnet", self._check_arg(tailnet, "tailnet")]
        )

    def disable_tailnet_lock(self, tailnet: str) -> None:
        """Revokes the capability. ionscale refuses this while a key authority is
        active, surfacing as a RuntimeError from the CLI."""
        self._run_command(
            ["tailnets", "disable-tailnet-lock", "--tailnet", self._check_arg(tailnet, "tailnet")]
        )

    def get_tailnet_by_organization(self, organization: str) -> Optional[Tailnet]:
        # `tailnets list` has no organization column; the HTTP repository is
        # needed for a reliable lookup.
        raise IonscaleError("unimplemented", "get_tailnet_by_organization requires ionscale.service_token")

    def update_tailnet(self, tailnet: str, *, name: Optional[str] = None, **flags: bool) -> Tailnet:
        raise IonscaleError("unimplemented", "update_tailnet requires ionscale.service_token")

    def delete_tailnet(self, tailnet: str, force: bool = False) -> None:
        args = ["tailnets", "delete", "--tailnet", self._check_arg(tailnet, "tailnet")]
        if force:
            args.append("--force")
        self._run_command(args)

    def list_users(self, tailnet: str) -> List[TailnetUser]:
        raise IonscaleError("unimplemented", "list_users requires ionscale.service_token")

    def revoke_account(self, external_id: str, organization: Optional[str] = None) -> List[str]:
        args = ["users", "revoke-account", "--external-id", self._check_arg(external_id, "external id")]
        if organization:
            args += ["--org", self._check_arg(organization, "organization")]
        try:
            self._run_command(args)
        except IonscaleError as exc:
            if exc.code == "not_found":
                return []
            raise
        return []


def _cli_error_code(stderr: str) -> str:
    """Best-effort mapping of connect error text as printed by the CLI."""
    text = stderr.lower()
    for code in ("not_found", "already_exists", "permission_denied", "unauthenticated",
                 "invalid_argument", "failed_precondition", "unavailable", "unimplemented"):
        if code in text:
            return code
    return "unknown"


def _build_default_repo() -> IonscaleRepo:
    """Construct the repository configured for the current environment.

    Resolution order:

    1. ``IONSCALE_REPOSITORY`` -- dotted path of a zero-argument factory (or
       class) returning an :class:`IonscaleRepo`, e.g.
       ``"ionscale.testing.FakeIonscaleRepository"`` in tests.
    2. ``IONSCALE_SERVICE_TOKEN`` -- the connect+JSON
       :class:`~ionscale.http_repo.IonscaleHttpRepository` (no binary needed).
    3. ``IONSCALE_ADMIN_KEY`` -- the legacy CLI-backed :class:`IonscaleRepository`.
    """
    dotted = getattr(settings, "IONSCALE_REPOSITORY", None)
    if dotted:
        return import_string(dotted)()
    if getattr(settings, "IONSCALE_SERVICE_TOKEN", None):
        from .http_repo import IonscaleHttpRepository

        return IonscaleHttpRepository(
            server_url=settings.IONSCALE_SERVER_URL,
            service_token=settings.IONSCALE_SERVICE_TOKEN,
            timeout=getattr(settings, "IONSCALE_TIMEOUT", 10.0),
            verify=getattr(settings, "IONSCALE_VERIFY_TLS", True),
        )
    if not getattr(settings, "IONSCALE_ADMIN_KEY", None):
        raise ValueError("ionscale is configured without a service_token or admin_key")
    return IonscaleRepository(
        server_url=settings.IONSCALE_SERVER_URL,
        admin_key=settings.IONSCALE_ADMIN_KEY,
    )


_repo: Optional[IonscaleRepo] = None


def get_ionscale_repo() -> IonscaleRepo:
    """Return the active ionscale repository, building it lazily on first use.

    Lazy construction means importing this module never requires live config —
    it is only needed when an ionscale operation actually runs. Call this at use-time rather than importing a module-level
    instance, so a repository swapped in via :func:`set_ionscale_repo` is seen by
    every consumer.
    """
    global _repo
    if _repo is None:
        _repo = _build_default_repo()
    return _repo


def set_ionscale_repo(repo: Optional[IonscaleRepo]) -> None:
    """Install an explicit repository (e.g. a fake in tests). Pass ``None`` to clear."""
    global _repo
    _repo = repo


def reset_ionscale_repo() -> None:
    """Drop the cached repository so the next access rebuilds it from settings."""
    global _repo
    _repo = None

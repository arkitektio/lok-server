from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class TailnetCreate(BaseModel):
    name: str = Field(..., description="The unique name of the tailnet")
    organization: Optional[str] = Field(
        default=None,
        description=(
            "Organization this tailnet is bound to. Must be the organization *pk* as a string, "
            "matching the `org` claim lok issues -- not the slug, which is mutable. ionscale "
            "only offers a bound tailnet to identities carrying the same organization, and the "
            "binding cannot be changed after creation."
        ),
    )


class DNSRecord(BaseModel):
    """A static MagicDNS record (``ionscale.v1.DNSRecord``): gives a service a
    stable name inside the tailnet, e.g. ``lok.<suffix>`` -> a machine's IP."""

    name: str
    type: str = ""  # "", "A" or "AAAA"; inferred from the address when empty
    value: str


class DNSConfig(BaseModel):
    """The per-tailnet DNS configuration pushed via ``SetDNSConfig``.

    Mirrors ionscale's ``ionscale.v1.DNSConfig``. ``SetDNSConfig`` *replaces* the
    whole config on every call, so callers must send the full desired state — lok
    is the source of truth. The MagicDNS suffix is server-configured
    (``dns.magic_dns_suffix``) and cannot be set per tailnet.
    """

    magic_dns: bool = False
    https_certs: bool = False
    override_local_dns: bool = False
    nameservers: List[str] = Field(default_factory=list)
    search_domains: List[str] = Field(default_factory=list)
    extra_records: List[DNSRecord] = Field(default_factory=list)


class Tailnet(BaseModel):
    id: str
    name: str
    dns_name: Optional[str] = None
    created_at: Optional[datetime] = None
    # The organization pk the tailnet is bound to (None for unbound tailnets).
    organization: Optional[str] = None

    # Use ConfigDict for Pydantic v2, or class Config for v1
    model_config = {"from_attributes": True}


class TailnetList(BaseModel):
    tailnets: List[Tailnet]


class Machine(BaseModel):
    id: str
    name: str
    tailnet: Optional[str] = None
    ipv4: Optional[str] = None
    ipv6: Optional[str] = None
    ephemeral: bool = False
    connected: bool = False
    last_seen: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    # `ionscale machines list` reports AUTHORIZED, so it's reliably available. None = unknown.
    authorized: Optional[bool] = None

    model_config = {"from_attributes": True}

class MachineDetail(Machine):
    os: Optional[str] = None
    key_expiry: Optional[datetime] = None
    # None means "not reported by the CLI" (unknown) — distinct from a known False. Not sourced
    # from the CLI yet, so it stays None rather than a misleading False.
    is_external: Optional[bool] = None
    fqdn: Optional[str] = None  # The machine's MagicDNS name, if present in the CLI output.

    # Add other fields as needed based on `ionscale machines get` output


class MachineList(BaseModel):
    machines: List[Machine]


class TailnetUser(BaseModel):
    """A user as known to ionscale (``ionscale.v1.User``)."""

    id: str
    name: str
    role: str = ""
    # The OIDC subject the user logged in with -- lok's user pk. None for users
    # an older ionscale does not report it for.
    external_id: Optional[str] = None


class NodeLockState(BaseModel):
    """One machine's standing under tailnet lock."""

    machine_id: str
    name: str
    # False means locked peers cannot reach this node until an existing signing
    # node signs its key -- it is registered but invisible to them.
    signed: bool


class TailnetLockStatus(BaseModel):
    """The two independent halves of tailnet lock.

    ``capability_enabled`` is granted by the control plane. ``authority_active``
    only becomes true once someone runs ``tailscale lock init`` on a node -- the
    control plane cannot create the key authority itself. A tailnet can sit with
    the capability granted and no authority indefinitely, which is exactly the
    state the kontrol UI has to explain.
    """

    capability_enabled: bool = False
    authority_active: bool = False
    # An authority that existed and was shut down with a disablement secret is
    # not the same as one that never existed.
    authority_disabled: bool = False
    head: str = ""
    nodes: List[NodeLockState] = []

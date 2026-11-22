"""The tailnet ACL (traffic) policy — owned by lok and rebuilt from the database.

Distinct from the IAM policy (``manager.sync``), which only decides who may log
in. ionscale's ACL engine only knows *accept* entries, and two nodes only see
each other when some entry links them, so the policy is an allow-list:

- members' own devices and standalone mesh machines (``tag:mesh-<org>``) reach
  each other, as they did under ionscale's default allow-all policy. Only full
  members can log devices in (``manager.sync``); a guest of some collaborations
  is on the tailnet only through their apps' sidecars;
- an app sidecar (``tag:app-<n>``) reaches the sidecar of every hub one of its
  live clients is bound to (``tag:hub-<m>``) — one way: the hub answers, it
  never initiates;
- a guest's app sidecar reaches only those of its hubs that run an instance of
  one of the guest's live grants allowing the mesh
  (``karakter.collaboration_grants.mesh_hub_ids``). A guest whose grants lapse
  or are taken back keeps their node but no rule: the next rewrite (a grant
  change, ``reconcile_meshes --sidecars``) cuts it off;
- nothing else reaches a sidecar;
- a peer relay of the organization (``tag:relay-<org>``, a machine an owner or
  admin let join as one) may be asked by every node above to forward UDP for it
  and a peer it finds no direct path to. The two capability grants say so in
  both directions, as tailscale's clients read them: who may ask the relay
  (``tailscale.com/cap/relay``), and whom the relay may be used towards
  (``tailscale.com/cap/relay-target``). They open no port: a relay is reached
  on its own UDP port from the internet, forwards what it cannot read
  (WireGuard stays end to end), and reaches nothing on the mesh itself. A
  grant is also what lets the nodes see the relay at all.

Every write replaces the whole policy (ionscale skips it when unchanged), so any
hand-edited ACL is overwritten.
"""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db import connection, transaction

if TYPE_CHECKING:
    from karakter.models import Organization

logger = logging.getLogger(__name__)

APP_TAG_PREFIX = "tag:app-"
HUB_TAG_PREFIX = "tag:hub-"
RELAY_TAG_PREFIX = "tag:relay-"
RESERVED_TAG_PREFIXES = (APP_TAG_PREFIX, HUB_TAG_PREFIX, RELAY_TAG_PREFIX)
"""Only lok mints these (``fakts.services.hubs``): a hand-minted key carrying one
would join the mesh as that app's or hub's sidecar, or as a relay every node
of the organization sends its traffic through."""

CAP_RELAY = "tailscale.com/cap/relay"
CAP_RELAY_TARGET = "tailscale.com/cap/relay-target"


def mesh_tag(organization_id: int) -> str:
    return f"tag:mesh-{organization_id}"


def relay_tag(organization_id: int) -> str:
    """The tag of the organization's peer relays (see the module docs)."""
    return f"{RELAY_TAG_PREFIX}{organization_id}"


def build_acl_policy(organization: "Organization | int") -> dict:
    """The ACL policy of ``organization``'s mesh, from the current database state."""
    from fakts import models
    from fakts.services.mesh import live_clients
    from karakter.collaboration_grants import mesh_hub_ids
    from karakter.models import Membership

    org_id = organization.pk if hasattr(organization, "pk") else organization
    peers = ["autogroup:member", mesh_tag(org_id)]
    acls = [{"action": "accept", "src": peers, "dst": [f"{p}:*" for p in peers]}]

    # hub pk -> app sidecar tags allowed to reach it
    reach: dict[int, set[str]] = {}
    enrollments = models.AppMeshEnrollment.objects.filter(
        organization_id=org_id, auth_key__isnull=False
    )
    for enrollment in enrollments:
        hub_ids = (
            live_clients(
                models.Client.objects.filter(
                    membership_id=enrollment.membership_id,
                    release__app_id=enrollment.app_id,
                    node_id=enrollment.device_id,
                    hub__isnull=False,
                )
            )
            .values_list("hub_id", flat=True)
            .distinct()
        )
        # A guest (a membership with grants, live or not) reaches no further than
        # their mesh grants; one left with none reaches nothing.
        guest = Membership.objects.filter(
            pk=enrollment.membership_id, collaboration_grants__isnull=False
        ).first()
        if guest is not None:
            hub_ids = set(hub_ids) & mesh_hub_ids(guest)
        for hub_id in hub_ids:
            reach.setdefault(hub_id, set()).add(enrollment.tag)

    for hub_id in sorted(reach):
        acls.append(
            {
                "action": "accept",
                "src": sorted(reach[hub_id]),
                "dst": [f"{HUB_TAG_PREFIX}{hub_id}:*"],
            }
        )

    # Everyone the rules above put on the mesh may use the organization's relays.
    sidecars = {tag for tags in reach.values() for tag in tags} | {
        f"{HUB_TAG_PREFIX}{hub_id}" for hub_id in reach
    }
    users = peers + sorted(sidecars)
    relay = relay_tag(org_id)
    grants = [
        {"src": users, "dst": [relay], "app": {CAP_RELAY: []}},
        {"src": [relay], "dst": users, "app": {CAP_RELAY_TARGET: []}},
    ]

    return {
        "acls": acls,
        "grants": grants,
        # ionscale's default SSH rule: members may SSH into their own devices.
        "ssh": [
            {
                "action": "check",
                "src": ["autogroup:member"],
                "dst": ["autogroup:self"],
                "users": ["autogroup:nonroot", "root"],
            }
        ],
    }


def apply_acl_policy(organization_id: int) -> bool:
    """Write the organization's ACL policy to its mesh. Never raises."""
    from .manager import get_org_mesh, ionscale_configured
    from .repo import get_ionscale_repo

    if not ionscale_configured():
        return False
    layer = get_org_mesh(organization_id)
    if layer is None:
        return False
    try:
        get_ionscale_repo().set_acl_policy(
            layer.tailnet_name, build_acl_policy(organization_id)
        )
    except Exception:
        logger.exception(
            "Could not apply the ACL policy to tailnet %s; `manage.py reconcile_meshes --sidecars` repairs it",
            layer.tailnet_name,
        )
        return False
    return True


def schedule_acl_apply(organization_id: int | None) -> None:
    """After commit, rewrite the organization's ACL policy (once per transaction)."""
    from .manager import ionscale_configured

    if organization_id is None or not ionscale_configured():
        return
    # One apply per organization per transaction, however many rows changed.
    for _, func, *_rest in connection.run_on_commit:
        if (
            getattr(func, "_acl_organization", None) == organization_id
            and not func._acl_done
        ):
            return

    def _run() -> None:
        _run._acl_done = True
        apply_acl_policy(organization_id)

    _run._acl_done = False

    _run._acl_organization = organization_id
    transaction.on_commit(_run)


def reserved_tags(tags: Iterable[str] | None) -> list[str]:
    return [t for t in tags or [] if t.startswith(RESERVED_TAG_PREFIXES)]

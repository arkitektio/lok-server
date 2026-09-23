"""The tailnet ACL (traffic) policy — owned by lok and rebuilt from the database.

Distinct from the IAM policy (``manager.sync``), which only decides who may log
in. ionscale's ACL engine only knows *accept* entries, and two nodes only see
each other when some entry links them, so the policy is an allow-list:

- members' own devices and standalone mesh machines (``tag:mesh-<org>``) reach
  each other, as they did under ionscale's default allow-all policy;
- an app sidecar (``tag:app-<n>``) reaches the sidecar of every hub one of its
  live clients is bound to (``tag:hub-<m>``) — one way: the hub answers, it
  never initiates;
- nothing else reaches a sidecar.

Every write replaces the whole policy (ionscale skips it when unchanged), so any
hand-edited ACL is overwritten.
"""

import logging

from django.db import connection, transaction

logger = logging.getLogger(__name__)

APP_TAG_PREFIX = "tag:app-"
HUB_TAG_PREFIX = "tag:hub-"
RESERVED_TAG_PREFIXES = (APP_TAG_PREFIX, HUB_TAG_PREFIX)
"""Only lok mints these (``fakts.services.hubs``): a hand-minted key carrying one
would join the mesh as that app's or hub's sidecar."""


def mesh_tag(organization_id) -> str:
    return f"tag:mesh-{organization_id}"


def build_acl_policy(organization) -> dict:
    """The ACL policy of ``organization``'s mesh, from the current database state."""
    from fakts import models
    from fakts.services.mesh import live_clients

    org_id = organization.pk if hasattr(organization, "pk") else organization
    peers = ["autogroup:member", mesh_tag(org_id)]
    acls = [{"action": "accept", "src": peers, "dst": [f"{p}:*" for p in peers]}]

    # hub pk -> app sidecar tags allowed to reach it
    reach: dict[int, set[str]] = {}
    enrollments = models.AppMeshEnrollment.objects.filter(organization_id=org_id, auth_key__isnull=False)
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
        for hub_id in hub_ids:
            reach.setdefault(hub_id, set()).add(enrollment.tag)

    for hub_id in sorted(reach):
        acls.append({"action": "accept", "src": sorted(reach[hub_id]), "dst": [f"{HUB_TAG_PREFIX}{hub_id}:*"]})

    return {
        "acls": acls,
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


def apply_acl_policy(organization_id) -> bool:
    """Write the organization's ACL policy to its mesh. Never raises."""
    from .manager import get_org_mesh, ionscale_configured
    from .repo import get_ionscale_repo

    if not ionscale_configured():
        return False
    layer = get_org_mesh(organization_id)
    if layer is None:
        return False
    try:
        get_ionscale_repo().set_acl_policy(layer.tailnet_name, build_acl_policy(organization_id))
    except Exception:
        logger.exception(
            "Could not apply the ACL policy to tailnet %s; `manage.py reconcile_meshes --sidecars` repairs it",
            layer.tailnet_name,
        )
        return False
    return True


def schedule_acl_apply(organization_id) -> None:
    """After commit, rewrite the organization's ACL policy (once per transaction)."""
    from .manager import ionscale_configured

    if organization_id is None or not ionscale_configured():
        return
    # One apply per organization per transaction, however many rows changed.
    for _, func, *_rest in connection.run_on_commit:
        if getattr(func, "_acl_organization", None) == organization_id and not func._acl_done:
            return

    def _run() -> None:
        _run._acl_done = True
        apply_acl_policy(organization_id)

    _run._acl_done = False

    _run._acl_organization = organization_id
    transaction.on_commit(_run)


def reserved_tags(tags) -> list[str]:
    return [t for t in tags or [] if t.startswith(RESERVED_TAG_PREFIXES)]

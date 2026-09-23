"""Repair drift between lok's meshes and ionscale's tailnets.

The signal-driven sync in :mod:`ionscale.sync` is best effort; anything it
missed (ionscale down, a crash mid-way, an organization renamed) is fixed here.
Run through ``manage.py reconcile_meshes``.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, TextIO

from django.db import IntegrityError, transaction

from fakts.models import IonscaleLayer
from karakter.models import Membership

from . import base_models
from .errors import IonscaleError
from .manager import apply_dns_config, sync
from .repo import get_ionscale_repo

logger = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    layer: IonscaleLayer
    created_tailnet: bool = False
    renamed_from: Optional[str] = None
    synced: bool = False
    dns_applied: bool = False
    revoked: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created_tailnet or self.renamed_from or self.revoked)


def desired_tailnet_name(organization) -> str:
    """The tailnet is named after the organization (see ``ensure_org_mesh``)."""
    return str(organization.slug or organization.pk)


def reconcile_layer(
    layer: IonscaleLayer,
    *,
    dry_run: bool = False,
    revoke_orphans: bool = False,
    out: Optional[TextIO] = None,
) -> ReconcileReport:
    """Bring one mesh in line with ionscale.

    - the organization's tailnet exists (created when missing);
    - it is named after the organization's current slug (renamed on drift, and
      the layer's ``tailnet_name``/``identifier`` follow);
    - its IAM ``subs`` and DNS config match lok;
    - with ``revoke_orphans``, ionscale users whose subject is no longer a
      member are revoked.

    Raises on unexpected control-plane errors so the caller can count failures.
    """
    repo = get_ionscale_repo()
    org = layer.organization
    report = ReconcileReport(layer=layer)

    def say(msg: str) -> None:
        if out is not None:
            out.write(msg + "\n")

    wanted = desired_tailnet_name(org)
    tailnet = repo.get_tailnet_by_organization(str(org.pk))

    if tailnet is None:
        report.created_tailnet = True
        say(f"[{org.slug}] tailnet missing on ionscale; creating {wanted!r}")
        if not dry_run:
            try:
                tailnet = repo.create_tailnet(
                    base_models.TailnetCreate(name=wanted, organization=str(org.pk))
                )
            except IonscaleError as exc:
                if exc.code != "already_exists":
                    raise
                # Raced with another creator, or the name is taken by an
                # unbound tailnet: report, do not steal it.
                report.warnings.append(f"could not create tailnet {wanted!r}: {exc.message}")
                say(f"[{org.slug}] WARNING {report.warnings[-1]}")
                tailnet = repo.get_tailnet_by_organization(str(org.pk))
                if tailnet is None:
                    return report

    if tailnet is not None and tailnet.name != wanted:
        report.renamed_from = tailnet.name
        say(f"[{org.slug}] tailnet {tailnet.name!r} -> {wanted!r}")
        if not dry_run:
            try:
                tailnet = repo.update_tailnet(tailnet.name, name=wanted)
            except IonscaleError as exc:
                if exc.code != "already_exists":
                    raise
                report.warnings.append(f"cannot rename tailnet to {wanted!r}: name taken on ionscale")
                say(f"[{org.slug}] WARNING {report.warnings[-1]}")
                wanted = tailnet.name

    if tailnet is not None and (layer.tailnet_name != tailnet.name or layer.identifier != tailnet.name):
        say(f"[{org.slug}] layer tailnet_name {layer.tailnet_name!r} -> {tailnet.name!r}")
        if not dry_run:
            try:
                with transaction.atomic():
                    layer.tailnet_name = tailnet.name
                    layer.identifier = tailnet.name
                    layer.save(update_fields=["tailnet_name", "identifier"])
            except IntegrityError:
                report.warnings.append(
                    f"another layer already uses tailnet_name {tailnet.name!r}; left unchanged"
                )
                say(f"[{org.slug}] WARNING {report.warnings[-1]}")

    if dry_run or tailnet is None:
        return report

    sync(layer)
    report.synced = True
    apply_dns_config(layer, raise_on_error=False)
    report.dns_applied = True

    if revoke_orphans:
        member_pks = {
            str(pk) for pk in Membership.objects.filter(organization=org).values_list("user_id", flat=True)
        }
        for user in repo.list_users(layer.tailnet_name):
            if user.external_id is None:
                report.warnings.append(
                    f"ionscale does not report external ids for {layer.tailnet_name!r}; cannot detect orphans"
                )
                say(f"[{org.slug}] WARNING {report.warnings[-1]}")
                break
            if user.external_id in member_pks:
                continue
            say(f"[{org.slug}] revoking orphan {user.name!r} (sub {user.external_id})")
            repo.revoke_account(user.external_id, str(org.pk))
            report.revoked.append(user.external_id)

    return report


def orphaned_tailnets(layers) -> List[base_models.Tailnet]:
    """Tailnets bound to an organization that has no mesh layer (or no longer
    exists). Reported only; deleting them is a manual decision."""
    known = {str(layer.organization_id) for layer in layers}
    return [
        t
        for t in get_ionscale_repo().list_tailnets()
        if t.organization and t.organization not in known
    ]


@dataclass
class SidecarReport:
    layer: IonscaleLayer
    reaped_enrollments: List[str] = field(default_factory=list)
    reaped_hubs: List[str] = field(default_factory=list)
    deleted_machines: List[str] = field(default_factory=list)
    acl_applied: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.reaped_enrollments or self.reaped_hubs or self.deleted_machines)


def reconcile_sidecars(
    layer: IonscaleLayer,
    *,
    dry_run: bool = False,
    migrate_tags: bool = False,
    out: Optional[TextIO] = None,
) -> SidecarReport:
    """Bring an organization's app and hub sidecars in line with their clients.

    - reaps enrollments no live client backs any more, and the mesh keys of hubs
      whose identity has no live session (catches refresh chains that simply
      expired, and anything a signal missed);
    - deletes nodes tagged ``tag:app-<n>`` / ``tag:hub-<m>`` with no enrollment,
      or no hub with a live identity, behind them;
    - with ``migrate_tags``, deletes sidecar nodes that still carry
      ``tag:mesh-<org>`` from before sidecars were isolated (ionscale cannot
      re-tag a node; they re-enroll on their next grant);
    - re-applies the ACL policy.
    """
    from fakts.models import AppMeshEnrollment, Hub
    from fakts.services.mesh import enrollment_is_live, hub_identity_is_live, reap_enrollment, reap_hub_sidecar

    from .acl import APP_TAG_PREFIX, HUB_TAG_PREFIX, apply_acl_policy, mesh_tag

    org = layer.organization
    report = SidecarReport(layer=layer)

    def say(message: str) -> None:
        logger.info(message)
        if out is not None:
            out.write(message + "\n")

    for enrollment in AppMeshEnrollment.objects.filter(organization=org):
        if enrollment_is_live(enrollment.membership_id, enrollment.app_id, enrollment.device_id):
            continue
        say(f"[{org.slug}] reaping {enrollment.tag}: no live client")
        report.reaped_enrollments.append(enrollment.tag)
        if not dry_run:
            reap_enrollment(enrollment.membership_id, enrollment.app_id, enrollment.device_id)

    live_hubs = set()
    for hub in Hub.objects.filter(organization=org):
        if hub_identity_is_live(hub):
            live_hubs.add(hub.mesh_tag)
        elif hub.auth_key_id:
            say(f"[{org.slug}] reaping {hub.mesh_tag}: the hub identity has no live session")
            report.reaped_hubs.append(hub.mesh_tag)
            if not dry_run:
                reap_hub_sidecar(hub)

    live_apps = {
        e.tag for e in AppMeshEnrollment.objects.filter(organization=org) if e.tag not in report.reaped_enrollments
    }
    repo = get_ionscale_repo()
    for machine in repo.list_machines(layer.tailnet_name):
        sidecar_tags = [t for t in machine.tags if t.startswith((APP_TAG_PREFIX, HUB_TAG_PREFIX))]
        if not sidecar_tags:
            continue
        orphan = not any(t in live_apps or t in live_hubs for t in sidecar_tags)
        stale_tag = migrate_tags and mesh_tag(org.pk) in machine.tags
        if not (orphan or stale_tag):
            continue
        reason = "orphaned sidecar" if orphan else "sidecar still tagged " + mesh_tag(org.pk)
        say(f"[{org.slug}] deleting node {machine.name} ({machine.id}): {reason}")
        report.deleted_machines.append(machine.id)
        if not dry_run:
            repo.delete_machine(machine.id)

    if not dry_run:
        report.acl_applied = apply_acl_policy(org.pk)
    return report

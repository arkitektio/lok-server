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

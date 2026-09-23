from django.core.management.base import BaseCommand, CommandError

from fakts.models import IonscaleLayer
from ionscale.manager import ionscale_configured
from ionscale.reconcile import orphaned_tailnets, reconcile_layer, reconcile_sidecars
from karakter.models import Organization


class Command(BaseCommand):
    help = (
        "Repair drift between lok's meshes and ionscale: create missing tailnets, "
        "follow organization renames, re-push members and DNS, and optionally revoke "
        "ionscale users that are no longer members."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would change without touching anything.")
        parser.add_argument("--organization", help="Only this organization (slug or pk).")
        parser.add_argument(
            "--revoke-orphans",
            action="store_true",
            help="Revoke ionscale users whose subject is no longer a member of the organization.",
        )
        parser.add_argument(
            "--sidecars",
            action="store_true",
            help="Also reap app/hub sidecars no live client backs, delete orphaned sidecar nodes and re-apply the ACL.",
        )
        parser.add_argument(
            "--sidecars-only",
            action="store_true",
            help="Only the sidecar pass (for the periodic job): skip tailnet, member and DNS reconciliation.",
        )
        parser.add_argument(
            "--migrate-tags",
            action="store_true",
            help="With the sidecar pass: delete sidecar nodes still tagged tag:mesh-<org> (one-off migration).",
        )

    def handle(self, *args, **options):
        if not ionscale_configured():
            raise CommandError("ionscale is not configured on this deployment")

        layers = IonscaleLayer.objects.select_related("organization").order_by("organization_id")
        if options["organization"]:
            selector = options["organization"]
            org = Organization.objects.filter(slug=selector).first()
            if org is None and selector.isdigit():
                org = Organization.objects.filter(pk=int(selector)).first()
            if org is None:
                raise CommandError(f"organization {selector!r} not found")
            layers = layers.filter(organization=org)

        failed = 0
        changed = 0
        sidecars = options["sidecars"] or options["sidecars_only"]
        for layer in layers:
            try:
                reports = []
                if not options["sidecars_only"]:
                    reports.append(
                        reconcile_layer(
                            layer,
                            dry_run=options["dry_run"],
                            revoke_orphans=options["revoke_orphans"],
                            out=self.stdout,
                        )
                    )
                if sidecars:
                    layer.refresh_from_db()
                    reports.append(
                        reconcile_sidecars(
                            layer,
                            dry_run=options["dry_run"],
                            migrate_tags=options["migrate_tags"],
                            out=self.stdout,
                        )
                    )
            except Exception as exc:  # one broken mesh must not stop the others
                failed += 1
                self.stderr.write(self.style.ERROR(f"[{layer.organization.slug}] failed: {exc}"))
                continue
            if any(r.changed for r in reports):
                changed += 1
            any_changed = any(r.changed for r in reports)
            status = "would change" if options["dry_run"] and any_changed else ("changed" if any_changed else "ok")
            style = self.style.WARNING if any(getattr(r, "warnings", None) for r in reports) else self.style.SUCCESS
            self.stdout.write(style(f"[{layer.organization.slug}] {status}"))

        if not options["organization"] and not options["sidecars_only"]:
            try:
                orphans = orphaned_tailnets(layers)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"could not list tailnets: {exc}"))
                orphans = []
            for tailnet in orphans:
                self.stdout.write(
                    self.style.WARNING(
                        f"orphan tailnet {tailnet.name!r} (id {tailnet.id}) is bound to organization "
                        f"{tailnet.organization} which has no mesh; delete it manually if unwanted"
                    )
                )

        self.stdout.write(f"{layers.count()} mesh(es), {changed} changed, {failed} failed")
        if failed:
            raise CommandError(f"{failed} mesh(es) could not be reconciled")

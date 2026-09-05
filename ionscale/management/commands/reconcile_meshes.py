from django.core.management.base import BaseCommand, CommandError

from fakts.models import IonscaleLayer
from ionscale.manager import ionscale_configured
from ionscale.reconcile import orphaned_tailnets, reconcile_layer
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
        for layer in layers:
            try:
                report = reconcile_layer(
                    layer,
                    dry_run=options["dry_run"],
                    revoke_orphans=options["revoke_orphans"],
                    out=self.stdout,
                )
            except Exception as exc:  # one broken mesh must not stop the others
                failed += 1
                self.stderr.write(self.style.ERROR(f"[{layer.organization.slug}] failed: {exc}"))
                continue
            if report.changed:
                changed += 1
            status = "would change" if options["dry_run"] and report.changed else ("changed" if report.changed else "ok")
            style = self.style.WARNING if report.warnings else self.style.SUCCESS
            self.stdout.write(style(f"[{layer.organization.slug}] {status}"))

        if not options["organization"]:
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

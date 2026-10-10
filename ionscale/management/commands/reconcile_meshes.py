from django.core.management.base import BaseCommand, CommandError

from fakts.models import IonscaleLayer
from django.conf import settings

from ionscale.manager import ensure_org_mesh, ionscale_configured
from ionscale.reconcile import orphaned_tailnets, reconcile_layer, reconcile_sidecars
from karakter.models import Organization


class Command(BaseCommand):
    help = (
        "Repair drift between lok's meshes and ionscale: give organizations without a "
        "mesh one (unless ionscale.auto_create_mesh is off), create missing tailnets, "
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
        organizations = Organization.objects.order_by("pk")
        if options["organization"]:
            selector = options["organization"]
            org = Organization.objects.filter(slug=selector).first()
            if org is None and selector.isdigit():
                org = Organization.objects.filter(pk=int(selector)).first()
            if org is None:
                raise CommandError(f"organization {selector!r} not found")
            layers = layers.filter(organization=org)
            organizations = organizations.filter(pk=org.pk)

        failed = 0
        changed = 0

        # Every organization has a mesh by default; one created while ionscale was
        # down, or before that was the rule, gets it here.
        if not options["sidecars_only"] and getattr(settings, "IONSCALE_AUTO_CREATE_MESH", False):
            for org in organizations.exclude(pk__in=IonscaleLayer.objects.values("organization_id")):
                if options["dry_run"]:
                    self.stdout.write(self.style.WARNING(f"[{org.slug}] has no mesh; would provision one"))
                    changed += 1
                elif ensure_org_mesh(org) is not None:
                    self.stdout.write(self.style.SUCCESS(f"[{org.slug}] mesh provisioned"))
                    changed += 1
                else:
                    failed += 1
                    self.stderr.write(self.style.ERROR(f"[{org.slug}] could not provision a mesh (see the log)"))
            # The loop below works on the meshes that exist now.
            layers = layers.all()
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

from collections import Counter

from django.core.management.base import BaseCommand

from fakts.services.alias_probe import probe_aliases, probeable_aliases


class Command(BaseCommand):
    help = (
        "Probe every public alias's signed challenge from lok (for hubs that never report their "
        "own health, e.g. hosted kommunity partners). Only a signature that verifies against the "
        "instance's challenge key counts as verified; the outcome is stored on each alias."
    )

    def add_arguments(self, parser):
        parser.add_argument("--hub", help="Only this hub (pk).")

    def handle(self, *args, **options):
        aliases = probeable_aliases()
        if options.get("hub"):
            aliases = aliases.filter(instance__hub_id=options["hub"])
        results = probe_aliases(list(aliases))
        counts = Counter(result.status for result in results)
        summary = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "nothing to probe"
        self.stdout.write(f"Probed {len(results)} alias(es) — {summary}")

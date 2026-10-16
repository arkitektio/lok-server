"""Keep the mandate keys the config names (``mandate_keys``) in place.

Running it again changes nothing: an entry whose key is unchanged is updated in
place, and only where it differs. A changed key supersedes the entry's mandate; an
entry gone from the config has its mandate revoked; a revoked key is never revived.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from fakts import enums, models
from fakts.base_models import MandatePin
from fakts.services import mandates
from karakter import models as karakter_models
from lok_server.configuration import MandateKeySettings


def _same_pin(current: MandatePin, wanted: MandatePin) -> bool:
    # An open pin is fixed by the first app that logs in; that is not a change.
    if wanted.identifier is None:
        current = current.model_copy(update={"identifier": None})
    return current == wanted


class Command(BaseCommand):
    help = "Keep the mandate keys of settings.MANDATE_KEYS in place, and revoke the ones the config dropped."

    def handle(self, *args, **kwargs):
        specs: list[MandateKeySettings] = settings.MANDATE_KEYS

        for spec in specs:
            with transaction.atomic():
                self._ensure(spec)

        dropped = models.Mandate.objects.filter(origin=enums.MandateOriginChoices.CONFIG, revoked_at__isnull=True).exclude(
            config_name__in=[spec.name for spec in specs]
        )
        for mandate in dropped:
            mandates.revoke_mandate(mandate, enums.MandateRevocationChoices.REMOVED_FROM_CONFIG)
            self.stdout.write(f"Mandate key '{mandate.config_name}' revoked: no longer in the config")

    def _ensure(self, spec: MandateKeySettings) -> None:
        key = spec.key.get_secret_value()
        membership = karakter_models.Membership.objects.select_related("organization", "user").get(
            user__username=spec.user, organization__slug=spec.organization
        )
        hub = models.Hub.objects.get(organization=membership.organization, identifier=spec.hub)
        pin = MandatePin(identifier=spec.app, scopes=sorted(set(spec.scopes)) if spec.scopes is not None else None)
        key_hash = mandates.hash_key(key)

        current = models.Mandate.objects.select_for_update().filter(config_name=spec.name, revoked_at__isnull=True).first()
        if current is not None and current.key_hash == key_hash:
            changed: list[str] = []
            if current.membership_id != membership.pk or current.hub_id != hub.pk:
                # The clients act as the previous user, or in the previous hub: they log in afresh.
                models.Client.objects.filter(mandate=current).delete()
                current.membership = membership
                current.organization = membership.organization
                current.hub = hub
                changed += ["membership", "organization", "hub"]
            if not _same_pin(current.pin, pin):
                current.subject_manifest = pin.model_dump(mode="json")
                changed.append("subject_manifest")
            for field, value in (("expires_at", spec.expires_at), ("max_clients", spec.max_clients), ("allow_mesh", spec.allow_mesh)):
                if getattr(current, field) != value:
                    setattr(current, field, value)
                    changed.append(field)
            if changed:
                current.save(update_fields=changed)
                self.stdout.write(f"Mandate key '{spec.name}' updated ({', '.join(changed)})")
            return

        if current is not None:
            mandates.revoke_mandate(current, enums.MandateRevocationChoices.SUPERSEDED)
        if models.Mandate.objects.filter(key_hash=key_hash).exists():
            self.stderr.write(f"Mandate key '{spec.name}' was revoked and stays revoked; give the entry a new key")
            return
        mandates.issue_mandate(
            issuer=mandates.ConfigIssuer(name=spec.name),
            grantor=membership,
            hub=hub,
            pin=pin,
            claimant=mandates.KeyClaimant(key=key),
            expires_at=spec.expires_at,
            max_clients=spec.max_clients,
            allow_mesh=spec.allow_mesh,
            label=spec.name,
        )
        self.stdout.write(f"Mandate key '{spec.name}' issued for {spec.user} in hub {hub.identifier}")

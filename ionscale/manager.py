import logging

from django.conf import settings

from fakts.models import IonscaleLayer
from karakter.models import Membership
from . import base_models
from .repo import get_ionscale_repo

logger = logging.getLogger(__name__)

# Kept as a literal (matches api.management.enums.LayerKind.IONSCALE) so this
# lower-level module doesn't import the management app. Layer.kind is a free
# CharField, so the raw value is all that's stored.
_IONSCALE_KIND = "ionscale"


def ionscale_configured() -> bool:
    """Whether an ionscale backend is available (real CLI settings or a test/fake
    repository). When False, mesh provisioning is skipped rather than crashing."""
    return bool(
        getattr(settings, "IONSCALE_REPOSITORY", None)
        or getattr(settings, "IONSCALE_SERVER_URL", None)
    )


def get_org_mesh(organization) -> IonscaleLayer | None:
    """Return the organization's existing ionscale mesh, or None. Read-only — does
    not provision (that only happens via explicit opt-in, `ensure_org_mesh`)."""
    return IonscaleLayer.objects.filter(organization=organization).first()


def ensure_org_mesh(organization) -> IonscaleLayer | None:
    """Return the organization's single ionscale mesh, provisioning it on first
    opt-in.

    Singleton: if the org already has an ``IonscaleLayer`` it is returned as-is
    (this also removes the nondeterministic ``.first()`` ambiguity in callers).
    Returns ``None`` (and logs) when ionscale isn't configured or provisioning
    fails, so callers can degrade gracefully instead of breaking org flows.
    """
    existing = get_org_mesh(organization)
    if existing:
        return existing

    if not ionscale_configured():
        logger.warning(
            "Ionscale is not configured; skipping mesh provisioning for organization %s",
            organization,
        )
        return None

    # There is exactly one network per organization, so the tailnet is named after
    # the organization itself (its slug) rather than a "<slug>-default" variant.
    tailnet_name = str(organization.slug or organization.pk)
    try:
        get_ionscale_repo().create_tailnet(
            base_models.TailnetCreate(
                name=tailnet_name,
                # The organization *pk*, matching the `org` claim lok issues.
                # ionscale binds this at creation and cannot rebind it later.
                organization=str(organization.pk),
            )
        )
        layer = IonscaleLayer.objects.create(
            organization=organization,
            name=organization.name or tailnet_name,
            kind=_IONSCALE_KIND,
            identifier=tailnet_name,
            tailnet_name=tailnet_name,
        )
        sync(layer)
        apply_dns_config(layer)
        return layer
    except Exception:
        logger.exception(
            "Failed to provision ionscale mesh for organization %s", organization
        )
        return None


def sync(layer: IonscaleLayer) -> IonscaleLayer:
    """Push the organization's membership list to the mesh's IAM policy.

    lok owns `subs` and nothing else in that policy. ionscale writes other keys
    itself -- notably `roles`, which it fills in at login from the identity's
    org roles -- and a wholesale rewrite silently dropped them on the next
    membership change. Read the current policy and carry everything that is not
    `subs` across.
    """
    if not ionscale_configured():
        logger.warning("Ionscale is not configured; skipping member sync for mesh %s", layer)
        return layer

    members = Membership.objects.filter(organization=layer.organization).select_related("user")

    repo = get_ionscale_repo()

    try:
        current = repo.get_policy(layer.tailnet_name) or {}
    except Exception:
        # A control-plane blip must not block membership sync; falling back to a
        # plain rewrite loses ionscale's keys, which it repopulates at next login.
        logger.warning(
            "Could not read the current IAM policy for %s; rewriting it wholesale",
            layer.tailnet_name,
            exc_info=True,
        )
        current = {}

    policy = {k: v for k, v in current.items() if k != "subs"}
    policy["subs"] = [str(m.user.pk) for m in members]

    repo.update_policy(layer.tailnet_name, policy)

    return layer


def apply_dns_config(layer: IonscaleLayer, raise_on_error: bool = False) -> IonscaleLayer:
    """Push the mesh's desired DNS state (MagicDNS + HTTPS certs) to ionscale.

    Kept separate from `sync` (which runs on every membership change) so a DNS/ACME
    failure can't break member management and DNS isn't needlessly re-pushed. lok is
    the source of truth: `set-dns` replaces the whole config, so we always send the
    full desired state built from the model.

    ``raise_on_error`` controls failure handling: provisioning (`ensure_org_mesh`)
    leaves it False so a DNS hiccup doesn't fail org creation, but an *explicit* user
    toggle passes True so the failure surfaces to the caller (and the UI) instead of
    silently reporting success while ionscale never got the change.
    """
    if not ionscale_configured():
        logger.warning(
            "Ionscale is not configured; skipping DNS config for mesh %s", layer
        )
        return layer

    config = base_models.DNSConfig(
        magic_dns=layer.magic_dns_enabled,
        https_certs=layer.https_enabled,
    )
    try:
        get_ionscale_repo().set_dns_config(layer.tailnet_name, config)
    except Exception:
        logger.exception("Failed to apply DNS config for mesh %s", layer)
        if raise_on_error:
            raise

    return layer


def teardown_tailnet(tailnet_name: str) -> None:
    """Delete a mesh's tailnet on ionscale. See :func:`ionscale.sync.teardown_tailnet`."""
    from .sync import teardown_tailnet as _teardown

    _teardown(tailnet_name)


def sync_organization_layers(organization) -> None:
    """Synchronously push the member list to every mesh of the organization,
    swallowing failures. Signal handlers use the on-commit variant in
    :mod:`ionscale.sync` instead."""
    from .sync import resync_organization

    resync_organization(getattr(organization, "pk", organization))
import logging

from django.conf import settings

from fakts.models import IonscaleLayer
from karakter.models import Membership
from . import base_models
from .repo import get_ionscale_repo


def desired_tailnet_name(organization) -> str:
    """The tailnet is named after the organization: its id, which a rename never changes (see ``ensure_org_mesh``)."""
    return str(organization.pk)

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


def magic_dns_name(name: str | None, tailnet: str | None, fqdn: str | None = None) -> str | None:
    """A machine's MagicDNS name: the FQDN ionscale reports if any, else derived.

    ionscale namespaces machines under their tailnet, so the derived form is
    `<name>.<tailnet>.<suffix>` (e.g. gpu-01.myorg.mesh.arkitekt.live). None
    without a suffix configured. Callers decide whether MagicDNS is on.
    """
    if fqdn:
        return fqdn.rstrip(".")
    suffix = getattr(settings, "IONSCALE_MAGIC_DNS_SUFFIX", None)
    if not (suffix and name):
        return None
    return ".".join([name, tailnet, suffix] if tailnet else [name, suffix])


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
    # the organization itself: its id, which a rename of its slug never changes.
    tailnet_name = desired_tailnet_name(organization)
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
        # Replace ionscale's default allow-all ACL with lok's (sidecars isolated).
        from .acl import apply_acl_policy

        apply_acl_policy(organization.pk)
        return layer
    except Exception:
        logger.exception(
            "Failed to provision ionscale mesh for organization %s", organization
        )
        return None


def replace_org_mesh(layer: IonscaleLayer) -> IonscaleLayer:
    """Wipe the mesh's tailnet on ionscale and recreate it empty, keeping the layer.

    The way out of a mesh that can no longer be administered, e.g. a tailnet lock
    whose signing key and disablement secrets are lost: every machine, auth key,
    tailnet user and key-authority record goes with the old tailnet. The new one
    has the same name and organization binding, so nothing that addresses the
    mesh by name changes.

    The layer row, and with it every app enrollment and hub key row, is kept on
    purpose: they still mark who was granted the mesh, so the ACL keeps its
    sidecar rules and apps and hubs get a key for the new tailnet from
    ``POST /f/meshkey/`` without re-approval. The key values themselves died
    with the old tailnet.

    A node that kept its tailscaled state from a locked tailnet still holds the
    old key authority and stays locked out after rejoining until it runs
    ``tailscale lock local-disable``; nodes with fresh state are unaffected
    (ionskale ``tests/tailnet_lock_test.go``).

    The delete runs synchronously (the recreate would collide with it
    otherwise), and a tailnet that is already gone counts as deleted, so a
    replace that failed halfway can simply be retried. Unlike provisioning,
    every failure raises: in particular a fresh tailnet without lok's ACL is
    allow-all.
    """
    from .acl import apply_acl_policy
    from .errors import IonscaleError

    if not ionscale_configured():
        raise RuntimeError("ionscale is not configured on this deployment")

    organization = layer.organization
    repo = get_ionscale_repo()

    try:
        repo.delete_tailnet(layer.tailnet_name, force=True)
    except IonscaleError as exc:
        if exc.code != "not_found":
            raise

    repo.create_tailnet(
        base_models.TailnetCreate(
            name=layer.tailnet_name,
            organization=str(organization.pk),
        )
    )
    sync(layer)
    apply_dns_config(layer, raise_on_error=True)
    if not apply_acl_policy(organization.pk):
        raise RuntimeError(f"Could not apply the ACL policy to the new tailnet {layer.tailnet_name}")

    from fakts.models import Hub
    from fakts.services.mesh import forget_hub_mesh_hosts

    # Keys no hub or enrollment points at (hand-made keys, keys whose revoke
    # failed on rotation) are dead now and nothing would ever clean them up.
    layer.auth_keys.filter(hubs__isnull=True, app_mesh_enrollments__isnull=True).delete()

    # The hubs' last reported mesh hosts point into the old tailnet; drop them
    # (and the cached lookups) until the hubs report from the new one.
    hubs = Hub.objects.filter(organization=organization)
    forget_hub_mesh_hosts(hubs.values_list("pk", flat=True))
    hubs.update(mesh_connected=None, mesh_host="")

    logger.warning("Replaced the mesh tailnet %s of organization %s", layer.tailnet_name, organization)
    return layer


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

    # Full members only. A guest of some collaborations (a scoped membership,
    # karakter.collaboration_grants) cannot log a device of their own into the tailnet,
    # so they never join `autogroup:member` and its member-to-member rule. Their
    # apps still enroll as sidecars (`tag:app-<n>`), which reach only the hub the
    # app is bound to (ionscale.acl).
    members = Membership.objects.filter(organization=layer.organization, collaboration_grants__isnull=True).select_related("user").order_by("id")

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
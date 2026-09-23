"""Resolving a hub's address on its organization's mesh (for ``kind: mesh`` aliases).

A hub that reports its mesh state on its health callback (see
``hubs.report_hub_health``) is authoritative while it is online. Otherwise the
hub's node is looked up in ionscale; envelopes re-render on every token
refresh, so that lookup is cached briefly.
"""

import logging

from django.core.cache import cache

from fakts import models

logger = logging.getLogger(__name__)

MESH_HOST_CACHE_SECONDS = 60
_MISS = ""  # cached "no node" (None would read as a cache miss)


def resolve_hub_mesh_host(hub: models.Hub) -> str | None:
    """The host clients on the mesh reach ``hub`` at: its node's MagicDNS name, or
    its 100.x IPv4 when MagicDNS is off for the mesh. ``None`` when the hub says it
    is off the mesh, the org has no mesh, or no node carries the hub's tag.
    Never raises."""
    if hub.online and hub.mesh_connected is not None:
        return (hub.mesh_host or None) if hub.mesh_connected else None

    key = f"hub-mesh-host:{hub.pk}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None

    try:
        host = _lookup(hub)
    except Exception:
        # Cache the miss too: an unreachable ionscale must not be retried on every
        # token refresh (the CLI repo spawns a subprocess per call).
        logger.warning("Could not resolve the mesh host of hub %s", hub, exc_info=True)
        cache.set(key, _MISS, MESH_HOST_CACHE_SECONDS)
        return None

    cache.set(key, host or _MISS, MESH_HOST_CACHE_SECONDS)
    return host


def _lookup(hub: models.Hub) -> str | None:
    from ionscale.manager import get_org_mesh, ionscale_configured, magic_dns_name
    from ionscale.repo import get_ionscale_repo

    if not ionscale_configured():
        return None
    layer = get_org_mesh(hub.organization)
    if layer is None:
        return None

    tagged = [m for m in get_ionscale_repo().list_machines(layer.tailnet_name) if hub.mesh_tag in m.tags]
    if not tagged:
        return None
    # The node the hub is running as: an online one, else the newest (ids are time-ordered).
    node = max(tagged, key=lambda m: (bool(m.connected), int(m.id)))

    if layer.magic_dns_enabled:
        name = magic_dns_name(node.name, layer.tailnet_name, getattr(node, "fqdn", None))
        if name:
            return name
    return node.ipv4 or None


# --------------------------------------------------------------------------- #
# Sidecar lifetime: a sidecar lives only while a live client backs it.
# --------------------------------------------------------------------------- #

BACKER_GRACE_SECONDS = 60 * 60
"""A freshly bound client has no tokens until it polls the device-code grant;
it backs its sidecar for this long before a live session is required."""


def live_clients(clients):
    """Narrow a ``Client`` queryset to clients with a live session: a non-revoked
    refresh chain within its sliding and absolute lifetimes, or a client created
    so recently that it has not polled for its first token yet."""
    import time

    from django.db.models import Q
    from django.utils import timezone
    from datetime import timedelta

    from authapp.models import OAuth2Token

    now = int(time.time())
    live_ids = OAuth2Token.objects.filter(
        revoked=False,
        refresh_token__isnull=False,
        issued_at__gte=now - OAuth2Token.REFRESH_TOKEN_LIFETIME,
        chain_started_at__gte=now - OAuth2Token.REFRESH_CHAIN_MAX_LIFETIME,
    ).values("client_id")
    # The grace only covers a client that never got a token: one whose tokens
    # were all revoked is dead at once, however new it is.
    never_polled = ~Q(client_id__in=OAuth2Token.objects.values("client_id"))
    fresh = timezone.now() - timedelta(seconds=BACKER_GRACE_SECONDS)
    return clients.filter(Q(client_id__in=live_ids) | (never_polled & Q(created_at__gte=fresh)))


def enrollment_clients(membership_id, app_id, device_id):
    """The app clients an ``AppMeshEnrollment`` stands for (bound, any hub)."""
    return models.Client.objects.filter(
        membership_id=membership_id,
        release__app_id=app_id,
        node_id=device_id,
    )


def enrollment_is_live(membership_id, app_id, device_id) -> bool:
    return live_clients(enrollment_clients(membership_id, app_id, device_id)).exists()


def hub_identity_is_live(hub: models.Hub) -> bool:
    if not hub.client_id:
        return False
    return live_clients(models.Client.objects.filter(pk=hub.client_id)).exists()


def reap_enrollment(membership_id, app_id, device_id) -> bool:
    """Delete the enrollment if no live client backs it any more. Its pre_delete
    revokes the key and nodes. Returns whether it was reaped."""
    if enrollment_is_live(membership_id, app_id, device_id):
        return False
    reaped = False
    for enrollment in models.AppMeshEnrollment.objects.filter(membership_id=membership_id, app_id=app_id, device_id=device_id):
        logger.info("Reaping mesh sidecar %s: no live client backs it", enrollment.tag)
        enrollment.delete()
        reaped = True
    return reaped


def reap_hub_sidecar(hub: models.Hub) -> bool:
    """Revoke the hub's mesh key and nodes if its identity has no live session.
    The hub itself stays; re-authorizing it enrolls a new sidecar."""
    # Without a key row there is nothing to revoke here; leftover nodes of such a
    # hub are removed by tag in the periodic sweep (ionscale.reconcile).
    if hub.auth_key_id is None or hub_identity_is_live(hub):
        return False
    from ionscale.sync import schedule_enrollment_revocation

    logger.info("Reaping mesh sidecar %s: the hub identity has no live session", hub.mesh_tag)
    schedule_enrollment_revocation(hub)
    if hub.auth_key_id:
        key = hub.auth_key
        models.Hub.objects.filter(pk=hub.pk).update(auth_key=None)
        key.delete()
    return True


def schedule_client_reap(*, membership_id=None, app_id=None, device_id=None, hub_id=None) -> None:
    """After commit, reap the sidecar a (deleted or revoked) client backed.

    The check runs at commit time on purpose: a re-authorization deletes the old
    client *and* binds the new one in the same transaction, and must not lose
    its sidecar."""
    from django.db import transaction

    def _run() -> None:
        try:
            if app_id is not None and membership_id is not None:
                reap_enrollment(membership_id, app_id, device_id)
            if hub_id is not None:
                hub = models.Hub.objects.filter(pk=hub_id).first()
                if hub is not None:
                    reap_hub_sidecar(hub)
        except Exception:
            logger.exception("Could not reap the mesh sidecar of a revoked client")

    transaction.on_commit(_run)


def schedule_reap_for_clients(clients) -> None:
    """Schedule reaps for every sidecar the given clients back (after a revocation)."""
    for client in clients.select_related("release"):
        hub_identity = models.Hub.objects.filter(client=client).values_list("pk", flat=True).first()
        schedule_client_reap(
            membership_id=client.membership_id,
            app_id=client.release.app_id if client.release_id else None,
            device_id=client.node_id,
            hub_id=hub_identity,
        )

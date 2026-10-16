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


def _clients_with_session(clients, grace_seconds: int):
    import time

    from django.db.models import Q
    from django.utils import timezone
    from datetime import timedelta

    from authapp.models import OAuth2Token

    now = int(time.time())
    held = OAuth2Token.objects.filter(
        revoked=False,
        refresh_token__isnull=False,
        issued_at__gte=now - OAuth2Token.REFRESH_TOKEN_LIFETIME - grace_seconds,
        chain_started_at__gte=now - OAuth2Token.REFRESH_CHAIN_MAX_LIFETIME - grace_seconds,
    ).values("client_id")
    # The grace only covers a client that never got a token: one whose tokens
    # were all revoked is dead at once, however new it is.
    never_polled = ~Q(client_id__in=OAuth2Token.objects.values("client_id"))
    fresh = timezone.now() - timedelta(seconds=BACKER_GRACE_SECONDS)
    session = Q(client_id__in=held) | (never_polled & Q(created_at__gte=fresh))
    # A client logging in with a mandate key has no refresh chain: it is backed
    # for as long as its mandate works (an expired one for the grace; a revoked
    # one never, and revoking deletes its clients anyway).
    mandated = models.Mandate.objects.filter(revoked_at__isnull=True, membership__user__is_active=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now() - timedelta(seconds=grace_seconds))
    )
    return clients.filter((Q(mandate__isnull=True) & session) | Q(mandate__in=mandated))


def live_clients(clients):
    """Narrow a ``Client`` queryset to clients with a live session: a non-revoked
    refresh chain within its sliding and absolute lifetimes, or a client created
    so recently that it has not polled for its first token yet.

    Live is what grants access: the ACL (``ionscale.acl``) and key renewal."""
    return _clients_with_session(clients, 0)


def retained_clients(clients):
    """Like ``live_clients``, but a session that merely *expired* still counts for
    ``MESH_SIDECAR_GRACE_SECONDS``. A revoked session never does.

    Retained is what keeps a sidecar's enrollment and nodes: an expired app has
    no ACL rule any more, but re-approving it within the grace resumes the same
    node, tag and address."""
    from django.conf import settings

    return _clients_with_session(clients, getattr(settings, "MESH_SIDECAR_GRACE_SECONDS", 0))


def enrollment_clients(membership_id, app_id, device_id):
    """The app clients an ``AppMeshEnrollment`` stands for (bound, any hub)."""
    return models.Client.objects.filter(
        membership_id=membership_id,
        release__app_id=app_id,
        node_id=device_id,
    )


def enrollment_is_live(membership_id, app_id, device_id) -> bool:
    return live_clients(enrollment_clients(membership_id, app_id, device_id)).exists()


def enrollment_is_retained(membership_id, app_id, device_id) -> bool:
    return retained_clients(enrollment_clients(membership_id, app_id, device_id)).exists()


def hub_identity_is_live(hub: models.Hub) -> bool:
    if not hub.client_id:
        return False
    return live_clients(models.Client.objects.filter(pk=hub.client_id)).exists()


def hub_identity_is_retained(hub: models.Hub) -> bool:
    if not hub.client_id:
        return False
    return retained_clients(models.Client.objects.filter(pk=hub.client_id)).exists()


def reap_enrollment(membership_id, app_id, device_id) -> bool:
    """Delete the enrollment if no client backs it any more (none live, none
    expired within the grace). Its pre_delete revokes the key and nodes. Returns
    whether it was reaped."""
    if enrollment_is_retained(membership_id, app_id, device_id):
        return False
    reaped = False
    for enrollment in models.AppMeshEnrollment.objects.filter(membership_id=membership_id, app_id=app_id, device_id=device_id):
        logger.info("Reaping mesh sidecar %s: no client backs it", enrollment.tag)
        enrollment.delete()
        reaped = True
    return reaped


def reap_hub_sidecar(hub: models.Hub) -> bool:
    """Revoke the hub's mesh key and nodes if its identity has no session (none
    live, none expired within the grace). The hub itself stays; re-authorizing it
    enrolls a new sidecar."""
    # Without a key row there is nothing to revoke here; leftover nodes of such a
    # hub are removed by tag in the periodic sweep (ionscale.reconcile).
    if hub.auth_key_id is None or hub_identity_is_retained(hub):
        return False
    from ionscale.acl import schedule_acl_apply
    from ionscale.sync import schedule_enrollment_revocation

    logger.info("Reaping mesh sidecar %s: the hub identity has no live session", hub.mesh_tag)
    schedule_enrollment_revocation(hub)
    if hub.auth_key_id:
        key = hub.auth_key
        models.Hub.objects.filter(pk=hub.pk).update(auth_key=None)
        key.delete()
    schedule_acl_apply(hub.organization_id)
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
    """Schedule reaps for every sidecar the given clients back (after a revocation).

    The ACL is re-applied either way: a sidecar that survives on a sibling client
    still loses the rule towards the revoked client's hub."""
    from ionscale.acl import schedule_acl_apply

    for client in clients.select_related("release"):
        hub_identity = models.Hub.objects.filter(client=client).values_list("pk", flat=True).first()
        schedule_client_reap(
            membership_id=client.membership_id,
            app_id=client.release.app_id if client.release_id else None,
            device_id=client.node_id,
            hub_id=hub_identity,
        )
        if client.organization_id:
            schedule_acl_apply(client.organization_id)


def reject_app_installation(client: models.Client) -> None:
    """Reject an app: end every session of the installation ``client`` belongs to
    and remove its mesh sidecar at once.

    An installation is every client of the same (membership, app, device), which
    is what one ``AppMeshEnrollment`` stands for: the same app bound to another
    hub, or (without a device) every deviceless install of it. Rejecting one of
    them while a sibling kept the node up would not be a rejection.

    A client without a release (a hub identity) only loses its own sessions; its
    sidecar is reaped the usual way."""
    from authapp.models import OAuth2Token

    if client.mandate_id is not None:
        # A client logging in with a key would just run its grant again: rejecting
        # it withdraws the key (which deletes the client and reaps its sidecar).
        from fakts.services.mandates import revoke_mandate

        revoke_mandate(client.mandate)
        return

    if client.release_id is None or client.membership_id is None:
        from fakts.services.mandates import revoke_issued_by

        OAuth2Token.objects.filter(client_id=client.client_id).update(revoked=True)
        revoke_issued_by(models.Client.objects.filter(pk=client.pk))
        schedule_reap_for_clients(models.Client.objects.filter(pk=client.pk))
        return

    from fakts.services.mandates import revoke_issued_by

    app_id = client.release.app_id
    siblings = enrollment_clients(client.membership_id, app_id, client.node_id)
    OAuth2Token.objects.filter(client_id__in=siblings.values("client_id")).update(revoked=True)
    # A rejected app takes everything it issued with it (and a mandated sibling
    # stops working only once its mandate does).
    revoke_issued_by(siblings)
    # An approved code that was not polled yet would still hand out tokens (and
    # the mesh key) to a client we are rejecting.
    models.DeviceCode.objects.filter(client__in=siblings).delete()
    # Deleted outright rather than reaped: a sibling inside its never-polled
    # grace must not keep the sidecar. pre_delete revokes the key and nodes.
    for enrollment in models.AppMeshEnrollment.objects.filter(
        membership_id=client.membership_id, app_id=app_id, device_id=client.node_id
    ):
        logger.info("Removing mesh sidecar %s: the app was rejected", enrollment.tag)
        enrollment.delete()
    schedule_reap_for_clients(siblings)

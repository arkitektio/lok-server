"""Client lifecycle on the unified Client model.

Registration (``create_public_client``) mints an unbound public client row;
approval (``bind_client``) fills the same row in place — membership,
organization, release/hub, instance mappings, and the real scope string.
Depends on :mod:`fakts.services.rendering` for ``auto_compose``.
"""

import hashlib
import json
import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from fakts import base_models, enums, models
from fakts.base_models import Manifest
from fakts.models import generate_client_id
from fakts.services.rendering import auto_compose
from karakter import models as karakter_models
from karakter.hashers import hash_device_id

logger = logging.getLogger(__name__)

# Every fakts client is a *public* OAuth2 client: it holds no secret, exchanges
# its device code (or redeem token) once, and from then on its identity is the
# rotated refresh-token chain. authorization_code is included so website-kind
# clients (with registered redirect URIs and PKCE) can use the standard code flow.
FAKTS_CLIENT_GRANT_TYPES = "urn:ietf:params:oauth:grant-type:device_code urn:fakts:grant-type:redeem refresh_token authorization_code"

# OIDC base scopes every fakts client may request on top of its granted
# organization scopes.
BASE_OIDC_SCOPES = ["openid", "profile", "email"]


class DeviceAuthRequired(Exception):
    """Raised when an organization requires device authentication but the client
    manifest carries no ``device_id``."""


class RedeemTokenExpired(Exception):
    """Raised when a redeem token has passed its expiry (and has been deleted)."""


class RedeemTokenExhausted(Exception):
    """Raised when a redeem token has been redeemed as many times as allowed."""


class RedeemTokenManifestChanged(Exception):
    """Raised when an already-redeemed token is re-redeemed with a different
    manifest while ``allow_reredeem`` is not set."""


class RedeemTokenManifestMismatch(Exception):
    """Raised when a redeem presents a manifest that does not satisfy the manifest the
    token was pinned to at mint time (see :func:`check_pinned_manifest`)."""


class UnknownScope(Exception):
    """Raised when a manifest requests a scope the organization does not define.

    A clean domain error (instead of ``Scope.DoesNotExist`` leaking out of the
    token endpoint as a 500) so the OAuth grants can map it to ``invalid_scope``.
    """


def hash_manifest(manifest: Manifest) -> str:
    """Return a stable SHA-256 hash of a manifest for change detection."""
    return hashlib.sha256(
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


def check_pinned_manifest(pinned: dict, manifest: Manifest) -> None:
    """Refuse a redeem whose manifest is not covered by the token's pinned manifest.

    Identity (``identifier``, ``version``) and placement (``device_id``, when pinned) must
    match exactly. ``scopes`` and ``requirements`` are a *ceiling*: the presented manifest
    may request a subset, never more — extra scopes would widen what the issued token can
    do, extra requirements would render extra service instances into the envelope.
    Cosmetic fields (title, description, logo, authors, keywords, public sources) are not
    compared: the running app assembles them from its image, and the deployer that pinned
    the token cannot reproduce them byte-for-byte.
    """
    if manifest.identifier != pinned.get("identifier"):
        raise RedeemTokenManifestMismatch(
            f"This redeem token is pinned to app '{pinned.get('identifier')}', "
            f"not '{manifest.identifier}'."
        )
    if manifest.version != pinned.get("version"):
        raise RedeemTokenManifestMismatch(
            f"This redeem token is pinned to version '{pinned.get('version')}' of "
            f"'{manifest.identifier}', not '{manifest.version}'."
        )
    # Tokens pinned before the rename stored the device under `node_id`.
    pinned_device = pinned.get("device_id") or pinned.get("node_id")
    if pinned_device and manifest.device_id != pinned_device:
        raise RedeemTokenManifestMismatch(
            "This redeem token is pinned to a different device than the one the manifest names."
        )
    extra_scopes = sorted(set(manifest.scopes or []) - set(pinned.get("scopes") or []))
    if extra_scopes:
        raise RedeemTokenManifestMismatch(
            f"This redeem token does not authorize the scope(s) {', '.join(extra_scopes)}."
        )
    pinned_requirements = {
        (r.get("key"), r.get("service")) for r in (pinned.get("requirements") or [])
    }
    extra_requirements = sorted(
        f"{r.key}={r.service}"
        for r in (manifest.requirements or [])
        if (r.key, r.service) not in pinned_requirements
    )
    if extra_requirements:
        raise RedeemTokenManifestMismatch(
            f"This redeem token does not authorize the requirement(s) {', '.join(extra_requirements)}."
        )


def create_public_client(
    kind: str = enums.ClientKindVanilla.DEVELOPMENT.value,
    role: str = enums.ClientRoleVanilla.INTERFACE.value,
    redirect_uris: list[str] | None = None,
    public: bool = False,
) -> models.Client:
    """Dynamic client registration: mint an *unbound* public client row.

    The row carries only identity and the requested attributes; it cannot get a
    token until :func:`bind_client` attaches a membership at approval.
    """
    return models.Client.objects.create(
        client_id=generate_client_id(),
        client_secret="",
        token_endpoint_auth_method="none",
        grant_types=FAKTS_CLIENT_GRANT_TYPES,
        scope="",
        kind=kind,
        role=role if isinstance(role, str) else role.value,
        redirect_uris=" ".join(redirect_uris) if redirect_uris else "",
        public=public,
    )


def finalize_client_scope(client: models.Client) -> str:
    """Write the client's real requestable scope: the granted organization
    scopes (``Client.scopes`` M2M) on top of the OIDC base scopes. This is the
    scope unification point — issued JWTs carry these instead of silently
    falling back to the OIDC defaults."""
    scope = " ".join(BASE_OIDC_SCOPES + sorted(client.scopes.values_list("identifier", flat=True)))
    client.scope = scope
    client.save(update_fields=["scope"])
    return scope


@transaction.atomic
def bind_client(
    client: models.Client,
    manifest: base_models.Manifest,
    membership: karakter_models.Membership,
    hub: models.Hub | None = None,
    declined_requirements: list[str] | None = None,
    device_name: str | None = None,
) -> models.Client:
    """Approve a registered client: bind it to a membership and fill in the
    app side (org-scoped App/Release, node, instance mappings, scopes) in place.

    Re-approval rotates identity: any *other* bound client for the same
    (release identity, membership, node, hub) is deleted — the old client_id
    and its refresh chain die, and this row (with its fresh client_id from
    registration) takes over.
    """
    from fakts.utils import download_logo

    organization = membership.organization
    user = membership.user

    try:
        logo = download_logo(manifest.logo) if manifest.logo else None
    except Exception as e:
        raise ValueError(f"Could not download logo {e}")

    display_name = manifest.title or manifest.identifier

    # Apps are org-scoped: the same identifier registered in two organizations
    # is two rows, so one tenant's manifest can never mutate another's catalog.
    app, _ = models.App.objects.get_or_create(
        identifier=manifest.identifier,
        organization=organization,
        defaults={"name": display_name},
    )
    dirty = False
    if logo:
        app.logo = logo
        dirty = True
    if manifest.title and app.name != manifest.title:
        app.name = manifest.title
        dirty = True
    if dirty:
        app.save()

    release, _ = models.Release.objects.update_or_create(
        app=app,
        version=manifest.version,
        defaults={
            "name": manifest.title or manifest.version,
            "logo": logo,
            "scopes": manifest.scopes,
            "requirements": manifest.model_dump()["requirements"],
        },
    )

    if organization.require_device_auth and not manifest.device_id:
        raise DeviceAuthRequired(
            "This organization requires device authentication; the client manifest "
            "must include a device_id."
        )

    if manifest.device_id:
        node = models.Device.objects.get_or_create(
            organization=organization,
            node_id=hash_device_id(manifest.device_id, organization),
            defaults={"name": device_name},
        )[0]
    else:
        node = None

    # Identity rotation on re-approval: the previous installation's client (and
    # with it its refresh chain and report history) is deleted.
    models.Client.objects.filter(
        release=release,
        membership=membership,
        node=node,
        hub=hub,
        kind=client.kind,
    ).exclude(pk=client.pk).delete()

    client.membership = membership
    client.organization = organization
    client.release = release
    client.hub = hub
    client.node = node
    client.name = display_name
    client.manifest = manifest.model_dump()
    client.logo = logo or release.logo
    client.public_sources = [t.model_dump() for t in manifest.public_sources] if manifest.public_sources else []
    client.save()

    client = auto_compose(client, manifest, user, organization, device=node, declined_requirements=declined_requirements)

    client.scopes.clear()
    for scope in manifest.scopes or []:
        try:
            client.scopes.add(karakter_models.Scope.objects.get(identifier=scope, organization=organization))
        except karakter_models.Scope.DoesNotExist:
            raise UnknownScope(f"Scope '{scope}' is not available in organization '{organization.slug}'")

    finalize_client_scope(client)

    return client


PRIOR_ACCESS_ACTIVE = "active"
PRIOR_ACCESS_EXPIRED = "expired"
PRIOR_ACCESS_REVOKED = "revoked"


def find_prior_clients(manifest: Manifest, user: karakter_models.User):
    """Bound clients ``user`` previously approved for the app/device this manifest
    describes, most recently seen first.

    Used by the configure page to say "you already authorized this app on this
    device into hub X" and to preselect that hub when the device re-registers
    after its refresh chain died. The lookup is derived from the surviving
    ``Client`` rows only (``bind_client`` deletes them at *re*-approval, never at
    token expiry), so nothing is persisted for it.

    Scoping is deliberate: ``device_id`` is self-asserted by an unauthenticated
    device, so the answer must never reveal anything about other users. Only
    clients bound to one of the caller's own memberships are considered, and the
    device id is hashed with each of the caller's organizations' salts (the hash
    is per-organization). A manifest without ``device_id`` matches nothing.
    """
    if not manifest.device_id:
        return models.Client.objects.none()

    memberships = karakter_models.Membership.objects.filter(user=user).select_related("organization")
    node_q = Q(pk__in=[])
    for membership in memberships:
        organization = membership.organization
        node_q |= Q(
            node__organization=organization,
            node__node_id=hash_device_id(manifest.device_id, organization),
        )

    return (
        models.Client.objects.filter(node_q)
        .filter(
            membership__user=user,
            hub__isnull=False,
            release__app__identifier=manifest.identifier,
        )
        .select_related("hub", "hub__organization", "release", "release__app", "node")
        .prefetch_related("scopes")
        .order_by("-last_reported_at", "-created_at")
    )


def client_access_state(client: models.Client) -> str:
    """Whether a previously approved client can still refresh.

    ``revoked``: its newest token was revoked (operator action or reuse
    detection) — the configure page must not present re-approval as a routine
    renewal in that case. ``expired``: the chain simply ran out (or never issued
    a token). ``active``: it can still refresh, i.e. the new registration is a
    parallel install rather than a renewal.
    """
    from authapp.models import OAuth2Token

    latest = OAuth2Token.objects.filter(client_id=client.client_id).order_by("-issued_at", "-id").first()
    if latest is None:
        return PRIOR_ACCESS_EXPIRED
    if latest.revoked:
        return PRIOR_ACCESS_REVOKED
    if latest.is_refresh_token_active():
        return PRIOR_ACCESS_ACTIVE
    return PRIOR_ACCESS_EXPIRED


@transaction.atomic
def validate_redeem_token(redeem_token: models.RedeemToken, manifest: Manifest, role: enums.ClientRoleVanilla = enums.ClientRoleVanilla.INTERFACE) -> models.RedeemToken:
    device_id = manifest.device_id
    hub = redeem_token.hub
    organization = redeem_token.hub.organization
    user = redeem_token.user
    membership = karakter_models.Membership.objects.get(user=user, organization=organization)

    if device_id:
        node, _ = models.Device.objects.get_or_create(organization=organization, node_id=hash_device_id(device_id, organization))
    else:
        node = None

    client = models.Client.objects.filter(
        release__app__identifier=manifest.identifier,
        release__app__organization=organization,
        release__version=manifest.version,
        kind="development",
        node=node,
        membership=membership,
        hub=hub,
    ).first()

    if not client:
        client = create_public_client(
            kind=enums.ClientKindVanilla.DEVELOPMENT.value,
            role=role.value if hasattr(role, "value") else role,
        )

    bind_client(
        client,
        manifest,
        membership,
        hub=hub,
    )

    redeem_token.client = client
    redeem_token.save()
    return redeem_token


def redeem_token(token: str, manifest: Manifest, role: enums.ClientRoleVanilla = enums.ClientRoleVanilla.INTERFACE) -> models.Client:
    """Redeem a token into a client.

    Raises ``RedeemToken.DoesNotExist`` for an unknown token and
    :class:`RedeemTokenExpired` for an expired one (which is deleted).
    """
    with transaction.atomic():
        # Lock the token row so simultaneous redeems of the same token serialize
        # instead of racing to create duplicate clients.
        valid_token = models.RedeemToken.objects.select_for_update().get(token=token)

        # A token with a redemption budget must stop working once it is spent.
        # Each redeem mints a fresh access+refresh pair, so without this an
        # unlimited token is a permanent, unrevocable foothold as its user.
        if valid_token.redemptions_exhausted():
            raise RedeemTokenExhausted(
                "This redeem token has already been redeemed the maximum number of times."
            )

        if not (valid_token.expires_at and valid_token.expires_at < timezone.now()):
            # A pre-authorized token is checked against its pin *before* anything is
            # looked up or provisioned, on every redeem: the pin is what makes the
            # token safe to hand to an unattended container.
            if valid_token.pinned_manifest:
                check_pinned_manifest(valid_token.pinned_manifest, manifest)

            incoming_hash = hash_manifest(manifest)

            if valid_token.client:
                if valid_token.manifest_hash is None:
                    # Pre-existing token from before manifest-hash tracking: record the
                    # hash and accept this redeem rather than treating it as a change.
                    valid_token.manifest_hash = incoming_hash
                    valid_token.save()
                    return valid_token.client
                if valid_token.manifest_hash == incoming_hash:
                    return valid_token.client
                if not valid_token.allow_reredeem:
                    raise RedeemTokenManifestChanged(
                        "This redeem token was already redeemed with a different manifest. "
                        "Re-redeeming with a changed manifest is not allowed unless allow_reredeem is set."
                    )
                # allow_reredeem is set and the manifest changed: re-validate to update the client.

            valid_token = validate_redeem_token(redeem_token=valid_token, manifest=manifest, role=role)
            valid_token.manifest_hash = incoming_hash
            valid_token.redemption_count = valid_token.redemption_count + 1
            valid_token.save()
            return valid_token.client

    # Reached only when the token is expired. Delete it *outside* the atomic block
    # above so the removal commits — deleting inside would be rolled back by the
    # raise (and the expired token would survive).
    models.RedeemToken.objects.filter(token=token).delete()
    raise RedeemTokenExpired("Redeem token expired")


def _resolve_reported_alias(client: models.Client, alias_id: str | None) -> models.InstanceAlias | None:
    """Resolve an alias id from a self-report, scoped to the client's organization.

    The alias id arrives from a Bearer-authenticated client and must never be
    trusted verbatim: scoping the lookup to the instances of the client's own
    organization keeps one tenant from attaching its reports to (or probing the
    existence of) another tenant's aliases. An unknown or foreign id is treated
    as "no alias" — the key's valid/reason are still recorded, but the foreign
    reference is not applied.
    """
    if not alias_id:
        return None
    if client.organization_id is None:
        return None
    alias = (
        models.InstanceAlias.objects.filter(id=alias_id, instance__hub__organization_id=client.organization_id)
        .select_related("instance")
        .first()
    )
    if alias is None:
        logger.warning(
            "Client %s reported alias %r which is not visible in its organization; ignoring the reference",
            client.client_id,
            alias_id,
        )
    return alias


@transaction.atomic
def report_client(client: models.Client, claim: base_models.ReportRequest) -> models.Client:
    """Record a client's self-report (functional flag + per-requirement alias reports).

    The client is resolved by the caller from its Bearer access token (the
    JWT's `client_id` claim) — the old opaque client token no longer exists.

    Also snapshots the report into a ``Report`` row, updates the client's
    ``last_healthy_report`` pointer when the client reports healthy, and prunes
    the client's report history to the latest ``settings.CLIENT_REPORT_RETENTION``
    (the last-healthy report is always kept, even if it falls outside that window).
    """
    # Lock the client row so concurrent reports don't race the prune / pointer update.
    client = models.Client.objects.select_for_update().get(pk=client.pk)
    client.functional = claim.functional
    # A fresh report is unacknowledged by definition: whatever an operator
    # resolved applied to the *previous* report. This is what makes a still-broken
    # client reappear on the dashboard's action list after being triaged.
    client.latest_report_resolved = False
    # The client has now reported, so an operator's outstanding request is
    # satisfied — clearing it here is what stops `please_report` from being
    # repeated on every subsequent token refresh.
    client.report_requested_at = None
    client.report_requested_by = None
    client.save()

    for req_key, alias_report in claim.alias_reports.items():
        alias = _resolve_reported_alias(client, alias_report.alias_id)

        models.UsedAlias.objects.update_or_create(
            client=client,
            key=req_key,
            defaults={
                "alias": alias,
                "valid": alias_report.valid,
                "reason": alias_report.reason,
            },
        )

    # Snapshot this report (raw payload; valid/reason are the frozen issue signal).
    report = models.Report.objects.create(
        client=client,
        functional=claim.functional,
        alias_reports={
            key: {"alias_id": r.alias_id, "valid": r.valid, "reason": r.reason}
            for key, r in claim.alias_reports.items()
        },
    )

    # Track the last healthy report; it persists across pruning.
    if claim.functional:
        client.last_healthy_report = report
        client.save(update_fields=["last_healthy_report"])

    # Keep only the latest N reports, but never delete the last-healthy pointer's target.
    retention = getattr(settings, "CLIENT_REPORT_RETENTION", 5)
    keep_ids = list(
        models.Report.objects.filter(client=client)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)[:retention]
    )
    if client.last_healthy_report_id:
        keep_ids.append(client.last_healthy_report_id)
    models.Report.objects.filter(client=client).exclude(id__in=keep_ids).delete()

    return client

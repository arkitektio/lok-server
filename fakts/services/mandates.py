"""Mandates: every headless login, and who may hand one out.

A mandate is a standing authority to act as its grantor, inside one hub, up to a
pinned manifest (see :class:`fakts.models.Mandate`). This module is the only place
mandates are issued, revoked and exchanged:

- :func:`issue_mandate` creates one, for every issuer (a member in kontrol, an app
  as itself, an agent under a parent mandate, a hub's client request, the
  deployment config) under one policy.
- :func:`authenticate_key` is what the ``urn:fakts:grant-type:mandate-key`` grant
  runs: it checks the mandate and returns the client the key logs in as. There is
  no refresh chain; the client runs the grant again when its access token
  expires, so every token is backed by a fresh check.
- :func:`revoke_mandate` withdraws a mandate and, at once, every child issued
  under it, and deletes their clients.

A mandate dies with its issuer: deleting the grantor membership, the issuing
client or the hub cascades in the database; rejecting or revoking the issuing app
revokes what it issued (:func:`revoke_issued_by`). Nothing runs on a timer.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from fakts import enums, models
from fakts.base_models import MandateKeyGrantRequest, MandatePin, MandatePinMismatch
from karakter import models as karakter_models
from karakter.hashers import hash_device_id

logger = logging.getLogger(__name__)

#: The longest an app may issue a key for, as itself.
APP_KEY_MAX_TTL = timedelta(days=30)
#: The longest an app may grant an agent mandate for.
AGENT_MANDATE_MAX_TTL = timedelta(days=365)
#: The grant type a client logging in with a mandate key is registered for.
MANDATE_KEY_GRANT_TYPE = "urn:fakts:grant-type:mandate-key"


class MandateError(Exception):
    """A refused mandate operation (mapped to a GraphQL or OAuth error)."""


class MandateNotLive(MandateError):
    """The mandate was revoked or has expired, or its grantor is no longer active."""


class MandateAgentMismatch(MandateError):
    """The caller is not the agent this mandate names."""


class MandateExhausted(MandateError):
    """The mandate's ``max_clients`` budget is used up."""


class MandateChaining(MandateError):
    """A client that itself logs in under a mandate may not issue mandates of its own."""


class MandateExceedsIssuer(MandateError):
    """The mandate would allow more than its issuer may."""


class UnknownMandateScope(MandateError):
    """The pin names a scope the organization does not define."""


class UnknownMandateKey(MandateError):
    """No mandate has this key."""


class MandateKeyReused(MandateError):
    """The key belongs, or belonged, to another mandate."""


# --------------------------------------------------------------------------- #
# Issuers and claimants
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MemberIssuer:
    """A member, through kontrol. They grant as themselves."""

    membership: karakter_models.Membership


@dataclass(frozen=True)
class ClientIssuer:
    """An app, as its own user. Capped at the app's own scopes and requirements."""

    client: models.Client


@dataclass(frozen=True)
class AgentIssuer:
    """An agent app issuing a keyed child under the agent mandate that names it."""

    parent: models.Mandate
    client: models.Client


@dataclass(frozen=True)
class HubIssuer:
    """Approving a hub that asks for a client (``HubManifest.clients``)."""

    hub: models.Hub
    request: str


@dataclass(frozen=True)
class ConfigIssuer:
    """The deployment config (``mandate_keys``), applied by ``ensuremandates``."""

    name: str


Issuer = MemberIssuer | ClientIssuer | AgentIssuer | HubIssuer | ConfigIssuer


@dataclass(frozen=True)
class KeyClaimant:
    """Whoever holds the key. ``key`` is one the issuer proposes; ``None`` lets lok mint it."""

    key: str | None = None


@dataclass(frozen=True)
class AgentClaimant:
    """The app named ``identifier`` may issue keyed children, optionally narrowed."""

    identifier: str
    device_id: str | None = None
    membership: karakter_models.Membership | None = None
    attestation: str = ""


Claimant = KeyClaimant | AgentClaimant


@dataclass(frozen=True)
class IssuedMandate:
    mandate: models.Mandate
    key: str | None
    """The key, when lok minted it: shown to the issuer once and never again."""


@dataclass(frozen=True)
class KeyGrant:
    client: models.Client
    mesh_key: models.IonscaleAuthKey | None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def hash_key(key: str) -> str:
    """The stored form of a mandate key. Keys are high-entropy, so a plain digest suffices."""
    return hashlib.sha256(key.encode()).hexdigest()


def is_org_admin(membership: karakter_models.Membership) -> bool:
    """The organization's owner, or a holder of its ``admin`` role."""
    if membership.organization.owner_id == membership.user_id:
        return True
    return membership.roles.filter(identifier="admin").exists()


def assert_is_agent(mandate: models.Mandate, client: models.Client | None) -> None:
    """Refuse unless ``client`` is (a narrowed-enough instance of) the mandate's agent."""
    if mandate.claimant != enums.MandateClaimantChoices.AGENT:
        raise MandateAgentMismatch("This mandate names no agent.")
    if client is None or client.organization_id != mandate.organization_id:
        raise MandateAgentMismatch("The caller is not the agent of this mandate.")
    app_identifier = client.release.app.identifier if client.release_id else None
    if app_identifier != mandate.agent_identifier:
        raise MandateAgentMismatch("The caller is not the agent of this mandate.")
    if mandate.agent_device_id is not None and client.node_id != mandate.agent_device_id:
        raise MandateAgentMismatch("This mandate is pinned to a different agent device.")
    if mandate.agent_membership_id is not None and client.membership_id != mandate.agent_membership_id:
        raise MandateAgentMismatch("This mandate is pinned to a different agent operator.")


def assert_live(mandate: models.Mandate) -> None:
    if not mandate.is_live():
        raise MandateNotLive("This mandate has been revoked or has expired.")
    if mandate.parent_id is not None and not mandate.parent.is_live():
        raise MandateNotLive("The mandate this one was issued under has been revoked or has expired.")


def _assert_known_scopes(organization: karakter_models.Organization, pin: MandatePin) -> None:
    if pin.scopes is None:
        return
    known = set(
        karakter_models.Scope.objects.filter(organization=organization, identifier__in=pin.scopes).values_list("identifier", flat=True)
    )
    unknown = sorted(set(pin.scopes) - known)
    if unknown:
        raise UnknownMandateScope(f"Scope(s) {', '.join(unknown)} are not available in organization '{organization.slug}'.")


def _client_ceiling(client: models.Client) -> MandatePin:
    """What an app may hand out as itself: its own scopes and requirements."""
    manifest_requirements = (client.manifest or {}).get("requirements") or []
    return MandatePin.model_validate(
        {
            "scopes": sorted(client.scopes.values_list("identifier", flat=True)),
            "requirements": [{"key": r["key"], "service": r["service"]} for r in manifest_requirements],
        }
    )


def _assert_expiry(expires_at: datetime | None, maximum: timedelta | None, *, required: bool) -> None:
    now = timezone.now()
    if expires_at is None:
        if required:
            raise MandateError("This mandate needs an expiry.")
        return
    if expires_at <= now:
        raise MandateError("The expiry must lie in the future.")
    if maximum is not None and expires_at > now + maximum:
        raise MandateError(f"The expiry may lie at most {maximum.days} days ahead.")


# --------------------------------------------------------------------------- #
# Issuing
# --------------------------------------------------------------------------- #


def _assert_policy(
    issuer: Issuer,
    *,
    grantor: karakter_models.Membership,
    hub: models.Hub,
    pin: MandatePin,
    claimant: Claimant,
    expires_at: datetime | None,
) -> None:
    """Who may issue what. One table for every origin; see the module docstring."""
    match issuer:
        case MemberIssuer(membership=membership):
            if membership.pk != grantor.pk:
                raise MandateError("A member grants mandates as themselves.")
            if pin.is_open and not is_org_admin(membership):
                raise MandateError("Only an owner or admin may issue a mandate for any app; name the app.")
            _assert_expiry(expires_at, None, required=False)

        case ClientIssuer(client=client):
            if client.mandate_id is not None:
                # A mandated app acting as the grantor could otherwise hand the
                # grant onward to an app the grantor never saw.
                raise MandateChaining("A client logged in under a mandate cannot issue mandates.")
            if client.membership_id != grantor.pk:
                raise MandateError("An app issues mandates as its own user.")
            if client.organization_id != hub.organization_id:
                raise MandateError("The hub does not belong to the app's organization.")
            if pin.is_open:
                raise MandateError("An app must name the app a mandate is for.")
            try:
                pin.assert_within(_client_ceiling(client))
            except MandatePinMismatch as e:
                raise MandateExceedsIssuer(f"{e} An app can only hand out what it holds itself.") from e
            if isinstance(claimant, KeyClaimant):
                _assert_expiry(expires_at, APP_KEY_MAX_TTL, required=True)
            else:
                _assert_expiry(expires_at, AGENT_MANDATE_MAX_TTL, required=False)

        case AgentIssuer(parent=parent, client=client):
            if not isinstance(claimant, KeyClaimant):
                raise MandateError("An agent issues keyed mandates only.")
            if parent.parent_id is not None:
                raise MandateError("A mandate issued by an agent cannot itself name an agent.")
            assert_is_agent(parent, client)
            assert_live(parent)
            if grantor.pk != parent.membership_id or hub.pk != parent.hub_id:
                raise MandateError("A child mandate acts as its parent's grantor, in its parent's hub.")
            if pin.device_id is None:
                raise MandateError("A device id is required: it tells the instances an agent runs apart.")
            try:
                pin.assert_within(parent.pin)
            except MandatePinMismatch as e:
                raise MandateExceedsIssuer(str(e)) from e
            if parent.expires_at is not None and (expires_at is None or expires_at > parent.expires_at):
                raise MandateError("A child mandate cannot outlive the mandate it was issued under.")
            _assert_expiry(expires_at, None, required=False)

        case HubIssuer():
            if not isinstance(claimant, KeyClaimant) or claimant.key is None:
                raise MandateError("A hub proposes the key of every client it asks for.")

        case ConfigIssuer():
            if not isinstance(claimant, KeyClaimant) or claimant.key is None:
                raise MandateError("A mandate in the config names its key.")


def issue_mandate(
    *,
    issuer: Issuer,
    grantor: karakter_models.Membership,
    hub: models.Hub,
    pin: MandatePin,
    claimant: Claimant,
    expires_at: datetime | None = None,
    max_clients: int | None = None,
    allow_mesh: bool = False,
    label: str = "",
) -> IssuedMandate:
    """Issue a mandate. Every path that creates one comes through here."""
    organization = grantor.organization
    if hub.organization_id != organization.pk:
        raise MandateError("The hub does not belong to the grantor's organization.")
    if max_clients is not None and max_clients < 1:
        raise MandateError("maxClients must be at least 1.")
    _assert_known_scopes(organization, pin)

    with transaction.atomic():
        if isinstance(issuer, AgentIssuer):
            # Lock the parent so concurrent issues serialize on its budget.
            parent = models.Mandate.objects.select_for_update().select_related("membership__user").get(pk=issuer.parent.pk)
            issuer = AgentIssuer(parent=parent, client=issuer.client)
            if parent.max_clients is not None and parent.children.live().count() >= parent.max_clients:
                raise MandateExhausted("This mandate has no slots left.")

        _assert_policy(issuer, grantor=grantor, hub=hub, pin=pin, claimant=claimant, expires_at=expires_at)

        mandate = models.Mandate(
            membership=grantor,
            organization=organization,
            hub=hub,
            subject_manifest=pin.model_dump(mode="json"),
            max_clients=max_clients,
            allow_mesh=allow_mesh,
            expires_at=expires_at,
            label=label,
        )

        minted: str | None = None
        match claimant:
            case KeyClaimant(key=proposed):
                key = proposed if proposed is not None else secrets.token_urlsafe(32)
                if len(key) < 32:
                    raise MandateError("A mandate key has at least 32 characters.")
                key_hash = hash_key(key)
                if models.Mandate.objects.filter(key_hash=key_hash).exists():
                    raise MandateKeyReused("This key is, or was, the key of another mandate; propose a new one.")
                mandate.claimant = enums.MandateClaimantChoices.KEY
                mandate.key_hash = key_hash
                mandate.key_prefix = key[:6]
                minted = None if proposed is not None else key
            case AgentClaimant(identifier=identifier, device_id=device_id, membership=agent_membership, attestation=attestation):
                if agent_membership is not None and agent_membership.organization_id != organization.pk:
                    raise MandateError("The agent membership does not belong to the grantor's organization.")
                mandate.claimant = enums.MandateClaimantChoices.AGENT
                mandate.agent_identifier = identifier
                mandate.agent_membership = agent_membership
                mandate.attestation = attestation
                if device_id:
                    mandate.agent_device, _ = models.Device.objects.get_or_create(
                        organization=organization, node_id=hash_device_id(device_id, organization)
                    )

        match issuer:
            case MemberIssuer():
                mandate.origin = enums.MandateOriginChoices.MEMBER
            case ClientIssuer(client=client):
                mandate.origin = enums.MandateOriginChoices.CLIENT
                mandate.issuing_client = client
            case AgentIssuer(parent=parent, client=client):
                mandate.origin = enums.MandateOriginChoices.AGENT
                mandate.parent = parent
                mandate.issuing_client = client
            case HubIssuer(request=request):
                mandate.origin = enums.MandateOriginChoices.HUB
                mandate.hub_request = request
            case ConfigIssuer(name=name):
                mandate.origin = enums.MandateOriginChoices.CONFIG
                mandate.config_name = name

        mandate.save()

    logger.info(
        "Mandate %s (%s, %s): membership %s → %s:%s",
        mandate.pk,
        mandate.claimant,
        mandate.origin,
        grantor.pk,
        pin.identifier or "*",
        pin.version or "*",
    )
    return IssuedMandate(mandate=mandate, key=minted)


# --------------------------------------------------------------------------- #
# Revoking
# --------------------------------------------------------------------------- #


def _delete_clients(mandate_ids: list[int]) -> int:
    from authapp.models import OAuth2Token
    from fakts.services.mesh import schedule_reap_for_clients

    clients = models.Client.objects.filter(mandate_id__in=mandate_ids)
    OAuth2Token.objects.filter(client_id__in=clients.values("client_id")).update(revoked=True)
    schedule_reap_for_clients(clients)
    deleted, _ = clients.delete()
    return deleted


@transaction.atomic
def revoke_mandate(mandate: models.Mandate, reason: enums.MandateRevocationChoices = enums.MandateRevocationChoices.REVOKED) -> int:
    """Withdraw a mandate and every child issued under it; delete their clients.

    Locks the mandate, then its children by pk: the same order :func:`issue_mandate`
    takes (the parent only) and the grant takes (one keyed row), so no two of them
    can deadlock. A grant racing the revoke either commits first (its client is
    deleted here) or sees the revocation and is refused.
    """
    root = models.Mandate.objects.select_for_update().get(pk=mandate.pk)
    children = list(models.Mandate.objects.select_for_update().filter(parent=root).order_by("pk"))
    now = timezone.now()
    if root.revoked_at is None:
        root.revoked_at = now
        root.revocation_reason = reason
        root.save(update_fields=["revoked_at", "revocation_reason"])
    for child in children:
        if child.revoked_at is None:
            child.revoked_at = now
            child.revocation_reason = enums.MandateRevocationChoices.PARENT_REVOKED
            child.save(update_fields=["revoked_at", "revocation_reason"])
    deleted = _delete_clients([root.pk, *(c.pk for c in children)])
    logger.info("Mandate %s revoked (%s); %s child mandate(s), %s client(s) deleted", root.pk, reason, len(children), deleted)
    return deleted


def revoke_issued_by(clients, reason: enums.MandateRevocationChoices = enums.MandateRevocationChoices.ISSUER_REVOKED) -> int:
    """Revoke every live mandate the given clients issued (the issuer was rejected or revoked)."""
    revoked = 0
    for mandate in models.Mandate.objects.filter(issuing_client__in=clients, revoked_at__isnull=True).order_by("pk"):
        revoke_mandate(mandate, reason)
        revoked += 1
    return revoked


def revoke_organization_mandates(organization: karakter_models.Organization) -> int:
    """The organization kill switch: every live mandate of it stops working."""
    revoked = 0
    for mandate in models.Mandate.objects.filter(organization=organization, revoked_at__isnull=True, parent__isnull=True).order_by("pk"):
        revoke_mandate(mandate, enums.MandateRevocationChoices.ORGANIZATION_REVOKED)
        revoked += 1
    return revoked


def can_manage(mandate: models.Mandate, membership: karakter_models.Membership | None, client: models.Client | None) -> bool:
    """The grantor, an org admin, the agent of an agent mandate, or the agent that issued a child."""
    if membership is not None and membership.organization_id == mandate.organization_id:
        if membership.pk == mandate.membership_id or is_org_admin(membership):
            return True
    if client is not None and mandate.issuing_client_id == client.pk:
        return True
    candidate = mandate.parent if mandate.parent_id is not None else mandate
    try:
        assert_is_agent(candidate, client)
        return True
    except MandateAgentMismatch:
        return False


# --------------------------------------------------------------------------- #
# The grant
# --------------------------------------------------------------------------- #


def _grant_mesh_key(client: models.Client) -> models.IonscaleAuthKey | None:
    """Best-effort, in a savepoint: without a mesh, or with ionscale failing, the
    client still logs in and falls back to the interactive mesh login."""
    from fakts.services.hubs import enroll_app_on_mesh

    try:
        with transaction.atomic():
            return enroll_app_on_mesh(
                user=client.membership.user,
                organization=client.organization,
                membership=client.membership,
                app=client.release.app,
                device=client.node,
            )
    except Exception:
        logger.warning("Could not mint a mesh key for client %s", client.client_id, exc_info=True)
        return None


def authenticate_key(request: MandateKeyGrantRequest) -> KeyGrant:
    """Exchange a mandate key for the client it logs in as.

    The client is one per (mandate, device): logging in again returns the same row,
    and a new release of the app updates it in place. It is (re-)bound only when it
    is new or its manifest, kind or role changed, so the hourly grant stays cheap.
    """
    from fakts.services.clients import DeviceAuthRequired, bind_client, create_public_client, hash_manifest

    manifest = request.manifest
    kind = request.requested_client_kind.value
    role = request.requested_client_role.value

    with transaction.atomic():
        mandate = (
            models.Mandate.objects.select_for_update(of=("self",))
            .select_related("membership__user", "membership__organization", "organization", "hub", "parent__membership__user")
            .filter(key_hash=hash_key(request.key.get_secret_value()))
            .first()
        )
        if mandate is None:
            raise UnknownMandateKey("Invalid mandate key.")
        assert_live(mandate)

        pin = mandate.pin
        if pin.is_open:
            # An open mandate is fixed to the first app that logs in with it.
            pin = pin.fixed_to(manifest.identifier)
            mandate.subject_manifest = pin.model_dump(mode="json")
        pin.check(manifest)

        organization = mandate.organization
        if organization.require_device_auth and not manifest.device_id:
            raise DeviceAuthRequired("This organization requires device authentication; the client manifest must include a device_id.")
        node = None
        if manifest.device_id:
            node, _ = models.Device.objects.get_or_create(organization=organization, node_id=hash_device_id(manifest.device_id, organization))

        client = mandate.clients.filter(node=node).first()
        if client is None:
            if mandate.max_clients is not None and mandate.clients.count() >= mandate.max_clients:
                raise MandateExhausted("This mandate key has been used on as many devices as it allows.")
            client = create_public_client(kind=kind, role=role, grant_types=MANDATE_KEY_GRANT_TYPE)

        if client.membership_id is None or client.manifest_hash != hash_manifest(manifest) or client.kind != kind or client.role != role:
            client.kind = kind
            client.role = role
            client = bind_client(client, manifest, mandate.membership, hub=mandate.hub, mandate=mandate)

        mandate.last_used_at = timezone.now()
        mandate.save(update_fields=["last_used_at", "subject_manifest"])

        mesh_key = _grant_mesh_key(client) if request.request_auth_key and mandate.allow_mesh else None

    return KeyGrant(client=client, mesh_key=mesh_key)

"""Mandates: a grantor pre-authorizes an agent app to provision a subject app as them.

The lifecycle:

1. ``create_mandate`` — a user (the grantor) approves a subject manifest for an
   agent app. Nothing is provisioned yet.
2. ``provision`` — the agent, unattended, asks for a credential for one instance
   of the subject. It receives a single-use redeem token *issued to the grantor*
   and pinned to the mandate's manifest plus the instance's device id. From there
   the ordinary redeem path runs unchanged (``redeem_token`` →
   ``check_pinned_manifest`` → ``bind_client``); the client is stamped with the
   mandate.
3. ``release_client`` — the agent (or grantor) retires one provisioned client.
4. ``revoke_mandate`` — the grantor (or an org admin) withdraws the mandate; every
   client provisioned under it is deleted, which kills its refresh chain.

Nothing here runs on a timer: expiry and revocation are read at provision and
redeem time.
"""

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from fakts import models
from fakts.base_models import Manifest
from karakter import models as karakter_models
from karakter.hashers import hash_device_id

logger = logging.getLogger(__name__)

# A provisioned token only has to survive until the agent starts the subject, so
# it is short-lived by default; the agent can ask for longer (a slow image pull).
PROVISION_TOKEN_TTL = timedelta(hours=1)
PROVISION_TOKEN_MAX_TTL = timedelta(days=1)


class MandateError(Exception):
    """Base class for refused mandate operations (mapped to a GraphQL error)."""


class MandateNotLive(MandateError):
    """The mandate was revoked or has expired."""


class MandateAgentMismatch(MandateError):
    """The caller is not the agent this mandate names."""


class MandateExhausted(MandateError):
    """The mandate's ``max_clients`` budget is used up."""


class MandateChaining(MandateError):
    """A client that was itself provisioned under a mandate may not grant mandates."""


class UnknownMandateScope(MandateError):
    """The subject manifest asks for a scope the organization does not define."""


def subject_pin(manifest: Manifest) -> dict:
    """The part of a subject manifest a mandate fixes: identity plus the ceilings.

    ``device_id`` is deliberately dropped — it is chosen per provisioned instance.
    Cosmetic fields are not pinned, for the same reason ``check_pinned_manifest``
    ignores them.
    """
    return {
        "identifier": manifest.identifier,
        "version": manifest.version,
        "scopes": sorted(set(manifest.scopes or [])),
        "requirements": [r.model_dump(mode="json") for r in manifest.requirements or []],
    }


def is_org_admin(membership: karakter_models.Membership) -> bool:
    return membership.roles.filter(identifier="admin").exists()


@transaction.atomic
def create_mandate(
    *,
    grantor: karakter_models.Membership,
    hub: models.Hub,
    agent_identifier: str,
    subject: Manifest,
    granting_client: models.Client | None = None,
    attestation: str = "",
    agent_device_id: str | None = None,
    agent_membership: karakter_models.Membership | None = None,
    max_clients: int | None = None,
    expires_at=None,
) -> models.Mandate:
    """Record a grantor's standing approval of ``subject`` for ``agent_identifier``."""
    organization = grantor.organization
    if hub.organization_id != organization.id:
        raise MandateError("The hub does not belong to the grantor's organization.")
    if agent_membership is not None and agent_membership.organization_id != organization.id:
        raise MandateError("The agent membership does not belong to the grantor's organization.")
    if granting_client is not None and granting_client.mandate_id is not None:
        # A mandated subject acting as the grantor could otherwise hand the grant
        # onward to an app the grantor never saw.
        raise MandateChaining("A client provisioned under a mandate cannot grant mandates.")
    if max_clients is not None and max_clients < 1:
        raise MandateError("maxClients must be at least 1.")

    known = set(
        karakter_models.Scope.objects.filter(organization=organization, identifier__in=subject.scopes or []).values_list(
            "identifier", flat=True
        )
    )
    unknown = sorted(set(subject.scopes or []) - known)
    if unknown:
        raise UnknownMandateScope(f"Scope(s) {', '.join(unknown)} are not available in organization '{organization.slug}'.")

    agent_device = None
    if agent_device_id:
        agent_device, _ = models.Device.objects.get_or_create(
            organization=organization, node_id=hash_device_id(agent_device_id, organization)
        )

    mandate = models.Mandate.objects.create(
        membership=grantor,
        organization=organization,
        hub=hub,
        agent_identifier=agent_identifier,
        agent_device=agent_device,
        agent_membership=agent_membership,
        subject_manifest=subject_pin(subject),
        attestation=attestation or "",
        max_clients=max_clients,
        expires_at=expires_at,
    )
    logger.info(
        "Mandate %s: membership %s lets %s provision %s:%s",
        mandate.pk,
        grantor.pk,
        agent_identifier,
        subject.identifier,
        subject.version,
    )
    return mandate


def assert_is_agent(mandate: models.Mandate, client: models.Client | None) -> None:
    """Refuse unless ``client`` is (a narrowed-enough instance of) the mandate's agent."""
    if client is None or client.organization_id != mandate.organization_id:
        raise MandateAgentMismatch("The caller is not the agent of this mandate.")
    app_identifier = client.release.app.identifier if client.release_id else None
    if app_identifier != mandate.agent_identifier:
        raise MandateAgentMismatch("The caller is not the agent of this mandate.")
    if mandate.agent_device_id is not None and client.node_id != mandate.agent_device_id:
        raise MandateAgentMismatch("This mandate is pinned to a different agent device.")
    if mandate.agent_membership_id is not None and client.membership_id != mandate.agent_membership_id:
        raise MandateAgentMismatch("This mandate is pinned to a different agent operator.")


def _slots_in_use(mandate: models.Mandate) -> int:
    """Live clients plus tokens that could still become one."""
    outstanding = mandate.redeem_tokens.filter(client__isnull=True).exclude(expires_at__lte=timezone.now()).count()
    return mandate.clients.count() + outstanding


def provision(
    *,
    mandate_id,
    agent: models.Client | None,
    device_id: str,
    ttl: timedelta | None = None,
) -> models.RedeemToken:
    """Mint a single-use redeem token for one instance of the mandate's subject.

    The token is issued to the grantor on the mandate's hub and pinned to the
    subject manifest plus ``device_id``. The device id must be unique per instance:
    a client's identity is (release, membership, node, hub), so two instances on one
    device id would rotate each other out.
    """
    if not device_id:
        raise MandateError("A device id is required: it tells provisioned instances apart.")
    ttl = ttl or PROVISION_TOKEN_TTL
    if ttl <= timedelta(0) or ttl > PROVISION_TOKEN_MAX_TTL:
        raise MandateError(f"The token lifetime must be positive and at most {PROVISION_TOKEN_MAX_TTL}.")

    with transaction.atomic():
        # Lock the mandate so concurrent provisions serialize on the budget check.
        mandate = models.Mandate.objects.select_for_update().select_related("membership", "hub").get(pk=mandate_id)
        assert_is_agent(mandate, agent)
        if not mandate.is_live():
            raise MandateNotLive("This mandate has been revoked or has expired.")
        if mandate.max_clients is not None and _slots_in_use(mandate) >= mandate.max_clients:
            raise MandateExhausted("This mandate has no client slots left.")

        token = models.RedeemToken.objects.create(
            token=uuid.uuid4().hex,
            user=mandate.membership.user,
            hub=mandate.hub,
            mandate=mandate,
            expires_at=timezone.now() + ttl,
            max_redemptions=1,
            pinned_manifest={**mandate.subject_manifest, "device_id": device_id},
        )

    logger.info("Mandate %s: provisioned token %s for device %s", mandate.pk, token.pk, device_id)
    return token


def assert_redeemable(token: models.RedeemToken) -> None:
    """Called by the redeem path: a token outlives neither its mandate's revocation nor its expiry."""
    if token.mandate_id is not None and not token.mandate.is_live():
        raise MandateNotLive("The mandate this token was provisioned under has been revoked or has expired.")


def can_manage(mandate: models.Mandate, membership: karakter_models.Membership | None, client: models.Client | None) -> bool:
    """The grantor, an org admin, or the agent may see and retire a mandate's clients."""
    if membership is not None and membership.organization_id == mandate.organization_id:
        if membership.pk == mandate.membership_id or is_org_admin(membership):
            return True
    try:
        assert_is_agent(mandate, client)
        return True
    except MandateAgentMismatch:
        return False


def release_client(client: models.Client) -> None:
    """Retire one provisioned client (and its refresh chain), freeing its slot."""
    logger.info("Mandate %s: released client %s", client.mandate_id, client.client_id)
    client.delete()


@transaction.atomic
def revoke_mandate(mandate: models.Mandate) -> int:
    """Withdraw a mandate: no further provisioning, every provisioned client deleted."""
    mandate = models.Mandate.objects.select_for_update().get(pk=mandate.pk)
    if mandate.revoked_at is None:
        mandate.revoked_at = timezone.now()
        mandate.save(update_fields=["revoked_at"])
    deleted, _ = models.Client.objects.filter(mandate=mandate).delete()
    mandate.redeem_tokens.filter(client__isnull=True).delete()
    logger.info("Mandate %s revoked; deleted %s client row(s)", mandate.pk, deleted)
    return deleted

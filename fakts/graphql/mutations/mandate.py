"""GraphQL surface for mandates (see :mod:`fakts.services.mandates`)."""

import logging
from datetime import timedelta
from typing import Optional

import strawberry
from django.utils import timezone
from graphql import GraphQLError
from kante.types import Info

from fakts import inputs, models, types
from fakts.graphql.mutations.redeem_token import _pinned_manifest
from fakts.services import mandates as services
from karakter import models as karakter_models
from karakter.authz import DENIED, get_or_denied, get_organization, get_scoped_or_denied, get_user

logger = logging.getLogger(__name__)

MANDATE_MAX_TTL_DAYS = 365


def caller_membership(info: Info) -> karakter_models.Membership:
    """The caller's membership in their active organization, or fail closed."""
    membership = getattr(info.context.request, "membership", None)
    if membership is not None:
        return membership
    return get_or_denied(karakter_models.Membership.objects, user=get_user(info), organization=get_organization(info))


def caller_client(info: Info) -> models.Client | None:
    return getattr(info.context.request, "client", None)


@strawberry.input(description="Pre-authorize an agent app to provision clients of a subject app that act as you.")
class CreateMandateInput:
    agent: str = strawberry.field(description="Identifier of the app allowed to provision (e.g. a deployer).")
    manifest: inputs.ManifestInput = strawberry.field(description="The subject app. Its scopes and requirements are the ceiling for every provisioned client; deviceId is ignored.")
    hub: Optional[strawberry.ID] = strawberry.field(default=None, description="The hub provisioned clients compose against. Defaults to the calling client's hub.")
    attestation: Optional[str] = strawberry.field(default=None, description="Opaque binding for the approving service (e.g. a release digest).")
    agent_device_id: Optional[str] = strawberry.field(default=None, description="Only an agent running on this device may provision.")
    agent_user: Optional[strawberry.ID] = strawberry.field(default=None, description="Only an agent acting as this user may provision.")
    max_clients: Optional[int] = strawberry.field(default=None, description="How many clients may exist under the mandate at once.")
    expires_in_days: Optional[int] = strawberry.field(default=None, description=f"Stop new provisioning after this many days (1–{MANDATE_MAX_TTL_DAYS}). Null means until revoked.")


@strawberry.input(description="Mint a single-use credential for one instance of a mandate's subject.")
class ProvisionInput:
    mandate: strawberry.ID
    device_id: str = strawberry.field(description="Unique per provisioned instance: a client's identity includes its device, so instances sharing one would replace each other.")
    ttl_minutes: Optional[int] = strawberry.field(default=None, description="How long the token stays redeemable (default 60, at most 1440).")


@strawberry.input
class ReleaseMandateClientInput:
    client_id: str = strawberry.field(description="The OAuth client id of a client provisioned under a mandate.")


@strawberry.input
class RevokeMandateInput:
    id: strawberry.ID


def create_mandate(info: Info, input: CreateMandateInput) -> types.Mandate:
    """Grant a mandate as the calling user, in their active organization."""
    grantor = caller_membership(info)
    client = caller_client(info)

    if input.hub is not None:
        hub = get_scoped_or_denied(models.Hub.objects, info, id=input.hub)
    else:
        hub = getattr(client, "hub", None)
        if hub is None:
            raise GraphQLError("No hub given and the calling client composes against none.")

    agent_membership = None
    if input.agent_user is not None:
        agent_membership = get_or_denied(karakter_models.Membership.objects, user_id=input.agent_user, organization=grantor.organization)

    expires_at = None
    if input.expires_in_days is not None:
        if not 1 <= input.expires_in_days <= MANDATE_MAX_TTL_DAYS:
            raise GraphQLError(f"expiresInDays must be between 1 and {MANDATE_MAX_TTL_DAYS}.")
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)

    try:
        return services.create_mandate(
            grantor=grantor,
            hub=hub,
            agent_identifier=input.agent,
            subject=_pinned_manifest(input.manifest),
            granting_client=client,
            attestation=input.attestation or "",
            agent_device_id=input.agent_device_id,
            agent_membership=agent_membership,
            max_clients=input.max_clients,
            expires_at=expires_at,
        )
    except services.MandateError as e:
        raise GraphQLError(str(e)) from e


def provision(info: Info, input: ProvisionInput) -> types.RedeemToken:
    """Called by the agent: a pinned, single-use redeem token issued to the grantor."""
    get_user(info)
    mandate = get_scoped_or_denied(models.Mandate.objects, info, id=input.mandate)
    ttl = timedelta(minutes=input.ttl_minutes) if input.ttl_minutes is not None else None
    try:
        return services.provision(mandate_id=mandate.pk, agent=caller_client(info), device_id=input.device_id, ttl=ttl)
    except services.MandateAgentMismatch:
        # Same answer as a missing mandate: the agent check must not be an oracle.
        raise GraphQLError(DENIED)
    except services.MandateError as e:
        raise GraphQLError(str(e)) from e


def _managed_mandate(info: Info, mandate: models.Mandate) -> models.Mandate:
    membership = caller_membership(info)
    if not services.can_manage(mandate, membership, caller_client(info)):
        raise GraphQLError(DENIED)
    return mandate


def release_mandate_client(info: Info, input: ReleaseMandateClientInput) -> str:
    """Retire one provisioned client; callable by the agent, the grantor or an org admin."""
    client = get_scoped_or_denied(models.Client.objects.filter(mandate__isnull=False), info, client_id=input.client_id)
    _managed_mandate(info, client.mandate)
    services.release_client(client)
    return input.client_id


def revoke_mandate(info: Info, input: RevokeMandateInput) -> types.Mandate:
    """Withdraw a mandate (grantor or org admin): every client provisioned under it is deleted."""
    mandate = get_scoped_or_denied(models.Mandate.objects, info, id=input.id)
    membership = caller_membership(info)
    if mandate.membership_id != membership.pk and not services.is_org_admin(membership):
        raise GraphQLError(DENIED)
    services.revoke_mandate(mandate)
    mandate.refresh_from_db()
    return mandate


def mandate(info: Info, id: strawberry.ID) -> types.Mandate:
    """One mandate, visible to its grantor, org admins and its agent."""
    return _managed_mandate(info, get_scoped_or_denied(models.Mandate.objects, info, id=id))


def mandate_token(info: Info, id: strawberry.ID) -> types.RedeemToken:
    """A token provisioned under a mandate, for the agent to see which client it produced."""
    token = get_scoped_or_denied(models.RedeemToken.objects.filter(mandate__isnull=False), info, field="hub__organization", id=id)
    _managed_mandate(info, token.mandate)
    return token

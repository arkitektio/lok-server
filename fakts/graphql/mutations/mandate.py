"""GraphQL surface for mandates on the app-facing schema (see :mod:`fakts.services.mandates`).

Callers here are apps, authenticated by their access token. What an app hands out
is capped at what it holds itself, and dies with it.
"""

import logging
from datetime import timedelta
from typing import Optional

import strawberry
from django.utils import timezone
from graphql import GraphQLError
from kante.types import Info

from fakts import inputs, models, types
from fakts.base_models import MandatePin, Manifest
from fakts.services import mandates as services
from karakter import models as karakter_models
from karakter.authz import DENIED, get_or_denied, get_organization, get_scoped_or_denied, get_user

logger = logging.getLogger(__name__)


def caller_membership(info: Info) -> karakter_models.Membership:
    """The caller's membership in their active organization, or fail closed."""
    membership = getattr(info.context.request, "membership", None)
    if membership is not None:
        return membership
    return get_or_denied(karakter_models.Membership.objects, user=get_user(info), organization=get_organization(info))


def caller_client(info: Info) -> models.Client:
    client = getattr(info.context.request, "client", None)
    if client is None:
        raise GraphQLError("Only an app can do this.")
    return client


def _manifest(manifest: inputs.ManifestInput) -> Manifest:
    """Assemble a Manifest from the input.

    Built by hand rather than via ``input.to_pydantic()``: the nested requirement and
    public-source inputs are backed by their own pydantic models, which the base
    ``Manifest`` does not accept as instances.
    """
    return Manifest(
        identifier=manifest.identifier,
        version=manifest.version,
        scopes=manifest.scopes or [],
        device_id=manifest.device_id or manifest.node_id,
        requirements=[strawberry.asdict(x) for x in manifest.requirements],
        public_sources=[strawberry.asdict(x) for x in manifest.public_sources] if manifest.public_sources else [],
    )


def _expires_at(days: int | None, maximum: timedelta) -> timezone.datetime | None:
    if days is None:
        return None
    if not 1 <= days <= maximum.days:
        raise GraphQLError(f"expiresInDays must be between 1 and {maximum.days}.")
    return timezone.now() + timedelta(days=days)


def _hub(info: Info, hub_id: strawberry.ID | None, client: models.Client) -> models.Hub:
    if hub_id is not None:
        return get_scoped_or_denied(models.Hub.objects, info, id=hub_id)
    if client.hub is None:
        raise GraphQLError("No hub given and the calling client composes against none.")
    return client.hub


@strawberry.input(description="Pre-authorize an agent app to issue mandate keys for a subject app, acting as you.")
class CreateMandateInput:
    agent: str = strawberry.field(description="Identifier of the app allowed to issue keys (e.g. a deployer).")
    manifest: inputs.ManifestInput = strawberry.field(
        description="The subject app. Its scopes and requirements are the ceiling for every key issued under it, and may not exceed your app's own; deviceId is ignored."
    )
    hub: Optional[strawberry.ID] = strawberry.field(default=None, description="The hub the subject composes against. Defaults to the calling client's hub.")
    attestation: Optional[str] = strawberry.field(default=None, description="Opaque binding for the approving service (e.g. a release digest).")
    agent_device_id: Optional[str] = strawberry.field(default=None, description="Only an agent running on this device may issue.")
    agent_user: Optional[strawberry.ID] = strawberry.field(default=None, description="Only an agent acting as this user may issue.")
    max_clients: Optional[int] = strawberry.field(default=None, description="How many live keys may exist under the mandate at once.")
    expires_in_days: Optional[int] = strawberry.field(
        default=None, description=f"The mandate (and every key issued under it) stops working after this many days (1–{services.AGENT_MANDATE_MAX_TTL.days}). Null: as long as your app."
    )


@strawberry.input(description="Issue a mandate key for an app, acting as you. It is capped at your app's own scopes and dies with your app.")
class IssueMandateKeyInput:
    manifest: inputs.ManifestInput = strawberry.field(
        description="The app the key logs in. Identifier, version and deviceId (when given) must match exactly; scopes and requirements are a ceiling."
    )
    expires_in_days: int = strawberry.field(description=f"The key stops working after this many days (1–{services.APP_KEY_MAX_TTL.days}).")
    hub: Optional[strawberry.ID] = strawberry.field(default=None, description="The hub the app composes against. Defaults to the calling client's hub.")
    any_version: bool = strawberry.field(default=False, description="Let every release of the app log in with the key, not only the manifest's version.")
    max_clients: Optional[int] = strawberry.field(default=None, description="On how many devices the key may log in. Null: unlimited.")
    allow_mesh: bool = strawberry.field(default=False, description="Whether a client logging in with the key may ask for a mesh key.")
    label: Optional[str] = None


@strawberry.input(description="Issue a mandate key for one instance of a mandate's subject (called by the mandate's agent).")
class IssueChildMandateKeyInput:
    mandate: strawberry.ID
    device_id: str = strawberry.field(description="Unique per instance: one key logs in one client per device.")
    expires_in_days: Optional[int] = strawberry.field(default=None, description="The key stops working after this many days. Null: as long as the mandate.")
    label: Optional[str] = None


@strawberry.input
class RevokeMandateInput:
    id: strawberry.ID


def create_mandate(info: Info, input: CreateMandateInput) -> types.Mandate:
    """Grant an agent mandate as the calling app's user, in their active organization."""
    grantor = caller_membership(info)
    client = caller_client(info)
    hub = _hub(info, input.hub, client)

    agent_membership = None
    if input.agent_user is not None:
        agent_membership = get_or_denied(karakter_models.Membership.objects, user_id=input.agent_user, organization=grantor.organization)

    try:
        return services.issue_mandate(
            issuer=services.ClientIssuer(client=client),
            grantor=grantor,
            hub=hub,
            pin=MandatePin.from_manifest(_manifest(input.manifest)),
            claimant=services.AgentClaimant(
                identifier=input.agent,
                device_id=input.agent_device_id,
                membership=agent_membership,
                attestation=input.attestation or "",
            ),
            expires_at=_expires_at(input.expires_in_days, services.AGENT_MANDATE_MAX_TTL),
            max_clients=input.max_clients,
        ).mandate
    except services.MandateError as e:
        raise GraphQLError(str(e)) from e


def issue_mandate_key(info: Info, input: IssueMandateKeyInput) -> types.IssuedMandateKey:
    """Issue a keyed mandate as the calling app's user. The key is shown here once."""
    grantor = caller_membership(info)
    client = caller_client(info)
    hub = _hub(info, input.hub, client)
    manifest = _manifest(input.manifest)

    try:
        issued = services.issue_mandate(
            issuer=services.ClientIssuer(client=client),
            grantor=grantor,
            hub=hub,
            pin=MandatePin.from_manifest(manifest, version=not input.any_version, device=True),
            claimant=services.KeyClaimant(),
            expires_at=_expires_at(input.expires_in_days, services.APP_KEY_MAX_TTL),
            max_clients=input.max_clients,
            allow_mesh=input.allow_mesh,
            label=input.label or "",
        )
    except services.MandateError as e:
        raise GraphQLError(str(e)) from e
    return types.IssuedMandateKey(mandate=issued.mandate, key=issued.key or "")


def issue_child_mandate_key(info: Info, input: IssueChildMandateKeyInput) -> types.IssuedMandateKey:
    """Called by the agent: a key for one instance of the subject, acting as the grantor."""
    get_user(info)
    parent = get_scoped_or_denied(models.Mandate.objects, info, id=input.mandate)
    client = caller_client(info)
    expires_at = None
    if input.expires_in_days is not None:
        if input.expires_in_days < 1:
            raise GraphQLError("expiresInDays must be at least 1.")
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)
    elif parent.expires_at is not None:
        expires_at = parent.expires_at

    try:
        issued = services.issue_mandate(
            issuer=services.AgentIssuer(parent=parent, client=client),
            grantor=parent.membership,
            hub=parent.hub,
            pin=parent.pin.narrowed_to_device(input.device_id),
            claimant=services.KeyClaimant(),
            expires_at=expires_at,
            allow_mesh=parent.allow_mesh,
            label=input.label or "",
        )
    except services.MandateAgentMismatch:
        # Same answer as a missing mandate: the agent check must not be an oracle.
        raise GraphQLError(DENIED)
    except services.MandateError as e:
        raise GraphQLError(str(e)) from e
    return types.IssuedMandateKey(mandate=issued.mandate, key=issued.key or "")


def _managed_mandate(info: Info, mandate: models.Mandate) -> models.Mandate:
    membership = caller_membership(info)
    if not services.can_manage(mandate, membership, getattr(info.context.request, "client", None)):
        raise GraphQLError(DENIED)
    return mandate


def revoke_mandate(info: Info, input: RevokeMandateInput) -> types.Mandate:
    """Withdraw a mandate (its grantor, an org admin, its issuing app, or the agent it was issued by).

    Every mandate issued under it stops working too, and their clients are deleted."""
    mandate = _managed_mandate(info, get_scoped_or_denied(models.Mandate.objects, info, id=input.id))
    services.revoke_mandate(mandate)
    mandate.refresh_from_db()
    return mandate


def mandate(info: Info, id: strawberry.ID) -> types.Mandate:
    """One mandate, visible to its grantor, org admins, its issuing app and its agent."""
    return _managed_mandate(info, get_scoped_or_denied(models.Mandate.objects, info, id=id))

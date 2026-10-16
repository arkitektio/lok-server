"""Mandates through kontrol: a member hands out a key that logs an app in as them."""

from datetime import timedelta

import kante
import strawberry
from django.utils import timezone
from graphql import GraphQLError
from kante import Info

import api.management.types as types
from api.management.authz import DENIED, assert_member, get_or_denied, get_user, is_owner_or_admin
from fakts import models as fakts_models
from fakts.base_models import MandatePin, RequirementPin
from fakts.services import mandates as services
from karakter import models


@kante.input
class ManagementRequirementPinInput:
    key: str
    service: str


@kante.input
class ManagementMandatePinInput:
    """The app a key admits. A field left out is open."""

    identifier: str
    version: str | None = strawberry.field(default=None, description="Pin one release. Left out: every release.")
    device_id: str | None = strawberry.field(default=None, description="Pin one device. Left out: any device.")
    scopes: list[str] | None = strawberry.field(default=None, description="The most scopes the app may ask for. Left out: the organization's.")
    requirements: list[ManagementRequirementPinInput] | None = strawberry.field(default=None, description="The most requirements the app may ask for. Left out: any.")


@kante.input
class IssueMandateKeyInput:
    """Input for issuing a mandate key that logs an app into a hub as you."""

    hub: strawberry.ID
    app: ManagementMandatePinInput | None = strawberry.field(
        default=None, description="The app the key admits. Left out (owners and admins only): the first app that logs in with it."
    )
    expires_in_days: int | None = strawberry.field(default=None, description="The key stops working after this many days. Left out: until revoked.")
    max_clients: int | None = strawberry.field(default=None, description="On how many devices the key may log in. Left out: unlimited.")
    allow_mesh: bool = strawberry.field(default=False, description="Whether a client logging in with the key may ask for a mesh key.")
    label: str | None = None


@kante.input
class RevokeMandateInput:
    id: strawberry.ID


def _pin(app: ManagementMandatePinInput | None) -> MandatePin:
    if app is None:
        return MandatePin()
    return MandatePin(
        identifier=app.identifier,
        version=app.version,
        device_id=app.device_id,
        scopes=sorted(set(app.scopes)) if app.scopes is not None else None,
        requirements=[RequirementPin(key=r.key, service=r.service) for r in app.requirements] if app.requirements is not None else None,
    )


def issue_mandate_key(info: Info, input: IssueMandateKeyInput) -> types.ManagementIssuedMandateKey:
    """Issue a mandate key for a hub, acting as you. The key is shown here once; it
    stops working when you leave the organization or it is revoked."""
    hub = get_or_denied(fakts_models.Hub.objects.select_related("organization"), id=input.hub)
    assert_member(info, hub.organization)
    membership = models.Membership.objects.select_related("organization").get(user=get_user(info), organization=hub.organization)

    expires_at = None
    if input.expires_in_days is not None:
        if input.expires_in_days < 1:
            raise GraphQLError("expiresInDays must be at least 1.")
        expires_at = timezone.now() + timedelta(days=input.expires_in_days)

    try:
        issued = services.issue_mandate(
            issuer=services.MemberIssuer(membership=membership),
            grantor=membership,
            hub=hub,
            pin=_pin(input.app),
            claimant=services.KeyClaimant(),
            expires_at=expires_at,
            max_clients=input.max_clients,
            allow_mesh=input.allow_mesh,
            label=input.label or "",
        )
    except services.MandateError as e:
        raise GraphQLError(str(e)) from e
    return types.ManagementIssuedMandateKey(mandate=issued.mandate, key=issued.key or "")


def revoke_mandate(info: Info, input: RevokeMandateInput) -> types.ManagementMandate:
    """Withdraw a mandate (its grantor, or the organization's owner/admins). Every
    mandate issued under it stops working too, and their clients are deleted."""
    mandate = get_or_denied(fakts_models.Mandate.objects.select_related("organization", "membership"), id=input.id)
    user = get_user(info)
    if mandate.membership.user_id != user.id and not is_owner_or_admin(user, mandate.organization):
        raise GraphQLError(DENIED)
    services.revoke_mandate(mandate)
    mandate.refresh_from_db()
    return mandate

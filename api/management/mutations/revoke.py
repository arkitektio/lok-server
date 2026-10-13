"""Session revocation mutations.

Complement the RFC 7009 ``/o/revoke/`` endpoint (which a client uses on its own
tokens) with the human side: reject one app installation, or revoke every
client and hub of an organization. Revoking sets ``OAuth2Token.revoked``,
which kills the refresh chains immediately (the refresh grant is DB-backed);
outstanding JWT access tokens age out within an hour.
"""

import strawberry
from kante import Info

import kante
from api.management import types
from graphql import GraphQLError

from api.management.authz import DENIED, assert_owner_or_admin, get_or_denied, get_user, is_owner_or_admin
from authapp.models import OAuth2Token
from fakts import models as fakts_models
from fakts.services.mesh import reject_app_installation, schedule_reap_for_clients
from karakter import models


@kante.input
class RevokeClientSessionsInput:
    """Input for revoking every issued token of one client."""

    client: strawberry.ID


def revoke_client_sessions(info: Info, input: RevokeClientSessionsInput) -> types.ManagementClient:
    """Reject a client: revoke all tokens of its installation (the same app of the
    same member on the same device) and remove its mesh sidecar. The app
    re-authorizes through a fresh device-code approval. Allowed to the member
    who approved the client, and to the organization's owner/admins."""
    client = get_or_denied(fakts_models.Client.objects.select_related("organization", "membership", "release"), id=input.client)

    user = get_user(info)
    granted_it = client.membership_id is not None and client.membership.user_id == user.id
    if not granted_it and not is_owner_or_admin(user, client.organization):
        raise GraphQLError(DENIED)

    reject_app_installation(client)

    return client


@kante.input
class RevokeOrganizationSessionsInput:
    """Input for revoking every issued token across an organization."""

    organization: strawberry.ID


def revoke_organization_sessions(info: Info, input: RevokeOrganizationSessionsInput) -> int:
    """Revoke all tokens of every client and hub in an organization (the
    org-level kill switch, e.g. after an incident). Owner/admin only. Returns
    the number of tokens revoked."""
    organization = get_or_denied(models.Organization.objects, id=input.organization)

    assert_owner_or_admin(info, organization)

    # Every client in the org — app clients and hub identities alike carry the
    # organization FK on the unified model.
    client_ids = list(
        fakts_models.Client.objects.filter(organization=organization).values_list("client_id", flat=True)
    )

    revoked = OAuth2Token.objects.filter(client_id__in=client_ids, revoked=False).update(revoked=True)
    schedule_reap_for_clients(fakts_models.Client.objects.filter(client_id__in=client_ids))
    return revoked

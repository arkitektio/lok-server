from django.core.exceptions import ObjectDoesNotExist
from kante.types import Info
from karakter import types


def _hub_for_client(client):
    """The hub a client belongs to: an app client's assigned hub, or the hub a
    hub-identity client *is*. None for clients bound to neither (relying parties)."""
    if client is None:
        return None
    if getattr(client, "hub_id", None):
        return client.hub
    try:
        return client.hub_identity
    except ObjectDoesNotExist:
        return None


def mycontext(info: Info) -> types.Context:
    request = info.context.request
    token = request.get_extension("token")

    try:
        client = request.client
    except ValueError:
        client = None

    return types.Context(
        user=request.user,
        organization=request.organization,
        roles=token.roles,
        scope=token.scopes,
        hub=_hub_for_client(client),
    )

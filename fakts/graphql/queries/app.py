import strawberry
from graphql import GraphQLError
from kante.types import Info
from fakts import models, scalars, types
from karakter.authz import DENIED, get_scoped_or_denied


def app(
    info: Info,
    id: strawberry.ID | None = None,
    identifier: scalars.AppIdentifier | None = None,
    client_id: strawberry.ID | None = None,
) -> types.App:
    """An app registration of the caller's organization, by id, identifier or
    via one of its clients' OAuth client_id."""
    if id:
        return get_scoped_or_denied(models.App.objects, info, id=id)

    if identifier:
        # App identifiers are unique *per organization*, so the scope is what
        # makes this a single-object lookup.
        return get_scoped_or_denied(models.App.objects, info, identifier=identifier)

    if client_id:
        client = get_scoped_or_denied(models.Client.objects, info, client_id=client_id)
        if client.release_id is None:
            # Hub identities / relying parties are not bound to an app release.
            raise GraphQLError(DENIED)
        return client.release.app

    raise GraphQLError("Either id, identifier or clientId must be provided")

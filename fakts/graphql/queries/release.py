import strawberry
from graphql import GraphQLError
from kante.types import Info
from fakts import models, scalars, types
from karakter.authz import get_scoped_or_denied


def release(
    info: Info,
    id: strawberry.ID | None = None,
    identifier: scalars.AppIdentifier | None = None,
    version: scalars.Version | None = None,
    client_id: strawberry.ID | None = None,
) -> types.Release:
    """A release of an app registered in the caller's organization.

    Looked up by pk, by the OAuth client_id of one of its clients, or by the
    app identifier + version (unique per organization).
    """
    if id is not None:
        return get_scoped_or_denied(models.Release.objects, info, field="app__organization", id=id)

    if client_id is not None:
        # `Release` has neither an `identifier` nor a `client_id` column (the old
        # lookup raised `FieldError`); the client reaches its release via
        # `Client.release`, i.e. the reverse `clients` relation here.
        return get_scoped_or_denied(
            models.Release.objects.distinct(),
            info,
            field="app__organization",
            clients__client_id=client_id,
        )

    if identifier is not None and version is not None:
        return get_scoped_or_denied(
            models.Release.objects,
            info,
            field="app__organization",
            app__identifier=identifier,
            version=version,
        )

    raise GraphQLError("Either id, clientId, or identifier and version must be provided")

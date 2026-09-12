import strawberry
from graphql import GraphQLError
from kante.types import Info
from fakts import enums, models, types
from karakter.authz import get_organization, get_scoped_or_denied, get_user


def client(
    info: Info, id: strawberry.ID | None = None, client_id: strawberry.ID | None = None
) -> types.Client:
    """A client of the caller's organization, by pk or OAuth client_id."""
    if id:
        return get_scoped_or_denied(models.Client.objects, info, id=id)
    if client_id:
        return get_scoped_or_denied(models.Client.objects, info, client_id=client_id)

    raise GraphQLError("Either id or clientId must be provided")


def my_managed_clients(info: Info, kind: enums.ClientKind) -> list[types.Client]:
    """The caller's own clients of a given kind, in their active organization.

    `Client` has no `tenant` field (the old filter raised `FieldError`); the
    operator is reachable through the client's membership.
    """
    return models.Client.objects.filter(
        membership__user=get_user(info),
        organization=get_organization(info),
        # `ClientKind` is a `str, Enum` built from `strawberry.enum_value(...)`, so
        # `.value` is not the raw string; the member names match the model's choices.
        kind=enums.ClientKindChoices[kind.name],
    )

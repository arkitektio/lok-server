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
        # The id another service knows the client by: its own, or the identity it
        # had under any release it has run (fakts.ClientIdentity).
        from django.db.models import Q

        named = models.Client.objects.filter(
            Q(client_id=client_id) | Q(identities__token_client_id=client_id)
        ).distinct()
        return get_scoped_or_denied(named, info)

    raise GraphQLError("Either id or clientId must be provided")


#: More ids than any page of rows names in one go; a longer list is refused rather than served slowly.
MAX_CLIENT_IDS = 500


def clients_by_client_id(
    info: Info, client_ids: list[strawberry.ID]
) -> list[types.ClientByClientId]:
    """The clients of the caller's organization other services know by these ids, in one query.

    An id is the ``client_id`` claim of a token: the client's own OAuth client id,
    or the identity it had under a release it ran (``fakts.ClientIdentity``). So
    the row's current ``clientId`` may differ from the id asked for, and every
    answer names the id it answers. Ids that match no client of the organization
    are left out.
    """
    if len(client_ids) > MAX_CLIENT_IDS:
        raise GraphQLError(f"Ask for at most {MAX_CLIENT_IDS} client ids at once.")
    from django.db.models import Q

    wanted = {str(i) for i in client_ids}
    rows = (
        models.Client.objects.filter(organization=get_organization(info))
        .filter(Q(client_id__in=wanted) | Q(identities__token_client_id__in=wanted))
        .prefetch_related("identities")
        .distinct()
    )
    by_id: dict[str, models.Client] = {}
    for row in rows:
        for known in [
            row.client_id,
            *(identity.token_client_id for identity in row.identities.all()),
        ]:
            if known in wanted:
                by_id[known] = row
    return [
        types.ClientByClientId(requested_id=strawberry.ID(i), client=by_id[i])
        for i in (str(c) for c in client_ids)
        if i in by_id
    ]


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

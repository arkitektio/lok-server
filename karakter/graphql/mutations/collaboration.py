import strawberry
from django.core.exceptions import PermissionDenied, ValidationError
from graphql import GraphQLError
from kante.types import Info

from karakter import collaborations, models, types
from karakter.authz import DENIED, get_or_denied, get_organization, get_user


@strawberry.input
class CreateCollaborationInput:
    name: str
    description: str | None = None


@strawberry.input
class UpdateCollaborationInput:
    id: strawberry.ID
    name: str | None = None
    description: str | None = None


@strawberry.input
class DeleteCollaborationInput:
    id: strawberry.ID


def _as_graphql(error: PermissionDenied | ValidationError) -> GraphQLError:
    if isinstance(error, ValidationError):
        return GraphQLError(" ".join(error.messages))
    return GraphQLError(DENIED)


def _changeable(info: Info, id: strawberry.ID) -> models.Collaboration:
    return get_or_denied(
        models.Collaboration.objects.select_related("organization"),
        id=id,
        organization=get_organization(info),
    )


def create_collaboration(
    info: Info, input: CreateCollaborationInput
) -> types.Collaboration:
    """Make a collaboration of the active organization: a key every one of its services puts objects in.

    Any full member but a guest may. It is empty until something is put in it at a service
    (`addToCollaboration`, `shareWorkflow`, with its key), and nobody sees it until it is
    granted (`createInvite(collaborations:)`).
    """
    try:
        return collaborations.make(  # type: ignore[return-value]
            get_user(info), get_organization(info), input.name, input.description
        )
    except (PermissionDenied, ValidationError) as e:
        raise _as_graphql(e) from e


def update_collaboration(
    info: Info, input: UpdateCollaborationInput
) -> types.Collaboration:
    """Rename or describe a collaboration. Its maker, and the organization's owner and admins, may."""
    try:
        return collaborations.change(  # type: ignore[return-value]
            get_user(info),
            _changeable(info, input.id),
            name=input.name,
            description=input.description,
        )
    except (PermissionDenied, ValidationError) as e:
        raise _as_graphql(e) from e


def delete_collaboration(info: Info, input: DeleteCollaborationInput) -> strawberry.ID:
    """Delete a collaboration: every grant of it goes, and a guest left with none leaves the organization.

    What was put in it stays in each service, under a key nobody is granted any more.
    Its maker, and the organization's owner and admins, may.
    """
    collaboration = _changeable(info, input.id)
    collaboration_id = collaboration.pk
    try:
        collaborations.delete(get_user(info), collaboration)
    except PermissionDenied as e:
        raise _as_graphql(e) from e
    return strawberry.ID(str(collaboration_id))

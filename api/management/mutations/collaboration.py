import kante
import strawberry
from django.core.exceptions import PermissionDenied, ValidationError
from graphql import GraphQLError
from kante.types import Info

from api.management import types
from api.management.authz import DENIED, get_or_denied
from karakter import collaborations, models


@kante.input
class CreateCollaborationInput:
    organization: strawberry.ID
    name: str
    description: str | None = None


@kante.input
class UpdateCollaborationInput:
    id: strawberry.ID
    name: str | None = None
    description: str | None = None


@kante.input
class DeleteCollaborationInput:
    id: strawberry.ID


def _denied_as_graphql(error: Exception) -> GraphQLError:
    if isinstance(error, ValidationError):
        return GraphQLError(" ".join(error.messages))
    return GraphQLError(str(error) or DENIED)


def create_collaboration(
    info: Info, input: CreateCollaborationInput
) -> types.ManagementCollaboration:
    """Make a collaboration of the organization: a key every one of its services puts objects in.

    Whoever may grant collaborations (any full member but a guest) may make one. It is empty
    until something is put in it at a service (`addToCollaboration`, `shareWorkflow`, with
    its key), and nobody sees it until it is granted (`createInvite(collaborations:)`).
    """
    organization = get_or_denied(models.Organization.objects, id=input.organization)
    try:
        return collaborations.make(  # type: ignore[return-value]
            info.context.request.user, organization, input.name, input.description
        )
    except (PermissionDenied, ValidationError) as e:
        raise _denied_as_graphql(e) from e


def _found(id: strawberry.ID) -> models.Collaboration:
    return get_or_denied(
        models.Collaboration.objects.select_related("organization"), id=id
    )


def update_collaboration(
    info: Info, input: UpdateCollaborationInput
) -> types.ManagementCollaboration:
    """Rename or describe a collaboration. Its maker, and the organization's owner and admins, may."""
    try:
        return collaborations.change(  # type: ignore[return-value]
            info.context.request.user,
            _found(input.id),
            name=input.name,
            description=input.description,
        )
    except PermissionDenied as e:
        raise GraphQLError(DENIED) from e
    except ValidationError as e:
        raise _denied_as_graphql(e) from e


def delete_collaboration(info: Info, input: DeleteCollaborationInput) -> strawberry.ID:
    """Delete a collaboration: every grant of it goes, and a guest left with none leaves the organization.

    What was put in it stays in each service, under a key nobody is granted any more.
    Its maker, and the organization's owner and admins, may.
    """
    collaboration = _found(input.id)
    collaboration_id = collaboration.pk
    try:
        collaborations.delete(info.context.request.user, collaboration)
    except PermissionDenied as e:
        raise GraphQLError(DENIED) from e
    return strawberry.ID(str(collaboration_id))

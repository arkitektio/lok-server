import kante
import strawberry
from graphql import GraphQLError
from kante.types import Info

from api.management import types
from api.management.authz import DENIED, get_or_denied, is_owner_or_admin
from api.management.mutations.invite import may_share
from karakter import models


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


def create_collaboration(
    info: Info, input: CreateCollaborationInput
) -> types.ManagementCollaboration:
    """Make a collaboration of the organization: a key every one of its services puts objects in.

    Whoever may grant collaborations (any full member but a guest) may make one. It is empty
    until something is put in it at a service (`addToCollaboration`, `shareWorkflow`, with
    its key), and nobody sees it until it is granted (`createInvite(collaborations:)`).
    """
    organization = get_or_denied(models.Organization.objects, id=input.organization)
    user = info.context.request.user
    if not may_share(user, organization):
        raise GraphQLError(
            "Only members of the organization can make its collaborations."
        )
    name = input.name.strip()
    if not name:
        raise GraphQLError("A collaboration needs a name.")
    return models.Collaboration.objects.create(  # type: ignore[return-value]
        organization=organization,
        name=name[:200],
        description=input.description,
        created_by=user,
    )


def _changeable(info: Info, id: strawberry.ID) -> models.Collaboration:
    collaboration = get_or_denied(
        models.Collaboration.objects.select_related("organization"), id=id
    )
    user = info.context.request.user
    # Its maker only while they may still make one: not once they left or are a guest.
    organization = collaboration.organization
    if not (
        (collaboration.created_by_id == user.id and may_share(user, organization))
        or is_owner_or_admin(user, organization)
    ):
        raise GraphQLError(DENIED)
    return collaboration


def update_collaboration(
    info: Info, input: UpdateCollaborationInput
) -> types.ManagementCollaboration:
    """Rename or describe a collaboration. Its maker, and the organization's owner and admins, may."""
    collaboration = _changeable(info, input.id)
    if input.name is not None:
        name = input.name.strip()
        if not name:
            raise GraphQLError("A collaboration needs a name.")
        collaboration.name = name[:200]
    if input.description is not None:
        collaboration.description = input.description
    collaboration.save(update_fields=["name", "description"])
    return collaboration  # type: ignore[return-value]


def delete_collaboration(info: Info, input: DeleteCollaborationInput) -> strawberry.ID:
    """Delete a collaboration: every grant of it goes, and a guest left with none leaves the organization.

    What was put in it stays in each service, under a key nobody is granted any more.
    Its maker, and the organization's owner and admins, may.
    """
    collaboration = _changeable(info, input.id)
    collaboration_id = collaboration.pk
    for grant in list(collaboration.grants.all()):
        grant.delete()  # one by one: a guest's last grant takes their membership
    collaboration.delete()
    return strawberry.ID(str(collaboration_id))

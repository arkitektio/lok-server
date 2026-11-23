"""Making, renaming and deleting an organization's collaborations: the rules both schemas share.

A collaboration (``models.Collaboration``) is a name and a key every service of the
organization puts objects in; who may see it is a grant (``karakter.collaboration_grants``).
Any full member but a guest may make one (``may_share``: the same bar as granting); its maker,
while they still may, and the organization's owner and admins may rename or delete it.
"""

from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied, ValidationError

from karakter import models, roles

if TYPE_CHECKING:
    from kante.context import User as Principal


def may_share(user: "Principal", organization: models.Organization) -> bool:
    """Any full member with a role other than `guest` may make and grant collaborations of the organization.

    They can already reach all of it, and a guest's roles in a collaboration are
    cut to the granter's own when a token is minted, so granting a part gives
    nothing they could not do themselves.
    """
    if organization.owner_id == user.id:
        return True
    membership = organization.memberships.filter(
        user=user, collaboration_grants__isnull=True
    ).first()
    if membership is None:
        return False
    return membership.roles.exclude(identifier="guest").exists()


def _name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("A collaboration needs a name.")
    return cleaned[:200]


def make(
    user: "Principal",
    organization: models.Organization,
    name: str,
    description: str | None = None,
) -> models.Collaboration:
    """A new, empty collaboration of ``organization``, made by ``user``."""
    if not may_share(user, organization):
        raise PermissionDenied(
            "Only members of the organization can make its collaborations."
        )
    return models.Collaboration.objects.create(
        organization=organization,
        name=_name(name),
        description=description,
        created_by=user,
    )


def may_change(user: "Principal", collaboration: models.Collaboration) -> bool:
    """Its maker, while they may still make one (not once they left or are a guest), or an owner or admin."""
    organization = collaboration.organization
    return (
        collaboration.created_by_id == user.id and may_share(user, organization)
    ) or roles.is_owner_or_admin(user, organization)


def change(
    user: "Principal",
    collaboration: models.Collaboration,
    name: str | None = None,
    description: str | None = None,
) -> models.Collaboration:
    """Rename or describe ``collaboration``."""
    if not may_change(user, collaboration):
        raise PermissionDenied("You may not change this collaboration.")
    if name is not None:
        collaboration.name = _name(name)
    if description is not None:
        collaboration.description = description
    collaboration.save(update_fields=["name", "description"])
    return collaboration


def delete(user: "Principal", collaboration: models.Collaboration) -> None:
    """Delete ``collaboration``: every grant of it goes, and a guest left with none leaves the organization.

    What was put in it stays in each service, under a key nobody is granted any more.
    """
    if not may_change(user, collaboration):
        raise PermissionDenied("You may not delete this collaboration.")
    for grant in list(collaboration.grants.all()):
        grant.delete()  # one by one: a guest's last grant takes their membership
    collaboration.delete()

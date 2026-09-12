from kante import Info
import strawberry
from api.management import types
from karakter import models
import kante
from django.db import IntegrityError
from graphql import GraphQLError
from api.management.authz import get_or_denied, is_owner


@kante.input
class CreateRoleSetInput:
    """Input for creating a named bundle of roles in an organization"""

    name: str
    organization: strawberry.ID
    roles: list[strawberry.ID] | None = None  # Role IDs to include in the set


def _assert_owner(info: Info, organization: models.Organization) -> None:
    if not is_owner(info.context.request.user, organization):
        raise GraphQLError("You must own the organization to manage role sets")


def _roles_for_org(role_ids: list[strawberry.ID] | None, organization: models.Organization):
    """Resolve role IDs to Role objects, scoped to the organization (ignores foreign ids)."""
    if not role_ids:
        return models.Role.objects.none()
    return models.Role.objects.filter(pk__in=role_ids, organization=organization)


def create_role_set(info: Info, input: CreateRoleSetInput) -> types.ManagementRoleSet:
    """Create a role set: a named bundle of roles that can be applied together."""
    organization = get_or_denied(models.Organization.objects, id=input.organization)
    _assert_owner(info, organization)

    if models.RoleSet.objects.filter(name=input.name, organization=organization).exists():
        raise GraphQLError(f"A role set named '{input.name}' already exists in this organization.")
    try:
        role_set = models.RoleSet.objects.create(name=input.name, organization=organization)
    except IntegrityError:
        raise GraphQLError(f"A role set named '{input.name}' already exists in this organization.")
    role_set.roles.set(_roles_for_org(input.roles, organization))
    return role_set


@kante.input
class UpdateRoleSetInput:
    """Input for updating a role set's name and/or roles"""

    id: strawberry.ID
    name: str | None = None
    roles: list[strawberry.ID] | None = None


def update_role_set(info: Info, input: UpdateRoleSetInput) -> types.ManagementRoleSet:
    """Update a role set's name and/or the roles it bundles."""
    role_set = get_or_denied(models.RoleSet.objects, id=input.id)
    _assert_owner(info, role_set.organization)

    if input.name is not None:
        if (
            models.RoleSet.objects.filter(name=input.name, organization=role_set.organization)
            .exclude(pk=role_set.pk)
            .exists()
        ):
            raise GraphQLError(f"A role set named '{input.name}' already exists in this organization.")
        role_set.name = input.name
        try:
            role_set.save()
        except IntegrityError:
            raise GraphQLError(f"A role set named '{input.name}' already exists in this organization.")
    if input.roles is not None:
        role_set.roles.set(_roles_for_org(input.roles, role_set.organization))
    return role_set


@kante.input
class DeleteRoleSetInput:
    """Input for deleting a role set"""

    id: strawberry.ID


def delete_role_set(info: Info, input: DeleteRoleSetInput) -> strawberry.ID:
    """Delete a role set. The roles themselves are not affected."""
    role_set = get_or_denied(models.RoleSet.objects, id=input.id)
    _assert_owner(info, role_set.organization)
    role_set.delete()
    return input.id

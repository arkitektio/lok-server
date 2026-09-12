import logging

from django.db import IntegrityError, transaction

from .models import Organization, Role, Membership, User, Scope

logger = logging.getLogger(__name__)


def create_role(organization: Organization, identifier: str):
    """
    Create a role for the organization with the given identifier.
    """
    role, _ = Role.objects.update_or_create(identifier=identifier, organization=organization)
    return role


def create_scope(organization: Organization, identifier: str):
    """
    Create a role for the organization with the given identifier.
    """
    role, _ = Scope.objects.update_or_create(identifier=identifier, organization=organization)
    return role


def create_default_roles_for_org(org: Organization):
    for identifier in ["admin", "guest", "user", "bot", "viewer", "editor", "contributor", "manager", "owner", "labeler"]:
        create_role(org, identifier)


def create_default_scopes_for_org(org: Organization):
    for identifier in ["openid", "profile", "email", "roles", "groups"]:
        create_scope(org, identifier)


def ensure_owner_is_admin(org: Organization):
    """
    Ensure that the admin user is added to the admin group of the organization.
    """
    membership, _ = Membership.objects.get_or_create(user=org.owner, organization=org)
    membership.roles.add(Role.objects.get(identifier="admin", organization=org))
    membership.save()


def add_user_roles(user: User, organization: Organization, roles: list[str]):
    """
    Make the given user an admin of the specified organization.
    """
    membership, _ = Membership.objects.update_or_create(
        user=user,
        organization=organization,
    )

    for srole in roles:
        role = Role.objects.get(organization=organization, identifier=srole)
        membership.roles.add(role)

    membership.save()


def create_user_default_organization(user: User):
    """Create a *new* organization for the user upon signup.

    This must never join an organization that already exists. It previously did
    ``get_or_create(slug=f"{user.username}-org")`` and then called
    ``add_user_roles(..., ["admin"])`` *outside* the ``if created`` branch. Slug
    is globally unique and freely chosen by users (``createOrganization`` /
    ``updateOrganization``, with no reserved-name list), so registering the
    username ``acme`` matched an existing organization whose handle was
    ``acme-org`` and silently made the newcomer its admin — a cross-tenant
    takeover with no interaction from the victim.

    The slug is therefore derived collision-free via ``suggest_slug``, and the
    organization is *created*, never fetched. The unique constraint is the
    backstop: a concurrent signup that grabs the slug in between raises
    ``IntegrityError``, which we retry against a freshly suggested slug rather
    than falling back to an existing row.
    """
    # Imported here rather than at module scope: ``slugs`` imports from
    # ``.models``, which imports ``.signals``, which imports this module.
    from karakter import slugs

    base_slug = slugs.slugify_name(f"{user.username}-org") or f"user-{user.pk}-org"

    for _ in range(5):
        slug = base_slug if not slugs.is_slug_taken(base_slug) else slugs.suggest_slug(base_slug)
        try:
            with transaction.atomic():
                org = Organization.objects.create(
                    slug=slug,
                    name=f"{user.username}'s Organization",
                    owner=user,
                )
        except IntegrityError:
            continue
        break
    else:
        logger.error(
            "Could not allocate a default organization slug for user %s; skipping.", user.pk
        )
        return None

    # `ensure_default_roles_for_org` already runs as an Organization post_save,
    # but call it explicitly so this function does not depend on signal ordering.
    create_default_roles_for_org(org)
    add_user_roles(user, org, ["admin"])
    return org

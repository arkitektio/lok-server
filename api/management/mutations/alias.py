from kante import Info
import strawberry
from api.management import types
import kante
from api.management.authz import assert_owner_or_admin, get_or_denied
from fakts import enums as fakts_enums
from fakts import models as fakts_models
from graphql import GraphQLError


def _host_for(kind: str, host: str | None) -> str | None:
    """A mesh alias resolves its own host; every other kind needs one."""
    if kind not in fakts_enums.AliasKindChoices.values:
        raise GraphQLError(f"Unknown alias kind '{kind}' (one of: {', '.join(fakts_enums.AliasKindChoices.values)}).")
    if kind == fakts_enums.AliasKindChoices.MESH.value:
        return None
    if not host:
        raise GraphQLError(f"An alias of kind '{kind}' needs a host.")
    return host


def _public_for(kind: str, public: bool, ssl: bool = True) -> bool:
    """A docker alias is only reachable from inside its hub's docker environment,
    so the coordination server could never health-check it. A public alias is
    probed by lok over the internet, so it must be served over https."""
    if public and kind == fakts_enums.AliasKindChoices.DOCKER.value:
        raise GraphQLError("An alias of kind 'docker' cannot be public.")
    if public and not ssl:
        raise GraphQLError("A public alias must be served over https.")
    return public


@kante.input
class CreateAliasInput:
    """Input for creating an alias for a service instance."""

    instance: strawberry.ID  # Service Instance ID to create the alias for
    port: int
    kind: str
    host: str | None = None
    path: str | None = None
    public: bool = False
    priority: int | None = None
    """Where clients get it, lower first. Unset: its reach's default."""
    enabled: bool = True


def _priority(priority: int | None) -> int | None:
    if priority is not None and not 0 <= priority <= 32767:
        raise GraphQLError("An alias's priority is between 0 and 32767.")
    return priority


def create_alias(info: Info, input: CreateAliasInput) -> types.ManagementInstanceAlias:
    """Create an alias for a service instance."""

    instance = get_or_denied(fakts_models.ServiceInstance.objects, id=input.instance)

    # Aliases are routing entries: whoever can create one for an instance can
    # direct that instance's traffic. Restrict to the owning org's owner/admins.
    assert_owner_or_admin(info, instance.organization)

    alias = fakts_models.InstanceAlias.objects.create(
        instance=instance,
        port=input.port,
        host=_host_for(input.kind, input.host),
        kind=input.kind,
        path=input.path,
        public=_public_for(input.kind, input.public),
        priority=_priority(input.priority),
        enabled=input.enabled,
    )

    return alias


@kante.input
class UpdateAliasInput:
    """Input for updating an existing alias of a service instance."""

    id: strawberry.ID
    port: int
    kind: str
    host: str | None = None
    path: str | None = None
    public: bool | None = None
    priority: int | None = strawberry.UNSET
    """Where clients get it, lower first. Null: back to its reach's default. Left out: unchanged."""
    enabled: bool | None = None


def update_alias(info: Info, input: UpdateAliasInput) -> types.ManagementInstanceAlias:
    """Update an existing alias for a service instance."""

    alias = get_or_denied(fakts_models.InstanceAlias.objects, id=input.id)

    assert_owner_or_admin(info, alias.instance.organization)

    alias.port = input.port
    alias.host = _host_for(input.kind, input.host)
    alias.kind = input.kind
    alias.path = input.path
    if input.public is not None:
        alias.public = input.public
    alias.public = _public_for(input.kind, alias.public, alias.ssl)
    if input.priority is not strawberry.UNSET:
        alias.priority = _priority(input.priority)
    if input.enabled is not None:
        alias.enabled = input.enabled
    alias.save()

    return alias


@kante.input
class DeleteAliasInput:
    """Input for deleting an alias."""

    id: strawberry.ID


def delete_alias(info: Info, input: DeleteAliasInput) -> strawberry.ID:
    """Delete an alias for a service instance, returning the deleted alias id."""
    alias = get_or_denied(fakts_models.InstanceAlias.objects, id=input.id)

    assert_owner_or_admin(info, alias.instance.organization)

    alias.delete()

    return input.id


@kante.input
class CreateAliasRuleInput:
    """Disable or re-rank an alias for some clients. Name exactly one of app, client, user, role."""

    alias: strawberry.ID
    effect: str
    """'disable' or 'priority'."""
    priority: int | None = None
    """The alias's priority for these clients (effect 'priority' only)."""
    app: strawberry.ID | None = None
    client: strawberry.ID | None = None
    user: strawberry.ID | None = None
    role: strawberry.ID | None = None


def create_alias_rule(info: Info, input: CreateAliasRuleInput) -> types.ManagementAliasRule:
    """Disable or re-rank an alias for one client, an app's clients, a user's, or a role's holders'."""
    from karakter import models as karakter_models

    alias = get_or_denied(fakts_models.InstanceAlias.objects, id=input.alias)
    organization = alias.instance.organization
    assert_owner_or_admin(info, organization)

    if input.effect not in fakts_enums.AliasRuleEffectChoices.values:
        raise GraphQLError(f"Unknown effect '{input.effect}' (one of: {', '.join(fakts_enums.AliasRuleEffectChoices.values)}).")
    ranking = input.effect == fakts_enums.AliasRuleEffectChoices.PRIORITY.value
    if ranking != (input.priority is not None):
        raise GraphQLError("A 'priority' rule needs a priority; a 'disable' rule takes none.")

    named = [name for name in ("app", "client", "user", "role") if getattr(input, name) is not None]
    if len(named) != 1:
        raise GraphQLError("A rule names exactly one of app, client, user or role.")

    subject: dict[str, object] = {}
    if input.app is not None:
        subject["app"] = get_or_denied(fakts_models.App.objects, id=input.app)
    elif input.client is not None:
        # Only a client of this organization: a rule cannot reach into another's.
        subject["client"] = get_or_denied(fakts_models.Client.objects, id=input.client, membership__organization=organization)
    elif input.user is not None:
        subject["user"] = get_or_denied(karakter_models.User.objects, id=input.user, memberships__organization=organization)
    else:
        subject["role"] = get_or_denied(karakter_models.Role.objects, id=input.role, organization=organization)

    return fakts_models.AliasRule.objects.create(
        alias=alias,
        effect=input.effect,
        priority=_priority(input.priority),
        **subject,
    )


@kante.input
class DeleteAliasRuleInput:
    """Input for deleting an alias rule."""

    id: strawberry.ID


def delete_alias_rule(info: Info, input: DeleteAliasRuleInput) -> strawberry.ID:
    """Delete an alias rule, returning its id."""
    rule = get_or_denied(fakts_models.AliasRule.objects, id=input.id)
    assert_owner_or_admin(info, rule.alias.instance.organization)
    rule.delete()
    return input.id

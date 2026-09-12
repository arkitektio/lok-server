"""Rendering of fakts envelopes + requirement/instance resolution.

These functions turn clients/hubs into the payloads handed back to apps
(alongside the OAuth2 token response — see ``authapp.fakts_grants``), resolve
which service instance satisfies a requirement, and (re)compose a client's
service-instance mappings from its manifest.
"""

from typing import Dict

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest

from fakts import base_models, errors, models
from fakts.base_models import (
    Alias,
    FaktsEnvelope,
    HubAuthClaim,
    HubClaimAnswer,
    HubClientClaim,
    HubInstanceClaim,
    InstanceClaim,
    SelfClaim,
)
from fakts.services.tokens import hash_requirements


def render_server_fakts(hub: models.Hub, context: base_models.ServerLinkingContext) -> HubClaimAnswer:
    self_claim = SelfClaim(
        deployment_name=context.deployment_name,
        alias=Alias(id="self", host=context.request.host, port=context.request.port, ssl=context.request.is_secure, path="lok", challenge="ht"),
    )

    auth_claim = HubAuthClaim(
        jwks_url=f"{context.request.base_url}/.well-known/jwks.json",
        ionscale_auth_key=hub.auth_key.key if hub.auth_key else None,
        ionscale_coord_url=settings.IONSCALE_COORD_URL,
    )

    instance_claims: Dict[str, HubInstanceClaim] = {}
    client_claims: Dict[str, HubClientClaim] = {}

    for instance in hub.instances.all():
        instance_claims[instance.token] = HubInstanceClaim(
            identifier=instance.token,
            private_key=instance.private_key,
        )

    for client in hub.clients.all():
        client_claims[client.client_id] = HubClientClaim(
            client_id=client.client_id,
        )

    claim = HubClaimAnswer(
        auth=auth_claim,
        self=self_claim,
        instances=instance_claims,
        clients=client_claims,
    )

    return claim


def render_envelope_from_context(client: models.Client, context: base_models.LinkingContext) -> dict:
    """Render the fakts envelope (self + instances + statuses) for a client.

    Auth material is *not* part of the envelope — it travels in the standard
    OAuth2 token-response fields the envelope is appended to.
    """
    self_claim = SelfClaim(
        deployment_name=context.deployment_name,
        alias=Alias(id="self", host=context.request.host, port=context.request.port, ssl=context.request.is_secure, path="lok", challenge="ht"),
    )

    instances_map: Dict[str, InstanceClaim] = {}

    for mapping in client.mappings.all():
        instance: models.ServiceInstance = mapping.instance

        value = instance.render(context)
        instances_map[mapping.key] = value

    envelope = FaktsEnvelope(
        self=self_claim,
        instances=instances_map,
        statuses=client.statuses,
    )

    return envelope.model_dump()


def render_envelope(request: HttpRequest, client: models.Client) -> dict:
    """Render the fakts envelope from an incoming HTTP request (the token
    endpoint's). Aliases stay host-aware: relative aliases resolve against this
    request's host, so every refresh re-renders them for where the client is
    actually connecting from."""
    context = create_linking_context(request, client)
    return render_envelope_from_context(client, context)


def render_hub_envelope(request: HttpRequest, hub: models.Hub) -> dict:
    """Render a hub's config envelope for the token response (the hub-client
    counterpart of :func:`render_envelope`): `self`, `auth` (jwks + ionscale),
    `instances` (with private keys) and `clients` (by OAuth2 client_id). Hub
    servers refresh hourly and pick up new instances/clients with each
    re-render."""
    context = create_serverlinking_context(request, hub)
    return render_server_fakts(hub, context).model_dump()


def find_instance_for_requirement_and_hub(requirement: base_models.Requirement, user: models.AbstractUser, hub: models.Hub) -> models.ServiceInstance | None:
    instance = (
        models.ServiceInstance.objects.filter(
            release__service__identifier=requirement.service,
            hub=hub,
        )
        .filter(
            models.Q(allowed_users__isnull=True)
            | models.Q(allowed_users=user) & (models.Q(denied_users__isnull=True) | ~models.Q(denied_users=user)) & (models.Q(allowed_groups__isnull=True) | models.Q(allowed_groups__in=user.groups.all())) & (models.Q(denied_groups__isnull=True) | ~models.Q(denied_groups__in=user.groups.all()))
        )
        .first()
    )

    return instance


@transaction.atomic
def auto_compose(client: models.Client, manifest: base_models.Manifest, user: models.AbstractUser, organization: models.Organization, device: models.Device | None = None, declined_requirements: list[str] | None = None) -> models.Client:
    requirements = manifest.requirements

    if not requirements:
        return client

    declined = set(declined_requirements or [])
    statuses: dict[str, str] = {}

    for old_mapping in client.mappings.all():
        old_mapping.delete()

    for req in requirements:
        if req.optional and req.key in declined:
            statuses[req.key] = "denied"
            continue

        try:
            instance = find_instance_for_requirement_and_hub(req, user, hub=client.hub)

            if instance is None:
                raise errors.InstanceNotFound(f"No instance for {req.service} in this hub.")

            models.ServiceInstanceMapping.objects.get_or_create(
                client=client,
                instance=instance,
                key=req.key,
            )
            statuses[req.key] = "granted"

        except Exception as e:
            if req.optional:
                statuses[req.key] = "unavailable"
            else:
                raise Exception(f"Unable to find instance for requirement {req.service}") from e

    client.requirements_hash = hash_requirements(requirements)
    client.statuses = statuses
    client.save()

    return client


def create_linking_context(request: HttpRequest, client: models.Client) -> base_models.LinkingContext:
    host_string = request.get_host().split(":")
    if len(host_string) == 2:
        host = host_string[0]
        port = host_string[1]
    else:
        host = host_string[0]
        port = None

    base_url = request.build_absolute_uri("/") + settings.MY_SCRIPT_NAME

    return base_models.LinkingContext(
        request=base_models.LinkingRequest(
            host=host,
            port=port,
            base_url=base_url,
            is_secure=request.is_secure(),
        ),
        secure=request.is_secure(),
        manifest=base_models.Manifest(
            identifier=client.release.app.identifier,
            version=client.release.version,
            scopes=client.release.scopes,
        ),
        client=base_models.LinkingClient(
            client_id=client.client_id,
            name=client.name,
        ),
    )


def create_serverlinking_context(request: HttpRequest, hub: models.Hub, claim: base_models.ServerClaimRequest | None = None) -> base_models.ServerLinkingContext:
    host_string = request.get_host().split(":")
    if len(host_string) == 2:
        host = host_string[0]
        port = host_string[1]
    else:
        host = host_string[0]
        port = None

    base_url = request.build_absolute_uri("/") + settings.MY_SCRIPT_NAME

    return base_models.ServerLinkingContext(
        request=base_models.LinkingRequest(
            host=host,
            port=port,
            base_url=base_url,
            is_secure=request.is_secure(),
        ),
    )


def create_fake_linking_context(client: models.Client, host, port, secure=False) -> base_models.LinkingContext:
    return base_models.LinkingContext(
        request=base_models.LinkingRequest(
            host=host,
            port=port,
            is_secure=secure,
        ),
        secure=secure,
        manifest=base_models.Manifest(
            identifier=client.release.app.identifier,
            version=client.release.version,
            scopes=client.release.scopes,
        ),
        client=base_models.LinkingClient(
            client_id=client.client_id,
            name=client.name,
        ),
    )

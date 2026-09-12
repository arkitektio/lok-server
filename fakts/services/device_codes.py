"""Device-code flow: staging device codes and validating them into clients."""

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from fakts import base_models, enums, models
from fakts.services.clients import bind_client, create_public_client
from fakts.services.tokens import create_challenge_code, create_device_code
from fakts.utils import download_logo
from karakter import models as karakter_models

logger = logging.getLogger(__name__)

# `expiration_time_seconds` arrives in an *unauthenticated* start request, and was
# applied verbatim — so an anonymous caller could stage a code that stays pending
# (and brute-forceable) indefinitely. Clamp it to a sane ceiling.
MAX_DEVICE_CODE_LIFETIME_SECONDS = 900


def _expires_at(requested_seconds: int):
    """Clamp a caller-supplied lifetime and turn it into an absolute deadline."""
    seconds = max(1, min(int(requested_seconds), MAX_DEVICE_CODE_LIFETIME_SECONDS))
    return timezone.now() + datetime.timedelta(seconds=seconds)


class LogoDownloadError(Exception):
    """Raised when a manifest logo could not be downloaded while staging a device code."""


def start_device_code(start_grant: base_models.DeviceCodeStartRequest) -> models.DeviceCode:
    """Stage a (client) device code from a start request, downloading the logo.

    This is also dynamic client registration: a *public* OAuth2 client is
    minted here so the device can immediately poll the token endpoint
    (grant_type urn:ietf:params:oauth:grant-type:device_code) as that client.
    It stays unusable until a human accepts the code — only then does it gain a
    membership (organization) and scopes. Purging the device code of a
    never-approved registration deletes the orphan OAuth2 client with it (see
    ``purge_expired_device_codes``).
    """
    manifest = start_grant.manifest

    try:
        # Validation only: the logo must be downloadable, but it is re-fetched
        # (and stored) at accept by bind_client.
        download_logo(manifest.logo) if manifest.logo else None
    except Exception as e:
        raise LogoDownloadError(str(e)) from e

    logger.info(f"Received start challenge for {manifest.identifier}:{manifest.version} {start_grant.request_public}")

    # Registration writes the requested attributes straight onto the staged
    # (unbound) client — the staged row IS the client.
    client = create_public_client(
        kind=start_grant.requested_client_kind.value,
        role=start_grant.requested_client_role.value,
        redirect_uris=start_grant.redirect_uris,
        public=start_grant.request_public,
    )

    return models.DeviceCode.objects.create(
        kind=enums.DeviceCodeKindChoices.APP.value,
        code=create_device_code(),
        secret=create_challenge_code(),
        client=client,
        staging_manifest=manifest.model_dump(),
        expires_at=_expires_at(start_grant.expiration_time_seconds),
    )


def purge_expired_device_codes() -> int:
    """Delete expired, never-approved device codes and their orphan OAuth2 clients.

    Approved codes are burned at token issuance; this reaps the ones nobody
    ever accepted so dynamically registered (but unbound) OAuth2 clients don't
    accumulate. Called opportunistically from the authorization endpoints.
    """
    count = 0
    expired = models.DeviceCode.objects.filter(
        expires_at__lt=timezone.now(), client__membership__isnull=True
    ).select_related("client")
    for device_code in expired:
        # Deleting the staged client cascades onto its device code.
        device_code.client.delete()
        count += 1
    return count


def start_hub_device_code(start_grant: base_models.HubStartRequest) -> models.DeviceCode:
    """Stage a hub device code from a start request, downloading the logo."""
    manifest = start_grant.hub

    try:
        logo = download_logo(manifest.logo) if manifest.logo else None  # noqa: F841 (validates logo is reachable)
    except Exception as e:
        raise LogoDownloadError(str(e)) from e

    logger.info(f"Received start challenge for {manifest.identifier}")

    client = create_public_client(kind=enums.ClientKindVanilla.HUB.value)

    return models.DeviceCode.objects.create(
        kind=enums.DeviceCodeKindChoices.HUB.value,
        code=create_device_code(),
        secret=create_challenge_code(),
        client=client,
        staging_manifest=manifest.model_dump(),
        expires_at=_expires_at(start_grant.expiration_time_seconds),
    )


def start_mesh_device_code(start_grant: base_models.MeshDeviceCodeStartRequest) -> models.MeshDeviceCode:
    """Stage a mesh device code from a start request.

    A machine requests to join an organization's mesh; a human authorizer later accepts
    (minting the pre-auth key) via the management GraphQL. ``code`` is the human-visible
    value for the configure URL, ``challenge_code`` is the secret the machine polls with.
    """
    logger.info(f"Received mesh start challenge for machine {start_grant.requested_machine_name!r}")

    return models.MeshDeviceCode.objects.create(
        code=create_device_code(),
        challenge_code=create_challenge_code(),
        requested_machine_name=start_grant.requested_machine_name,
        description=start_grant.description,
        staging_ephemeral=start_grant.ephemeral,
        staging_tags=start_grant.tags,
        expires_at=_expires_at(start_grant.expiration_time_seconds),
    )


@transaction.atomic
def validate_device_code(
    device_code: models.DeviceCode,
    user: models.AbstractUser,
    organization: models.Organization,
    hub: models.Hub,
    device_name: str | None = None,
    declined_requirements: list[str] | None = None,
) -> models.DeviceCode:
    """Approve a device code: bind the staged (registered-at-start) client to
    the approving user's membership in place.

    Re-approval rotates identity: ``bind_client`` deletes any other bound
    client for the same (release, membership, node, hub) — the previous
    installation's client_id and refresh chain die.
    """
    membership = karakter_models.Membership.objects.get(user=user, organization=organization)

    client = bind_client(
        device_code.client,
        device_code.manifest_as_model,
        membership,
        hub=hub,
        declined_requirements=declined_requirements,
        device_name=device_name,
    )

    device_code.organization = organization
    device_code.granted_scope = client.scope
    device_code.save()
    return device_code

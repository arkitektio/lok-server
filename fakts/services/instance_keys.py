"""Service instance keys: the coord vouches for each instance's public key.

A hub's manifest carries, per service instance, ``challenge_key``: the base64 of the instance's
raw 32-byte Ed25519 public key. Konstruktor generated the pair and left the private half with the
instance; lok only ever sees the public half. It is stored on ``ServiceInstance.public_key`` when
the hub is accepted (a re-authorized hub sends a new key: that is the rotation) and published as
the hub's **trust bundle**, a JWKS at ``/.well-known/hub-keys/<hub>``: each key names the service
and instance it belongs to, and its ``kid`` is its RFC 7638 thumbprint, which the instance
computes itself. The hub's services verify each other's signed requests (and rekuest's
provenance tokens, through ``?service=``) against it.
"""

from __future__ import annotations

import base64
import binascii

from joserfc.jwk import OKPKey

from fakts import models


def validate_challenge_key(value: str) -> str:
    """``value`` if it is base64 of exactly 32 bytes (a raw Ed25519 public key), else ``ValueError``."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("challenge_key is not base64") from error
    if len(raw) != 32:
        raise ValueError(f"challenge_key must be a raw 32-byte Ed25519 public key, got {len(raw)} bytes")
    return value


def public_jwk(instance: models.ServiceInstance) -> dict | None:
    """The instance's key as a JWK with its thumbprint ``kid`` and its service, or None without a key."""
    if not instance.public_key:
        return None
    raw = base64.b64decode(instance.public_key)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    key = OKPKey.import_key({"kty": "OKP", "crv": "Ed25519", "x": x})
    return {
        **key.as_dict(private=False),
        "kid": key.thumbprint(),
        "use": "sig",
        "alg": "Ed25519",
        "service": instance.release.service.identifier,
        "instance": str(instance.pk),
        "hub": str(instance.hub_id),
    }


def hub_keys(hub_id: int, service: str | None = None) -> dict:
    """The trust bundle of one hub: every instance key, or only ``service``'s."""
    instances = models.ServiceInstance.objects.filter(hub_id=hub_id, public_key__isnull=False).exclude(public_key="").select_related("release__service").order_by("pk")
    if service:
        instances = instances.filter(release__service__identifier=service)
    return {"keys": [jwk for jwk in (public_jwk(i) for i in instances) if jwk is not None]}

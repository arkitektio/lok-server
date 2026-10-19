"""Service instance keys: what lok is told of each instance's public key.

A hub's manifest carries, per service instance, ``challenge_key``: the base64 of the instance's
raw 32-byte Ed25519 public key. Konstruktor generated the pair and left the private half with the
instance; lok only ever sees the public half. It is stored on ``ServiceInstance.public_key`` when
the hub is authorized (a re-authorized hub sends a new key: that is the rotation) and handed to
apps as the instance's ``challenge_key``, which the alias probe checks.

The hub's services trust each other through the hub's own bundle, which konstruktor writes into
every service's config: lok publishes no keys.
"""

from __future__ import annotations

import base64
import binascii



def validate_challenge_key(value: str) -> str:
    """``value`` if it is base64 of exactly 32 bytes (a raw Ed25519 public key), else ``ValueError``."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("challenge_key is not base64") from error
    if len(raw) != 32:
        raise ValueError(f"challenge_key must be a raw 32-byte Ed25519 public key, got {len(raw)} bytes")
    return value

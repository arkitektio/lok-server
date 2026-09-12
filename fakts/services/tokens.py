"""Small token / hashing helpers used across the fakts services."""

import secrets
from hashlib import sha256
from uuid import uuid4

from fakts import base_models

# Unambiguous alphabet for the human-transcribable code: no 0/O, 1/I/L, or U/V
# confusion when read off a screen and typed into another device.
_DEVICE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ23456789"
_DEVICE_CODE_LENGTH = 10


def create_api_token() -> str:
    """Return a fresh opaque API token (used as a client's fakts token)."""
    return str(uuid4())


def create_device_code() -> str:
    """Return a short, human-transcribable device code.

    This previously read ``"".join([str(uuid4())[-1] for _ in range(8)])`` — which
    generated eight UUIDs and kept only the *last character* of each. A uuid4
    string's last character is a hex digit, so the result was 8 symbols from an
    alphabet of 16: about 2**32, not the 122 bits per uuid4 that the code reads
    like it provides. The poll endpoints have no rate limit and no attempt
    counter, so that was brute-forceable given volume.

    10 symbols from a 29-symbol alphabet is ~2**48, while staying short enough to
    read aloud and retype.
    """
    return "".join(secrets.choice(_DEVICE_CODE_ALPHABET) for _ in range(_DEVICE_CODE_LENGTH))


def create_challenge_code() -> str:
    """Return the machine-to-machine polling secret for a device-code flow.

    Distinct from :func:`create_device_code`: this value is never shown to a
    human, so it carries full entropy rather than being optimised for
    transcription.
    """
    return secrets.token_urlsafe(32)


def hash_requirements(requirements: list[base_models.Requirement]) -> str:
    """Stable hash of a manifest's requirements (order-independent)."""
    return sha256(".".join(sorted([req.service + req.key for req in requirements])).encode()).hexdigest()

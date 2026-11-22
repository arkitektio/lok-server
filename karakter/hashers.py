import hashlib
import hmac
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from karakter.models import Organization


def hash_device_id(raw_device_id: str, organization: "Organization") -> str:
    """Deterministic, non-reversible, per-organization hash of a raw device id.

    Uses HMAC-SHA256 keyed with the global ``SECRET_KEY`` (pepper) combined with the
    organization's ``device_salt``. The same device hashes to different values across
    organizations, and the raw id is never persisted. The result is 64 hex chars.
    """
    key = f"{settings.SECRET_KEY}:{organization.device_salt}".encode()
    return hmac.new(key, raw_device_id.encode(), hashlib.sha256).hexdigest()

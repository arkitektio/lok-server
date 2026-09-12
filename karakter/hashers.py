import hashlib
import hmac

from django.conf import settings




def hash_device_id(raw_device_id: str, organization) -> str:
    """Deterministic, non-reversible, per-organization hash of a raw device id.

    Uses HMAC-SHA256 keyed with the global ``SECRET_KEY`` (pepper) combined with the
    organization's ``device_salt``. The same device hashes to different values across
    organizations, and the raw id is never persisted. The result is 64 hex chars.
    """
    key = f"{settings.SECRET_KEY}:{organization.device_salt}".encode()
    return hmac.new(key, raw_device_id.encode(), hashlib.sha256).hexdigest()

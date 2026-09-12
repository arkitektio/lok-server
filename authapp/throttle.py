"""
authapp.throttle

Minimal per-IP fixed-window rate limiting for the anonymous OAuth surfaces
(app/hub authorization, token endpoint). These endpoints are deliberately
unauthenticated — dynamic registration and device-code polling need no prior
credential — so they are the natural brute-force targets.

Uses Django's cache (LocMem by default: per-process, which is sufficient — the
goal is taking bulk brute force off the table, not precise global accounting).
Fails open if the cache is unavailable: a broken cache must not take down
authentication.
"""

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

# Requests per window per client IP. Generous by design: a polling device makes
# ~12 token requests/min, but many devices can share a NAT.
AUTHORIZATION_LIMIT_PER_MINUTE = getattr(settings, "OAUTH_AUTHORIZATION_THROTTLE_PER_MINUTE", 30)
TOKEN_LIMIT_PER_MINUTE = getattr(settings, "OAUTH_TOKEN_THROTTLE_PER_MINUTE", 240)
WINDOW_SECONDS = 60


def _client_ip(request) -> str:
    """The address to bucket this request under.

    This used to take the **leftmost** X-Forwarded-For entry, which is entirely
    client-supplied: rotating `X-Forwarded-For: 1.2.3.<n>` produced a fresh
    bucket per request and made `is_throttled` a no-op against exactly the
    brute-force targets this module exists to protect.

    Each proxy *appends* the address it saw, so the only trustworthy entries are
    at the right-hand end — one per proxy you actually control. With
    `django.trusted_proxy_depth = N` the client is the Nth entry from the right;
    with the default 0 there is no trusted proxy, so the header is ignored
    entirely and REMOTE_ADDR (which cannot be spoofed) is used.
    """
    depth = getattr(settings, "TRUSTED_PROXY_DEPTH", 0)
    remote_addr = request.META.get("REMOTE_ADDR", "unknown")

    if depth <= 0:
        return remote_addr

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if len(parts) < depth:
        # Fewer hops than configured: the chain is not what we expect, so fall
        # back to the peer address rather than trusting a short header.
        return remote_addr
    return parts[-depth]


def is_throttled(request, bucket: str, limit: int) -> bool:
    key = f"oauth-throttle:{bucket}:{_client_ip(request)}"
    try:
        if cache.add(key, 1, timeout=WINDOW_SECONDS):
            return False
        return cache.incr(key) > limit
    except Exception:
        return False


def throttled_response() -> JsonResponse:
    # OAuth vocabulary (RFC 8628's slow_down) with the conventional HTTP status.
    return JsonResponse({"error": "slow_down"}, status=429)

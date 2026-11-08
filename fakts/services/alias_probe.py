"""lok's own probe of public aliases: is the instance up, and is it the instance?

A hub hosted somewhere public (a kommunity partner, typically) may never call
``/f/hubhealth/`` itself: a partner-configured hub has no client identity to
report with. Its ``public`` aliases (``lok_checks`` in a manifest) are reachable
from lok, so lok asks them directly, the same way an app does before it trusts
an alias:

    GET https://<host>[:<port>]/<path>/<challenge>?nonce=<nonce>
    -> 200 {"signature": base64(Ed25519(instance key, "fakts-challenge-v1:" + nonce))}

(``arkitekt_service.trust.sign_challenge`` on the service side; it answers only
while the service's health view returns 200.) A 200 alone proves nothing: anyone
can answer at an address. Only a signature that verifies against the instance's
``challenge_key`` (``ServiceInstance.public_key``) counts as ``verified``, and
only ``verified`` makes a hub ``reachable``.

The URLs come from hub manifests, so this is a server-side request primitive:
only https, only hosts resolving to public addresses, no redirects, tight
timeouts and a small body cap. What is stored is a coarse status, never the
far end's error text, so the result cannot be used as an oracle into lok's
network.

One partner hub keeps its uid in every organization, so its aliases repeat
across organizations: each distinct URL is fetched once per run and the answer
is checked against every distinct key of the rows sharing it.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Iterable, Optional

import certifi
import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from fakts import enums, models
from fakts.utils import assert_public_https_url

logger = logging.getLogger(__name__)

Status = enums.AliasProbeStatusChoices

#: The domain tag of a signed alias challenge (``arkitekt_service.trust.CHALLENGE_DOMAIN``).
CHALLENGE_DOMAIN = "fakts-challenge-v1"
CONNECT_TIMEOUT = 3.05
READ_TIMEOUT = 5.0
#: A signed answer is ~100 bytes of JSON; anything far bigger is not one.
MAX_BODY_BYTES = 4096
#: A manual "check now" within this many seconds of the last probe is not re-run.
MIN_RECHECK_SECONDS = 10


@dataclass(frozen=True)
class Fetched:
    """What one request to an alias's challenge URL got back."""

    error: Optional[str] = None
    """A status (``Status`` value) when no signed-answer candidate came back."""
    http_status: Optional[int] = None
    signature: Optional[str] = None
    latency_ms: Optional[int] = None


@dataclass(frozen=True)
class ProbeResult:
    alias_id: int
    status: str
    http_status: Optional[int]
    latency_ms: Optional[int]


Fetcher = Callable[[str, str, Optional[str]], Fetched]


def new_nonce() -> str:
    """32 url-safe characters, the length fakts clients send."""
    return secrets.token_urlsafe(24)


def is_probeable(alias: models.InstanceAlias) -> bool:
    return bool(alias.public and alias.ssl and alias.host and alias.kind == enums.AliasKindChoices.ABSOLUTE.value)


def probeable_aliases() -> QuerySet:
    """Every alias lok checks itself: public, https, absolute, with a host."""
    return (
        models.InstanceAlias.objects.filter(public=True, ssl=True, kind=enums.AliasKindChoices.ABSOLUTE.value)
        .exclude(host__isnull=True)
        .exclude(host="")
        .select_related("instance__hub")
    )


def challenge_url(alias: models.InstanceAlias) -> str:
    """``https://host[:port]/path/challenge`` for an alias, as a fakts client builds it."""
    host = alias.host or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # a bare IPv6 address
    netloc = f"{host}:{alias.port}" if alias.port else host
    segments = [part.strip("/") for part in (alias.path or "", alias.challenge or "") if part and part.strip("/")]
    return f"https://{netloc}/{'/'.join(segments)}"


def fetch_challenge(url: str, nonce: str, ca_bundle: Optional[str] = None, *, guard: bool = True) -> Fetched:
    """Ask ``url`` to sign ``nonce``. ``ca_bundle``: a PEM file to trust instead of the system CAs."""
    if guard:
        try:
            assert_public_https_url(url, "Alias")
        except ValueError:
            return Fetched(error=Status.NON_PUBLIC_ADDRESS.value)

    started = time.monotonic()
    try:
        with requests.get(
            url,
            params={"nonce": nonce},
            headers={"Accept": "application/json", "User-Agent": "lok-alias-probe"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=False,
            stream=True,
            verify=ca_bundle or True,
        ) as response:
            latency_ms = int((time.monotonic() - started) * 1000)
            if response.status_code != 200:
                return Fetched(error=Status.UNHEALTHY.value, http_status=response.status_code, latency_ms=latency_ms)
            body = b""
            for chunk in response.iter_content(chunk_size=1024):
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    # Not a signed answer: most likely the health page passed through unsigned.
                    return Fetched(error=Status.UNSIGNED.value, http_status=200, latency_ms=latency_ms)
    except requests.exceptions.SSLError:
        return Fetched(error=Status.TLS_FAILED.value)
    except requests.exceptions.Timeout:
        return Fetched(error=Status.TIMEOUT.value)
    except requests.exceptions.RequestException:
        return Fetched(error=Status.UNREACHABLE.value)

    try:
        payload = json.loads(body)
    except ValueError:
        return Fetched(error=Status.UNSIGNED.value, http_status=200, latency_ms=latency_ms)
    signature = payload.get("signature") if isinstance(payload, dict) else None
    if not isinstance(signature, str):
        return Fetched(error=Status.UNSIGNED.value, http_status=200, latency_ms=latency_ms)
    return Fetched(http_status=200, signature=signature, latency_ms=latency_ms)


def verify_signature(public_key: str, nonce: str, signature: str) -> bool:
    """Whether ``signature`` is the instance key's Ed25519 signature of the tagged nonce."""
    try:
        raw_signature = base64.b64decode(signature, validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    except (binascii.Error, ValueError):
        return False
    if len(raw_signature) != 64:
        return False
    try:
        key.verify(raw_signature, f"{CHALLENGE_DOMAIN}:{nonce}".encode())
    except InvalidSignature:
        return False
    return True


def judge(fetched: Fetched, nonce: str, public_key: Optional[str]) -> str:
    """The status of one alias row, from its URL's answer and its own instance's key."""
    if fetched.error and fetched.error != Status.UNSIGNED.value:
        return fetched.error
    if not public_key:
        # Up (it answered 200), but there is nothing to prove who it is against.
        return Status.NO_KEY.value
    if fetched.signature is None:
        return Status.UNSIGNED.value
    if verify_signature(public_key, nonce, fetched.signature):
        return Status.VERIFIED.value
    return Status.BAD_SIGNATURE.value


def probe_aliases(
    aliases: Optional[Iterable[models.InstanceAlias]] = None,
    *,
    fetch: Optional[Fetcher] = None,
    concurrency: Optional[int] = None,
) -> list[ProbeResult]:
    """Probe ``aliases`` (default: every probeable one) and store each outcome on its row."""
    rows = [alias for alias in (probeable_aliases() if aliases is None else aliases) if is_probeable(alias)]
    if not rows:
        return []
    fetch = fetch or fetch_challenge

    # A hub whose gateway serves its own self-signed certificate is trusted on that
    # certificate, as its apps trust it — and on the public CAs too, for a hub whose
    # public aliases sit behind a CA-issued proxy. Identity comes from the signature
    # either way; TLS only has to make the transport honest.
    groups: dict[tuple[str, Optional[str]], list[models.InstanceAlias]] = defaultdict(list)
    for alias in rows:
        hub = alias.instance.hub if alias.instance.hub_id else None
        groups[(challenge_url(alias), (hub.tls_certificate or None) if hub else None)].append(alias)

    with tempfile.TemporaryDirectory(prefix="alias-probe-") as tmp:
        bundles: dict[str, str] = {}
        for _url, pem in groups:
            if pem and pem not in bundles:
                path = f"{tmp}/ca-{len(bundles)}.pem"
                with open(certifi.where()) as public_cas, open(path, "w") as handle:
                    handle.write(public_cas.read().rstrip() + "\n" + pem)
                bundles[pem] = path

        jobs = [(key, new_nonce()) for key in groups]

        def run(job):
            (url, pem), nonce = job
            try:
                return fetch(url, nonce, bundles.get(pem) if pem else None)
            except Exception:  # a probe must never take the run down
                logger.exception("Probing %s failed unexpectedly", url)
                return Fetched(error=Status.UNREACHABLE.value)

        workers = max(1, min(concurrency or getattr(settings, "HUB_PROBE_CONCURRENCY", 8), len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            answers = list(pool.map(run, jobs))

    now = timezone.now()
    results: list[ProbeResult] = []
    updated: list[models.InstanceAlias] = []
    for ((key, nonce), fetched) in zip(jobs, answers):
        for alias in groups[key]:
            status = judge(fetched, nonce, alias.instance.public_key)
            alias.probe_status = status
            alias.probed_at = now
            alias.probe_http_status = fetched.http_status
            alias.probe_latency_ms = fetched.latency_ms
            if status == Status.VERIFIED.value:
                alias.last_verified_at = now
            updated.append(alias)
            results.append(ProbeResult(alias.id, status, fetched.http_status, fetched.latency_ms))

    models.InstanceAlias.objects.bulk_update(
        updated, ["probe_status", "probed_at", "probe_http_status", "probe_latency_ms", "last_verified_at"]
    )
    return results


def probe_hub(hub: models.Hub, *, fetch: Optional[Fetcher] = None) -> list[ProbeResult]:
    """Probe one hub's public aliases now, unless they were probed moments ago."""
    aliases = list(probeable_aliases().filter(instance__hub=hub))
    recent = timezone.now() - timedelta(seconds=MIN_RECHECK_SECONDS)
    if aliases and all(alias.probed_at and alias.probed_at > recent for alias in aliases):
        return []
    return probe_aliases(aliases, fetch=fetch)

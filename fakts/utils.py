from uuid import uuid4
import collections.abc
import ipaddress
import socket
from urllib.parse import urlparse

import requests
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from karakter.datalayer import get_current_datalayer
from karakter.models import MediaStore
from django.conf import settings

# `download_logo` is reached from the *unauthenticated* device-code start
# endpoints (/f/start/, /f/servicestart/, /f/hubstart/) with a URL taken straight
# from the request body, so it is a server-side request forgery primitive: lok
# sits on the internal network alongside minio, postgres and ionscale. These
# constants bound what an anonymous caller can make the server do.
LOGO_ALLOWED_SCHEMES = ("https",)
LOGO_CONNECT_TIMEOUT = 3.05  # seconds; also caps the "point it at a tarpit" DoS
LOGO_READ_TIMEOUT = 10.0
LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB is generous for a PNG logo


def update_nested(d, u):
    """Update a nested dictionary or similar mapping."""
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update_nested(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def _assert_public_url(url: str) -> None:
    """Reject anything that is not an https URL resolving to a public address.

    Every address the host resolves to is checked, not just the first: a name
    that returns one public and one internal address would otherwise be a bypass.
    This is checked before the request and redirects are disabled at the call
    site, so a redirect cannot be used to step past it afterwards.
    """
    parsed = urlparse(url)
    if parsed.scheme not in LOGO_ALLOWED_SCHEMES:
        raise ValueError(f"Logo URL must use https, got {parsed.scheme or 'no scheme'!r}")
    if not parsed.hostname:
        raise ValueError("Logo URL has no host")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve logo host {parsed.hostname!r}") from exc

    for family, _type, _proto, _canonname, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_multicast:
            # Covers loopback, RFC1918, link-local (including the 169.254.169.254
            # metadata address), unique-local v6, and the unspecified address.
            raise ValueError(f"Logo host {parsed.hostname!r} resolves to non-public address {ip}")


def download_logo(url: str) -> File:
    """Download a logo from a URL and return a Django File object, that can be
    used directly in a model.

    Only public https URLs are fetched, redirects are not followed, and the
    response is size-capped and streamed — see the module constants for why.
    """
    _assert_public_url(url)

    img_tmp = NamedTemporaryFile(delete=True)
    with requests.get(
        url,
        timeout=(LOGO_CONNECT_TIMEOUT, LOGO_READ_TIMEOUT),
        allow_redirects=False,
        stream=True,
    ) as response:
        # Raises rather than asserts: this validates a *remote* response, and
        # `assert` is stripped under `python -O`, which would store whatever the
        # far end returned — an error page, or a non-image payload.
        if response.status_code != 200:
            raise ValueError(f"Could not download logo from {url}: HTTP {response.status_code}")
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type != "image/png":
            raise ValueError(f"Expected PNG image, got {content_type}")

        # Enforce the cap while streaming: a Content-Length header is caller
        # controlled and may be absent or a lie.
        written = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            written += len(chunk)
            if written > LOGO_MAX_BYTES:
                raise ValueError(f"Logo at {url} exceeds {LOGO_MAX_BYTES} bytes")
            img_tmp.write(chunk)
        img_tmp.flush()

    key = f"{uuid4()}.png"
    bucket = settings.MEDIA_BUCKET

    img_tmp.seek(0)

    store = MediaStore.objects.create(
        path=f"{bucket}/{key}", key=key, bucket=bucket
    )

    store.put_file(get_current_datalayer(), img_tmp)

    return store

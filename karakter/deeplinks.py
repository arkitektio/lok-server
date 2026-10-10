"""The apps an organization lets kontrol forward its deep links to.

Kontrol's ``/deeplink/...`` and ``/smartlink/...`` pages hand a signed-in member
over to an app by navigating the browser to ``<protocol>://...``. Which apps are
acceptable is an organization setting (``Organization.deeplink_apps``), a list of
entries::

    {"protocol": "orkestrator", "name": "Orkestrator",
     "install_url": "https://arkitekt.live/docs/use/install", "mobile": true}

``protocol`` is the app's URL scheme, ``install_url`` where someone who does not
have the app yet is sent, and ``mobile`` whether the app also exists on phones
and tablets. Order matters: a link that names no protocol opens the first entry,
or on a mobile device the first entry with ``mobile`` set. The rules live here
so every writer agrees on them.
"""

import re
from urllib.parse import urlsplit

# RFC 3986 scheme shape, lowercased and length-capped.
PROTOCOL_RE = re.compile(r"^[a-z][a-z0-9+.-]{0,31}$")

MAX_APPS = 10
MAX_NAME_LENGTH = 60
MAX_INSTALL_URL_LENGTH = 500

# Schemes the browser itself gives meaning to. Forwarding to one of these would
# turn the link page into an open redirect (or, for ``javascript``, into script
# execution on kontrol's origin) instead of an app hand-off.
FORBIDDEN_PROTOCOLS = frozenset(
    {
        "http",
        "https",
        "javascript",
        "data",
        "file",
        "blob",
        "vbscript",
        "about",
        "ftp",
        "ws",
        "wss",
        "mailto",
        "tel",
    }
)


def default_deeplink_apps() -> list[dict]:
    return [
        {
            "protocol": "orkestrator",
            "name": "Orkestrator",
            "install_url": "https://arkitekt.live/docs/use/install",
            "mobile": True,
        }
    ]


def normalize_protocol(raw: str) -> str:
    """Lowercase and trim a scheme, dropping a pasted ``://`` or ``:``.

    Raises ``ValueError`` on anything that is not a plain app scheme.
    """
    protocol = (raw or "").strip().lower().removesuffix("://").removesuffix(":")
    if not PROTOCOL_RE.match(protocol):
        raise ValueError(
            f"'{raw}' is not a valid protocol. Use a URL scheme such as 'orkestrator': "
            "a letter followed by letters, digits, '+', '.' or '-'."
        )
    if protocol in FORBIDDEN_PROTOCOLS:
        raise ValueError(f"'{protocol}' cannot be used as a deep link protocol.")
    return protocol


def normalize_install_url(raw: str | None) -> str | None:
    """An https page where the app can be installed, or ``None``.

    A bare ``arkitekt.live/docs/use/install`` is read as https. The page is
    shown as a link on kontrol, so it is held to https: nothing else may ride
    in on it.
    """
    url = (raw or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = f"https://{url}"
    if len(url) > MAX_INSTALL_URL_LENGTH:
        raise ValueError(f"An install link can be at most {MAX_INSTALL_URL_LENGTH} characters long.")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or any(c.isspace() for c in url):
        raise ValueError(f"'{raw}' is not a valid install link. Use an https address.")
    return url


def normalize_deeplink_apps(raw: list[dict]) -> list[dict]:
    """Normalise and validate a list of deep link apps.

    Each entry needs a ``protocol``; ``name`` defaults to the capitalised
    protocol, ``install_url`` to none and ``mobile`` to false. The order is
    kept (it decides the default) and a protocol may appear once. An empty list
    is valid and switches forwarding off. Raises ``ValueError`` (surfaced to the
    client as a GraphQL error) on anything else.
    """
    apps: list[dict] = []
    for entry in raw:
        protocol = normalize_protocol(entry.get("protocol") or "")
        if any(app["protocol"] == protocol for app in apps):
            raise ValueError(f"'{protocol}' is listed more than once.")
        name = (entry.get("name") or "").strip() or protocol.capitalize()
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"An app name can be at most {MAX_NAME_LENGTH} characters long.")
        apps.append(
            {
                "protocol": protocol,
                "name": name,
                "install_url": normalize_install_url(entry.get("install_url")),
                "mobile": bool(entry.get("mobile")),
            }
        )

    if len(apps) > MAX_APPS:
        raise ValueError(f"An organization can allow at most {MAX_APPS} deep link apps.")
    return apps

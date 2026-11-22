"""The body of ``/.well-known/security.txt`` (RFC 9116), as BSI TR-03183-3 4.2 lays it out.

lok only renders the *unsigned* text. TR-03183-3 4.2.10 requires the file to be
clearsigned with OpenPGP, and the signing key does not belong on a server, so the
operator signs this output offline and the gateway serves the signed bytes as a
static file. Nothing may rewrite them afterwards, or the signature breaks.
"""

from __future__ import annotations

import datetime

from lok_server.configuration import SecuritySettings


def _grouped(fingerprint: str) -> str:
    return " ".join(fingerprint[i : i + 4] for i in range(0, len(fingerprint), 4))


def expires_value(day: datetime.date) -> str:
    """RFC 3339 with an upper-case ``T`` and ``Z`` (TR-03183-3 4.2.9a)."""
    return f"{day.isoformat()}T00:00:00Z"


def render_security_txt(sec: SecuritySettings) -> str:
    base = sec.page_url.rstrip("/")
    lines: list[str] = [
        f"# Security contacts of {sec.vendor} (RFC 9116, BSI TR-03183-3).",
        f"# Reporting guidance and our CVD policy: {base}",
        f"# This file is OpenPGP clearsigned with key {_grouped(sec.signing_key.fingerprint)}",
        f"# ({sec.signing_key.url}).",
        "",
        "# Our canonical URI",
        *(f"Canonical: {url}" for url in sec.canonical),
        "",
        "# Our security addresses",
        f"Contact: mailto:{sec.psirt.email}",
        f"Contact: mailto:{sec.csirt.email}",
        f"Contact: {base}",
        "",
        "# Our OpenPGP keys",
        f"# {sec.psirt.email}: {_grouped(sec.psirt.key.fingerprint)}",
        f"Encryption: {sec.psirt.key.url}",
        f"# {sec.csirt.email}: {_grouped(sec.csirt.key.fingerprint)}",
        f"Encryption: {sec.csirt.key.url}",
        "",
        "# Our security acknowledgements page",
        f"Acknowledgments: {base}/acknowledgements",
        "",
        "# Our preferred languages",
        f"Preferred-Languages: {', '.join(sec.preferred_languages)}",
        "",
        "# Our security policy",
        f"Policy: {base}/policy",
    ]
    if sec.csaf:
        lines += ["", "# Our security advisories", f"CSAF: {sec.csaf}"]
    lines += [
        "",
        "# Expiry date of this file",
        f"Expires: {expires_value(sec.expires)}",
    ]

    for line in lines:
        # TR-03183-3 4.2.1c: only ASCII 0x20-0x7E outside comments.
        if not line.startswith("#") and any(not (0x20 <= ord(c) <= 0x7E) for c in line):
            raise ValueError(f"security.txt field is not printable ASCII: {line!r}")
    return "\n".join(lines) + "\n"

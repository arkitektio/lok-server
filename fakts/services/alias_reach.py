"""An alias's reach: where its address works. One word for what ``kind`` and ``scope`` used to
split between them (``absolute``/``mesh``/``docker`` and ``local``/``network``/``public``/
``ionscale``, two of them product names):

- ``loopback`` — this machine only (``localhost``, ``127.0.0.0/8``, ``::1``);
- ``internal`` — only inside the deployment's own network (its docker network today);
- ``lan`` — the local network;
- ``mesh`` — the organization's mesh; without a host of its own, lok fills it in from the
  hub's node;
- ``public`` — the internet.

Stored as it always was — ``kind`` and ``scope`` — so the previous release runs on the same
rows: reach is derived from them (:func:`reach_of`), and a staged alias that says its reach is
written as the ``kind`` and ``scope`` it stands for (:func:`legacy_of`).
"""

from __future__ import annotations

import ipaddress
from typing import Literal

Reach = Literal["loopback", "internal", "lan", "mesh", "public"]
REACHES: tuple[Reach, ...] = ("loopback", "internal", "lan", "mesh", "public")


def is_loopback_host(host: str | None) -> bool:
    """``localhost``, ``*.localhost``, or a loopback address."""
    if not host:
        return False
    name = host.strip().strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def reach_of(kind: str | None, scope: str | None, host: str | None) -> Reach:
    """The reach of an alias stored as ``kind`` and ``scope``.

    ``scope: local`` meant "nothing outside this machine uses it", which was said of loopback
    addresses and of the gateway's name on the hub's own network alike: a loopback host is
    ``loopback``, any other is ``internal``.
    """
    if kind == "mesh":
        return "mesh"
    if kind == "docker":
        return "internal"
    if scope == "ionscale":
        return "mesh"
    if scope == "public":
        return "public"
    if scope == "network":
        return "lan"
    return "loopback" if is_loopback_host(host) else "internal"


def legacy_of(reach: Reach, host: str | None) -> tuple[str, str]:
    """The ``kind`` and ``scope`` an alias of ``reach`` is stored as. A mesh alias without a host
    is the placeholder lok resolves (``kind: mesh``); with one, its host is literal."""
    if reach == "mesh":
        return ("absolute" if host else "mesh", "ionscale")
    if reach == "internal":
        return ("docker", "local")
    return ("absolute", {"loopback": "local", "lan": "network", "public": "public"}[reach])

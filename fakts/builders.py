"""Backwards-compatibility shim.

The client-building logic now lives in :mod:`fakts.services.clients`. This module
re-exports it so existing imports (``from fakts.builders import create_client``)
keep working. Prefer importing from ``fakts.services.clients`` in new code.
"""

from fakts.services.clients import bind_client, create_public_client

__all__ = ["bind_client", "create_public_client"]

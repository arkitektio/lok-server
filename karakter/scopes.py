"""What an app may do at lok itself, on a user's behalf.

lok is no service a hub registers, so its scopes are its own: made in every organization beside
the OIDC ones (``openid``, ``profile``, ...), built in, never pruned. Every root field of the
client-facing schema asks for one of them (``lok_server.schema``); a generated client gathers
them, and ``lok_all`` stands for all of them. The management API is lok's own web app, signed in
with a session: it asks for none.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LokScope:
    key: str
    description: str
    is_all_scope: bool = False


READ = "lok_read"
PROFILE = "lok_profile"
ORGANIZATIONS = "lok_organizations"
MANDATES = "lok_mandates"
MESSAGES = "lok_messages"
SERVICES = "lok_services"
ALL = "lok_all"

LOK_SCOPES = (
    LokScope(READ, "See your organizations, their members, groups and roles, and the devices, apps, hubs and services registered with them."),
    LokScope(PROFILE, "Change your profile, your groups' profiles, your devices and your colours in an organization, and upload the images they show."),
    LokScope(ORGANIZATIONS, "Create organizations, invite people, add them, and accept or decline who asks to join."),
    LokScope(MANDATES, "Issue and revoke mandates and their keys, which let an app act on your behalf."),
    LokScope(MESSAGES, "Send notifications, acknowledge yours, and register where you receive them."),
    LokScope(SERVICES, "Register and change service instances, and render their configuration."),
    LokScope(ALL, "Do everything in Lok on your behalf.", is_all_scope=True),
)


__all__ = ["ALL", "LOK_SCOPES", "MANDATES", "MESSAGES", "ORGANIZATIONS", "PROFILE", "READ", "SERVICES", "LokScope"]

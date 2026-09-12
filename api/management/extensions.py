"""Schema-wide authorization guards for the management GraphQL API.

The management endpoint (`/lok/managementgraphql/`) exposes organization, user,
client, hub, device, membership, OAuth and mesh administration. Individual
resolvers are responsible for object-level authorization (scoping lookups to the
caller's organizations), but that is easy to forget on a new resolver. This
extension adds a single, endpoint-wide gate: every root query/mutation field
requires an authenticated user, except an explicit allow-list of intentionally
public fields.

This is defense in depth, not a replacement for per-resolver checks: it stops
anonymous callers reaching *any* resolver, but authenticated cross-tenant IDOR
still has to be prevented in the resolvers themselves.
"""

import inspect

from asgiref.sync import sync_to_async
from graphql import GraphQLError
from strawberry.extensions import SchemaExtension


# GraphQL field names (camelCase) that may be resolved without authentication.
# `inviteByCode` powers the public invite-preview page (`/invite/:code`); the
# resolver itself still hides private invites from anonymous visitors.
PUBLIC_ROOT_FIELDS = frozenset({"inviteByCode"})


def _request_from_context(context: object):
    """Return the Django request from a strawberry context (object or dict)."""
    request = getattr(context, "request", None)
    if request is None and isinstance(context, dict):
        request = context.get("request")
    return request


def _is_authenticated(request: object) -> bool:
    """Whether ``request`` carries an authenticated principal. Fails closed.

    Must be called from a sync context: on the production view the request is a
    Django ``HttpRequest`` whose ``.user`` is a session-backed ``SimpleLazyObject``,
    so *any* attribute access on it (including ``is_authenticated``) can run a DB
    query. Reading ``request.user`` also raises outright on kante's
    ``UniversalRequest`` when no principal was set, so every access is guarded.
    """
    try:
        user = request.user
    except Exception:
        return False
    if user is None:
        return False
    try:
        return bool(user.is_authenticated)
    except Exception:
        return False


class RequireAuthenticationExtension(SchemaExtension):
    """Reject anonymous access to every root field outside the allow-list."""

    async def resolve(self, _next, root, info, *args, **kwargs):
        # `info.path.prev is None` identifies a top-level query/mutation field.
        # Introspection meta-fields (`__schema`, `__type`, `__typename`) are left
        # alone so schema tooling keeps working.
        is_root_field = info.path.prev is None
        if (
            is_root_field
            and not info.field_name.startswith("__")
            and info.field_name not in PUBLIC_ROOT_FIELDS
        ):
            request = _request_from_context(info.context)
            # Resolving the session user touches the DB, and this hook runs on the
            # event loop — hand it to a thread. `thread_sensitive` keeps it on
            # Django's usual sync worker, and resolving the lazy user here caches
            # it on the request for the resolvers that follow.
            authenticated = request is not None and await sync_to_async(
                _is_authenticated, thread_sensitive=True
            )(request)
            if not authenticated:
                raise GraphQLError("Authentication required to access the management API.")

        result = _next(root, info, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

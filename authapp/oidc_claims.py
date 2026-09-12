"""Per-client OIDC claim shaping (``sub`` and ``email``).

One aspect of the claims lok issues to an OpenID relying party is configurable
**per client** (see ``Client.email_template``, provisioned from the
``openid_apps`` config):

The ``sub`` claim is always the *user* id, so the same human is the same
subject across every organization they belong to; the organization is carried
separately in the ``org`` claim. A relying party that needs to tell a person's
organizations apart resolves them on ``(sub, org)``. (A ``membership_is_subject``
option used to fake a per-membership ``sub`` for relying parties that could not
do that; ionscale, its only user, now keys on the pair, and the option is gone.)

- **Email** — by default the ``email`` claim is the user's email (falling back
  to a synthetic ``<pk>@users.noreply`` address). A client can instead supply an
  ``email_template`` such as ``"{username}@corp.example"`` rendered from a fixed
  set of membership variables.

The variable set and template validation here are **pure** (stdlib only, no
Django imports) so the config layer (``lok_server.configuration``) can validate
templates at load time. The render/resolve helpers take a ``Membership`` at
runtime.
"""

import string
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:  # avoid importing Django models at config-load time
    from karakter.models import Membership


#: Variable names an ``email_template`` may reference. Kept flat (no attribute
#: or index access) so validation stays simple and templates can't reach into
#: arbitrary object internals.
EMAIL_TEMPLATE_VARIABLES = frozenset(
    {
        "username",
        "user_id",
        "email",
        "membership_id",
        "org_slug",
        "org_name",
    }
)


def validate_email_template(template: str) -> None:
    """Validate an ``email_template`` string against the available variables.

    Raises ``ValueError`` if the template references an unknown variable, uses
    attribute/index access (e.g. ``{user.email}`` or ``{roles[0]}``), or has no
    substitutable content. The message lists the offending and allowed names so
    a misconfiguration is actionable at boot rather than at first login.
    """
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:  # malformed braces, e.g. a stray "{"
        raise ValueError(f"email_template {template!r} is not a valid format string: {exc}") from exc

    field_names = [field for _, field, _, _ in parsed if field is not None]
    if not field_names:
        raise ValueError(
            f"email_template {template!r} contains no variables — it would render the same "
            f"email for every user. Include at least one of: {_allowed()}."
        )

    unknown = [f for f in field_names if f not in EMAIL_TEMPLATE_VARIABLES]
    if unknown:
        raise ValueError(
            f"email_template {template!r} references unknown variable(s) "
            f"{', '.join(repr(u) for u in unknown)}. Available variables: {_allowed()}. "
            f"Attribute/index access (e.g. '{{user.email}}') is not allowed."
        )


def _allowed() -> str:
    return ", ".join(sorted(EMAIL_TEMPLATE_VARIABLES))


def build_email_variables(membership: "Membership") -> Dict[str, str]:
    """Build the substitution dict for ``email_template`` from a membership.

    Nullable source fields (``user.email``, ``organization.slug/name``) coerce to
    the empty string so a template never renders the literal ``"None"``.
    """
    user = membership.user
    organization = membership.organization
    return {
        "username": user.username or "",
        "user_id": str(user.id),
        "email": user.email or "",
        "membership_id": str(membership.id),
        "org_slug": organization.slug or "",
        "org_name": organization.name or "",
    }


def resolve_sub(membership: "Membership") -> str:
    """Resolve the ``sub`` claim for a membership.

    Always the user id: one human is one subject, and the organization travels
    in the ``org`` claim alongside it.

    Must be computed identically for the id_token and the userinfo response —
    OIDC requires the two ``sub`` values to match (Core §5.3.2).
    """
    return str(membership.user.id)


def resolve_email(membership: "Membership", email_template: Optional[str]) -> str:
    """Resolve the ``email`` claim for a membership given the client's policy.

    When ``email_template`` is set it is rendered from ``build_email_variables``;
    otherwise the default is the user's email, falling back to a synthetic
    ``<membership pk>@users.noreply`` address.
    """
    if email_template:
        return email_template.format_map(build_email_variables(membership))
    return membership.user.email or f"{membership.pk}@users.noreply"


def build_claims(
    membership: "Membership",
    scope: Optional[str],
    email_template: Optional[str],
    roles: bool = True,
) -> Dict[str, object]:
    """The OIDC claim set for ``membership``, filtered by the granted ``scope``.

    OIDC Core §5.4 ties claims to scopes: ``profile`` buys the name claims,
    ``email`` buys the address. Both claim producers used to accept a ``scope``
    argument and drop it on the floor, so an RP granted nothing but ``openid``
    still received the user's email in its id_token *and* from the userinfo
    endpoint. authlib does not filter downstream either — ``OpenIDCode`` hands
    whatever ``generate_user_info`` returns straight into the id_token payload
    (``authlib/oidc/core/grants/util.py``) — so the filtering has to happen
    here, in the one place both callers share. Sharing it is also what keeps
    ``sub`` identical between the two paths, which §5.3.2 requires.

    ``roles`` and ``org`` are deliberately *not* scope-gated. They are
    non-standard claims (§5.4 permits additional ones) that authentikate and
    every resource server already read; gating them behind a scope no existing
    client requests would break the fleet for no compliance gain.

    ``roles`` is skipped entirely when ``roles=False`` — the id_token carries
    the org but not the role list, matching what it carried before.
    """
    claims: Dict[str, object] = {
        "sub": resolve_sub(membership),
        "org": str(membership.organization_id),
    }

    granted = set((scope or "").split())

    if "profile" in granted:
        username = membership.user.username
        claims["name"] = username
        claims["nickname"] = username
        claims["preferred_username"] = username

    if "email" in granted:
        claims["email"] = resolve_email(membership, email_template)

    if roles:
        claims["roles"] = [role.identifier for role in membership.roles.all()]

    return claims

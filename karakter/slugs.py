"""Slug generation and validation for organizations.

A slug is the URL-safe handle of an organization (e.g. `acme-inc`). We keep the
rules strict and centralised here so that creation, updates, and any future
caller all agree on what a valid slug looks like and how one is derived from a
name.

The canonical shape is a classic URL slug: lowercase alphanumerics separated by
single hyphens, with no leading, trailing, or doubled hyphens.
"""

import re

from .models import Organization

# The canonical slug shape. Anything a user types is *normalised* down to this
# form (see ``normalize_slug``); the regex is the authoritative gate.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Funny prefixes we try, in order, when a desired slug is already taken.
_SUGGESTION_PREFIXES = ("the-real-", "the-actual-", "the-one-true-")


def slugify_name(name: str) -> str:
    """Derive a clean slug from a human name.

    Lowercases, turns any run of non-``[a-z0-9]`` characters into a single
    hyphen, and trims leading/trailing hyphens. Deterministic and suffix-free —
    ``"Acme Inc!"`` -> ``"acme-inc"``. Returns ``""`` if nothing usable remains
    (callers must handle that via ``validate_slug``).
    """
    lowered = (name or "").lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return collapsed.strip("-")


def normalize_slug(raw: str) -> str:
    """Normalise a user-supplied slug into the canonical form.

    Applies the same rules as ``slugify_name`` so ``"My_Weird..Org"`` becomes
    ``"my-weird-org"``. This means we accept forgiving input but always store a
    clean slug.
    """
    return slugify_name(raw)


def validate_slug(slug: str) -> None:
    """Check that ``slug`` is a valid canonical slug.

    Raises ``ValueError`` (surfaced to the client as a GraphQL error) when the slug
    is empty (e.g. a name made entirely of symbols) or otherwise malformed.

    Deliberately a raise rather than an ``assert``: this is input validation, and
    ``assert`` is stripped under ``python -O``, which would silently admit malformed
    slugs. ``ValueError`` keeps this module free of a GraphQL dependency — its
    callers are resolvers, and graphql-core surfaces the message either way.
    """
    if not slug:
        raise ValueError(
            "A slug is required — please provide a name or handle with at least one letter or number."
        )
    if not SLUG_RE.match(slug):
        raise ValueError(
            "Handle must be lowercase letters, numbers and single hyphens only "
            "(e.g. 'my-organization')."
        )


def is_slug_taken(slug: str) -> bool:
    """Whether any organization already uses this slug (case-insensitive).

    Uses the default manager so the check is global — it is intentionally not
    subject to the per-user GraphQL queryset scoping on ``ManagementOrganization``.
    """
    return Organization.objects.filter(slug__iexact=slug).exists()


def suggest_slug(slug: str) -> str:
    """Suggest an available alternative for a taken slug.

    Tries the funny prefixes first (``the-real-acme``), then numeric suffixes
    (``acme-2``, ``acme-3``, …). Deterministic so it is easy to test and so the
    same collision always yields the same suggestion.
    """
    for prefix in _SUGGESTION_PREFIXES:
        candidate = f"{prefix}{slug}"
        if not is_slug_taken(candidate):
            return candidate

    counter = 2
    while is_slug_taken(f"{slug}-{counter}"):
        counter += 1
    return f"{slug}-{counter}"

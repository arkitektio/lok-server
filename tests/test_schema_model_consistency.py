"""Schema ↔ model consistency sweep.

Every strawberry-django type is checked against the Django model it wraps:

* ``NULLABILITY`` — a GraphQL field declared non-null whose backing model column is
  ``null=True`` (→ "Cannot return null for non-nullable field" at query time).
* ``MISSING-ATTR`` — a custom resolver that reads ``self.<attr>`` the model doesn't have
  (→ ``AttributeError`` at query time; this is how ``ManagementClient.token`` broke).
* ``BARE-FK`` — an auto field whose name maps to nothing on the model.

No database needed — this only imports and introspects the two schemas. Findings that
are deliberate (e.g. a ``null=True`` column that every creation path sets, a resolver with
a non-null fallback) are listed in ``ACCEPTED`` with the reason; anything else fails.
"""

import ast
import inspect
import textwrap

from django.core.exceptions import FieldDoesNotExist
from strawberry.types.base import StrawberryList, StrawberryOptional

from api.management.schema import schema as management_schema
from lok_server.schema import schema as main_schema

# (schema, graphql type, field) -> why it is fine.
ACCEPTED = {
    # Profiles are created by karakter.signals post_save hooks for every user/org.
    ("mgmt", "ManagementUser", "profile"): "auto-created by signal",
    ("main", "User", "profile"): "auto-created by signal",
    ("main", "Organization", "profile"): "auto-created by signal",
    # Every Organization/Role/Scope creation path sets these keys.
    ("mgmt", "ManagementOrganization", "slug"): "always set on creation",
    ("main", "Organization", "slug"): "always set on creation",
    ("mgmt", "ManagementScope", "identifier"): "always set on creation",
    ("mgmt", "ManagementRole", "identifier"): "always set on creation",
    ("main", "Role", "identifier"): "always set on creation",
    # Resolvers with a non-null fallback (`self.description or self.identifier`, ...).
    ("mgmt", "ManagementScope", "description"): "resolver falls back to identifier",
    ("mgmt", "ManagementRole", "description"): "resolver falls back to identifier",
    ("main", "Role", "description"): "resolver falls back to identifier",
    ("main", "Organization", "name"): "resolver falls back to slug/id",
    # `Comment.resolved` is a bool resolver over `resolved_by` (substring false positive).
    ("main", "Comment", "resolved"): "bool resolver, not the nullable column",
}

IGNORED_TYPE_SUFFIXES = ("Filter", "Order", "Ordering", "Input")
LOGICAL_OPS = {"AND", "OR", "NOT", "DISTINCT"}


def _is_nonnull(t):
    return not isinstance(t, StrawberryOptional)


def _model_field(model, name):
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _model_has(model, name):
    return _model_field(model, name) is not None or hasattr(model, name)


def _self_attrs(fn):
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return set()
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            out.add(node.attr)
    return out


def _sweep(schema, label):
    findings = []
    seen = set()
    for name, sd in schema.schema_converter.type_map.items():
        if name.endswith(IGNORED_TYPE_SUFFIXES):
            continue
        defn = getattr(sd, "definition", None)
        if defn is None or not hasattr(defn, "origin"):
            continue
        origin = defn.origin
        if id(origin) in seen:
            continue
        seen.add(id(origin))
        sdd = getattr(origin, "__strawberry_django_definition__", None)
        if sdd is None:
            continue
        model = sdd.model
        for f in defn.fields:
            fname = f.python_name
            if fname in LOGICAL_OPS:
                continue
            key = (label, name, fname)
            resolver = getattr(f, "base_resolver", None)
            if resolver is not None:
                fn = resolver.wrapped_func
                for attr in sorted(_self_attrs(fn)):
                    if not _model_has(model, attr):
                        findings.append((*key, "MISSING-ATTR", f"self.{attr} not on {model.__name__}"))
                mf = _model_field(model, fname)
                if (
                    mf is not None
                    and getattr(mf, "null", False)
                    and _is_nonnull(f.type)
                    and not isinstance(f.type, StrawberryList)
                    and f"return self.{fname}" in textwrap.dedent(inspect.getsource(fn))
                ):
                    findings.append((*key, "NULLABILITY", f"{model.__name__}.{fname} null=True, resolver returns it non-null"))
                continue
            dj_name = getattr(f, "django_name", None) or fname
            mf = _model_field(model, dj_name)
            if mf is None:
                if not hasattr(model, dj_name):
                    findings.append((*key, "BARE-FK", f"{model.__name__} has no '{dj_name}'"))
                continue
            if getattr(mf, "null", False) and _is_nonnull(f.type) and not isinstance(f.type, StrawberryList):
                if mf.many_to_many or mf.one_to_many:
                    continue
                findings.append((*key, "NULLABILITY", f"{model.__name__}.{dj_name} null=True but field non-null"))
    return findings


def _unexpected(schema, label):
    return [f for f in _sweep(schema, label) if (f[0], f[1], f[2]) not in ACCEPTED]


def test_management_schema_matches_models():
    bad = _unexpected(management_schema, "mgmt")
    assert not bad, "\n".join("\t".join(f) for f in bad)


def test_main_schema_matches_models():
    bad = _unexpected(main_schema, "main")
    assert not bad, "\n".join("\t".join(f) for f in bad)


def test_accepted_list_has_no_stale_entries():
    """Keep ACCEPTED honest: every entry must still correspond to a real finding."""
    live = {(f[0], f[1], f[2]) for f in _sweep(management_schema, "mgmt") + _sweep(main_schema, "main")}
    stale = sorted(k for k in ACCEPTED if k not in live)
    assert not stale, f"stale ACCEPTED entries (finding no longer exists, remove them): {stale}"

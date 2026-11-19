"""Which aliases a client is handed, and in what order: the hub owner's alias policy.

An alias is handed out when it is ``enabled``, ranked by its ``priority`` (or its reach's
default, :func:`fakts.services.alias_reach.default_priority`), lower first. Rules
(:class:`fakts.models.AliasRule`) change that for some clients: one client, every client of an
app, every client of a user, or every client of a member holding a role. Where several rules
name the same alias, the most specific subject wins (client, then app, then user, then role),
and at the same one a ``disable`` beats a ``priority``.

Clients follow the order they are handed unless their own alias policy narrows it; what they
can reach is still decided by their challenge.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterable

from django.db.models import Q

from fakts import enums
from fakts.services.alias_reach import default_priority

if TYPE_CHECKING:
    from fakts import models

Rules = dict[int, list["models.AliasRule"]]
"""The rules that apply to one client, by alias id."""

#: Lower is more specific.
_SPECIFICITY = ("client", "app", "user", "role")


def rules_for(client: "models.Client") -> Rules:
    """Every rule that names ``client``: itself, its app, its user, or a role its member holds."""
    from fakts import models

    subjects = Q(client=client)
    if client.release_id:
        subjects |= Q(app_id=client.release.app_id)
    if client.membership_id:
        subjects |= Q(user_id=client.membership.user_id)
        subjects |= Q(role__in=client.membership.roles.all())
    rules: Rules = defaultdict(list)
    for rule in models.AliasRule.objects.filter(subjects):
        rules[rule.alias_id].append(rule)
    return rules


def _specificity(rule: "models.AliasRule") -> int:
    return next(index for index, field in enumerate(_SPECIFICITY) if getattr(rule, f"{field}_id"))


def effective_priority(alias: "models.InstanceAlias", rules: Iterable["models.AliasRule"] = ()) -> int | None:
    """Where ``alias`` ranks for a client its ``rules`` were found for; ``None`` when that client
    is not handed it (disabled, or disabled for it)."""
    if not alias.enabled:
        return None
    ranked = sorted(rules, key=lambda rule: (_specificity(rule), rule.effect != enums.AliasRuleEffectChoices.DISABLE.value))
    if ranked:
        winner = ranked[0]
        if winner.effect == enums.AliasRuleEffectChoices.DISABLE.value:
            return None
        return winner.priority
    return own_priority(alias)


def own_priority(alias: "models.InstanceAlias") -> int:
    """The alias's priority before any rule: the owner's, or its reach's default."""
    return alias.priority if alias.priority is not None else default_priority(alias.reach, alias.ssl)

"""Which ``ServiceInstance`` satisfies a manifest requirement, and how
``auto_compose`` records the outcome.

``find_instance_for_requirement_and_hub`` chains ``|``/``&``/``~`` over four
M2M allow/deny lists — exactly the shape that regresses silently when someone
reorders a term. ``tests/test_services.py`` only covers the no-requirements and
rollback cases of ``auto_compose``. These pin the ACL semantics, hub isolation,
and every ``statuses`` outcome (granted / denied / unavailable), plus that
re-composing drops stale mappings.
"""

from typing import Any

import pytest
from django.contrib.auth.models import Group

from fakts import base_models
from fakts import models as fmodels
from fakts.services import hubs, rendering, versions
from karakter.models import User
from tests import factories


def _instance(
    hub: fmodels.Hub, service_identifier: str = "com.example.db"
) -> fmodels.ServiceInstance:
    service = factories.make_service(identifier=service_identifier)
    return factories.make_service_instance(
        hub=hub, release=factories.make_service_release(service=service)
    )


def _requirement(
    service: str = "com.example.db", key: str = "db", optional: bool = False
) -> base_models.Requirement:
    return base_models.Requirement(key=key, service=service, optional=optional)


def _manifest(*requirements: base_models.Requirement) -> base_models.Manifest:
    return base_models.Manifest(
        identifier="com.example.app",
        version="1.0.0",
        scopes=[],
        requirements=list(requirements),
    )


def _find(
    instance_hub: fmodels.Hub, user: User, **kw: Any
) -> fmodels.ServiceInstance | None:
    return rendering.find_instance_for_requirement_and_hub(
        _requirement(**kw), user, instance_hub
    )


@pytest.mark.django_db
def test_instance_without_an_allowlist_is_open_to_everyone() -> None:
    hub = factories.make_hub()
    instance = _instance(hub)

    assert _find(hub, factories.make_user()) == instance


@pytest.mark.django_db
def test_allowlisted_user_gets_the_instance_and_others_do_not() -> None:
    hub = factories.make_hub()
    instance = _instance(hub)
    allowed, other = factories.make_user(), factories.make_user()
    instance.allowed_users.add(allowed)

    assert _find(hub, allowed) == instance
    assert _find(hub, other) is None


@pytest.mark.django_db
def test_denied_user_is_excluded_even_when_also_allowlisted() -> None:
    hub = factories.make_hub()
    instance = _instance(hub)
    user = factories.make_user()
    instance.allowed_users.add(user)
    instance.denied_users.add(user)

    assert _find(hub, user) is None


@pytest.mark.django_db
def test_group_deny_overrides_a_user_allow() -> None:
    hub = factories.make_hub()
    instance = _instance(hub)
    user = factories.make_user()
    banned = Group.objects.create(name="banned")
    user.groups.add(banned)
    instance.allowed_users.add(user)
    instance.denied_groups.add(banned)

    assert _find(hub, user) is None


@pytest.mark.django_db
def test_same_service_in_another_hub_is_never_returned() -> None:
    mine, theirs = factories.make_hub(), factories.make_hub()
    _instance(theirs)

    assert _find(mine, factories.make_user()) is None


@pytest.mark.django_db
def test_service_identifier_must_match() -> None:
    hub = factories.make_hub()
    _instance(hub, "com.example.other")

    assert _find(hub, factories.make_user(), service="com.example.db") is None


# --------------------------------------------------------------------------- #
# auto_compose statuses
# --------------------------------------------------------------------------- #


def _client_in_hub() -> tuple[fmodels.Hub, fmodels.Client]:
    hub = factories.make_hub()
    membership = factories.make_membership(organization=hub.organization)
    return hub, factories.make_client(membership=membership, hub=hub)


@pytest.mark.django_db
def test_granted_requirement_is_mapped_and_recorded() -> None:
    hub, client = _client_in_hub()
    instance = _instance(hub)

    rendering.auto_compose(
        client, _manifest(_requirement()), client.user, client.organization
    )

    client.refresh_from_db()
    assert client.statuses == {"db": "granted"}
    assert list(client.mappings.values_list("key", "instance_id")) == [
        ("db", instance.id)
    ]
    assert client.requirements_hash


@pytest.mark.django_db
def test_a_version_outside_the_requirement_is_said_and_still_handed_out() -> None:
    hub, client = _client_in_hub()
    service = factories.make_service(identifier="com.example.db")
    local = factories.make_service_release(service=service, version=versions.LOCAL)
    instance = factories.make_service_instance(hub=hub, release=local)
    wanted = base_models.Requirement(
        key="db", service="com.example.db", version=">=12.1"
    )
    rendering.auto_compose(client, _manifest(wanted), client.user, client.organization)
    mapping = client.mappings.get()
    assert versions.satisfies(instance.release.version, mapping.wants_version) is None

    report = base_models.HubHealthReport(
        healthy=True,
        instances={instance.instance_id: {"healthy": True, "version": "11.4.0"}},
    )
    hubs.report_hub_health(hub, report)

    # What the service says it runs is its release, and a manifest that cannot tell keeps it.
    instance.refresh_from_db()
    assert instance.release.version == "11.4.0"
    assert versions.release_for(service, "1.0.0", instance) == instance.release
    assert versions.satisfies(instance.release.version, mapping.wants_version) is False
    assert client.statuses == {"db": "granted"}


@pytest.mark.django_db
def test_declined_optional_requirement_is_recorded_and_not_mapped() -> None:
    hub, client = _client_in_hub()
    _instance(hub)

    rendering.auto_compose(
        client,
        _manifest(_requirement(optional=True)),
        client.user,
        client.organization,
        declined_requirements=["db"],
    )

    client.refresh_from_db()
    assert client.statuses == {"db": "denied"}
    assert not client.mappings.exists()


@pytest.mark.django_db
def test_declining_a_required_requirement_is_ignored() -> None:
    """Only *optional* requirements can be declined; a required one is still resolved."""
    hub, client = _client_in_hub()
    _instance(hub)

    rendering.auto_compose(
        client,
        _manifest(_requirement()),
        client.user,
        client.organization,
        declined_requirements=["db"],
    )

    client.refresh_from_db()
    assert client.statuses == {"db": "granted"}


@pytest.mark.django_db
def test_missing_optional_requirement_is_unavailable_and_the_rest_still_compose() -> (
    None
):
    hub, client = _client_in_hub()
    _instance(hub)

    rendering.auto_compose(
        client,
        _manifest(
            _requirement(),
            _requirement(service="com.missing", key="cache", optional=True),
        ),
        client.user,
        client.organization,
    )

    client.refresh_from_db()
    assert client.statuses == {"db": "granted", "cache": "unavailable"}
    assert list(client.mappings.values_list("key", flat=True)) == ["db"]


@pytest.mark.django_db
def test_recomposing_with_a_shrunk_manifest_drops_the_stale_mapping() -> None:
    hub, client = _client_in_hub()
    _instance(hub)
    _instance(hub, "com.example.cache")

    rendering.auto_compose(
        client,
        _manifest(
            _requirement(), _requirement(service="com.example.cache", key="cache")
        ),
        client.user,
        client.organization,
    )
    assert set(client.mappings.values_list("key", flat=True)) == {"db", "cache"}

    rendering.auto_compose(
        client, _manifest(_requirement()), client.user, client.organization
    )

    client.refresh_from_db()
    assert list(client.mappings.values_list("key", flat=True)) == ["db"]
    assert client.statuses == {"db": "granted"}

"""The ``ensuremandates`` setup job keeps the config's mandate keys in place.

It runs for every build, so a second run changes nothing; an entry dropped from the
config has its key revoked, and a revoked key is never revived.
"""

import json

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from fakts import models
from lok_server.configuration import MandateKeySettings
from tests import factories

KEY = "k" * 40
GRANT = "urn:fakts:grant-type:mandate-key"
APP = {"identifier": "com.example.configured", "version": "1.0.0", "scopes": [], "requirements": []}


def _spec(hub, **overrides) -> MandateKeySettings:
    return MandateKeySettings(
        name="pytest",
        key=overrides.pop("key", KEY),
        user=hub.creator.username,
        organization=hub.organization.slug,
        hub=hub.identifier,
        **overrides,
    )


def _run(*specs):
    with override_settings(MANDATE_KEYS=list(specs)):
        call_command("ensuremandates")


def _snapshot():
    return list(models.Mandate.objects.order_by("pk").values())


def _login(client, key=KEY, manifest=APP):
    return client.post(reverse("token"), data={"grant_type": GRANT, "key": key, "manifest": json.dumps(manifest)}, secure=True)


@pytest.fixture
def hub():
    hub = factories.make_hub()
    factories.make_membership(user=hub.creator, organization=hub.organization)
    return hub


@pytest.mark.django_db
def test_a_configured_key_logs_an_app_in_and_a_second_run_changes_nothing(client, hub):
    _run(_spec(hub))
    first = _snapshot()
    assert _login(client).status_code == 200

    before = _snapshot()
    _run(_spec(hub))

    assert len(first) == 1 and first[0]["origin"] == "config"
    # The open pin was fixed by the first login; the second run leaves it (and all else) alone.
    assert _snapshot() == before


@pytest.mark.django_db
def test_an_entry_dropped_from_the_config_is_revoked_and_stays_revoked(client, hub):
    _run(_spec(hub))
    _run()

    mandate = models.Mandate.objects.get()
    assert mandate.revocation_reason == "removed_from_config"
    assert _login(client).status_code == 400

    _run(_spec(hub))
    assert models.Mandate.objects.live().count() == 0


@pytest.mark.django_db
def test_a_changed_key_supersedes_the_old_one(client, hub):
    _run(_spec(hub))
    _run(_spec(hub, key="n" * 40))

    assert _login(client).status_code == 400
    assert _login(client, key="n" * 40).status_code == 200
    assert models.Mandate.objects.get(revoked_at__isnull=False).revocation_reason == "superseded"


@pytest.mark.django_db
def test_changed_settings_are_written_in_place(hub):
    _run(_spec(hub))
    mandate = models.Mandate.objects.get()

    _run(_spec(hub, app="com.example.configured", max_clients=2, allow_mesh=True))

    mandate.refresh_from_db()
    assert (mandate.pin.identifier, mandate.max_clients, mandate.allow_mesh) == ("com.example.configured", 2, True)
    assert models.Mandate.objects.count() == 1

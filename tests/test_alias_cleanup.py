"""Regression tests for the alias-system consistency cleanup.

Covers: the unified `upsert_instance_alias` helper (full field set, null-challenge
guard, upsert identity), the nullable `layer` GraphQL field, the pruned
management order/filter, the removed wrong-entity `*_instance_alias` mutations,
and `UsedAlias.used_at` refreshing on re-report.
"""

import pytest
from asgiref.sync import sync_to_async

from api.management.schema import schema as management_schema
from fakts import base_models, models
from fakts.services import aliases
from lok_server.schema import schema as lok_schema
from tests import factories
from tests.conftest import build_auth_context


# --------------------------------------------------------------------------- #
# upsert_instance_alias — the single shared persistence helper.
# --------------------------------------------------------------------------- #

def _staging(**kw) -> base_models.StagingAlias:
    kw.setdefault("id", "alias-x")
    kw.setdefault("host", "example.com")
    return base_models.StagingAlias(**kw)


@pytest.mark.django_db
def test_upsert_persists_full_field_set():
    instance = factories.make_service_instance()
    alias, created = aliases.upsert_instance_alias(
        instance,
        _staging(name="friendly", scope="network", public=True, challenge="ch", kind="absolute", port=8080, path="/p"),
    )
    assert created is True
    alias.refresh_from_db()
    assert (alias.name, alias.scope, alias.public, alias.challenge) == ("friendly", "network", True, "ch")
    assert (alias.host, alias.port, alias.path, alias.kind) == ("example.com", 8080, "/p", "absolute")


@pytest.mark.django_db
def test_upsert_null_challenge_falls_back_to_model_default():
    """A staging alias without a challenge must not violate the NOT NULL column."""
    instance = factories.make_service_instance()
    alias, _ = aliases.upsert_instance_alias(instance, _staging(challenge=None))
    alias.refresh_from_db()
    assert alias.challenge == "ht"  # model default, not NULL


@pytest.mark.django_db
def test_upsert_null_name_falls_back_to_alias_id():
    instance = factories.make_service_instance()
    alias, _ = aliases.upsert_instance_alias(instance, _staging(id="the-id", name=None))
    assert alias.name == "the-id"


@pytest.mark.django_db
def test_upsert_is_idempotent_on_natural_key():
    """Re-upserting the same (host, port, ssl, path, kind) updates rather than duplicates."""
    instance = factories.make_service_instance()
    a1, created1 = aliases.upsert_instance_alias(instance, _staging(kind="absolute", public=False))
    a2, created2 = aliases.upsert_instance_alias(instance, _staging(kind="absolute", public=True))

    assert created1 is True and created2 is False
    assert a1.id == a2.id
    assert instance.aliases.count() == 1
    a2.refresh_from_db()
    assert a2.public is True  # descriptive field updated in place


@pytest.mark.django_db
def test_both_persistence_paths_produce_equivalent_rows():
    """hubs path and the helper agree on the stored row for one staging alias."""
    staging = _staging(name="n", scope="network", public=True, challenge="ht", kind="absolute", port=9, path="/x")

    org_a = factories.make_organization()
    manifest = base_models.HubManifest(
        identifier="com.example.parity",
        instances=[
            base_models.InstanceRequest(
                identifier="inst-p",
                manifest=base_models.ServiceManifest(identifier="com.example.psvc", version="1.0.0"),
                aliases=[staging],
            )
        ],
    )
    from fakts.services import hubs

    hubs.create_hub_from_manifest(manifest, org_a)
    via_path = models.InstanceAlias.objects.get(name="n")

    instance_b = factories.make_service_instance()
    via_helper, _ = aliases.upsert_instance_alias(instance_b, staging)

    fields = ("name", "scope", "public", "challenge", "host", "port", "path", "kind", "ssl")
    assert {f: getattr(via_path, f) for f in fields} == {f: getattr(via_helper, f) for f in fields}


# --------------------------------------------------------------------------- #
# GraphQL surface regressions.
# --------------------------------------------------------------------------- #

ALIASES_WITH_LAYER = """
    query {
        serviceInstances { id aliases { id layer { id } } }
    }
"""


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_aliases_layer_resolves_when_null():
    """`aliases { layer }` must not raise a non-null violation for the (normal) null layer."""
    def setup():
        membership = factories.make_membership()
        request_client = factories.make_client(membership=membership)
        # Service instances are tenant-scoped on this schema, so the instance
        # must live in the caller's organization to be listed.
        hub = factories.make_hub(organization=membership.organization)
        instance = factories.make_service_instance(hub=hub)
        models.InstanceAlias.objects.create(instance=instance, host="example.com", kind="absolute")
        return build_auth_context(membership.user, membership.organization, request_client)

    context = await sync_to_async(setup)()
    result = await lok_schema.execute(ALIASES_WITH_LAYER, context_value=context)
    assert not result.errors, result.errors
    all_aliases = [a for inst in result.data["serviceInstances"] for a in inst["aliases"]]
    assert all_aliases, "expected the created alias to be returned (test would otherwise be vacuous)"
    # every alias resolves layer as null without error
    for alias in all_aliases:
        assert alias["layer"] is None


def test_management_alias_order_and_filter_have_no_phantom_columns():
    sdl = str(management_schema)
    # order type exposes only `name` now (no createdAt/updatedAt)
    assert "ManagementInstanceAliasOrder" in sdl
    assert "createdAt" not in _block(sdl, "input ManagementInstanceAliasOrder")
    assert "updatedAt" not in _block(sdl, "input ManagementInstanceAliasOrder")
    # filter dropped the `functional` field
    assert "functional" not in _block(sdl, "input ManagementInstanceAliasFilter")


def test_wrong_entity_instance_alias_mutations_removed():
    sdl = str(lok_schema)
    assert "createInstanceAlias" not in sdl
    assert "updateInstanceAlias" not in sdl
    # the correct service-instance mutations remain
    assert "createServiceInstance" in sdl
    assert "updateServiceInstance" in sdl


def _block(sdl: str, header: str) -> str:
    """Return the `{ ... }` body of the GraphQL type/input whose declaration starts with `header`."""
    start = sdl.index(header)
    open_brace = sdl.index("{", start)
    close_brace = sdl.index("}", open_brace)
    return sdl[open_brace:close_brace]


# --------------------------------------------------------------------------- #
# UsedAlias.used_at refreshes on re-report (auto_now).
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_report_client_updates_usage_in_place():
    from fakts.services import clients

    client = factories.make_client()
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(instance=instance, host="example.com", kind="absolute")

    clients.report_client(client, base_models.ReportRequest(
        alias_reports={"db": base_models.AliasReport(alias_id=str(alias.id), valid=True)},
    ))
    usage = models.UsedAlias.objects.get(client=client, key="db")
    first_ts = usage.used_at
    assert usage.valid is True

    clients.report_client(client, base_models.ReportRequest(
        alias_reports={"db": base_models.AliasReport(alias_id=str(alias.id), valid=False, reason="unreachable")},
    ))
    usage.refresh_from_db()
    # same row updated, not duplicated
    assert models.UsedAlias.objects.filter(client=client, key="db").count() == 1
    assert usage.valid is False
    assert usage.reason == "unreachable"
    assert usage.used_at >= first_ts  # auto_now refreshed the timestamp

"""The data step of ``fakts.0015_alias_kind_drop_relative``.

``relative`` is no longer an alias kind. Rows stored with it become absolute
aliases at their stored host; rows that cannot (no host) or need not (an
absolute twin exists) are deleted, along with their usage reports.

A relative row can no longer be written through the ORM, so the rows are
created with another kind and turned into relative ones in SQL.
"""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from fakts import models
from tests import factories

migration = import_module("fakts.migrations.0015_alias_kind_drop_relative")


def _make_relative(alias: models.InstanceAlias, *, host: str | None) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE fakts_instancealias SET kind = 'relative', host = %s WHERE id = %s",
            [host, alias.pk],
        )
    return alias.pk


def _run() -> None:
    migration.drop_relative_aliases(apps, SimpleNamespace(connection=connection))


@pytest.mark.django_db
def test_a_relative_alias_with_a_host_becomes_absolute_at_that_host():
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(instance=instance, host="svc.example", port=8443, path="svc", kind="mesh")
    usage = models.UsedAlias.objects.create(alias=alias, client=factories.make_client(), key="svc")
    pk = _make_relative(alias, host="svc.example")

    _run()

    converted = models.InstanceAlias.objects.get(pk=pk)
    assert (converted.kind, converted.host, converted.port, converted.path) == ("absolute", "svc.example", 8443, "svc")
    assert models.UsedAlias.objects.filter(pk=usage.pk, alias=converted).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("host", [None, ""])
def test_a_relative_alias_without_a_host_is_deleted_with_its_usages(host):
    instance = factories.make_service_instance()
    alias = models.InstanceAlias.objects.create(instance=instance, host="placeholder", port=80, kind="mesh")
    usage = models.UsedAlias.objects.create(alias=alias, client=factories.make_client(), key="svc")
    pk = _make_relative(alias, host=host)

    _run()

    assert not models.InstanceAlias.objects.filter(pk=pk).exists()
    assert not models.UsedAlias.objects.filter(pk=usage.pk).exists()


@pytest.mark.django_db
def test_a_relative_alias_that_duplicates_an_absolute_one_is_deleted():
    """Converting it would break the one-alias-per-address constraint."""
    instance = factories.make_service_instance()
    absolute = models.InstanceAlias.objects.create(instance=instance, host="svc.example", port=443, path="svc", kind="absolute")
    twin = models.InstanceAlias.objects.create(instance=instance, host="svc.example", port=443, path="svc", kind="mesh")
    pk = _make_relative(twin, host="svc.example")

    _run()

    assert not models.InstanceAlias.objects.filter(pk=pk).exists()
    assert models.InstanceAlias.objects.get(pk=absolute.pk).kind == "absolute"


@pytest.mark.django_db
def test_other_aliases_are_left_alone():
    instance = factories.make_service_instance()
    absolute = models.InstanceAlias.objects.create(instance=instance, host="svc.example", port=443, kind="absolute")
    docker = models.InstanceAlias.objects.create(instance=instance, host="gateway", port=80, ssl=False, kind="docker")
    mesh = models.InstanceAlias.objects.create(instance=instance, port=80, ssl=False, kind="mesh")

    _run()

    kinds = dict(models.InstanceAlias.objects.filter(instance=instance).values_list("pk", "kind"))
    assert kinds == {absolute.pk: "absolute", docker.pk: "docker", mesh.pk: "mesh"}

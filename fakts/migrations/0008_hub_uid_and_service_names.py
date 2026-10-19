"""Existing hubs get a uid, and their instances the name of their service in the hub.

A hub authorized before hubs named themselves has no uid of its own: it is given one here, and
replaced the next time it is authorized (it then sends its own). An instance's ``instance_id`` was
``default`` (device-code hubs) or the catalogue's display name (kommunity hubs); it becomes the
service's name, the last part of its identifier (``live.arkitekt.mikro`` -> ``mikro``), with the
instance's pk added where two instances of one hub would share it.
"""

import uuid

from django.db import migrations


def forwards(apps, schema_editor):
    Hub = apps.get_model("fakts", "Hub")
    ServiceInstance = apps.get_model("fakts", "ServiceInstance")
    for hub in Hub.objects.filter(uid__isnull=True):
        hub.uid = uuid.uuid4()
        hub.save(update_fields=["uid"])
    taken: dict[int, set[str]] = {}
    for instance in ServiceInstance.objects.select_related("service").order_by("hub_id", "pk"):
        name = instance.service.identifier.rsplit(".", 1)[-1].lower() or "service"
        names = taken.setdefault(instance.hub_id, set())
        if name in names:
            name = f"{name}-{instance.pk}"
        names.add(name)
        instance.instance_id = name
        instance.save(update_fields=["instance_id"])


class Migration(migrations.Migration):
    dependencies = [("fakts", "0007_hub_uid")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

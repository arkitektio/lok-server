from django.db import migrations
from django.db.models import Q


def name_nameless_services(apps, schema_editor):
    """Hubs authorized by device code made their services without a name: name them as hubs.py does."""
    Service = apps.get_model("fakts", "Service")
    for service in Service.objects.filter(Q(name="") | Q(name__isnull=True)):
        service.name = service.identifier
        service.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("fakts", "0011_real_service_versions"),
    ]

    operations = [
        migrations.RunPython(name_nameless_services, migrations.RunPython.noop),
    ]

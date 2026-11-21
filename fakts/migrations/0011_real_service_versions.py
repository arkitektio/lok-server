from django.db import migrations

PLACEHOLDER = "1.0.0"
LOCAL = "0+local"


def placeholder_is_no_version(apps, schema_editor):
    """Installers wrote 1.0.0 for every service: such a release names no version."""
    ServiceRelease = apps.get_model("fakts", "ServiceRelease")
    ServiceInstance = apps.get_model("fakts", "ServiceInstance")
    for release in ServiceRelease.objects.filter(version=PLACEHOLDER):
        local = ServiceRelease.objects.filter(service_id=release.service_id, version=LOCAL).first()
        if local is None:
            release.version = LOCAL
            release.save(update_fields=["version"])
        else:
            ServiceInstance.objects.filter(release=release).update(release=local)
            release.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("fakts", "0010_service_versions"),
    ]

    operations = [
        # The version an instance runs is its release's. Only the model forgets the field: the
        # column stays (nullable, unread) so that 10.12, which reads it, still runs on this
        # schema. Drop it in the next major.
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.RemoveField(model_name="serviceinstance", name="reported_version")],
        ),
        migrations.RunPython(placeholder_is_no_version, migrations.RunPython.noop),
    ]

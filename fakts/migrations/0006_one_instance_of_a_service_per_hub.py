"""A hub runs one instance of a service per instance_id.

Authorizing a hub again used to make a second instance of a service when another admin accepted
it, or when the service came with another release: the lookup keyed on the steward, the release
and the device, which are what an instance has, not what it is. Each duplicate had its own id,
which tokens name (``aud``) and guests' collaborations are listed under, so neither the hub nor
lok could tell which one a service is.

Here every instance gets its service (its release's), and the instances of one service in one
hub with one instance_id become one: the newest (the latest release and key), which takes over
what pointed at the others. Their aliases go with them unless it has none of its own.
"""

import django.db.models.deletion
from django.db import migrations, models


def services_of_releases(apps, schema_editor):
    ServiceInstance = apps.get_model("fakts", "ServiceInstance")
    for instance in ServiceInstance.objects.select_related("release").iterator():
        ServiceInstance.objects.filter(pk=instance.pk).update(service_id=instance.release.service_id)


def merge_duplicates(apps, schema_editor):
    ServiceInstance = apps.get_model("fakts", "ServiceInstance")
    Mapping = apps.get_model("fakts", "ServiceInstanceMapping")
    Permission = apps.get_model("fakts", "InstancePermission")
    Alias = apps.get_model("fakts", "InstanceAlias")
    Role = apps.get_model("karakter", "Role")
    Scope = apps.get_model("karakter", "Scope")
    Grant = apps.get_model("karakter", "CollaborationGrant")

    groups = (
        ServiceInstance.objects.values("hub_id", "service_id", "instance_id")
        .annotate(count=models.Count("id"))
        .filter(count__gt=1)
    )
    for group in groups:
        instances = list(
            ServiceInstance.objects.filter(hub_id=group["hub_id"], service_id=group["service_id"], instance_id=group["instance_id"]).order_by("pk")
        )
        keep, merged = instances[-1], instances[:-1]
        for old in merged:
            Mapping.objects.filter(instance=old).update(instance=keep)
            Permission.objects.filter(instance=old).update(instance=keep)
            if not Alias.objects.filter(instance=keep).exists():
                Alias.objects.filter(instance=old).update(instance=keep)
            Role.objects.filter(creating_instance=old).update(creating_instance=keep)
            Scope.objects.filter(creating_instance=old).update(creating_instance=keep)
            for role in Role.objects.filter(used_by=old):
                role.used_by.add(keep)
            for scope in Scope.objects.filter(used_by=old):
                scope.used_by.add(keep)
            for grant in Grant.objects.filter(instance=old):
                held = Grant.objects.filter(membership_id=grant.membership_id, instance=keep, collaboration_id=grant.collaboration_id).first()
                if held is None:
                    grant.instance = keep
                    grant.save(update_fields=["instance"])
                else:
                    held.roles = sorted({*(held.roles or []), *(grant.roles or [])})
                    held.save(update_fields=["roles"])
                    grant.delete()
            for name in ("denied_users", "denied_groups", "allowed_users", "allowed_groups", "allowed_organizations"):
                getattr(keep, name).add(*getattr(old, name).all())
            old.delete()


class Migration(migrations.Migration):
    # The data steps run in transactions of their own: Postgres refuses to alter a table with
    # foreign-key checks still pending from rows changed in the same transaction.
    atomic = False

    dependencies = [
        ("fakts", "0005_device_code_polling_client_id"),
        ("karakter", "0005_collaboration_grants"),
    ]

    operations = [
        migrations.AddField(
            model_name="serviceinstance",
            name="service",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="instances",
                to="fakts.service",
                help_text="The service this is an instance of: its release's, held here so that a hub runs one instance of a service per instance_id",
            ),
        ),
        migrations.RunPython(services_of_releases, migrations.RunPython.noop),
        migrations.RunPython(merge_duplicates, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="serviceinstance",
            name="service",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="instances",
                to="fakts.service",
                help_text="The service this is an instance of: its release's, held here so that a hub runs one instance of a service per instance_id",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="serviceinstance",
            name="Only one instance_id per release, organization and device and instance",
        ),
        migrations.AddConstraint(
            model_name="serviceinstance",
            constraint=models.UniqueConstraint(fields=("hub", "service", "instance_id"), name="one_instance_of_a_service_per_hub_and_instance_id"),
        ),
    ]

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    """Every hub has its uid now; a hub is one per uid in an organization, and a service one per name in a hub."""

    dependencies = [("fakts", "0008_hub_uid_and_service_names")]

    operations = [
        migrations.RemoveConstraint(model_name="hub", name="Only one hub identifier per organization"),
        migrations.RemoveConstraint(model_name="serviceinstance", name="one_instance_of_a_service_per_hub_and_instance_id"),
        migrations.AlterField(
            model_name="hub",
            name="uid",
            field=models.UUIDField(
                default=uuid.uuid4,
                help_text="The hub's identity: a uuid the hub generated for itself and sends when it is authorized (a kommunity partner, in lok's config). "
                "Tokens for its services carry it (`hub`); the same hub authorized in another organization keeps it."
            ),
        ),
        migrations.AlterField(
            model_name="serviceinstance",
            name="instance_id",
            field=models.CharField(max_length=1000, help_text="The service's name in its hub (`mikro`): unique there, and what tokens for it name as their audience."),
        ),
        migrations.AddConstraint(model_name="hub", constraint=models.UniqueConstraint(fields=("organization", "uid"), name="one_hub_of_a_uid_per_organization")),
        migrations.AddConstraint(model_name="serviceinstance", constraint=models.UniqueConstraint(fields=("hub", "instance_id"), name="one_service_of_a_name_per_hub")),
    ]

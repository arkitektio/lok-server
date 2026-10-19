from django.db import migrations, models


class Migration(migrations.Migration):
    """A hub's own identity (``Hub.uid``), nullable until 0008 gives the existing hubs one."""

    dependencies = [("fakts", "0006_one_instance_of_a_service_per_hub")]

    operations = [
        migrations.AddField(
            model_name="hub",
            name="uid",
            field=models.UUIDField(null=True, help_text="The hub's identity: a uuid the hub generated for itself and sends when it is authorized."),
        ),
    ]

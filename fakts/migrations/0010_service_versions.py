from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fakts", "0009_alias_priority"),
    ]

    operations = [
        migrations.AddField(
            model_name="serviceinstance",
            name="reported_version",
            field=models.CharField(
                blank=True,
                help_text="The version the service runs, as its hub last reported it (/f/hubhealth/). Null until it says.",
                max_length=1000,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="serviceinstancemapping",
            name="wants_version",
            field=models.CharField(
                blank=True,
                help_text="The requirement's version specifier (`>=12.1,<14`), if it named one.",
                max_length=1000,
                null=True,
            ),
        ),
    ]

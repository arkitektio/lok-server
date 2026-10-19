"""Each membership caches whether it holds the organization's admin role (karakter.roles).

The column is added unset and filled from the roles here. The previous release runs on this
schema without keeping it: every build resyncs it (the ``syncadmins`` setup job) before serving.
"""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Exists, OuterRef


def fill_from_roles(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Set each membership's flag from whether it holds the admin role."""
    Membership = apps.get_model("karakter", "Membership")
    holds = Membership.roles.through.objects.filter(membership_id=OuterRef("pk"), role__identifier="admin")
    Membership.objects.update(is_admin=Exists(holds))


class Migration(migrations.Migration):
    dependencies = [
        ("karakter", "0006_invite_and_grant_mesh"),
    ]

    operations = [
        migrations.AddField(
            model_name="membership",
            name="is_admin",
            field=models.BooleanField(
                db_default=False,
                default=False,
                editable=False,
                help_text="Whether the member holds the organization's admin role: recomputed from `roles` whenever they change (karakter.roles), never set by hand.",
            ),
        ),
        migrations.RunPython(fill_from_roles, migrations.RunPython.noop),
    ]

# The `pak` (stashes) and `komment` (comments) apps were removed from lok on
# 2026-09-13; both features live in kraph now. Django cannot migrate an app that
# is no longer installed, so their tables, migration history and content types
# are cleaned up here. Every statement is idempotent: on a fresh database the
# tables were never created and this is a no-op.

from django.db import migrations


def drop_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    # Cascades to auth permissions and guardian object permissions.
    ContentType.objects.filter(app_label__in=["pak", "komment"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("karakter", "0004_organization_access_token_lifetime"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DROP TABLE IF EXISTS pak_stashitem CASCADE;
                DROP TABLE IF EXISTS pak_stash_shared_with CASCADE;
                DROP TABLE IF EXISTS pak_stash CASCADE;
                DROP TABLE IF EXISTS komment_comment_mentions CASCADE;
                DROP TABLE IF EXISTS komment_comment CASCADE;
                DELETE FROM django_migrations WHERE app IN ('pak', 'komment');
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(drop_content_types, migrations.RunPython.noop),
    ]

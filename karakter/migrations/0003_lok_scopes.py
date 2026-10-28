"""Give every organization lok's own scopes (karakter.scopes), which new ones get when made."""

from django.db import migrations

#: As karakter.scopes says them on the day of this migration: a migration never imports what changes.
LOK_SCOPES = (
    ("lok_read", "See your organizations, their members, groups and roles, and the devices, apps, hubs and services registered with them.", False),
    ("lok_profile", "Change your profile, your groups' profiles, your devices and your colours in an organization, and upload the images they show.", False),
    ("lok_organizations", "Create organizations, invite people, add them, and accept or decline who asks to join.", False),
    ("lok_mandates", "Issue and revoke mandates and their keys, which let an app act on your behalf.", False),
    ("lok_messages", "Send notifications, acknowledge yours, and register where you receive them.", False),
    ("lok_services", "Register and change service instances, and render their configuration.", False),
    ("lok_all", "Do everything in Lok on your behalf.", True),
)


def add_lok_scopes(apps, schema_editor):
    Organization = apps.get_model("karakter", "Organization")
    Scope = apps.get_model("karakter", "Scope")
    for organization in Organization.objects.all():
        for key, description, is_all_scope in LOK_SCOPES:
            Scope.objects.update_or_create(
                identifier=key,
                organization=organization,
                defaults={"description": description, "is_all_scope": is_all_scope, "is_builtin": True},
            )


class Migration(migrations.Migration):
    dependencies = [("karakter", "0002_service_role_flags")]

    operations = [migrations.RunPython(add_lok_scopes, migrations.RunPython.noop)]

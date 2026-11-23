"""Collaborations become lok's: one key across the organization's services.

A grant named a collaboration by its service instance and its id there; now it
names a ``Collaboration`` row, whose key every service holds its objects under.
Each distinct (organization, instance, id) a grant or an invite named becomes one
collaboration here, so every guest stays a guest of what they were granted. The
services' own rows get keys of their own, so what was shared under the old ids
is shared again under the new key before a guest sees it.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _collaboration(apps, cache: dict, organization_id: int, instance: str, local: str):  # noqa: ANN001, ANN202
    key = (organization_id, str(instance), str(local))
    if key not in cache:
        collaboration = apps.get_model("karakter", "Collaboration")
        cache[key] = collaboration.objects.create(
            organization_id=organization_id,
            name=f"Collaboration {local} of instance {instance}"[:200],
        )
    return cache[key]


def name_each_collaboration(apps, schema_editor) -> None:  # noqa: ANN001
    grant_model = apps.get_model("karakter", "CollaborationGrant")
    invite_model = apps.get_model("karakter", "Invite")
    cache: dict = {}
    for grant in grant_model.objects.select_related("membership"):
        grant.collaboration = _collaboration(
            apps,
            cache,
            grant.membership.organization_id,
            grant.instance_id,
            grant.local_id,
        )
        grant.save(update_fields=["collaboration"])
    for invite in invite_model.objects.filter(shared_collaborations__isnull=False):
        invite.shared_collaborations = [
            {
                "collaboration": str(
                    _collaboration(
                        apps,
                        cache,
                        invite.created_for_id,
                        entry.get("instance"),
                        entry.get("collaboration"),
                    ).key
                ),
                "roles": list(entry.get("roles") or []),
            }
            for entry in invite.shared_collaborations or []
        ]
        invite.save(update_fields=["shared_collaborations"])


class Migration(migrations.Migration):
    # The backfill queues deferred foreign-key checks on PostgreSQL; the ALTER TABLEs
    # after it would refuse to run in the same transaction.
    atomic = False

    dependencies = [
        ("karakter", "0004_membership_created_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Collaboration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "key",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        help_text="The collaboration's key across the hub: what tokens list, and every service names it by",
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="collaborations_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="collaborations",
                        to="karakter.organization",
                    ),
                ),
            ],
        ),
        # The old id's column is `collaboration_id`, the one the new foreign key takes:
        # out of its way first (and the constraint over it with it).
        migrations.RemoveConstraint(
            model_name="collaborationgrant",
            name="one_grant_per_collaboration_and_membership",
        ),
        migrations.RenameField(
            model_name="collaborationgrant", old_name="collaboration_id", new_name="local_id"
        ),
        migrations.AddField(
            model_name="collaborationgrant",
            name="collaboration",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="grants",
                to="karakter.collaboration",
            ),
        ),
        migrations.RunPython(name_each_collaboration, migrations.RunPython.noop),
        migrations.RemoveField(model_name="collaborationgrant", name="instance"),
        migrations.RemoveField(model_name="collaborationgrant", name="local_id"),
        migrations.AlterField(
            model_name="collaborationgrant",
            name="collaboration",
            field=models.ForeignKey(
                help_text="The collaboration granted",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="grants",
                to="karakter.collaboration",
            ),
        ),
        migrations.AlterField(
            model_name="collaborationgrant",
            name="mesh",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="Whether the guest's apps may join the organization's mesh to reach its hubs. "
                "Set from the invite it was granted through.",
            ),
        ),
        migrations.AlterField(
            model_name="invite",
            name="mesh",
            field=models.BooleanField(
                db_default=True,
                default=True,
                help_text="For a collaboration invite: whether its guests' apps may join the organization's mesh, "
                "reaching only the organization's hubs. Copied onto every grant it makes.",
            ),
        ),
        migrations.AlterField(
            model_name="invite",
            name="shared_collaborations",
            field=models.JSONField(
                blank=True,
                help_text="The collaborations this invite grants, as `{collaboration, roles}` entries (the collaboration's key). "
                "Null for an invite that grants full membership.",
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="collaborationgrant",
            constraint=models.UniqueConstraint(
                fields=("membership", "collaboration"),
                name="one_grant_per_collab_member",
            ),
        ),
    ]

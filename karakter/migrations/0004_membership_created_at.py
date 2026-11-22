"""Membership.created_at: when the user became a member.

Three steps, so existing rows never get the migration's own time:

1. the column is added without a default, so every existing membership starts null;
2. memberships an invite or a membership request created take the time that
   invite or request was answered; the others stay null (nothing says when);
3. only then does the column get its database default, which stamps the rows
   inserted from now on, including by a build older than this field.

Not atomic: on PostgreSQL the backfill's UPDATE queues the table's deferred
foreign-key checks, and step 3's ALTER TABLE refuses to run while they are
pending ("cannot ALTER TABLE ... because it has pending trigger events"). So each
step commits on its own, the backfill in its own transaction. An empty table (the
test database) has nothing pending, which is why only a real deployment failed.
"""

import django.db.models.functions.datetime
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill(apps, schema_editor):
    Membership = apps.get_model("karakter", "Membership")
    Invite = apps.get_model("karakter", "Invite")
    MembershipRequest = apps.get_model("karakter", "MembershipRequest")

    Membership.objects.filter(created_at__isnull=True, created_through__isnull=False).update(
        created_at=Subquery(Invite.objects.filter(pk=OuterRef("created_through_id")).values("responded_at")[:1])
    )
    Membership.objects.filter(created_at__isnull=True).update(
        created_at=Subquery(
            MembershipRequest.objects.filter(created_membership_id=OuterRef("pk"), responded_at__isnull=False)
            .order_by("responded_at")
            .values("responded_at")[:1]
        )
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("karakter", "0003_lok_scopes"),
    ]

    operations = [
        migrations.AddField(
            model_name="membership",
            name="created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop, atomic=True),
        migrations.AlterField(
            model_name="membership",
            name="created_at",
            field=models.DateTimeField(
                blank=True,
                db_default=django.db.models.functions.datetime.Now(),
                help_text="When the user became a member. Null for memberships older than this field whose invite or request did not tell (karakter migration 0004 backfilled the others).",
                null=True,
            ),
        ),
    ]

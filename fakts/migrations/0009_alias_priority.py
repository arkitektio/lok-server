from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('fakts', '0008_mesh_device_code_relay'),
        ('karakter', '0003_lok_scopes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='instancealias',
            name='priority',
            field=models.PositiveSmallIntegerField(blank=True, null=True, help_text="Where the hub's owner ranks this alias, lower first. Unset: its reach's default (internal 10, loopback 20, lan 30, mesh 40, public 50; one more for the other of plain and https)."),
        ),
        migrations.AddField(
            model_name='instancealias',
            name='enabled',
            field=models.BooleanField(default=True, help_text='Whether clients are handed this alias at all.'),
        ),
        migrations.CreateModel(
            name='AliasRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effect', models.CharField(choices=[('disable', 'Disable the alias for these clients'), ('priority', 'Rank the alias differently for these clients')], help_text='What the rule does to the alias for the clients it names.', max_length=10)),
                ('priority', models.PositiveSmallIntegerField(blank=True, null=True, help_text="The alias's priority for these clients (effect 'priority' only), lower first.")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('alias', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rules', to='fakts.instancealias')),
                ('app', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alias_rules', to='fakts.app', help_text='Every client of this app.')),
                ('client', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alias_rules', to='fakts.client', help_text='This one client.')),
                ('role', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alias_rules', to='karakter.role', help_text="Every client of a member holding this role.")),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alias_rules', to=settings.AUTH_USER_MODEL, help_text="Every client of this user.")),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(
                        condition=(
                            models.Q(app__isnull=False, client__isnull=True, user__isnull=True, role__isnull=True)
                            | models.Q(app__isnull=True, client__isnull=False, user__isnull=True, role__isnull=True)
                            | models.Q(app__isnull=True, client__isnull=True, user__isnull=False, role__isnull=True)
                            | models.Q(app__isnull=True, client__isnull=True, user__isnull=True, role__isnull=False)
                        ),
                        name='alias_rule_names_one_subject',
                    ),
                    models.CheckConstraint(
                        condition=models.Q(effect='priority', priority__isnull=False) | models.Q(effect='disable', priority__isnull=True),
                        name='alias_rule_priority_iff_ranking',
                    ),
                ],
            },
        ),
    ]

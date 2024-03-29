# Generated by Django 3.2.18 on 2023-05-02 12:08

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('oauth2_provider', '0001_initial'),
        ('infos', '0002_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='client',
            name='oauth2_client',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='client', to='oauth2_provider.application'),
        ),
        migrations.AddConstraint(
            model_name='client',
            constraint=models.UniqueConstraint(fields=('release', 'user'), name='Only one release per user'),
        ),
    ]

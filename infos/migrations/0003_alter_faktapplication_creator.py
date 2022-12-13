# Generated by Django 3.2.16 on 2022-11-17 14:15

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('infos', '0002_faktapplication_user'),
    ]

    operations = [
        migrations.AlterField(
            model_name='faktapplication',
            name='creator',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='managed_applications', to=settings.AUTH_USER_MODEL),
        ),
    ]

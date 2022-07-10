# Generated by Django 3.2.12 on 2022-05-19 08:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('infos', '0005_auto_20220516_1257'),
    ]

    operations = [
        migrations.AddField(
            model_name='configurationgraph',
            name='host',
            field=models.CharField(default='undefined', help_text='Is this appearing on a selection of hosts?', max_length=500),
            preserve_default=False,
        ),
    ]
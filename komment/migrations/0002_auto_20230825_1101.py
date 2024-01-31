# Generated by Django 3.2.19 on 2023-08-25 11:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('komment', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='comment',
            name='content_type',
        ),
        migrations.AddField(
            model_name='comment',
            name='object_identifier',
            field=models.CharField(default='FALSE', max_length=1000),
            preserve_default=False,
        ),
    ]
# Generated by Django 3.2.16 on 2022-11-17 13:58

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import infos.models
import infos.storage
import oauth2_provider.generators
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('oauth2_provider', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='App',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identifier', infos.models.IdentifierField(max_length=1000)),
                ('version', infos.models.VersionField(max_length=1000)),
                ('name', models.CharField(max_length=1000)),
                ('logo', models.ImageField(blank=True, max_length=1000, null=True, storage=infos.storage.PrivateMediaStorage(), upload_to='')),
            ],
        ),
        migrations.CreateModel(
            name='ConfigurationGraph',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=400)),
                ('version', models.CharField(max_length=600)),
                ('host', models.CharField(help_text='Is this appearing on a selection of hosts?', max_length=500)),
            ],
        ),
        migrations.CreateModel(
            name='Member',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=7000)),
            ],
        ),
        migrations.CreateModel(
            name='FaktApplication',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('website', 'Website'), ('desktop', 'Dekstop'), ('user', 'User')], max_length=1000, null=True)),
                ('token', models.CharField(default=uuid.uuid4, max_length=10000, unique=True)),
                ('client_id', models.CharField(default=oauth2_provider.generators.generate_client_id, max_length=1000, unique=True)),
                ('client_secret', models.CharField(default=oauth2_provider.generators.generate_client_secret, max_length=1000)),
                ('scopes', models.JSONField(default=list)),
                ('logo', models.ImageField(blank=True, max_length=1000, null=True, upload_to='')),
                ('app', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='fakt_applications', to='infos.app')),
                ('application', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='oauth2_provider.application')),
                ('creator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='DeviceCode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('code', models.CharField(max_length=100, unique=True)),
                ('name', models.CharField(max_length=100, null=True)),
                ('version', models.CharField(max_length=1000, null=True)),
                ('identifier', models.CharField(max_length=1000, null=True)),
                ('scopes', models.JSONField(default=list)),
                ('graph', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='codes', to='infos.configurationgraph')),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ConfigurationElement',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=1000)),
                ('values', models.JSONField()),
                ('graph', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='elements', to='infos.configurationgraph')),
            ],
        ),
        migrations.AddConstraint(
            model_name='app',
            constraint=models.UniqueConstraint(fields=('identifier', 'version'), name='Only one per identifier and version'),
        ),
        migrations.AddConstraint(
            model_name='faktapplication',
            constraint=models.UniqueConstraint(condition=models.Q(('kind__in', ['website', 'desktop'])), fields=('app', 'application'), name='Only one unique app per identifier and version'),
        ),
        migrations.AddConstraint(
            model_name='faktapplication',
            constraint=models.UniqueConstraint(condition=models.Q(('kind__in', ['user'])), fields=('app', 'application', 'creator'), name='Only one unique app per identifier and version if it is a user app'),
        ),
    ]

# Generated by Django 4.2.11 on 2024-08-07 07:15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('project_limits', '0002_ratelimitedtwofactorlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='PillowLagGaugeDefinition',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(db_index=True, max_length=512, unique=True)),
                ('wait_for_seconds', models.IntegerField()),
                ('acceptable_value', models.FloatField(blank=True, default=None, null=True)),
                ('aggregator', models.CharField(choices=[('AVG', 'Average'), ('MAX', 'Maximum')], max_length=10)),
                ('is_enabled', models.BooleanField(default=True)),
                ('max_value', models.FloatField(blank=True, default=None, null=True)),
                ('average_value', models.FloatField(blank=True, default=None, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]

# Generated by Django 2.2.25 on 2022-02-09 20:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0041_migrate_reprs'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeactivateMobileWorkerTrigger',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(max_length=255)),
                ('user_id', models.CharField(max_length=255)),
                ('deactivate_after', models.DateField()),
            ],
        ),
    ]

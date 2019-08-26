# Generated by Django 1.11.12 on 2018-05-02 20:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0017_update_ui_type'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='alertevent',
            name='time_to_wait',
        ),
        migrations.AddField(
            model_name='alertevent',
            name='minutes_to_wait',
            field=models.IntegerField(default=0),
            preserve_default=False,
        ),
    ]

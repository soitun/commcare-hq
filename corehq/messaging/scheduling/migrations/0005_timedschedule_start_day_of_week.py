# -*- coding: utf-8 -*-
# Generated by Django 1.10.7 on 2017-08-10 14:59
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0004_timedschedule_start_offset'),
    ]

    operations = [
        migrations.AddField(
            model_name='timedschedule',
            name='start_day_of_week',
            field=models.IntegerField(default=-1),
        ),
    ]
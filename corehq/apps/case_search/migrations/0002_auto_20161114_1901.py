# Generated by Django 1.9.11 on 2016-11-14 19:01

from django.db import migrations
import jsonfield.fields


class Migration(migrations.Migration):

    dependencies = [
        ('case_search', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='casesearchconfig',
            name='_config',
            field=jsonfield.fields.JSONField(default=dict),
        ),
    ]

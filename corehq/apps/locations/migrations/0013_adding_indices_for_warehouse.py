# Generated by Django 1.11.8 on 2018-01-30 10:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0012_get_location_fixture_ids'),
    ]

    operations = [
        migrations.AlterField(
            model_name='locationtype',
            name='last_modified',
            field=models.DateTimeField(auto_now=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='sqllocation',
            name='last_modified',
            field=models.DateTimeField(auto_now=True, db_index=True),
        ),
    ]

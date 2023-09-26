# Generated by Django 3.2.20 on 2023-09-13 19:15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('form_processor', '0094_add_partial_index_xforms'),
    ]

    operations = [
        migrations.AlterField(
            model_name='xforminstance',
            name='state',
            field=models.PositiveSmallIntegerField(choices=[(1, 'normal'), (2, 'archived'), (4, 'deprecated'), (8, 'duplicate'), (16, 'error'), (32, 'submission_error')], default=1),
        ),
    ]

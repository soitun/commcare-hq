# Generated by Django 3.2.20 on 2023-09-17 15:22

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('repeaters', '0005_datasourcerepeater'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    # Actual removal in the database is planned later.
                    model_name='repeater',
                    name='repeater_id',
                ),
                migrations.AlterField(
                    # Adding a default has no associated SQL
                    model_name='repeater',
                    name='id',
                    field=models.UUIDField(db_column='id_', default=uuid.uuid4, primary_key=True, serialize=False),
                ),
            ],
        ),
    ]

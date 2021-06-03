# Generated by Django 2.2.20 on 2021-05-21 17:32

from django.db import migrations, models


ACCESS_INDEX = "audit_access_couch_10d1b_idx"
ACCESS_TABLE = "auditcare_accessaudit"
NAVIGATION_EVENT_INDEX = "audit_nav_couch_875bc_idx"
NAVIGATION_EVENT_TABLE = "auditcare_navigationeventaudit"


def _create_index_sql(table_name, index_name):
    return """
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {} ON {} (couch_id)
        WHERE couch_id IS NOT NULL
    """.format(index_name, table_name)


def _drop_index_sql(index_name):
    return "DROP INDEX CONCURRENTLY IF EXISTS {}".format(index_name)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('auditcare', '0003_truncatechars'),
    ]

    operations = [
        migrations.AddField(
            model_name='accessaudit',
            name='couch_id',
            field=models.CharField(max_length=126, null=True),
        ),
        migrations.RunSQL(
            sql=_create_index_sql(ACCESS_TABLE, ACCESS_INDEX),
            reverse_sql=_drop_index_sql(ACCESS_INDEX),
            state_operations=[
                migrations.AddIndex(
                    model_name='accessaudit',
                    index=models.UniqueConstraint(fields=['couch_id'], condition=models.Q(couch_id__isnull=False),
                                                  name=ACCESS_INDEX),
                ),
            ]
        ),
        migrations.AddField(
            model_name='navigationeventaudit',
            name='couch_id',
            field=models.CharField(max_length=126, null=True),
        ),
        migrations.RunSQL(
            sql=_create_index_sql(NAVIGATION_EVENT_TABLE, NAVIGATION_EVENT_INDEX),
            reverse_sql=_drop_index_sql(NAVIGATION_EVENT_INDEX),
            state_operations=[
                migrations.AddIndex(
                    model_name='navigationeventaudit',
                    index=models.UniqueConstraint(fields=['couch_id'], condition=models.Q(couch_id__isnull=False),
                                                  name=NAVIGATION_EVENT_INDEX),
                ),
            ]
        ),
    ]

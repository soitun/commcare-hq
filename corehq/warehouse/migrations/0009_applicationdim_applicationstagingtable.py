# Generated by Django 1.10.7 on 2017-07-10 22:05

import corehq.warehouse.etl
import corehq.warehouse.models.shared
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0008_batch_record_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApplicationDim',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(max_length=255)),
                ('dim_last_modified', models.DateTimeField(auto_now=True)),
                ('dim_created_on', models.DateTimeField(auto_now_add=True)),
                ('deleted', models.BooleanField()),
                ('application_id', models.CharField(max_length=255)),
                ('name', models.CharField(max_length=255)),
                ('application_last_modified', models.DateTimeField(null=True)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='warehouse.Batch')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model, corehq.warehouse.models.shared.WarehouseTable, corehq.warehouse.etl.CustomSQLETLMixin),
        ),
        migrations.CreateModel(
            name='ApplicationStagingTable',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('application_id', models.CharField(max_length=255)),
                ('name', models.CharField(max_length=255)),
                ('domain', models.CharField(max_length=100)),
                ('application_last_modified', models.DateTimeField(null=True)),
                ('doc_type', models.CharField(max_length=100)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='warehouse.Batch')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model, corehq.warehouse.models.shared.WarehouseTable, corehq.warehouse.etl.HQToWarehouseETLMixin),
        ),
    ]

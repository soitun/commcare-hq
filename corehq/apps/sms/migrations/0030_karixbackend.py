# Generated by Django 1.11.14 on 2018-08-30 22:14

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('sms', '0029_daily_outbound_sms_limit_reached'),
    ]

    operations = [
        migrations.CreateModel(
            name='KarixBackend',
            fields=[
            ],
            options={
                'proxy': True,
                'indexes': [],
            },
            bases=('sms.sqlsmsbackend',),
        ),
    ]

# -*- coding: utf-8 -*-
# Generated by Django 1.11.9 on 2018-02-01 15:00
from __future__ import unicode_literals

from __future__ import absolute_import
from couchdbkit import ResourceNotFound
from django.db import migrations

from corehq.toggles import LINKED_DOMAINS
from toggle.models import Toggle


def _migrate_linked_apps_toggle(apps, schema_editor):
    try:
        linked_apps_toggle = Toggle.get('linked_apps')
    except ResourceNotFound:
        pass
    else:
        try:
            Toggle.get(LINKED_DOMAINS.slug)
        except ResourceNotFound:
            linked_domains_toggle = Toggle(
                slug=LINKED_DOMAINS.slug, enabled_users=linked_apps_toggle.enabled_users
            )
            linked_domains_toggle.save()


def noop(*args, **kwargs):
    pass


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('linked_domain', '0004_domainlinkhistory'),
    ]

    operations = [
        migrations.RunPython(_migrate_linked_apps_toggle, noop)
    ]

from __future__ import absolute_import
from __future__ import unicode_literals
from django.contrib import admin
from .models import *


class OwnershipCleanlinessFlagAdmin(admin.ModelAdmin):

    model = OwnershipCleanlinessFlag
    list_display = [
        'domain',
        'owner_id',
        'is_clean',
        'last_checked',
        'hint',
    ]

    search_fields = [
        'domain',
        'owner_id',
    ]

    list_filter = [
        'is_clean',
    ]


admin.site.register(OwnershipCleanlinessFlag, OwnershipCleanlinessFlagAdmin)

from django.core.management.base import BaseCommand

from corehq.util.log import with_progress_bar
from corehq.apps.users.models import Invitation
from corehq.util.queries import queryset_to_iterator


class Command(BaseCommand):
    help = "Copies invitation supply_point field to location_id field."

    def handle(self, **options):
        invitations_queryset = (Invitation.objects
                                .exclude(supply_point__isnull=True)
                                .filter(location_id__isnull=True))
        for invitation in with_progress_bar(
                queryset_to_iterator(invitations_queryset, Invitation), invitations_queryset.count()):
            invitation.location_id = invitation.supply_point
            invitation.save()

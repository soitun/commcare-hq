from memoized import memoized

from corehq.apps.locations.models import LocationType, SQLLocation
from custom.icds.location_reassignment.models import Transition
from custom.icds.location_reassignment.utils import get_supervisor_id


class Processor(object):
    def __init__(self, domain, transitions):
        """
        Fails without performing any operation if:
            1. any parent location that is not getting newly created is not found in the system
            2. creation of any new location fails
        Fails at any transition if:
            1. any of the old and new location is not found
            2. transition fails

        :param domain: domain
        :param transitions: transitions in format generated by Parser
        """
        self.domain = domain
        self.location_types_by_code = {lt.code: lt for lt in LocationType.objects.by_domain(self.domain)}
        self.transitions = transitions

    def process(self):
        # process each sheet, in order of hierarchy
        # so that newly created parent locations are present when needed
        for location_type_code in self.location_types_by_code:
            for transition in self.transitions[location_type_code]:
                Transition(**transition).perform()


class HouseholdReassignmentProcessor():
    def __init__(self, domain, reassignments):
        self.domain = domain
        self.reassignments = reassignments

    def process(self):
        from custom.icds.location_reassignment.utils import reassign_household
        old_site_codes = set()
        new_site_codes = set()
        for household_id, details in self.reassignments.items():
            old_site_codes.add(details['old_site_code'])
            new_site_codes.add(details['new_site_code'])
        old_locations_by_site_code = {
            loc.site_code: loc
            for loc in SQLLocation.objects.filter(domain=self.domain, site_code__in=old_site_codes)}
        new_locations_by_site_code = {
            loc.site_code: loc
            for loc in SQLLocation.active_objects.filter(domain=self.domain, site_code__in=new_site_codes)}
        for household_id, details in self.reassignments.items():
            old_owner_id = old_locations_by_site_code[details['old_site_code']].location_id
            new_owner_id = new_locations_by_site_code[details['new_site_code']].location_id
            supervisor_id = self._supervisor_id(old_owner_id)
            reassign_household(self.domain, household_id, old_owner_id, new_owner_id, supervisor_id)

    @memoized
    def _supervisor_id(self, location_id):
        return get_supervisor_id(self.domain, location_id)

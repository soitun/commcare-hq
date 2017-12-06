from __future__ import absolute_import
from itertools import groupby
from collections import defaultdict
from xml.etree.cElementTree import Element

from casexml.apps.phone.fixtures import FixtureProvider
from corehq.apps.custom_data_fields.dbaccessors import get_by_domain_and_type
from corehq.apps.fixtures.utils import get_index_schema_node
from corehq.apps.locations.models import SQLLocation, LocationType, LocationFixtureConfiguration
from corehq import toggles

import six
from django.db.models.expressions import Value
from django.db.models.query import Q
from django.db.models.aggregates import Min


class LocationSet(object):
    """
    Very simple class for keeping track of a set of locations
    """

    def __init__(self, locations=None):
        self.by_id = {}
        self.root_locations = set()
        self.by_parent = defaultdict(set)
        if locations is not None:
            for loc in locations:
                self.add_location(loc)

    def add_location(self, location):
        self.by_id[location.location_id] = location
        parent = location.parent
        parent_id = parent.location_id if parent else None
        if parent_id is None:  # this is a root
            self.add_root(location)
        self.by_parent[parent_id].add(location)

    def add_root(self, location):
        self.root_locations.add(location)

    def __contains__(self, item):
        return item in self.by_id


def should_sync_locations(last_sync, locations_queryset, restore_user):
    """
    Determine if any locations (already filtered to be relevant
    to this user) require syncing.
    """
    if (
        not last_sync or
        not last_sync.date or
        restore_user.get_fixture_last_modified() >= last_sync.date
    ):
        return True

    return (
        locations_queryset.filter(last_modified__gte=last_sync.date).exists()
        or LocationType.objects.filter(domain=restore_user.domain,
                                       last_modified__gte=last_sync.date).exists()
    )


class LocationFixtureProvider(FixtureProvider):

    def __init__(self, id, serializer):
        self.id = id
        self.serializer = serializer

    def __call__(self, restore_state):
        """
        By default this will generate a fixture for the users
        location and it's "footprint", meaning the path
        to a root location through parent hierarchies.

        There is an admin feature flag that will make this generate
        a fixture with ALL locations for the domain.
        """
        restore_user = restore_state.restore_user

        if not self.serializer.should_sync(restore_user):
            return []

        # This just calls get_location_fixture_queryset but is memoized to the user
        locations_queryset = restore_user.get_locations_to_sync()
        if not should_sync_locations(restore_state.last_sync_log, locations_queryset, restore_user):
            return []

        data_fields = _get_location_data_fields(restore_user.domain)
        return self.serializer.get_xml_nodes(self.id, restore_user, locations_queryset, data_fields)


class HierarchicalLocationSerializer(object):

    def should_sync(self, restore_user):
        return should_sync_hierarchical_fixture(restore_user.project)

    def get_xml_nodes(self, fixture_id, restore_user, locations_queryset, data_fields):
        locations_db = LocationSet(locations_queryset)

        root_node = Element('fixture', {'id': fixture_id, 'user_id': restore_user.user_id})
        root_locations = locations_db.root_locations

        if root_locations:
            _append_children(root_node, locations_db, root_locations, data_fields)
        else:
            # There is a bug on mobile versions prior to 2.27 where
            # a parsing error will cause mobile to ignore the element
            # after this one if this element is empty.
            # So we have to add a dummy empty_element child to prevent
            # this element from being empty.
            root_node.append(Element("empty_element"))
        return [root_node]


class FlatLocationSerializer(object):

    def should_sync(self, restore_user):
        return should_sync_flat_fixture(restore_user.project)

    def get_xml_nodes(self, fixture_id, restore_user, locations_queryset, data_fields):

        all_types = LocationType.objects.filter(domain=restore_user.domain).values_list(
            'code', flat=True
        )
        location_type_attrs = ['{}_id'.format(t) for t in all_types if t is not None]
        attrs_to_index = ['@{}'.format(attr) for attr in location_type_attrs]
        attrs_to_index.extend(_get_indexed_field_name(field.slug) for field in data_fields
                              if field.index_in_fixture)
        attrs_to_index.extend(['@id', '@type', 'name'])

        return [get_index_schema_node(fixture_id, attrs_to_index),
                self._get_fixture_node(fixture_id, restore_user, locations_queryset,
                                       location_type_attrs, data_fields)]

    def _get_fixture_node(self, fixture_id, restore_user, locations_queryset,
                          location_type_attrs, data_fields):
        root_node = Element('fixture', {'id': fixture_id,
                                        'user_id': restore_user.user_id,
                                        'indexed': 'true'})
        outer_node = Element('locations')
        root_node.append(outer_node)
        all_locations = list(locations_queryset.order_by('site_code'))
        locations_by_id = {location.pk: location for location in all_locations}
        for location in all_locations:
            attrs = {
                'type': location.location_type.code,
                'id': location.location_id,
            }
            attrs.update({attr: '' for attr in location_type_attrs})
            attrs['{}_id'.format(location.location_type.code)] = location.location_id

            current_location = location
            while current_location.parent_id:
                try:
                    current_location = locations_by_id[current_location.parent_id]
                except KeyError:
                    current_location = current_location.parent

                    # For some reason this wasn't included in the locations we already fetched
                    from corehq.util.soft_assert import soft_assert
                    _soft_assert = soft_assert('{}@{}.com'.format('frener', 'dimagi'))
                    message = (
                        "The flat location fixture didn't prefetch all parent "
                        "locations: {domain}: {location_id}. User id: {user_id}"
                    ).format(
                        domain=current_location.domain,
                        location_id=current_location.location_id,
                        user_id=restore_user.user_id,
                    )
                    _soft_assert(False, msg=message)

                attrs['{}_id'.format(current_location.location_type.code)] = current_location.location_id

            location_node = Element('location', attrs)
            _fill_in_location_element(location_node, location, data_fields)
            outer_node.append(location_node)

        return root_node


def should_sync_hierarchical_fixture(project):
    # Sync hierarchical fixture for domains with fixture toggle enabled for migration and
    # configuration set to use hierarchical fixture
    # Even if both fixtures are set up, this one takes priority for domains with toggle enabled
    return (
        project.uses_locations and
        toggles.HIERARCHICAL_LOCATION_FIXTURE.enabled(project.name) and
        LocationFixtureConfiguration.for_domain(project.name).sync_hierarchical_fixture
    )


def should_sync_flat_fixture(project):
    # Sync flat fixture for domains with conf for flat fixture enabled
    # This does not check for toggle for migration to allow domains those domains to migrate to flat fixture
    return (
        project.uses_locations and
        LocationFixtureConfiguration.for_domain(project.name).sync_flat_fixture
    )


location_fixture_generator = LocationFixtureProvider(
    id='commtrack:locations', serializer=HierarchicalLocationSerializer()
)
flat_location_fixture_generator = LocationFixtureProvider(
    id='locations', serializer=FlatLocationSerializer()
)


def get_location_fixture_queryset(user):
    def filter_include_only(query, location_type):
        return query.filter(location_type__in=location_type.include_only.all())

    if toggles.SYNC_ALL_LOCATIONS.enabled(user.domain):
        return SQLLocation.active_objects.filter(domain=user.domain).prefetch_related('location_type')

    user_locations = user.get_sql_locations(user.domain).prefetch_related('location_type')


    class Array(Func):
        function = "Array"
    class array_append(Func):
        function = "array_append"

    # TODO new django query constructs
    #x   With.recursive(cte_func): create WITH RECURSIVE cte object
    #x   cte.col: CTE columns object
    #   cte.join(model, id=cte.col.parent_id): JOIN model to CTE
    #x   cte.queryset(): WITH cte AS (...) SELECT ... FROM cte
    #x   QuerySet.with_cte(cte): add CTE query to queryset

    # find level of deepest include_without_expanding location type
    def ancestors_cte(cte):
        return SQLLocation.active_objects.filter(
            domain__exact=user.domain,
            is_archived=False,
            location_type_id__in=Subquery(
                user_locations.filter(
                    location_type__include_without_expanding_id__isnull=False
                ).values(
                    "location_type__include_without_expanding_id"
                )
            )
        ).values(
            "parent_id",
            level=Value(0),
        ).union(
            cte.join(SQLLocation, id=cte.col.parent_id)
                .values("parent_id", level=cte.col.level + 1),
            all=True,
        )
    ancestors = With.recursive(ancestors_cte)
    max_level = ancestors.queryset().annotate(
        value=Max(ancestors.col.level)
    ).values("value")

    # TODO CTE for expand_from location type ids and expand_to levels

    def descendants_cte(cte):
        return SQLLocation.active_objects.annotate(
            level=Value(0),
            path=Array("id"),
            base_id=Value(None), # id if id in expand_froms else null
        ).filter(
            Q(parent__isnull=True) | Q(id__in=expand_froms),
            domain__exact=user.domain,
        ).union(
            cte.join(
                SQLLocation.active_objects.all(),
                parent_id=cte.col.id,
            ).annotate(
                level=descendants.col.level + 1,
                path=array_append(descendants.col.path, "id"),
                base_id=descendants.col.base_id,
            ).filter(
                Q(
                    base_id__isnull=True,
                    level__lte=max_level.col.value
#                ) | Q(
#                    base_id__in=expand_froms,
#                    level__lte=# expand_froms[base_id].level
                ),
            ),
            all=True,
        )
    return With.recursive(descendants_cte).queryset().with_cte(max_level)


    all_locations = _get_include_without_expanding_locations(user.domain, user_locations)

    unions = []
    for user_location in user_locations:
        # calculate levels between expand_from and expand_to with ancestors cte
        # 

        location_type = user_location.location_type
        expand_to_level = _get_level_to_expand_to(user.domain, location_type.expand_to)
        expand_from_level = location_type.expand_from or location_type
        expand_from_locations = _get_locs_to_expand_from(user.domain, user_location, expand_from_level)
        locs_below_expand_from = _get_children(expand_from_locations, expand_to_level)
        locs_at_or_above_expand_from = (SQLLocation.active_objects
            .get_queryset_ancestors(expand_from_locations, include_self=True))
        #locations_to_sync = locs_at_or_above_expand_from.union(locs_below_expand_from)
        unions.append(locs_at_or_above_expand_from)
        unions.append(locs_below_expand_from)
        if location_type.include_only.exists():
            unions[-2] = filter_include_only(unions[-2], location_type)
            unions[-1] = filter_include_only(unions[-1], location_type)
        #    locations_to_sync = locations_to_sync.filter(location_type__in=location_type.include_only.all())
        #all_locations = all_locations.union(locations_to_sync)

    if unions:
        all_locations = all_locations.union(*unions)
    print("-- all_locations\n{}".format(all_locations.query))
    raise Exception('stop')
    return all_locations


def _get_level_to_expand_to(domain, expand_to):
    if expand_to is None:
        return None
    return (SQLLocation.active_objects
            .filter(domain__exact=domain, location_type=expand_to)
            .values_list('level', flat=True)
            .first())


def _get_locs_to_expand_from(domain, user_location, expand_from):
    """From the users current location, return all locations of the highest
    level they want to start expanding from.
    """
    if user_location.location_type.expand_from_root:
        return SQLLocation.root_locations(domain=domain)
    else:
        ancestors = (
            user_location
            .get_ancestors(include_self=True)
            .filter(location_type=expand_from, is_archived=False)
            .prefetch_related('location_type')
        )
        return ancestors


def _get_children(expand_from_locations, expand_to_level):
    """From the topmost location, get all the children we want to sync
    """
    children = (SQLLocation.active_objects
                .get_queryset_descendants(expand_from_locations)
                .prefetch_related('location_type'))
    if expand_to_level is not None:
        children = children.filter(level__lte=expand_to_level)
    return children


def _get_include_without_expanding_locations(domain, assigned_locations):
    """returns all locations set for inclusion along with their ancestors
    """
    # all loctypes to include, based on all assigned location types
    location_type_ids = {
        loc.location_type.include_without_expanding_id
        for loc in assigned_locations
        if loc.location_type.include_without_expanding_id is not None
    }
    class Array(Func):
        function = "Array"
    class array_append(Func):
        function = "array_append"

    def ancestors_cte(ancestors):
        return SQLLocation.active_objects.filter(
            domain__exact=domain,
            is_archived=False,
            location_type_id__in=location_type_ids
        ).values(
            "id", "parent_id", level=Value(0),
        ).union(
            SQLLocation.objects
                # JOIN ancestors ON SQLLocation.id = ancestors.parent_id
                .filter(id=ancestors.col.parent_id)
                .values("id", "parent_id", level=ancestors.col.level + 1),
            all=True,
        )
    ancestors = With.recursive(ancestors_cte)
    max_level = ancestors.annotate(
        value=Max(ancestors.col.level)
    ).values("value")

    def descendants_cte(descendants):
        return SQLLocation.active_objects.annotate(
            level=Value(0),
            path=Array("id"),
        ).filter(
            parent__isnull=True,
            domain__exact=domain,
        ).union(
            SQLLocation.active_objects.filter(
                # JOIN descendants ON SQLLocation.parent_id = descendants.id
                parent_id=descendants.col.id,
            ).annotate(
                level=descendants.col.level + 1,
                path=array_append(descendants.col.path, "id"),
                base_id=descendants.col.base_id,
            ).filter(
                level__lte=max_level.col.value
            ),
            all=True,
        )
    return With.recursive(descendants_cte).with_cte(max_level)

#    # all levels to include, based on the above loctypes
#    forced_levels = (SQLLocation.active_objects
#                     .filter(domain__exact=domain,
#                             location_type_id__in=location_type_ids)
#                     .values_list('level', flat=True)
#                     .order_by('level')
#                     .distinct('level'))
#    if forced_levels:
#        return (SQLLocation.active_objects
#                .filter(domain__exact=domain,
#                        level__lte=max(forced_levels))
#                .prefetch_related('location_type'))
#    else:
#        return SQLLocation.objects.none()


def _append_children(node, location_db, locations, data_fields):
    for type, locs in _group_by_type(locations):
        locs = sorted(locs, key=lambda loc: loc.name)
        node.append(_types_to_fixture(location_db, type, locs, data_fields))


def _group_by_type(locations):
    key = lambda loc: (loc.location_type.code, loc.location_type)
    for (code, type), locs in groupby(sorted(locations, key=key), key=key):
        yield type, list(locs)


def _types_to_fixture(location_db, type, locs, data_fields):
    type_node = Element('%ss' % type.code)  # hacky pluralization
    for loc in locs:
        type_node.append(_location_to_fixture(location_db, loc, type, data_fields))
    return type_node


def _get_metadata_node(location, data_fields):
    node = Element('location_data')
    # add default empty nodes for all known fields: http://manage.dimagi.com/default.asp?247786
    for field in data_fields:
        element = Element(field.slug)
        element.text = six.text_type(location.metadata.get(field.slug, ''))
        node.append(element)
    return node


def _location_to_fixture(location_db, location, type, data_fields):
    root = Element(type.code, {'id': location.location_id})
    _fill_in_location_element(root, location, data_fields)
    _append_children(root, location_db, location_db.by_parent[location.location_id], data_fields)
    return root


def _fill_in_location_element(xml_root, location, data_fields):
    fixture_fields = [
        'name',
        'site_code',
        'external_id',
        'latitude',
        'longitude',
        'location_type',
        'supply_point_id',
    ]
    for field in fixture_fields:
        field_node = Element(field)
        val = getattr(location, field)
        field_node.text = six.text_type(val if val is not None else '')
        xml_root.append(field_node)

    # in order to be indexed, custom data fields need to be top-level
    # so we stick them in there with the prefix data_
    for field in data_fields:
        if field.index_in_fixture:
            field_node = Element(_get_indexed_field_name(field.slug))
            val = location.metadata.get(field.slug)
            field_node.text = six.text_type(val if val is not None else '')
            xml_root.append(field_node)

    xml_root.append(_get_metadata_node(location, data_fields))


def _get_location_data_fields(domain):
    from corehq.apps.locations.views import LocationFieldsView
    fields_definition = get_by_domain_and_type(domain, LocationFieldsView.field_type)
    if fields_definition:
        return fields_definition.fields
    else:
        return []


def _get_indexed_field_name(slug):
    return "data_{}".format(slug)

from __future__ import absolute_import
from itertools import groupby
from collections import defaultdict
from xml.etree.cElementTree import Element

from casexml.apps.phone.fixtures import FixtureProvider
from corehq.apps.custom_data_fields.dbaccessors import get_by_domain_and_type
from corehq.apps.fixtures.utils import get_index_schema_node
from corehq.apps.locations.cte import With
from corehq.apps.locations.models import SQLLocation, LocationType, LocationFixtureConfiguration
from corehq import toggles

import six
from django.db.models import IntegerField
from django.db.models.expressions import (
    Case, Exists, ExpressionWrapper, F, Func, RawSQL, Subquery, Value, When,
)
from django.db.models.functions import Coalesce
from django.db.models.query import Q
from django.db.models.aggregates import Max
from django.contrib.postgres.fields.array import ArrayField


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
    if toggles.SYNC_ALL_LOCATIONS.enabled(user.domain):
        return SQLLocation.active_objects.filter(domain=user.domain).prefetch_related('location_type')

    user_locations = user.get_sql_locations(user.domain).prefetch_related('location_type')

    intField = IntegerField()
    intArray = ArrayField(intField)

    class Array(Func):
        function = "Array"
        template = '%(function)s[%(expressions)s]'
        output_field = intArray

    class array_append(Func):
        function = "array_append"
        output_field = intArray

    class array_prepend(Func):
        function = "array_prepend"
        output_field = intArray

    # expand_to CTE fields:
    # - expand_from_path: array of location ids (expand_from_id and ancestors)
    # - expand_to_type_id: location type id to expand to (used for grouping)
    # - expand_to_depth: int
    #
    # Examples:
    # _from_path   | _to_type_id | _to_depth
    # -------------|-------------|----------
    # NULL         | NULL        | 3  -- include_without_expanding IS NOT NULL or expand_from_root = TRUE
    # [1, 10]      | 1001        | 4  -- expand_from_type = loc_10.location_type (expand_from IS NULL)
    # [2, 20, 200] | 20001       | 5  -- expand_from_type = loc_200.location_type.expand_from
    #
    # - Locations with depth <= expand_to_depth will be included when
    #   expand_to_type_id IS NULL.
    # - Locations identified in expand_from_path will be included.
    # - Locations whose path contains expand_from_path (i.e., descendants
    #   of the last element in expand_from_path) and with depth <=
    #   expand_to_depth will be included.
    #
    # There may be ambiguities in location type configurations:
    # - User location will be ignored if include_without_expanding IS NULL and
    #   expand_to IS NULL.
    # - expand_from_root = TRUE seems to do the same thing as
    #   include_without_expanding IS NOT NULL.

    def expand_to_cte(cte):
        expand_from_type = Coalesce(
            F("location_type___expand_from"),
            F("location_type"),
        )
        return SQLLocation.active_objects.filter(
            domain__exact=user.domain,
            location_type_id__in=Subquery(
                user_locations.filter(Q(
                    location_type__include_without_expanding__isnull=False,
                ) | Q(
                    location_type__expand_to__isnull=False,
                )).values(
                    value=Coalesce(
                        F("location_type__include_without_expanding"),
                        F("location_type__expand_to"),
                    ),
                )
            )
        ).values(
            "parent_id",
            # expand_to_type_id IS NULL when include_without_expanding IS NOT NULL
            expand_from_type_id=Case(
                When(Q(
                    location_type__include_without_expanding__isnull=False,
                ) | Q(
                    location_type___expand_from_root=Value(True),
                ), then=None),
                default=expand_from_type,
                output_field=intField,
            ),
            expand_from_path=Case(
                When(
                    location_type__include_without_expanding__isnull=True,
                    location_type___expand_from_root=Value(False),
                    location_type=expand_from_type,
                    then=Array("id"),
                ),
                default=Value(None, output_field=intArray),
                output_field=intArray,
            ),
            expand_to_type_id=Case(
                When(Q(
                    location_type__include_without_expanding__isnull=False,
                ) | Q(
                    location_type___expand_from_root=Value(True),
                ), then=None),
                default=F("location_type__expand_to"),
                output_field=intField,
            ),
            depth=Value(0, output_field=intField),
        ).union(
            cte.join(
                SQLLocation.active_objects.all(),
                id=cte.col.parent_id,
            ).annotate(
                cte_expand_from_path=ExpressionWrapper(
                    cte.col.expand_from_path,
                    output_field=intArray,
                ),
            ).values(
                "parent_id",
                expand_from_type_id=cte.col.expand_from_type_id,
                expand_from_path=Case(
                    When(
                        # prepend id to path if path has been started
                        cte_expand_from_path__isnull=False,
                        then=array_prepend("id", cte.col.expand_from_path),
                    ),
                    When(
                        # start path at expand_from_type_id
                        location_type_id=cte.col.expand_from_type_id,
                        then=Array("id"),
                    ),
                    default=Value(None, output_field=intArray),
                    output_field=intArray,
                ),
                expand_to_type_id=cte.col.expand_to_type_id,
                depth=cte.col.depth + Value(1, output_field=intField)
            ),
            all=True,
        )
    expand_to_inner = With.recursive(expand_to_cte)
    expand_to = With(
        expand_to_inner.queryset().with_cte(expand_to_inner).filter(
            # exclude all but the root items
            parent_id__isnull=True,
        ).order_by().values(
            # expand_from_path is null for include_without_expanding locations
            # so will not cause grouping problems (see expand_to_depth below)
            expand_from_path=expand_to_inner.col.expand_from_path,

            # expand_to_type_id is either not null or -> include_without_expanding
            expand_to_type_id=expand_to_inner.col.expand_to_type_id,

            # expand_to_depth is aggregated on expand_to_type_id
            # (other group by fields are irrelevant)
            expand_to_depth=Max(expand_to_inner.col.depth, output_field=intField),
        ),
        "expand_to",
    )

    def descendants_cte(cte):
        is_included = Exists(
            expand_to.queryset().values(
                expand_to_type_id=expand_to.col.expand_to_type_id,
                # HACK had to use RawSQL because OuterRef is broken
                # https://code.djangoproject.com/ticket/28621
                outer_id=RawSQL('"locations_sqllocation"."id"', []),
                depth=RawSQL('"locations_sqllocation"."depth"', []),
                path=RawSQL('"locations_sqllocation"."path"', [], output_field=intArray),
            ).filter(Q(
                # expand_to_type_id is null -> include_without_expanding
                expand_to_type_id__isnull=True,
                depth__lte=expand_to.col.expand_to_depth,
            ) | Q(
                outer_id__in=expand_to.col.expand_from_path,
            ) | Q(
                path__contains=expand_to.col.expand_from_path,
                depth__lte=expand_to.col.expand_to_depth,
            )),
        )
        return SQLLocation.active_objects.annotate(
            is_included=is_included,
        ).values(
            "id",
            "parent_id",
            depth=Value(0, output_field=intField),
            path=Array("id"),
        ).filter(
            domain__exact=user.domain,
            parent__isnull=True,  # start at the root
            is_included=True,
        ).union(
            cte.join(SQLLocation.active_objects.all(), parent_id=cte.col.id).annotate(
                depth=cte.col.depth + Value(1, output_field=intField),
                path=array_append(cte.col.path, "id"),
                is_included=is_included,
            ).filter(
                domain__exact=user.domain,
                is_included=True,
            ).values(
                "id",
                "parent_id",
                "depth",
                "path",
            ),
            all=True,
        )
    descendants = With.recursive(descendants_cte, "descendants")
    result = descendants.join(
        SQLLocation.objects.all().with_cte(descendants).with_cte(expand_to),
        id=descendants.col.id
    ).annotate(
        path=descendants.col.path,
        depth=descendants.col.depth,
    ).order_by("path")
    print(result.query)
    raise Exception("stop")
    return result

#def get_location_fixture_queryset(user):
#    if toggles.SYNC_ALL_LOCATIONS.enabled(user.domain):
#        return SQLLocation.active_objects.filter(domain=user.domain).prefetch_related('location_type')
#
#    user_locations = user.get_sql_locations(user.domain).prefetch_related('location_type')
#
#    all_locations = _get_include_without_expanding_locations(user.domain, user_locations)
#
#    for user_location in user_locations:
#        location_type = user_location.location_type
#        # returns either None or the level (integer) to exand to
#        expand_to_level = _get_level_to_expand_to(user.domain, location_type.expand_to)
#        expand_from_level = location_type.expand_from or location_type
#
#        # returns either all root locations or a single location (of expand_from_level type)
#        expand_from_locations = _get_locs_to_expand_from(user.domain, user_location, expand_from_level)
#
#        locs_below_expand_from = _get_children(expand_from_locations, expand_to_level)
#        locs_at_or_above_expand_from = (SQLLocation.active_objects
#                                        .get_queryset_ancestors(expand_from_locations, include_self=True))
#        locations_to_sync = locs_at_or_above_expand_from | locs_below_expand_from
#        if location_type.include_only.exists():
#            locations_to_sync = locations_to_sync.filter(location_type__in=location_type.include_only.all())
#        all_locations |= locations_to_sync
#
#    return all_locations


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
    # all levels to include, based on the above loctypes
    forced_levels = (SQLLocation.active_objects
                     .filter(domain__exact=domain,
                             location_type_id__in=location_type_ids)
                     .values_list('level', flat=True)
                     .order_by('level')
                     .distinct('level'))
    if forced_levels:
        return (SQLLocation.active_objects
                .filter(domain__exact=domain,
                        level__lte=max(forced_levels))
                .prefetch_related('location_type'))
    else:
        return SQLLocation.objects.none()


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

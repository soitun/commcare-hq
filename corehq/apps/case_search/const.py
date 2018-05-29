from __future__ import absolute_import
from __future__ import unicode_literals

from collections import namedtuple

# Case properties nested documents
CASE_PROPERTIES_PATH = 'case_properties'
VALUE = 'value'

# Case indices nested documents
INDICES_PATH = 'indices'
REFERENCED_ID = 'referenced_id'
IDENTIFIER = 'identifier'

# Added to each case response when case searches are performed
RELEVANCE_SCORE = "commcare_search_score"

# Added to each case on the index for debugging when a case was added to ES
INDEXED_ON = '@indexed_on'

# Properties added to the case search mapping to provide extra information
SYSTEM_PROPERTIES = [
    CASE_PROPERTIES_PATH,
    INDEXED_ON,
]

# Properties that are inconsitent between case models stored in HQ and casedb
# expressions. We store these as case properties in the case search index so
# they are easily searchable, then remove them when pulling the case source
# from ES
SpecialCaseProperty = namedtuple('SpecialCaseProperty', 'key value_getter sort_property')
SPECIAL_CASE_PROPERTIES_MAP = {
    '@case_id': SpecialCaseProperty('@case_id', lambda doc: doc.get('_id'), '_id'),
    '@case_type': SpecialCaseProperty('@case_type', lambda doc: doc.get('type'), 'type.exact'),

    '@owner_id': SpecialCaseProperty('@owner_id', lambda doc: doc.get('owner_id'), 'owner_id'),

    '@status': SpecialCaseProperty('@status', lambda doc: 'closed' if doc.get('closed') else 'open', 'closed'),

    'name': SpecialCaseProperty('name', lambda doc: doc.get('name'), 'name'),
    'case_name': SpecialCaseProperty('case_name', lambda doc: doc.get('name'), 'name.exact'),

    'external_id': SpecialCaseProperty('external_id', lambda doc: doc.get('external_id', ''), 'external_id'),

    'date_opened': SpecialCaseProperty('date_opened', lambda doc: doc.get('opened_on'), 'opened_on'),
    'last_modified': SpecialCaseProperty('last_modified', lambda doc: doc.get('modified_on'), 'modified_on'),
}
SPECIAL_CASE_PROPERTIES = list(SPECIAL_CASE_PROPERTIES_MAP.keys())

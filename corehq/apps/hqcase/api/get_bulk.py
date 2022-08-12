import dataclasses
from dataclasses import dataclass, field
from operator import itemgetter

from corehq.apps.es.case_search import ElasticCaseSearch, CaseSearchES
from corehq.apps.hqcase.api.core import serialize_es_case, UserError
from corehq.apps.hqcase.api.get_list import MAX_PAGE_SIZE
from corehq.form_processor.models.util import sort_with_id_list


@dataclass
class BulkFetchResults:
    cases: list = field(default_factory=list)
    matching_records: int = 0
    missing_records: int = 0

    def merge(self, results):
        self.cases.extend(results.cases)
        self.matching_records += results.matching_records
        self.missing_records += results.missing_records


def get_bulk(domain, case_ids=None, external_ids=None):
    """Get cases in bulk.

    This must return a result for each case ID passed in and the results must
    be in the same order as the original list of case IDs.

    If both case IDs and external IDs are passed then results will include
    cases loaded by ID first followed by cases loaded by external ID.

    If the case is not found or belongs to a different domain then
    an error stub is included in the result set.
    """
    case_ids = case_ids or []
    external_ids = external_ids or []
    if len(case_ids) + len(external_ids) > MAX_PAGE_SIZE:
        raise UserError(f"You cannot request more than {MAX_PAGE_SIZE} cases per request.")

    results = BulkFetchResults()
    if case_ids:
        results.merge(_get_cases_by_id(domain, case_ids))

    if external_ids:
        results.merge(_get_cases_by_external_id(domain, external_ids))

    return dataclasses.asdict(results)


def _get_cases_by_id(domain, case_ids):
    es_results = ElasticCaseSearch().get_docs(case_ids)
    return _prepare_result(
        domain, es_results, case_ids,
        es_id_field='_id', serialized_id_field='case_id'
    )


def _get_cases_by_external_id(domain, external_ids):
    query = CaseSearchES().domain(domain).external_id(external_ids)
    es_results = query.run().hits

    return _prepare_result(
        domain, es_results, external_ids,
        es_id_field='external_id', serialized_id_field='external_id'
    )


def _prepare_result(domain, es_results, doc_ids, es_id_field, serialized_id_field):
    def _serialize_doc(doc):
        found_ids.add(doc[es_id_field])

        if doc['domain'] == domain:
            return serialize_es_case(doc)

        error_ids.add(doc[es_id_field])
        return _get_error_doc(doc[es_id_field], serialized_id_field)

    error_ids = set()
    found_ids = set()

    final_results = [_serialize_doc(doc) for doc in es_results]

    missing_ids = set(doc_ids) - found_ids
    final_results.extend([
        _get_error_doc(missing_id, serialized_id_field) for missing_id in missing_ids
    ])

    sort_with_id_list(final_results, doc_ids, serialized_id_field, operator=itemgetter)

    total = len(doc_ids)
    not_found = len(error_ids) + len(missing_ids)
    return BulkFetchResults(final_results, total - not_found, not_found)


def _get_error_doc(id_value, id_field):
    return {id_field: id_value, 'error': 'not found'}

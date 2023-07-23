from datetime import datetime, timedelta

from django.utils.translation import gettext
from eulxml.xpath import serialize
from eulxml.xpath.ast import Step

from corehq.apps.case_search.dsl_utils import unwrap_value
from corehq.apps.case_search.exceptions import CaseFilterError, XPathFunctionException
from corehq.apps.case_search.xpath_functions.value_functions import value_to_date
from corehq.apps.case_search.const import RANGE_OP_MAPPING, EQ, NEQ, SPECIAL_CASE_PROPERTIES
from corehq.apps.es import filters
from corehq.apps.es.case_search import case_property_query, case_property_range_query
from corehq.util.timezones.utils import get_timezone_for_domain
from corehq.util.timezones.conversions import UserTime


def property_comparison_query(context, case_property_name_raw, op, value_raw, node):
    if not isinstance(case_property_name_raw, Step):
        raise CaseFilterError(
            gettext("We didn't understand what you were trying to do with {}").format(serialize(node)),
            serialize(node)
        )

    case_property_name = serialize(case_property_name_raw)
    value = unwrap_value(value_raw, context)
    # adjust the user's input date based on project timezones
    is_user_input = False
    if case_property_name in SPECIAL_CASE_PROPERTIES:
        try:
            # this might be inconsistent in daylight savings situations
            timezone = get_timezone_for_domain(context.domain)
            return _create_timezone_adjusted_datetime_query(case_property_name, op, value, node, timezone)
        except (XPathFunctionException, AssertionError):
            # AssertionError is caused by tests that use domains without
            # a valid timezone (in get_timezeone_for_domain)
            pass
    return _create_query(context, case_property_name, op, value, node, is_user_input)


def _create_query(context, case_property_name, op, value, node, is_user_input):
    if op in [EQ, NEQ]:
        query = case_property_query(case_property_name, value, fuzzy=context.fuzzy)
        if op == NEQ:
            query = filters.NOT(query)
        return query
    else:
        op_value_dict = {RANGE_OP_MAPPING[op]: value}
        return _case_property_range_query(case_property_name, op_value_dict, node, is_user_input)


def _case_property_range_query(case_property_name: str, op_value_dict, node,
                               is_user_input: bool = False):
        try:
            return case_property_range_query(case_property_name, is_user_input=is_user_input,
                                             **op_value_dict)
        except (TypeError, ValueError):
            raise CaseFilterError(
                gettext("The right hand side of a comparison must be a number or date. "
                  "Dates must be surrounded in quotation marks"),
                serialize(node),
            )


def _create_timezone_adjusted_datetime_query(case_property_name, op, value, node, timezone):
    utc_datetime_value = adjust_input_date_by_timezone(value_to_date(node, value), timezone, op)
    op_val_dict = {RANGE_OP_MAPPING[op]: utc_datetime_value}
    return _case_property_range_query(case_property_name, op_val_dict, node, is_user_input=True)


def adjust_input_date_by_timezone(date, timezone, op):
    date = datetime.combine(date, datetime.min.time())
    if op == '>' or op == '<=':
        date += timedelta(days=1)
    return UserTime(date, tzinfo=timezone).server_time().done().isoformat()

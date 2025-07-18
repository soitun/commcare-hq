from lxml.builder import E

from casexml.apps.phone.fixtures import FixtureProvider

from corehq import extensions
from corehq.apps.case_search.exceptions import CaseFilterError
from corehq.apps.case_search.filter_dsl import build_filter_from_xpath
from corehq.apps.case_search.models import CaseSearchConfig
from corehq.apps.es.case_search import CaseSearchES
from corehq.messaging.templating import (
    MessagingTemplateRenderer,
    NestedDictTemplateParam,
)
from corehq.toggles import CSQL_FIXTURE

from .models import CSQLFixtureExpression


def _get_user_template_info(restore_user):
    return {
        "username": restore_user.username,
        "uuid": restore_user.user_id,
        "user_data": restore_user.user_session_data,
        "location_ids": " ".join(restore_user.get_location_ids(restore_user.domain)),
    }


def _get_template_renderer(restore_user):
    renderer = MessagingTemplateRenderer()
    renderer.set_context_param('user', NestedDictTemplateParam(_get_user_template_info(restore_user)))
    for name, param in custom_csql_fixture_context(restore_user.domain, restore_user):
        renderer.set_context_param(name, param)
    return renderer


@extensions.extension_point
def custom_csql_fixture_context(domain, restore_user):
    '''Register custom template params to be available in CSQL templates'''


def _run_query(domain, csql, index):
    try:
        filter_ = build_filter_from_xpath(csql, domain=domain)
    except CaseFilterError:
        return "ERROR"
    return str(CaseSearchES(index=index or None)
               .domain(domain)
               .filter(filter_)
               .count())


def _get_index(domain):
    return (CaseSearchConfig.objects
            .filter(domain=domain)
            .values_list('index_name', flat=True)
            .first()) or None


class CaseSearchFixtureProvider(FixtureProvider):
    id = 'case-search-fixture'

    def __call__(self, restore_state):
        if not CSQL_FIXTURE.enabled(restore_state.domain):
            return
        indicators = _get_indicators_for_user(restore_state.domain,
                                              restore_state.restore_user._couch_user)
        if indicators:
            with restore_state.timing_context('_get_template_renderer'):
                renderer = _get_template_renderer(restore_state.restore_user)
            index = _get_index(restore_state.domain)
            for name, csql_template in indicators:
                with restore_state.timing_context(name):
                    value = _run_query(restore_state.domain, renderer.render(csql_template), index)
                yield self._to_xml(name, value)

    def _to_xml(self, name, value):
        return E.fixture(E.value(value), id=f"{self.id}:{name}")


def _get_indicators_for_user(domain, user):
    user_data = user.get_user_data(domain)
    expressions = CSQLFixtureExpression.by_domain(domain).values_list('name', 'csql', 'user_data_criteria')

    return [
        (name, csql)
        for name, csql, user_data_criteria in expressions
        if CSQLFixtureExpression.matches_user_data_criteria(user_data, user_data_criteria)
    ]


case_search_fixture_generator = CaseSearchFixtureProvider()

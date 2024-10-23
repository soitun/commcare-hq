from django.test import SimpleTestCase, TestCase
from datetime import datetime

from corehq.apps.es.forms import form_adapter
from corehq.apps.es.tests.utils import es_test
from corehq.apps.users.models import CommCareUser
from corehq.form_processor.tests.utils import create_form_for_test
from corehq.apps.enterprise.iterators import (
    raise_after_max_elements,
    domain_form_generator,
    multi_domain_form_generator,
)


class TestRaiseAfterMaxElements(SimpleTestCase):
    def test_iterating_beyond_max_items_will_raise_the_default_exception(self):
        it = raise_after_max_elements([1, 2, 3], 2)
        with self.assertRaisesMessage(Exception, 'Too Many Elements'):
            list(it)

    def test_iterating_beyond_max_items_will_raise_provided_exception(self):
        it = raise_after_max_elements([1, 2, 3], 2, Exception('Test Message'))
        with self.assertRaisesMessage(Exception, 'Test Message'):
            list(it)

    def test_can_iterate_through_all_elements_with_no_exception(self):
        it = raise_after_max_elements([1, 2, 3], 3)
        self.assertEqual(list(it), [1, 2, 3])


@es_test(requires=[form_adapter])
class TestMultiDomainFormGenerator(TestCase):
    def setUp(self):
        self.user = CommCareUser.create('test-domain', 'test-user', 'password', None, None)
        self.addCleanup(self.user.delete, None, None)

    def test_iterates_through_multiple_domains(self):
        forms = [
            self._create_form('domain1', form_id='1', received_on=datetime(year=2024, month=7, day=1)),
            self._create_form('domain2', form_id='2', received_on=datetime(year=2024, month=7, day=2)),
            self._create_form('domain3', form_id='3', received_on=datetime(year=2024, month=7, day=3)),
        ]
        form_adapter.bulk_index(forms, refresh=True)

        it = multi_domain_form_generator(
            ['domain1', 'domain2', 'domain3'],
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=15)
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['1', '2', '3'])

    def test_respects_limit_across_multiple_domains(self):
        forms = [
            self._create_form('domain1', form_id='1', received_on=datetime(year=2024, month=7, day=1)),
            self._create_form('domain1', form_id='2', received_on=datetime(year=2024, month=7, day=2)),
            self._create_form('domain2', form_id='3', received_on=datetime(year=2024, month=7, day=3)),
            self._create_form('domain2', form_id='4', received_on=datetime(year=2024, month=7, day=4)),
        ]
        form_adapter.bulk_index(forms, refresh=True)

        it = multi_domain_form_generator(
            ['domain1', 'domain2'],
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=15),
            limit=3
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['2', '1', '4'])

    def _create_form(self, domain, form_id=None, received_on=None):
        form_data = {
            '#type': 'fake-type',
            'meta': {
                'userID': self.user._id,
                'instanceID': form_id,
            },
        }
        return create_form_for_test(
            domain,
            user_id=self.user._id,
            form_data=form_data,
            form_id=form_id,
            received_on=received_on
        )


@es_test(requires=[form_adapter])
class TestDomainFormGenerator(TestCase):
    def setUp(self):
        self.user = CommCareUser.create('test-domain', 'test-user', 'password', None, None)
        self.addCleanup(self.user.delete, None, None)

    def test_iterates_through_all_forms_in_domain(self):
        form1 = self._create_form('test-domain', form_id='1', received_on=datetime(year=2024, month=7, day=2))
        form2 = self._create_form('test-domain', form_id='2', received_on=datetime(year=2024, month=7, day=3))
        form3 = self._create_form('test-domain', form_id='3', received_on=datetime(year=2024, month=7, day=4))
        form_adapter.bulk_index([form1, form2, form3], refresh=True)

        it = domain_form_generator(
            'test-domain',
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=15),
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['3', '2', '1'])

    def test_handles_empty_domain(self):
        it = domain_form_generator(
            'empty-domain',
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=15),
        )

        self.assertEqual(list(it), [])

    def test_includes_inclusive_boundaries(self):
        form1 = self._create_form('test-domain', form_id='1', received_on=datetime(year=2024, month=7, day=1))
        form2 = self._create_form('test-domain', form_id='2', received_on=datetime(year=2024, month=7, day=2))
        form_adapter.bulk_index([form1, form2], refresh=True)

        it = domain_form_generator(
            'test-domain',
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=2)
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['2', '1'])

    def test_ignores_form_in_another_domain(self):
        form1 = self._create_form('test-domain', form_id='1', received_on=datetime(year=2024, month=7, day=2))
        form2 = self._create_form('not-test-domain', form_id='2', received_on=datetime(year=2024, month=7, day=2))
        form_adapter.bulk_index([form1, form2], refresh=True)

        it = domain_form_generator(
            'test-domain',
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=15),
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['1'])

    def test_sorts_by_date_then_id(self):
        form2 = self._create_form('test-domain', form_id='2', received_on=datetime(year=2024, month=7, day=1))
        form1 = self._create_form('test-domain', form_id='1', received_on=datetime(year=2024, month=7, day=1))
        form_adapter.bulk_index([form2, form1], refresh=True)

        it = domain_form_generator(
            'test-domain',
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=2),
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['1', '2'])

    def test_does_not_return_forms_beyond_limit(self):
        form1 = self._create_form('test-domain', form_id='1', received_on=datetime(year=2024, month=7, day=1))
        form2 = self._create_form('test-domain', form_id='2', received_on=datetime(year=2024, month=7, day=1))
        form_adapter.bulk_index([form1, form2], refresh=True)

        it = domain_form_generator(
            'test-domain',
            start_date=datetime(year=2024, month=7, day=1),
            end_date=datetime(year=2024, month=7, day=2),
            limit=1
        )

        form_ids = [form['_id'] for form in list(it)]
        self.assertEqual(form_ids, ['1'])

    def _create_form(self, domain, form_id=None, received_on=None):
        form_data = {
            '#type': 'fake-type',
            'meta': {
                'userID': self.user._id,
                'instanceID': form_id,
            },
        }
        return create_form_for_test(
            domain,
            user_id=self.user._id,
            form_data=form_data,
            form_id=form_id,
            received_on=received_on
        )

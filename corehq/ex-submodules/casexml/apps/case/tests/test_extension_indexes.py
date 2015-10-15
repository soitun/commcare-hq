import json
import os.path
import uuid

import datetime
import re
from casexml.apps.case.mock import CaseBlock
from casexml.apps.case.tests.util import assert_user_doesnt_have_cases, \
    assert_user_has_cases, delete_all_cases, delete_all_sync_logs, \
    delete_all_xforms
from casexml.apps.case.util import post_case_blocks
from casexml.apps.case.xml import V2
from casexml.apps.phone.models import User
from casexml.apps.phone.tests.restore_test_utils import \
    run_with_cleanliness_restore
from django.test import TestCase


def get_test_file_json(filename):
    base = os.path.dirname(__file__)
    file_path = 'data'
    path = os.path.join(base, file_path, '%s.json' % filename)
    with open(path) as f:
        file_contents = f.read()

    return json.loads(file_contents)


def test_generator(test_name):
    @run_with_cleanliness_restore
    def test(self):
        self.build_case_blocks(test_name)
        desired_cases = self._get_test(test_name).get('outcome', [])
        undesired_cases = [case for case in self.ALL_CASES if case not in desired_cases]
        assert_user_has_cases(self, self.user, desired_cases)
        assert_user_doesnt_have_cases(self, self.user, undesired_cases)
    return test


class TestSequenceMeta(type):
    def __new__(mcs, name, bases, dict):
        for test_name in [test['name'] for test in
                          get_test_file_json('case_relationship_tests')]:
            # Create a new testcase that the test runner is able to find
            dict["test_%s" % re.sub("\s", "_", test_name)] = test_generator(test_name)

        return type.__new__(mcs, name, bases, dict)


class IndexTreeTest(TestCase):
    """Fetch all testcases from data/case_relationship_tests.json and run them

    Each testcase is structured as follows:
    {
        "name": The name of the test,
        "owned": Cases whose owner id is set to the user. Cases not in this list are owned by someone else,
        "subcases": [ A list of ordered pairs e.g. ["a","b"] means "a creates a child index pointing to b"],
        "extensions": [ A list of ordered pairs e.g. ["a","b"] means "a creates an extension index pointing to b"],
        "closed": A list of the closed cases,
        "outcome": When syncing all the cases, which cases should be sent to the phone,
    }
    """
    __metaclass__ = TestSequenceMeta

    USER_ID = uuid.uuid4().hex
    ALL_CASES = ['a', 'b', 'c', 'd', 'e']

    def setUp(self):
        delete_all_cases()
        delete_all_xforms()
        delete_all_sync_logs()
        self.user = User(user_id=self.USER_ID, username='USERNAME',
                         password="changeme", date_joined=datetime.datetime(2011, 6, 9))

    @property
    def all_tests(self):
        """All the test cases in a dict"""
        all_tests = {}
        tests = get_test_file_json('case_relationship_tests')
        for test in tests:
            all_tests[test['name']] = test
        return all_tests

    def _get_test(self, test_name):
        return self.all_tests[test_name]

    def build_case_blocks(self, test_name):
        test = self._get_test(test_name)
        case_blocks = []
        child_indices = {case: {} for case in self.ALL_CASES}
        extension_indices = {case: {} for case in self.ALL_CASES}

        subcases = test.get('subcases', [])
        for i, subcase in enumerate(subcases):
            child_indices[subcase[0]].update({'child_{}'.format(i): ('case', subcase[1], 'child')})

        extensions = test.get('extensions', [])
        for i, extension in enumerate(extensions):
            extension_indices[extension[0]].update({'host_{}'.format(i): ('case', extension[1], 'extension')})

        for case in self.ALL_CASES:
            case_indices = {}
            case_child_indices = child_indices.get(case, None)
            if case_child_indices:
                case_indices.update(case_child_indices)
            case_extension_indices = extension_indices.get(case, None)
            if case_extension_indices:
                case_indices.update(case_extension_indices)

            case_blocks.append(CaseBlock(
                create=True,
                case_id=case,
                user_id=self.USER_ID,
                owner_id=self.USER_ID if case in test.get('owned', []) else uuid.uuid4().hex,
                version=V2,
                index=case_indices,
                close=case in test.get('closed', []),
            ).as_xml())

        post_case_blocks(case_blocks)

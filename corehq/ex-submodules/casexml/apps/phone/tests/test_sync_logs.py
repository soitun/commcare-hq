import uuid
from django.test import TestCase, SimpleTestCase
from casexml.apps.case.xml import V1, V2
from casexml.apps.phone.models import SyncLog, CaseState
from casexml.apps.case.sharedmodels import CommCareCaseIndex
from casexml.apps.phone.restore import RestoreParams, RestoreConfig, restore_payload_path_cache_key
from casexml.apps.phone.tests.utils import create_restore_user, generate_restore_payload
from corehq.apps.app_manager.models import Application
from corehq.apps.domain.models import Domain
from corehq.util.test_utils import flag_enabled
from corehq.form_processor.tests.utils import use_sql_backend


class PhoneFootprintTest(SimpleTestCase):

    def test_empty(self):
        log = SyncLog()
        self.assertEqual(0, len(log.get_footprint_of_cases_on_phone()))
        
        log = SyncLog(cases_on_phone=[])
        self.assertEqual(0, len(log.get_footprint_of_cases_on_phone()))
        
        log = SyncLog(dependent_cases_on_phone=[])
        self.assertEqual(0, len(log.get_footprint_of_cases_on_phone()))
        
        log = SyncLog(cases_on_phone=[], dependent_cases_on_phone=[])
        self.assertEqual(0, len(log.get_footprint_of_cases_on_phone()))
    
    def test_cases_in_footprint(self):
        log = SyncLog(cases_on_phone=[CaseState(case_id="c1", indices=[]),
                                      CaseState(case_id="c2", indices=[])])
        self.assertEqual(2, len(log.get_footprint_of_cases_on_phone()))
        
        log.cases_on_phone.append(CaseState(case_id="c3", indices=[]))
        self.assertEqual(3, len(log.get_footprint_of_cases_on_phone()))
        
    def test_dependent_cases(self):
        log = SyncLog(cases_on_phone=[CaseState(case_id="c1", 
                                                indices=[CommCareCaseIndex(identifier="d1-id",
                                                                           referenced_id="d1")])],
                      dependent_cases_on_phone=[CaseState(case_id="d1", indices=[]),
                                                CaseState(case_id="d2", indices=[])])
        
        # d1 counts because it's referenced, d2 doesn't
        self.assertEqual(2, len(log.get_footprint_of_cases_on_phone()))
        self.assertTrue("d1" in log.get_footprint_of_cases_on_phone())
        self.assertFalse("d2" in log.get_footprint_of_cases_on_phone())
        
    def test_archive(self):
        log = SyncLog(cases_on_phone=[CaseState(case_id="c1", 
                                                indices=[CommCareCaseIndex(identifier="d1-id",
                                                                           referenced_id="d1")]),
                                      CaseState(case_id="c2", 
                                                indices=[CommCareCaseIndex(identifier="d1-id",
                                                                           referenced_id="d1"),
                                                         CommCareCaseIndex(identifier="d2-id",
                                                                           referenced_id="d2")]),
                                      CaseState(case_id="c3", indices=[])],
                      dependent_cases_on_phone=[CaseState(case_id="d1", indices=[]),
                                                CaseState(case_id="d2", indices=[])])
        self.assertEqual(5, len(log.get_footprint_of_cases_on_phone()))
        
        self.assertTrue("c3" in log.get_footprint_of_cases_on_phone())
        log.archive_case("c3")
        self.assertEqual(4, len(log.get_footprint_of_cases_on_phone()))
        self.assertFalse("c3" in log.get_footprint_of_cases_on_phone())
        
        self.assertTrue("c2" in log.get_footprint_of_cases_on_phone())
        self.assertTrue("d2" in log.get_footprint_of_cases_on_phone())
        log.archive_case("c2")
        self.assertEqual(2, len(log.get_footprint_of_cases_on_phone()))
        self.assertFalse("c2" in log.get_footprint_of_cases_on_phone())
        self.assertFalse("d2" in log.get_footprint_of_cases_on_phone())
        
        self.assertTrue("c1" in log.get_footprint_of_cases_on_phone())
        self.assertTrue("d1" in log.get_footprint_of_cases_on_phone())
        log.archive_case("c1")
        self.assertEqual(0, len(log.get_footprint_of_cases_on_phone()))


class SimpleCachingResponseTest(SimpleTestCase):

    def test_switch_restore_response(self):
        '''
        Ensures that when switching from using a FileRestoreResponse to a
        BlobRestoreResponse that we don't use the old FileRestoreResponse
        cache
        '''

        def get_restore_payload_path_cache_key():
            return restore_payload_path_cache_key(
                domain='domain',
                user_id='user_id',
                version='2.0',
                sync_log_id='synclogid',
                device_id='DEVICEID',
            )
        key1 = get_restore_payload_path_cache_key()
        with flag_enabled('BLOBDB_RESTORE'):
            key2 = get_restore_payload_path_cache_key()
        self.assertNotEqual(key1, key2)


class SyncLogModelTest(TestCase):
    domain = 'sync-log-model-test'

    @classmethod
    def setUpClass(cls):
        super(SyncLogModelTest, cls).setUpClass()
        cls.project = Domain(name=cls.domain)
        cls.project.save()
        cls.restore_user = create_restore_user(cls.domain, username=uuid.uuid4().hex)

    @classmethod
    def tearDownClass(cls):
        cls.project.delete()
        super(SyncLogModelTest, cls).tearDownClass()

    def test_basic_properties(self):
        # kick off a restore to generate the sync log
        generate_restore_payload(self.project, self.restore_user, items=True)
        sync_log = SyncLog.last_for_user(self.restore_user.user_id)
        self.assertEqual(self.restore_user.user_id, sync_log.user_id)
        self.assertEqual(self.restore_user.domain, sync_log.domain)

    def test_build_id(self):
        app = Application(domain=self.domain)
        app.save()
        config = RestoreConfig(
            project=self.project,
            restore_user=self.restore_user,
            params=RestoreParams(
                app=app,
            ),
        )
        config.get_payload()  # this generates the sync log
        sync_log = SyncLog.last_for_user(self.restore_user.user_id)
        self.assertEqual(self.restore_user.user_id, sync_log.user_id)
        self.assertEqual(self.restore_user.domain, sync_log.domain)
        self.assertEqual(app._id, sync_log.build_id)
        self.addCleanup(app.delete)

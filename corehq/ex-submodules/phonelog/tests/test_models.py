import uuid
from datetime import datetime
from django.test import TestCase
from phonelog.models import get_version_errors, DeviceReportEntry


def _create_report(message, version_number, domain, type):
    entry = DeviceReportEntry(
        date=datetime.utcnow(),
        xform_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        server_date=datetime.utcnow(),
        i="1",
        type=type,
        msg=message,
        app_version=_app_version(version_number),
        domain=domain
    )
    entry.save()
    return entry


def _app_version(version_number):
    return ('CommCare ODK, version "2.23"(380171). App v{num}. '
            "CommCare Version 2.23. Build 380171, "
            "built on: 2015-09-11".format(num=version_number))


class TestVersionErrors(TestCase):
    message_1 = ("org.javarosa.xpath.XPathMissingInstanceException"
                 "[XPath evaluation: Instance referenced by "
                 "instance(session)/user/dlajkslfjs/case does not exist]")
    message_2 = "message_2"

    def setUp(self):
        self.domain = 'domain'

    def test_returns_errors_for_version(self):
        _create_report(self.message_1, "2", self.domain, "exception")
        _create_report(self.message_2, "2", self.domain, "exception")

        errors = get_version_errors(self.domain, "2")
        self.assertItemsEqual(errors, [self.message_1, self.message_2])

    def test_other_types_not_returned(self):
        _create_report(self.message_1, "2", self.domain, "exception")
        _create_report(self.message_2, "2", self.domain, "blah")

        errors = get_version_errors(self.domain, "2")
        self.assertItemsEqual(errors, [self.message_1])

    def test_errors_for_differt_versions_not_returned(self):
        _create_report(self.message_1, "2", self.domain, "exception")
        _create_report(self.message_2, "3", self.domain, "exception")

        errors = get_version_errors(self.domain, "2")
        self.assertItemsEqual(errors, [self.message_1])

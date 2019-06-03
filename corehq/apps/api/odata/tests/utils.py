from __future__ import absolute_import
from __future__ import unicode_literals

import base64

from django.urls import reverse


class OdataTestMixin(object):

    view_urlname = None

    def _execute_query(self, credentials):
        return self.client.get(self.view_url, HTTP_AUTHORIZATION='Basic ' + credentials)

    @staticmethod
    def _get_correct_credentials():
        return CaseOdataTestMixin._get_basic_credentials('test_user', 'my_password')

    @staticmethod
    def _get_basic_credentials(username, password):
        return base64.b64encode("{}:{}".format(username, password).encode('utf-8')).decode('utf-8')


class CaseOdataTestMixin(OdataTestMixin):

    @property
    def view_url(self):
        return reverse(self.view_urlname, kwargs={'domain': self.domain.name})


class FormOdataTestMixin(OdataTestMixin):


    @property
    def view_url(self):
        return reverse(self.view_urlname, kwargs={'domain': self.domain.name, 'app_id': 'my_app_id'})

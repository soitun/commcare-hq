from datetime import datetime
from urllib.parse import urljoin

from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from dateutil import tz
from tastypie import fields
from tastypie.exceptions import ImmediateHttpResponse

from corehq.apps.accounting.models import BillingAccount
from corehq.apps.accounting.utils.account import (
    get_account_or_404,
    request_has_permissions_for_enterprise_admin,
)
from corehq.apps.api.odata.utils import FieldMetadata
from corehq.apps.api.odata.views import add_odata_headers
from corehq.apps.api.resources import HqBaseResource
from corehq.apps.api.resources.auth import ODataAuthentication
from corehq.apps.enterprise.enterprise import (
    EnterpriseReport,
    EnterpriseDomainReport,
    EnterpriseFormReport,
    EnterpriseMobileWorkerReport,
    EnterpriseODataReport,
    EnterpriseWebUserReport,
    CacheableReport,
    ExpiredCacheException,
)


class EnterpriseODataAuthentication(ODataAuthentication):
    def is_authenticated(self, request, **kwargs):
        authenticated = super().is_authenticated(request, **kwargs)
        if authenticated is not True:
            return authenticated

        domain = kwargs['domain'] if 'domain' in kwargs else request.domain
        account = get_account_or_404(domain)
        if not request_has_permissions_for_enterprise_admin(request, account):
            raise ImmediateHttpResponse(
                HttpResponseForbidden(_(
                    "You do not have permission to view this feed."
                ))
            )

        return True


class ODataResource(HqBaseResource):
    class Meta:
        include_resource_uri = False
        collection_name = 'value'
        authentication = ODataAuthentication()
        limit = 2000
        max_limit = 10000

    def alter_list_data_to_serialize(self, request, data):
        result = super().alter_list_data_to_serialize(request, data)

        path = urljoin(request.get_full_path(), 'schema/#feed')
        result['@odata.context'] = request.build_absolute_uri(path)

        meta = result['meta']
        result['@odata.count'] = meta['total_count']
        if 'next' in meta and meta['next']:
            result['@odata.nextLink'] = request.build_absolute_uri(meta['next'])

        del result['meta']
        return result

    def get_object_list(self, request):
        '''Intended to be overwritten in subclasses with query logic'''
        raise NotImplementedError()

    def obj_get_list(self, bundle, **kwargs):
        return self.get_object_list(bundle.request)

    def determine_format(self, request):
        # Currently a hack to force JSON. XML is supported by OData, but "Control Information" fields
        # (https://docs.oasis-open.org/odata/odata-json-format/v4.01/odata-json-format-v4.01.html#_Toc38457735)
        # are not valid attribute names in XML -- instead, they need to be implemented on the object's element.
        # https://docs.oasis-open.org/odata/odata-atom-format/v4.0/cs02/odata-atom-format-v4.0-cs02.html#_Entity
        # provides an example.
        # In the interrim, it seems forcing JSON to avoid XML is the best option
        return 'application/json'

    def create_response(self, request, data, response_class=HttpResponse, **response_kwargs):
        response = super().create_response(request, data, response_class, **response_kwargs)
        response['OData-Version'] = '4.0'
        return response

    def get_schema(self, request, **kwargs):
        """
        Returns the OData Schema Representation of this resource, in XML.
        Only supports GET requests.
        """
        # ripped from Tastypie Resource's get_schema, only skipping building the bundle and using create_response
        self.method_check(request, allowed=['get'])
        self.is_authenticated(request)
        self.throttle_check(request)
        self.log_throttled_access(request)

        primary_keys = self.get_primary_keys()

        metadata = render_to_string('api/odata_metadata.xml', {
            'fields': self.get_fields(),
            'primary_keys': primary_keys,
        })

        return add_odata_headers(HttpResponse(metadata, content_type='application/xml'))

    def get_fields(self):
        result = []
        for field_name, field_object in self.fields.items():
            result.append(FieldMetadata(field_name, self.get_odata_class(field_object)))

        return result

    @staticmethod
    def get_odata_class(field_object):
        mapping = {
            fields.CharField: 'Edm.String',
            fields.DateTimeField: 'Edm.DateTimeOffset',
            fields.DateField: 'Edm.Date',
            fields.IntegerField: 'Edm.Int32'
        }

        for field_type, odata_type_name in mapping.items():
            if isinstance(field_object, field_type):
                return odata_type_name

        raise KeyError(type(field_object))

    def get_primary_keys(self):
        raise NotImplementedError()

    @classmethod
    def convert_datetime(cls, datetime_string):
        # OData's edm:DateTimeOffset expects datetimes in ISO format: https://docs.oasis-open.org/odata/odata/v4.0/errata02/os/complete/part3-csdl/odata-v4.0-errata02-os-part3-csdl-complete.html#_Toc406398065  # noqa: E501
        if not datetime_string:
            return None

        time = datetime.strptime(datetime_string, EnterpriseReport.DATE_ROW_FORMAT)
        time = time.astimezone(tz.gettz('UTC'))
        return time.isoformat()


class ODataEnterpriseReportResource(ODataResource):
    class Meta(ODataResource.Meta):
        authentication = EnterpriseODataAuthentication()

    def get_list(self, request, **kwargs):
        try:
            response = super().get_list(request, **kwargs)
        except ExpiredCacheException:
            response = HttpResponseNotFound()

        return response

    def get_object_list(self, request):
        query_id = request.GET.get('query_id', None)
        report = CacheableReport(self.get_report(request), query_id)
        # HACK: Tastypie doesn't provide a hook to easily pass data to the paginator
        # it is not ideal to perform a side effect within this get method, but the paginator
        # assembles link data from the GET dictionary, so we are forced to modify the request somewhere
        self._add_query_id_to_request(request, report.query_id)

        return report.rows

    def get_report(self, request):
        raise NotImplementedError()

    def _add_query_id_to_request(self, request, query_id):
        if 'report' not in request.GET:
            new_params = request.GET.copy()
            new_params['query_id'] = query_id
            request.GET = new_params


class DomainResource(ODataEnterpriseReportResource):
    domain = fields.CharField()
    created_on = fields.DateTimeField()
    num_apps = fields.IntegerField()
    num_mobile_users = fields.IntegerField()
    num_web_users = fields.IntegerField()
    num_sms_last_30_days = fields.IntegerField()
    last_form_submission = fields.DateTimeField()

    def get_report(self, request):
        account = BillingAccount.get_account_by_domain(request.domain)
        return EnterpriseDomainReport(account, request.couch_user)

    def dehydrate(self, bundle):
        bundle.data['domain'] = bundle.obj[6]
        bundle.data['created_on'] = self.convert_datetime(bundle.obj[0])
        bundle.data['num_apps'] = bundle.obj[1]
        bundle.data['num_mobile_users'] = bundle.obj[2]
        bundle.data['num_web_users'] = bundle.obj[3]
        bundle.data['num_sms_last_30_days'] = bundle.obj[4]
        bundle.data['last_form_submission'] = self.convert_datetime(bundle.obj[5])

        return bundle

    def get_primary_keys(self):
        return ('domain',)


class WebUserResource(ODataEnterpriseReportResource):
    email = fields.CharField()
    name = fields.CharField()
    role = fields.CharField()
    last_login = fields.DateTimeField(null=True)
    last_access_date = fields.DateField(null=True)
    status = fields.CharField()
    domain = fields.CharField()

    def get_report(self, request):
        account = BillingAccount.get_account_by_domain(request.domain)
        return EnterpriseWebUserReport(account, request.couch_user)

    def dehydrate(self, bundle):
        bundle.data['email'] = bundle.obj[0]
        bundle.data['name'] = self.convert_not_available(bundle.obj[1])
        bundle.data['role'] = bundle.obj[2]
        bundle.data['last_login'] = self.convert_datetime(self.convert_not_available(bundle.obj[3]))
        bundle.data['last_access_date'] = self.convert_not_available(bundle.obj[4])
        bundle.data['status'] = bundle.obj[5]
        bundle.data['domain'] = bundle.obj[7]

        return bundle

    @classmethod
    def convert_not_available(cls, value):
        return None if value == 'N/A' else value

    def get_primary_keys(self):
        return ('email',)


class MobileUserResource(ODataEnterpriseReportResource):
    username = fields.CharField()
    name = fields.CharField()
    email = fields.CharField()
    role = fields.CharField()
    created_at = fields.DateTimeField()
    last_sync = fields.DateTimeField()
    last_submission = fields.DateTimeField()
    commcare_version = fields.CharField(blank=True)
    user_id = fields.CharField()
    domain = fields.CharField()

    def get_report(self, request):
        account = BillingAccount.get_account_by_domain(request.domain)
        return EnterpriseMobileWorkerReport(account, request.couch_user)

    def dehydrate(self, bundle):
        bundle.data['username'] = bundle.obj[0]
        bundle.data['name'] = bundle.obj[1]
        bundle.data['email'] = bundle.obj[2]
        bundle.data['role'] = bundle.obj[3]
        bundle.data['created_at'] = self.convert_datetime(bundle.obj[4])
        bundle.data['last_sync'] = self.convert_datetime(bundle.obj[5])
        bundle.data['last_submission'] = self.convert_datetime(bundle.obj[6])
        bundle.data['commcare_version'] = bundle.obj[7]
        bundle.data['user_id'] = bundle.obj[8]
        bundle.data['domain'] = bundle.obj[10]

        return bundle

    def get_primary_keys(self):
        return ('user_id',)


class ODataFeedResource(ODataEnterpriseReportResource):
    '''
    A Resource for listing all Domain-level OData feeds which belong to the Enterprise.
    Currently includes summary rows as well as individual reports
    '''

    domain = fields.CharField(null=True)
    num_feeds_used = fields.IntegerField(null=True)
    num_feeds_available = fields.IntegerField(null=True)
    report_name = fields.CharField(null=True)
    report_rows = fields.IntegerField(null=True)

    def get_report(self, request):
        account = BillingAccount.get_account_by_domain(request.domain)
        return EnterpriseODataReport(account, request.couch_user)

    def dehydrate(self, bundle):
        bundle.data['num_feeds_used'] = bundle.obj[0]
        bundle.data['num_feeds_available'] = bundle.obj[1]
        bundle.data['report_name'] = bundle.obj[2]
        bundle.data['report_rows'] = bundle.obj[3]
        bundle.data['domain'] = bundle.obj[5] if len(bundle.obj) >= 5 else None

        return bundle

    def get_primary_keys(self):
        return ('report_name',)  # very odd report that makes coming up with an actual key challenging


class FormSubmissionResource(ODataEnterpriseReportResource):
    class Meta(ODataEnterpriseReportResource.Meta):
        limit = 10000
        max_limit = 20000

    form_id = fields.CharField()
    form_name = fields.CharField()
    submitted = fields.DateTimeField()
    app_name = fields.CharField()
    mobile_user = fields.CharField()
    domain = fields.CharField()

    def get_report(self, request):
        enddate = datetime.strptime(request.GET['enddate'], '%Y-%m-%d') if 'enddate' in request.GET else None
        startdate = datetime.strptime(request.GET['startdate'], '%Y-%m-%d') if 'startdate' in request.GET else None
        account = BillingAccount.get_account_by_domain(request.domain)
        return EnterpriseFormReport(
            account, request.couch_user, start_date=startdate, end_date=enddate, include_form_id=True)

    def dehydrate(self, bundle):
        bundle.data['form_id'] = bundle.obj[0]
        bundle.data['form_name'] = bundle.obj[1]
        bundle.data['submitted'] = self.convert_datetime(bundle.obj[2])
        bundle.data['app_name'] = bundle.obj[3]
        bundle.data['mobile_user'] = bundle.obj[4]
        bundle.data['domain'] = bundle.obj[6]

        return bundle

    def get_primary_keys(self):
        return ('form_id', 'submitted',)

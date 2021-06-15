from django.core.management import BaseCommand, CommandError

from corehq.apps.linked_domain.applications import get_downstream_app_id
from corehq.apps.linked_domain.models import DomainLink
from corehq.apps.userreports.dbaccessors import get_report_configs_for_domain
from corehq.apps.userreports.models import DataSourceConfiguration, ReportConfiguration


def migrate_linked_reports(upstream_domain=None):
    if upstream_domain:
        domain_links = DomainLink.objects.filter(master_domain=upstream_domain)
    else:
        domain_links = DomainLink.objects.filter(deleted=False)

    for domain_link in domain_links:
        reports = get_report_configs_for_domain(domain_link.linked_domain)
        for report in reports:
            if report.report_meta.master_id and not report.config.meta.build.app_id:
                upstream_report = ReportConfiguration.get(report.report_meta.master_id)
                upstream_datasource = DataSourceConfiguration.get(upstream_report.config_id)
                downstream_app_id = get_downstream_app_id(
                    domain_link.linked_domain,
                    upstream_datasource.meta.build.app_id,
                )
                if not downstream_app_id:
                    # just as a backup in case upstream_app_id is not set but family_id is
                    downstream_app_id = get_downstream_app_id(
                        domain_link.linked_domain,
                        upstream_datasource.meta.build.app_id,
                        use_upstream_app_id=False
                    )
                if not downstream_app_id:
                    raise CommandError(f"Could not find downstream_app_id for upstream app"
                                       f" {upstream_datasource.meta.build.app_id} "
                                       f"in downstream domain {domain_link.linked_domain}")

                report.config.meta.build.app_id = downstream_app_id
                report.config.save()


class Command(BaseCommand):
    """
    Searches for existing linked reports that do not contain an app_id, and adds the app_id
    """

    def add_arguments(self, parser):
        parser.add_argument('upstream_domain', nargs='?')

    def handle(self, upstream_domain, **options):
        migrate_linked_reports(upstream_domain)

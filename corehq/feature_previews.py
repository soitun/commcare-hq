"""
Feature Previews are built on top of toggle, so if you migrate a toggle to
a feature preview, you shouldn't need to migrate the data, as long as the
slug is kept intact.
"""
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_prbac.utils import has_privilege as prbac_has_privilege
from memoized import memoized

from corehq.util.quickcache import quickcache
from .privileges import LOOKUP_TABLES
from .toggles import (
    StaticToggle,
    NAMESPACE_DOMAIN,
    TAG_PREVIEW,
    all_toggles_by_name_in_scope,
)


class FeaturePreview(StaticToggle):
    """
    FeaturePreviews should be used in conjunction with normal role based access.
    Check the FeaturePreview first since that's a faster operation.

    e.g.

    if feature_previews.BETA_FEATURE.enabled(domain) \
            and has_privilege(request, privileges.BETA_FEATURE):
        # do cool thing for BETA_FEATURE
    """

    def __init__(self, slug, label, description, help_link=None, privilege=None,
                 save_fn=None, can_self_enable_fn=None):
        self.privilege = privilege

        # a function determining whether this preview can be enabled
        # according to the request object
        self.can_self_enable_fn = can_self_enable_fn

        super(FeaturePreview, self).__init__(
            slug, label, TAG_PREVIEW, description=description,
            help_link=help_link, save_fn=save_fn, namespaces=[NAMESPACE_DOMAIN]
        )

    def has_privilege(self, request):
        has_privilege = True
        if self.privilege:
            has_privilege = prbac_has_privilege(request, self.privilege)

        can_self_enable = True
        if self.can_self_enable_fn:
            can_self_enable = self.can_self_enable_fn(request)

        return has_privilege and can_self_enable


def all_previews():
    return list(all_previews_by_name().values())


@memoized
def all_previews_by_name():
    return all_toggles_by_name_in_scope(globals(), toggle_class=FeaturePreview)


def previews_dict(domain):
    by_name = all_previews_by_name()
    enabled = previews_enabled_for_domain(domain)
    return {by_name[name].slug: True for name in enabled if name in by_name}


def preview_values_by_name(domain):
    """
    Loads all feature previews into a dictionary for use in JS
    """
    enabled_previews = previews_enabled_for_domain(domain)
    return {
        name: name in enabled_previews
        for name in all_previews_by_name().keys()
    }


@quickcache(["domain"], timeout=24 * 60 * 60, skip_arg=lambda _: settings.UNIT_TESTING)
def previews_enabled_for_domain(domain):
    """Return set of preview names that are enabled for the given domain"""
    return {
        name
        for name, preview in all_previews_by_name().items()
        if preview.enabled(domain)
    }


CALC_XPATHS = FeaturePreview(
    slug='calc_xpaths',
    label=_('Custom Calculations in Case List'),
    description=_(
        "Specify a custom xpath expression to calculate a value "
        "in the case list or case detail screen."),
    help_link=('https://dimagi.atlassian.net/wiki/spaces/commcarepublic/'
               'pages/2143951603/Case+List+and+Case+Detail+Configuration#'
               'Calculations-in-the-Case-List%2FDetail')
)

ENUM_IMAGE = FeaturePreview(
    slug='enum_image',
    label=_('Icons in Case List'),
    description=_(
        "Display a case property as an icon in the case list. "
        "For example, to show that a case is late, "
        'display a red square instead of "late: yes".'
    ),
    help_link=('https://dimagi.atlassian.net/wiki/spaces/commcarepublic/pages/2143945372/'
               'Application+Icons#Adding-Icons-in-Case-List-and-Case-Detail-screen')
)

CONDITIONAL_ENUM = FeaturePreview(
    slug='conditional_enum',
    label=_('Conditional ID Mapping in Case List'),
    description=_(
        "Specify a custom xpath expression to calculate a lookup key in the case list, case detail screen or "
        "case tile enum columns."
    ),
)

SPLIT_MULTISELECT_CASE_EXPORT = FeaturePreview(
    slug='split_multiselect_case_export',
    label=_('Split multi-selects in case export'),
    description=_(
        "This setting allows users to split multi-select questions into multiple "
        "columns in case exports."
    )
)

USE_LOCATION_DISPLAY_NAME = FeaturePreview(
    slug='use_location_display_name',
    label=_('Use location name'),
    description=_(
        "This setting changes the location dropdown to display location name instead of "
        "the full location path."
    )
)


def enable_callcenter(domain_name, checked):
    from corehq.apps.domain.models import Domain
    domain_obj = Domain.get_by_name(domain_name)
    domain_obj.call_center_config.enabled = checked
    domain_obj.save()


def can_enable_callcenter(request):
    # This will only allow domains to remove themselves from the
    # call center feature preview, but no new domains can currently activate
    # the preview. A request from product
    return CALLCENTER.enabled_for_request(request)


CALLCENTER = FeaturePreview(
    slug='callcenter',
    label=_("Call Center"),
    description=_(
        'The call center application setting allows an application to reference a '
        'mobile user as a case that can be monitored using CommCare.  '
        'This allows supervisors to view their workforce within CommCare.  '
        'From here they can do things like monitor workers with performance issues, '
        'update their case with possible reasons for poor performance, '
        'and offer guidance towards solutions.'),
    save_fn=enable_callcenter,
    can_self_enable_fn=can_enable_callcenter,
)


# Only used in Vellum
VELLUM_ADVANCED_ITEMSETS = FeaturePreview(
    slug='advanced_itemsets',
    label=_("Custom Single and Multiple Answer Questions"),
    description=_(
        "Allows display of custom lists, such as case sharing groups or locations as choices in Single Answer or "
        "Multiple Answer lookup Table questions. Configuring these questions requires specifying advanced logic. "
        "Available in form builder, as an additional option on the Lookup Table Data for lookup "
        "table questions."
    ),
    privilege=LOOKUP_TABLES,
)

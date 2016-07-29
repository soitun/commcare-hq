from django.conf import settings

from corehq.toggles import NAMESPACE_DOMAIN, NEW_EXPORTS, TF_USES_SQLITE_BACKEND
from dimagi.utils.logging import notify_exception

import threading

_thread_local = threading.local()


def get_local_domain_sql_backend_override(domain):
    try:
        return _thread_local.use_sql_backend[domain]
    except (AttributeError, KeyError):
        return None


def set_local_domain_sql_backend_override(domain):
    use_sql_backend_dict = getattr(_thread_local, 'use_sql_backend', {})
    use_sql_backend_dict[domain] = True
    _thread_local.use_sql_backend = use_sql_backend_dict


def should_use_sql_backend(domain_object_or_name):
    if settings.UNIT_TESTING:
        return _should_use_sql_backend_in_tests(domain_object_or_name)

    return _should_use_sql_backend_in_prod(domain_object_or_name)


def _should_use_sql_backend_in_prod(domain_object_or_name):
    from corehq.apps.domain.models import Domain
    # TODO: remove toggle once all domains have been migrated
    if isinstance(domain_object_or_name, Domain):
        domain_name = domain_object_or_name.name
        domain_object = domain_object_or_name
    else:
        domain_name = domain_object_or_name
        domain_object = Domain.get_by_name(domain_name)

    if domain_object is None:
        return False

    local_override = get_local_domain_sql_backend_override(domain_name)
    if local_override is not None:
        return local_override

    return domain_object.use_sql_backend


def _should_use_sql_backend_in_tests(domain_object_or_name):
    """The default return value is False unless the ``TESTS_SHOULD_USE_SQL_BACKEND`` setting
    has been set or a Domain object with the same name exists."""
    assert settings.UNIT_TESTING
    from corehq.apps.domain.models import Domain
    override = getattr(settings, 'TESTS_SHOULD_USE_SQL_BACKEND', None)
    if override is not None:
        return override

    if domain_object_or_name and getattr(settings, 'DB_ENABLED', True):
        domain_object = domain_object_or_name \
            if isinstance(domain_object_or_name, Domain) \
            else Domain.get_by_name(domain_object_or_name)
        return domain_object and domain_object.use_sql_backend
    else:
        return False


def use_new_exports(domain_name):
    return NEW_EXPORTS.enabled(domain_name) or should_use_sql_backend(domain_name)


def use_sqlite_backend(domain_name):
    return TF_USES_SQLITE_BACKEND.enabled(domain_name) or should_use_sql_backend(domain_name)


def is_commcarecase(obj):
    from casexml.apps.case.models import CommCareCase
    from corehq.form_processor.models import CommCareCaseSQL
    return isinstance(obj, (CommCareCase, CommCareCaseSQL))

from corehq.apps.domain.signals import commcare_domain_post_save
from corehq.apps.groups.signals import commcare_group_post_save
from corehq.apps.users.signals import commcare_user_post_save, couch_user_post_save

from corehq.pillows import cacheinvalidate

cache_pillow = cacheinvalidate.CacheInvalidatePillow()


def invalidate_cached_domain(sender, **kwargs):
    #cache_pillow.change_trigger(kwargs['domain'].to_json())
    cache_pillow.change_trigger({'doc': kwargs['domain'].to_json(), 'id': kwargs['domain']._id})
    return


commcare_domain_post_save.connect(invalidate_cached_domain)


def invalidate_cached_user(sender, **kwargs):
    print "invalidate cached_user triggered: %s" % kwargs['couch_user']['_id']
    cache_pillow.change_trigger({'doc': kwargs['couch_user'].to_json(), 'id': kwargs['couch_user']['_id']})
    #foo = kwargs['couch_user'].to_json()


couch_user_post_save.connect(invalidate_cached_user)


def invalidate_cached_group(sender, **kwargs):
    #cache_pillow.change_trigger(kwargs['group'])
    cache_pillow.change_trigger({'doc': kwargs['group'].to_json(), 'id': kwargs['group']._id})
    return


commcare_group_post_save.connect(invalidate_cached_group)

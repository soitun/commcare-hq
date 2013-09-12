from django.dispatch import Signal

commcare_group_post_save = Signal(providing_args=["group"])

from __future__ import absolute_import
from __future__ import print_function
from contextlib import contextmanager
from django.db.models import Manager
from django.db.models import IntegerField
from django.db.models.expressions import Value
from django.db.models.query import Q, QuerySet
from mptt.models import MPTTModel

from .cte import With

int_field = IntegerField()


class ALManager(Manager):

    def get_ancestors(self, node, ascending=False, include_self=False):
        """Query node ancestors

        :param node: A model instance or a QuerySet or Q object querying
        the adjacency list model. If a QuerySet, it should query a
        single value with something like `.values('pk')`. If Q the
        `include_self` argument will be ignored.
        :param ascending: Order of results. The default (`False`) gets
        results in descending order (root ancestor first, immediate
        parent last).
        :param include_self:
        :returns: A `QuerySet` instance.
        """
        parent_col = self.model.parent_id_attr

        if isinstance(node, Q):
            where = node
        elif include_self:
            if isinstance(node, QuerySet):
                where = Q(pk__in=node)
            else:
                where = Q(pk=node.id)
        elif isinstance(node, QuerySet):
            where = Q(pk__in=node.values(parent_col))
        else:
            where = Q(pk=getattr(node, parent_col))

        def make_cte_query(cte):
            return self.filter(where).values(
                "pk",
                parent_col,
                _depth=Value(0, output_field=int_field),
            ).union(
                cte.join(self.model, pk=getattr(cte.col, parent_col)).values(
                    "pk",
                    parent_col,
                    _depth=cte.col._depth - Value(1, output_field=int_field),
                ),
                all=True,
            )

        cte = With.recursive(make_cte_query)
        queryset = (
            cte
            .join(self.all(), pk=cte.col.pk)
            .with_cte(cte)
            .order_by(("-" if ascending else "") + "{}._depth".format(cte.name))
        )
        print(queryset.query)
        #raise Exception('stop')
        return queryset

    def get_descendants(self, node, include_self=False):
        """Query node descendants

        :param node: A model instance or a QuerySet or Q object querying
        the adjacency list model. If a QuerySet, it should query a
        single value with something like `.values('pk')`. If Q the
        `include_self` argument will be ignored.
        :returns: A `QuerySet` instance.
        """
        parent_col = self.model.parent_id_attr

        if isinstance(node, Q):
            where = node
        elif include_self:
            if isinstance(node, QuerySet):
                where = Q(pk__in=node)
            else:
                where = Q(pk=node.id)
        elif isinstance(node, QuerySet):
            where = Q(**{parent_col + "__in": node})
        else:
            where = Q(**{parent_col: node.id})

        def make_cte_query(cte):
            return self.filter(where).values(
                "pk",
                parent_col,
            ).union(
                cte.join(self.model, **{parent_col: cte.col.pk}).values(
                    "pk",
                    parent_col,
                ),
                all=True,
            )

        cte = With.recursive(make_cte_query)
        print(cte.join(self.all(), pk=cte.col.pk).with_cte(cte).query)
        #raise Exception('stop')
        return cte.join(self.all(), pk=cte.col.pk).with_cte(cte)

    get_queryset_ancestors = get_ancestors
    get_queryset_descendants = get_descendants

    @contextmanager
    def delay_mptt_updates(self):
        yield


class ALModel(MPTTModel):
    """Base class for tree models implemented with adjacency list pattern

    For more on adjacency lists, see
    https://explainextended.com/2009/09/24/adjacency-list-vs-nested-sets-postgresql/
    """

    parent_id_attr = 'parent_id'

    objects = ALManager()

    class Meta:
        abstract = True

    def get_children(self):
        return self.children

    def get_ancestors(self, **kw):
        return type(self).objects.get_ancestors(self, **kw)

    def get_descendants(self, **kw):
        return type(self).objects.get_descendants(self, **kw)

#    def _mptt_is_obsolete(self, *args, **kw):
#        raise NotImplementedError("MPTTModel method not implemented")
#
#    # methods of MPTTModel not referenced in HQ
#    # can be removed once this model no longer inherits from MPTTModel
#    get_descendant_count = _mptt_is_obsolete
#    get_family = _mptt_is_obsolete
#    get_next_sibling = _mptt_is_obsolete
#    get_previous_sibling = _mptt_is_obsolete
#    get_root = _mptt_is_obsolete
#    get_siblings = _mptt_is_obsolete
#    #insert_at = _mptt_is_obsolete
#    is_child_node = _mptt_is_obsolete
#    is_leaf_node = _mptt_is_obsolete
#    #is_root_node = _mptt_is_obsolete
#    move_to = _mptt_is_obsolete

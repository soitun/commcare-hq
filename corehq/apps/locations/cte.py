from __future__ import absolute_import
from __future__ import print_function
import weakref

import django
from django.db import connections
from django.db.models.expressions import Col, Expression
from django.db.models.options import Options
from django.db.models.query import QuerySet
from django.db.models.sql import (
    AggregateQuery, DeleteQuery, InsertQuery, Query, UpdateQuery,
)
from django.db.models.sql.compiler import (
    SQLAggregateCompiler, SQLCompiler, SQLDeleteCompiler, SQLInsertCompiler,
    SQLUpdateCompiler,
)


class With(object):
    """Common Table Expression primitive for Django
    """

    def __init__(self, queryset, name="cte"):
        self._queryset = queryset
        self.name = name
        self.col = CTEColumns(self)
        # Purposely overwrite recursive classmethod with boolean value
        # since the method is not useful on instances, and the attribute
        # is used to tell if the CTE is recursive or not.
        self.recursive = False
        self.alias = None

    @classmethod
    def recursive(cls, make_cte_queryset, name="cte"):
        """Create a recursive CTE

        This method is overwritten with a boolean value on CTE
        instances. The instance attribute indicates if the CTE is
        recursive or not. In other words
        ``callable(With.recursive) != callable(With(qs).recursive)``.

        :param make_cte_queryset: Function taking a single argument (a
        not-yet-fully-constructed cte object) and returning a `QuerySet`
        object. The returned `QuerySet` normally consists of an initial
        statement unioned with a recursive statement.
        :returns: The fully constructed recursive cte object.
        """
        cte = cls(None, name)
        cte._queryset = make_cte_queryset(cte)
        cte.recursive = True
        return cte

    def _add_to_query(self, query):
        if not query.tables:
            # HACK? prevent CTE becoming the initial alias
            query.get_initial_alias()
        num = len(query._cte_refs)
        while True:
            name = "{}{}".format(self.name, num)
            if name not in query.tables:
                break
            num += 1
        query.add_extra(
            select=None,
            select_params=None,
            where=None,
            params=None,
            tables=[name],
            order_by=None,
        )
        alias = name
        query._cte_refs[self] = (name, alias)
        return name, alias

    def _get_name_alias(self, query):
        """Get `(name, alias)` for this CTE in the given query

        This adds the CTE to the query's extra tables if it is not
        already reference by the query.
        """
        if isinstance(query.model, CTEModel):
            cte = query.model._meta._cte()
            assert cte is self, (cte, self)
            assert query is not self._queryset.query
            return self._get_name_alias(self._queryset.query)
        try:
            return query._cte_refs[self]
        except KeyError:
            return self._add_to_query(query)

    def _resolve_ref(self, name):
        return self._queryset.query.resolve_ref(name)

    def join(self, model_or_queryset, *filter_q, **filter_kw):
        """Join this CTE to the given model or queryset

        This CTE will be refernced by the returned queryset, but the
        corresponding `WITH ...` statement will not be prepended
        to the queryset's SQL output; use `cte.queryset()` or
        `CTEQuerySet.with_cte(cte)` to achieve that outcome.

        :param model_or_queryset: Model class or queryset to which the
        CTE should be joined.
        :param *filter_q: Join condition Q expressions (optional).
        :param **filter_kw: Join conditions. All LHS fields (kwarg keys)
        are assumed to reference `model_or_queryset` fields. Use
        `cte.col.name` on the RHS to recursively reference CTE query
        columns. For example: `cte.join(Book, id=cte.col.id)`
        :returns: A queryset with the given model or queryset joined to
        this CTE.
        """
        if isinstance(model_or_queryset, QuerySet):
            queryset = model_or_queryset.all()
        else:
            queryset = model_or_queryset.objects.all()
        query = queryset.query
        self._add_to_query(query)
        return queryset.filter(*filter_q, **filter_kw)

    def queryset(self, model=None):
        """Get a queryset selecting from this CTE

        The CTE `WITH ...` clause will be prepended to the queryset's
        SQL output.

        :param model: Optional model class to use as the queryset's
        primary model. If this is provided the CTE will be added to
        the queryset's table list and join conditions may be added
        with a subsequent `.filter(...)` call.
        :returns: A queryset.
        """
        query = CTEQuery(model)
        if model is None:
            model = query.model = CTEModel(self, query)
        query._with_ctes.append(self)
        return CTEQuerySet(model, query)


class CTEModel(object):
    # TODO make this implement model interface needed by query/queryset

    def __init__(self, cte, query):
        self._meta = CTEMeta(cte, query)

    def _copy_for_query(self, query):
        return type(self)(self._meta._cte(), query)


class CTEMeta(Options):

    def __init__(self, cte, query):
        super(CTEMeta, self).__init__(None)
        self.managed = False
        self.model = None
        self._cte = weakref.ref(cte)
        self._query = weakref.ref(query)

    @property
    def db_table(self):
        return self._cte()._get_name_alias(self._query())[0]

    @db_table.setter
    def db_table(self, value):
        if value != '':
            raise AttributeError("CTEMeta.db_table is read-only")

    @property
    def local_fields(self):
        return [col.target
            for col in self._cte()._queryset.query.select
            if isinstance(col, Col)]

    @local_fields.setter
    def local_fields(self, value):
        if value != []:
            raise AttributeError("CTEMeta.local_fields is read-only")

    @property
    def _relation_tree(self):
        return []


class CTEColumns(object):

    def __init__(self, cte):
        self._cte = weakref.ref(cte)

    def __getattr__(self, name):
        return CTEColumn(self._cte(), name)


class CTEColumn(Expression):

    def __init__(self, cte, name):
        self.cte = cte
        self.name = name

    def __repr__(self):
        return "<{} {}>".format(self.__class__.__name__, self.name)

    def _ref(self):
        return self.cte._resolve_ref(self.name)

    @property
    def output_field(self):
        return self._ref().output_field

    def as_sql(self, compiler, connection):
        qn = compiler.quote_name_unless_alias
        alias = self.cte._get_name_alias(compiler.query)[1]
        ref = self._ref()
        if isinstance(ref, Col):
            # resolve column references like 'pk'
            column = ref.target.column
        else:
            column = self.name
        return "%s.%s" % (qn(alias), qn(column)), []


class LazyReentrantGenerator(object):

    def __init__(self, get_items):
        self.get_items = get_items
        self.items = None

    def __iter__(self):
        if self.items is None:
            self.items = self.get_items()
        return iter(self.items)


class CTEQuerySet(QuerySet):
    """
    The QuerySet which ensures all CTE Node SQL compilation is processed by the
    CTE Compiler and has the appropriate extra syntax, selects, tables, and
    WHERE clauses.
    """

    def __init__(self, model=None, query=None, using=None, hints=None):
        # Only create an instance of a Query if this is the first invocation in
        # a query chain.
        if query is None:
            query = CTEQuery(model)
        super(CTEQuerySet, self).__init__(model, query, using, hints)

    def with_cte(self, cte):
        """Add a Common Table Expression to the given queryset

        The CTE `WITH ...` clause will be prepended to the queryset's
        SQL output so it can be referenced in annotations, filters, etc.
        """
        qs = self._clone()
        qs.query._with_ctes.append(cte)
        return qs

    def aggregate(self, *args, **kwargs):
        raise NotImplementedError("not implemented")


class CTEQuery(Query):
    """
    A Query which processes SQL compilation through the CTE Compiler, as well as
    keeps track of extra selects, the CTE table, and the corresponding WHERE
    clauses.
    """

    def __init__(self, *args, **kwargs):
        super(CTEQuery, self).__init__(*args, **kwargs)
        self._with_ctes = []
        self._cte_refs = {}

    def get_compiler(self, using=None, connection=None):
        """ Overrides the Query method get_compiler in order to return
            a CTECompiler.
        """
        # Copy the body of this method from Django except the final
        # return statement. We will ignore code coverage for this.
        if using is None and connection is None:  # pragma: no cover
            raise ValueError("Need either using or connection")
        if using:
            connection = connections[using]
        # Check that the compiler will be able to execute the query
        for alias, aggregate in self.annotation_select.items():
            connection.ops.check_expression_support(aggregate)
        # Instantiate the custom compiler.
        klass = COMPILER_TYPES.get(self.__class__, CTEQueryCompiler)
        return klass(self, connection, using)

    def __chain(self, _name, klass=None, memo=None, **kwargs):
        klass = QUERY_TYPES.get(klass, self.__class__)
        clone = getattr(super(CTEQuery, self), _name)(klass, memo, **kwargs)
        if isinstance(clone.model, CTEModel):
            clone.model = clone.model._copy_for_query(clone)
        clone._with_ctes = self._with_ctes[:]
        clone._cte_refs = self._cte_refs.copy()
        return clone

    if django.VERSION < (2, 0):
        def clone(self, klass=None, memo=None, **kwargs):
            """ Overrides Django's Query clone in order to return appropriate CTE
                compiler based on the target Query class. This mechanism is used by
                methods such as 'update' and '_update' in order to generate UPDATE
                queries rather than SELECT queries.
            """
            return self.__chain("clone", klass=None, memo=None, **kwargs)

    else:
        def chain(self, klass=None):
            """ Overrides Django's Query clone in order to return appropriate CTE
                compiler based on the target Query class. This mechanism is used by
                methods such as 'update' and '_update' in order to generate UPDATE
                queries rather than SELECT queries.
            """
            return self.__chain("chain", klass=None)


class CTECompiler(object):

    CTE = "WITH {name} AS ({query}) "
    RECURSIVE = "WITH RECURSIVE {name} AS ({query}) "

    @classmethod
    def generate_sql(cls, connection, query, as_sql):
        if query.combinator:
            return as_sql()

        sql = []
        params = []
        for cte in query._with_ctes:
            compiler = cte._queryset.query.get_compiler(connection=connection)
            cte_sql, cte_params = compiler.as_sql()
            cte_name = cte._get_name_alias(query)[0]
            temp = cls.RECURSIVE if cte.recursive else cls.CTE
            sql.append(temp.format(name=cte_name, query=cte_sql))
            params.extend(cte_params)

        base_sql, base_params = as_sql()
        sql.append(base_sql)
        params.extend(base_params)
        return "".join(sql), tuple(params)


class CTEUpdateQuery(UpdateQuery, CTEQuery):
    pass


class CTEInsertQuery(InsertQuery, CTEQuery):
    pass


class CTEDeleteQuery(DeleteQuery, CTEQuery):
    pass


class CTEAggregateQuery(AggregateQuery, CTEQuery):
    pass


QUERY_TYPES = {
    UpdateQuery: CTEUpdateQuery,
    InsertQuery: CTEInsertQuery,
    DeleteQuery: CTEDeleteQuery,
    AggregateQuery: CTEAggregateQuery,
}


class CTEQueryCompiler(SQLCompiler):

    def as_sql(self, *args, **kwargs):
        def _as_sql():
            return super(CTEQueryCompiler, self).as_sql(*args, **kwargs)
        return CTECompiler.generate_sql(self.connection, self.query, _as_sql)


class CTEUpdateQueryCompiler(SQLUpdateCompiler):

    def as_sql(self, *args, **kwargs):
        def _as_sql():
            return super(CTEUpdateQueryCompiler, self).as_sql(*args, **kwargs)
        return CTECompiler.generate_sql(self.connection, self.query, _as_sql)


class CTEInsertQueryCompiler(SQLInsertCompiler):

    def as_sql(self, *args, **kwargs):
        def _as_sql():
            return super(CTEInsertQueryCompiler, self).as_sql(*args, **kwargs)
        return CTECompiler.generate_sql(self.connection, self.query, _as_sql)


class CTEDeleteQueryCompiler(SQLDeleteCompiler):

    def as_sql(self, *args, **kwargs):
        def _as_sql():
            return super(CTEDeleteQueryCompiler, self).as_sql(*args, **kwargs)
        return CTECompiler.generate_sql(self.connection, self.query, _as_sql)


class CTEAggregateQueryCompiler(SQLAggregateCompiler):

    def as_sql(self, *args, **kwargs):
        """
        Overrides the :class:`SQLAggregateCompiler` method in order to
        prepend the necessary CTE syntax, as well as perform pre- and post-
        processing, including adding the extra CTE table and WHERE clauses.

        :param qn:
        :type qn:
        :return:
        :rtype:
        """
        def _as_sql():
            return super(CTEAggregateQueryCompiler, self).as_sql(*args, **kwargs)
        return CTECompiler.generate_sql(self.connection, self.query, _as_sql)


COMPILER_TYPES = {
    CTEUpdateQuery: CTEUpdateQueryCompiler,
    CTEInsertQuery: CTEInsertQueryCompiler,
    CTEDeleteQuery: CTEDeleteQueryCompiler,
    CTEAggregateQuery: CTEAggregateQueryCompiler,
}

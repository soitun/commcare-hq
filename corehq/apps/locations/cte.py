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
    """Common Table Expression query object: `WITH ...`

    :param queryset: A queryset to use as the body of the CTE.
    :param name: Optional name parameter for the CTE (default: "cte").
    This must be a unique name that does not conflict with other
    entities (tables, views, functions, other CTE(s), etc.) referenced
    in the given query as well any query to which this CTE will
    eventually be added.
    """

    def __init__(self, queryset, name="cte"):
        self._queryset = queryset
        self.name = name
        self.col = CTEColumns(self)

    def __repr__(self):
        return "<With {}>".format(self.name)

    @classmethod
    def recursive(cls, make_cte_queryset, name="cte"):
        """Recursive Common Table Expression: `WITH RECURSIVE ...`

        :param make_cte_queryset: Function taking a single argument (a
        not-yet-fully-constructed cte object) and returning a `QuerySet`
        object. The returned `QuerySet` normally consists of an initial
        statement unioned with a recursive statement.
        :param name: See `name` parameter of `__init__`.
        :returns: The fully constructed recursive cte object.
        """
        cte = cls(None, name)
        cte._queryset = make_cte_queryset(cte)
        return cte

    def _add_to_query(self, query):
        if not query.tables:
            # prevent CTE becoming the initial alias
            query.get_initial_alias()
        name = self.name
        if name in query.tables:
            raise ValueError("cannot add CTE with name '%s' because "
                "an entity with that name is already referenced in this "
                "query's FROM clause" % name)
        query.add_extra(
            select=None,
            select_params=None,
            where=None,
            params=None,
            tables=[name],
            order_by=None,
        )

    def _resolve_ref(self, name):
        return self._queryset.query.resolve_ref(name)

    def join(self, model_or_queryset, *filter_q, **filter_kw):
        """Join this CTE to the given model or queryset

        This CTE will be refernced by the returned queryset, but the
        corresponding `WITH ...` statement will not be prepended to the
        queryset's SQL output; use `<CTEQuerySet>.with_cte(cte)` to
        achieve that outcome.

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

        This CTE will be refernced by the returned queryset, but the
        corresponding `WITH ...` statement will not be prepended to the
        queryset's SQL output; use `<CTEQuerySet>.with_cte(cte)` to
        achieve that outcome.

        :param model: Optional model class to use as the queryset's
        primary model. If this is provided the CTE will be added to
        the queryset's table list and join conditions may be added
        with a subsequent `.filter(...)` call.
        :returns: A queryset.
        """
        query = CTEQuery(model)
        if model is None:
            model = query.model = CTEModel(self, query)
        else:
            self._add_to_query(query)
        return CTEQuerySet(model, query)


class CTEModel(object):

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
        return self._cte().name

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
        if self.cte._queryset is None:
            raise ValueError(
                "cannot resolve '{cte}.{name}' output field in recursive CTE "
                "setup. Hint: use ExpressionWrapper({cte}.col.{name}, "
                "output_field=...)".format(cte=self.cte.name, name=self.name)
            )
        return self._ref().output_field

    def as_sql(self, compiler, connection):
        qn = compiler.quote_name_unless_alias
        ref = self._ref()
        if isinstance(ref, Col) and self.name == "pk":
            column = ref.target.column
        else:
            column = self.name
        return "%s.%s" % (qn(self.cte.name), qn(column)), []


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
        """Add a Common Table Expression to this queryset

        The CTE `WITH ...` clause will be prepended to the queryset's
        SQL output so it can be referenced in annotations, filters, etc.
        """
        qs = self._clone()
        qs.query._with_ctes.insert(0, cte)
        return qs


class CTEQuery(Query):
    """
    A Query which processes SQL compilation through the CTE Compiler, as well as
    keeps track of extra selects, the CTE table, and the corresponding WHERE
    clauses.
    """

    def __init__(self, *args, **kwargs):
        super(CTEQuery, self).__init__(*args, **kwargs)
        self._with_ctes = []

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

    TEMPLATE = "{name} AS ({query})"

    @classmethod
    def generate_sql(cls, connection, query, as_sql):
        if query.combinator:
            return as_sql()

        ctes = []
        params = []
        for cte in query._with_ctes:
            compiler = cte._queryset.query.get_compiler(connection=connection)
            cte_sql, cte_params = compiler.as_sql()
            ctes.append(cls.TEMPLATE.format(name=cte.name, query=cte_sql))
            params.extend(cte_params)

        # Always use WITH RECURSIVE
        # https://www.postgresql.org/message-id/13122.1339829536%40sss.pgh.pa.us
        sql = ["WITH RECURSIVE", ", ".join(ctes)] if ctes else []
        base_sql, base_params = as_sql()
        sql.append(base_sql)
        params.extend(base_params)
        return " ".join(sql), tuple(params)


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

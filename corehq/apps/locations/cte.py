from __future__ import absolute_import
from __future__ import print_function
import weakref

import django
from django.db import connections
from django.db.models.expressions import Col
from django.db.models.query import QuerySet
from django.db.models.sql import (
    AggregateQuery, DeleteQuery, InsertQuery, Query, UpdateQuery,
)
from django.db.models.sql.compiler import (
    SQLAggregateCompiler, SQLCompiler, SQLDeleteCompiler, SQLInsertCompiler,
    SQLUpdateCompiler,
)
from django.db.models.sql.constants import INNER
from django.db.models.sql.datastructures import Join
from django.core.exceptions import FieldDoesNotExist


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

    def _ref(self, query):
        """Get alias of this CTE in the given query

        This adds the CTE to the query's extra tables if it is not
        already reference by the query.
        """
        if isinstance(query.model, CTEModel):
            cte = query.model._meta._cte()
            assert cte is self, (cte, self)
            return self.name
        if self not in query._cte_refs:
            alias = self._add_to_query(query)[1]
        else:
            alias = query._cte_refs[self][1]
        return alias

    def _get_name(self, query):
        """Get name of this CTE in the given query

        This adds the CTE to the query's extra tables if it is not
        already reference by the query.
        """
        if isinstance(query.model, CTEModel):
            cte = query.model._meta._cte()
            assert cte is self, (cte, self)
            return self.name
        if self not in query._cte_refs:
            name = self._add_to_query(query)[0]
        else:
            name = query._cte_refs[self][0]
        return name

    def _resolve_ref(self, name):
        return self._queryset.query.resolve_ref(name)

    def join(self, model_or_queryset, **conditions):
        """Recursively join this CTE to the given model class

        :param model_or_queryset: Model class or queryset to which the
        CTE should be joined.
        :param **conditions: Join conditions. All LHS fields (kwarg keys)
        are assumed to reference `model` fields. Use `cte.col.name` on
        the RHS to recursively reference CTE query fields.
        :returns: A queryset with the given model joined to this CTE.
        """
        if isinstance(model_or_queryset, QuerySet):
            queryset = model_or_queryset.clone()
        else:
            queryset = model_or_queryset.objects.get_queryset()
        query = queryset.query
        num = len(query._cte_refs)
        while True:
            cte_name = "{}{}".format(self.name, num)
            if cte_name not in query.tables:
                break
            num += 1
        alias = query.get_initial_alias()
        join_field = CTEJoinField(self, query, **conditions)
        # Joining this way allows more natrual join semantics for the
        # generated SQL, but less expressive join conditions (can only
        # use `=` operator and other limitations of
        # `django.db.models.sql.datastructures.Join`). Would be nice to
        # support full Q() expressions for join conditions.
        join = Join(cte_name, alias, None, INNER, join_field, nullable=False)
        cte_alias = query.join(join)
        query._cte_refs[self] = (cte_name, cte_alias)
        return queryset

    def queryset(self, model=None):
        query = CTEQuery(model)
        if model is None:
            model = query.model = CTEModel(self, query)
        else:
            query._cte_queries.append(self)
        return CTEQuerySet(model, query)


class CTEJoinField(object):

    def __init__(self, cte, query, **conditions):
        self.cte = weakref.ref(cte)
        self.query = query
        self.conditions = conditions

    def get_joining_columns(self):
        def cols(key, value):
            if not isinstance(value, CTEColumn):
                raise TypeError("bad CTE join value: {!r}".format(value))
            if value.cte is not self.cte():
                raise ValueError("unexpected CTE reference: {!r}".format(value))
            col = self.query.resolve_ref(key, allow_joins=False)
            x, lhs_column = col.target.get_attname_column()
            x, rhs_column = value.target.get_attname_column()
            return lhs_column, rhs_column
        def get_cols():
            return sorted(cols(k, v) for k, v in self.conditions.items())
        # HACK because `django.db.models.sql.datastructures.Join` is too
        # eager in calling `join_field.get_joining_columns()`
        return LazyReentrantGenerator(get_cols)

    def get_extra_restriction(self, *args, **kw):
        return None


class CTEModel(object):
    # TODO make this implement model interface needed by query/queryset

    def __init__(self, cte, query):
        self._meta = CTEMeta(cte, query)

    def _copy_for_query(self, query):
        return type(self)(self._meta._cte(), query)


class CTEMeta(object):

    def __init__(self, cte, query):
        self._cte = weakref.ref(cte)
        self._query = weakref.ref(query)

    @property
    def db_table(self):
        return self._cte()._get_name(self._query())

    def get_field(self, field_name):
        for field in self.get_fields():
            if field.name == field_name:
                return field
        raise FieldDoesNotExist(
            "%s has no field named '%s'" % (self.db_table, field_name))

    def get_fields(self):
        return [col.target for col in self._cte()._queryset.query.select]

    @property
    def concrete_fields(self):
        return [f for f in self.get_fields() if f.concrete]

    @property
    def related_objects(self):
        return [
            f for f in self.get_fields()
            if (f.one_to_many or f.one_to_one)
            and f.auto_created and not f.concrete
        ]


class CTEColumns(object):

    def __init__(self, cte):
        self._cte = weakref.ref(cte)

    def __getattr__(self, name):
        return CTEColumn(self._cte(), name)


class CTEColumn(Col):

    def __init__(self, cte, name):
        self.cte = cte
        self.name = name

    def __repr__(self):
        return "<{} {}>".format(self.__class__.__name__, self.name)

    @property
    def target(self):
        return self.cte._resolve_ref(self.name).target

    @property
    def output_field(self):
        return self.cte._resolve_ref(self.name).output_field

    def resolve_expression(self, query, *args, **kw):
        copy = super(CTEColumn, self).resolve_expression(query, *args, **kw)
        copy.alias = self.cte._ref(query)
        return copy


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
        qs = self._clone()
        qs.query._cte_queries.append(cte)
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
        self._cte_queries = []
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

    def _chain(self, _name, klass=None, memo=None, **kwargs):
        klass = QUERY_TYPES.get(klass, self.__class__)
        clone = getattr(super(CTEQuery, self), _name)(klass, memo, **kwargs)
        if isinstance(clone.model, CTEModel):
            clone.model = clone.model._copy_for_query(clone)
        clone._cte_queries = self._cte_queries[:]
        clone._cte_refs = self._cte_refs.copy()
        return clone

    if django.VERSION < (2, 0):
        def clone(self, klass=None, memo=None, **kwargs):
            """ Overrides Django's Query clone in order to return appropriate CTE
                compiler based on the target Query class. This mechanism is used by
                methods such as 'update' and '_update' in order to generate UPDATE
                queries rather than SELECT queries.
            """
            return self._chain("clone", klass=None, memo=None, **kwargs)

    else:
        def chain(self, klass=None):
            """ Overrides Django's Query clone in order to return appropriate CTE
                compiler based on the target Query class. This mechanism is used by
                methods such as 'update' and '_update' in order to generate UPDATE
                queries rather than SELECT queries.
            """
            return self._chain("chain", klass=None, memo=None, **kwargs)


class CTECompiler(object):

    CTE = "WITH {name} AS ({query}) "
    RECURSIVE = "WITH RECURSIVE {name} AS ({query}) "

    @classmethod
    def generate_sql(cls, connection, query, as_sql):
        if query.combinator:
            return as_sql()

        sql = []
        params = []
        tables = []
        for cte in query._cte_queries:
            name = cte._get_name(query)
            # add the CTE table to the Query's extras precisely once
            # (because we could be combining multiple CTE Queries).
#            if name not in query.extra_tables and name not in tables:
#                tables.append(name)

            compiler = cte._queryset.query.get_compiler(connection=connection)
            cte_sql, cte_params = compiler.as_sql()
            temp = cls.RECURSIVE if cte.recursive else cls.CTE
            sql.append(temp.format(name=name, query=cte_sql))
            params.extend(cte_params)

#        if tables:
#            # HACK should not be mutating query here
#            query.add_extra(
#                select=None,
#                select_params=None,
#                where=None,
#                params=None,
#                tables=tables,
#                order_by=None,
#            )

        base_sql, base_params = as_sql()
        sql.append(base_sql)
        params.extend(base_params)
        return "".join(sql), params


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

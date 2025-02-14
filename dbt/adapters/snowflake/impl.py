from dataclasses import dataclass
from typing import Mapping, Any, Optional, List, Union, Set, Tuple, Dict

import agate

from dbt.adapters.base.impl import AdapterConfig
from dbt.adapters.sql import SQLAdapter
from dbt.adapters.sql.impl import (
    LIST_SCHEMAS_MACRO_NAME,
    LIST_RELATIONS_MACRO_NAME,
)
from dbt.adapters.snowflake import SnowflakeConnectionManager
from dbt.adapters.snowflake import SnowflakeRelation
from dbt.adapters.snowflake import SnowflakeColumn
from dbt.contracts.graph.manifest import Manifest
from dbt.exceptions import raise_compiler_error, RuntimeException, DatabaseException
from dbt.utils import filter_null_values

LIST_DATABASE_OBJECTS_MACRO_NAME = "snowflake__list_database_objects"


@dataclass
class SnowflakeConfig(AdapterConfig):
    transient: Optional[bool] = None
    cluster_by: Optional[Union[str, List[str]]] = None
    automatic_clustering: Optional[bool] = None
    secure: Optional[bool] = None
    copy_grants: Optional[bool] = None
    snowflake_warehouse: Optional[str] = None
    query_tag: Optional[str] = None
    merge_update_columns: Optional[str] = None


class SnowflakeAdapter(SQLAdapter):
    Relation = SnowflakeRelation
    Column = SnowflakeColumn
    ConnectionManager = SnowflakeConnectionManager

    AdapterSpecificConfigs = SnowflakeConfig

    @classmethod
    def date_function(cls):
        return "CURRENT_TIMESTAMP()"

    @classmethod
    def _catalog_filter_table(
        cls, table: agate.Table, manifest: Manifest
    ) -> agate.Table:
        # On snowflake, users can set QUOTED_IDENTIFIERS_IGNORE_CASE, so force
        # the column names to their lowercased forms.
        lowered = table.rename(column_names=[c.lower() for c in table.column_names])
        return super()._catalog_filter_table(lowered, manifest)

    def _make_match_kwargs(self, database, schema, identifier):
        quoting = self.config.quoting
        if identifier is not None and quoting["identifier"] is False:
            identifier = identifier.upper()

        if schema is not None and quoting["schema"] is False:
            schema = schema.upper()

        if database is not None and quoting["database"] is False:
            database = database.upper()

        return filter_null_values(
            {"identifier": identifier, "schema": schema, "database": database}
        )

    def _get_warehouse(self) -> str:
        _, table = self.execute("select current_warehouse() as warehouse", fetch=True)
        if len(table) == 0 or len(table[0]) == 0:
            # can this happen?
            raise RuntimeException("Could not get current warehouse: no results")
        return str(table[0][0])

    def _use_warehouse(self, warehouse: str):
        """Use the given warehouse. Quotes are never applied."""
        self.execute("use warehouse {}".format(warehouse))

    def pre_model_hook(self, config: Mapping[str, Any]) -> Optional[str]:
        default_warehouse = self.config.credentials.warehouse
        warehouse = config.get("snowflake_warehouse", default_warehouse)
        if warehouse == default_warehouse or warehouse is None:
            return None
        previous = self._get_warehouse()
        self._use_warehouse(warehouse)
        return previous

    def post_model_hook(
        self, config: Mapping[str, Any], context: Optional[str]
    ) -> None:
        if context is not None:
            self._use_warehouse(context)

    def list_schemas(self, database: str) -> List[str]:
        try:
            results = self.execute_macro(
                LIST_SCHEMAS_MACRO_NAME, kwargs={"database": database}
            )
        except DatabaseException as exc:
            msg = (
                f"Database error while listing schemas in database "
                f'"{database}"\n{exc}'
            )
            raise RuntimeException(msg)
        # this uses 'show terse schemas in database', and the column name we
        # want is 'name'

        return [row["name"] for row in results]

    def get_columns_in_relation(self, relation):
        try:
            return super().get_columns_in_relation(relation)
        except DatabaseException as exc:
            if "does not exist or not authorized" in str(exc):
                return []
            else:
                raise

    def list_relations_without_caching(
        self, schema_relation: SnowflakeRelation
    ) -> List[SnowflakeRelation]:
        kwargs = {"schema_relation": schema_relation}
        try:
            results = self.execute_macro(LIST_RELATIONS_MACRO_NAME, kwargs=kwargs)
        except DatabaseException as exc:
            # if the schema doesn't exist, we just want to return.
            # Alternatively, we could query the list of schemas before we start
            # and skip listing the missing ones, which sounds expensive.
            if "Object does not exist" in str(exc):
                return []
            raise

        relations = []
        quote_policy = {"database": True, "schema": True, "identifier": True}

        columns = ["database_name", "schema_name", "name", "kind"]
        for _database, _schema, _identifier, _type in results.select(columns):
            try:
                _type = self.Relation.get_relation_type(_type.lower())
            except ValueError:
                _type = self.Relation.External
            relations.append(
                self.Relation.create(
                    database=_database,
                    schema=_schema,
                    identifier=_identifier,
                    quote_policy=quote_policy,
                    type=_type,
                )
            )

        return relations

    def quote_seed_column(self, column: str, quote_config: Optional[bool]) -> str:
        quote_columns: bool = False
        if isinstance(quote_config, bool):
            quote_columns = quote_config
        elif quote_config is None:
            pass
        else:
            raise_compiler_error(
                f'The seed configuration value of "quote_columns" has an '
                f"invalid type {type(quote_config)}"
            )

        if quote_columns:
            return self.quote(column)
        else:
            return column

    def timestamp_add_sql(
        self, add_to: str, number: int = 1, interval: str = "hour"
    ) -> str:
        return f"DATEADD({interval}, {number}, {add_to})"

    def set_relations_cache(self, manifest: Manifest, clear: bool = False) -> None:
        """Run a query that gets a populated cache of the relations in the
        database and set the cache on this adapter.
        """
        with self.cache.lock:
            if clear:
                self.cache.clear()
            self._relations_cache_for_schemas(manifest)

    def _relations_cache_for_schemas(self, manifest: Manifest) -> None:
        """Populate the relations cache for the given schemas. Returns an
        iterable of the schemas populated, as strings.
        """
        cache_schemas = self._get_cache_schemas(manifest)
        schema_databases: Set[str] = set()
        cache_schema_names: Set[str] = set()
        for cache_schema in cache_schemas:
            schema_databases.add(cache_schema.database)
            cache_schema_names.add(cache_schema.schema.lower())

        for database in schema_databases:
            try:
                results = self.execute_macro(
                    LIST_DATABASE_OBJECTS_MACRO_NAME, kwargs={"database": database}
                )
            except DatabaseException as exc:
                msg = (
                    f"Database error while listing objects in database "
                    f'"{database}"\n{exc}'
                )
                raise RuntimeException(msg)

            for database_object in results:
                if database_object["schema_name"].lower() in cache_schema_names:
                    relation = self._database_object_to_relation(database_object)
                    self.cache.add(relation)

        # it's possible that there were no relations in some schemas. We want
        # to insert the schemas we query into the cache's `.schemas` attribute
        # so we can check it later
        cache_update: Set[Tuple[Optional[str], Optional[str]]] = set()
        for relation in cache_schemas:
            cache_update.add((relation.database, relation.schema))
        self.cache.update_schemas(cache_update)

    def _database_object_to_relation(self, database_object: Dict) -> SnowflakeRelation:
        quote_policy = {"database": True, "schema": True, "identifier": True}
        try:
            _type = self.Relation.get_relation_type(database_object["kind"].lower())
        except ValueError:
            _type = self.Relation.External

        relation = self.Relation.create(
            database=database_object["database_name"],
            schema=database_object["schema_name"],
            identifier=database_object["name"],
            quote_policy=quote_policy,
            type=_type,
        )

        return relation

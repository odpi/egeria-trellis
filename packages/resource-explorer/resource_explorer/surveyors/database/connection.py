"""Database connection abstraction for different database types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from resource_explorer.registry import DatabaseEntity


@dataclass(frozen=True)
class EngineCapabilities:
    """What this connection's engine can report, declared per capability
    rather than as one blanket "native support" flag.

    Design doc §5.1: "the connection layer gains a capability declaration per
    engine (`supports: {column_stats, tuple_counters, replication_status,
    query_stats, ...}`), and each catalog-fed analysis states which capability
    it needs. A finding whose capability is absent is `not_established`, not
    `nothing_found`."

    Only the capabilities a build actually extracts are declared True.
    `query_stats` stays False on every engine as of this slice — nothing
    reads `pg_stat_statements` yet (design §5.1 lists it as a later input to
    `db_classification`, Phase 1 slice 9). Declaring a capability True ahead
    of any code that reads it would make "not yet implemented"
    indistinguishable from "measured, and there was nothing" — the exact
    collapse this field exists to prevent.

    `replication_status`, `resilience` and `external_dependencies` were
    False through Phase 1 slice 7; slice 8's `postgres_operations` step
    (design §5.5, §5.7) is what reads `pg_is_in_recovery()`,
    `pg_stat_replication`, `pg_settings.archive_mode`, `pg_stat_archiver`,
    `pg_extension`, `pg_foreign_server`/`pg_foreign_table` and
    `pg_publication`/`pg_subscription`, so Postgres declares them True from
    this slice on (see `PostgreSQLConnection.capabilities` below).
    `privileges` (`pg_roles`, `role_table_grants`, `pg_default_acl`) is new
    in this slice too, for the same reason — `privilege_audit` existed as a
    catalog analysis id before this slice but had no dedicated capability
    or step backing it (confirmed "aspirational" per
    `COORDINATOR-BRIEF-MULTI-RESOURCE.md`'s Phase 0 target-shape audit).
    """

    column_stats: bool = False
    tuple_counters: bool = False
    index_stats: bool = False
    replication_status: bool = False
    query_stats: bool = False
    resilience: bool = False
    external_dependencies: bool = False
    #: `pg_roles` / `information_schema.role_table_grants` / `pg_default_acl`
    #: — added Phase 1 slice 8 alongside the others above, not part of the
    #: original PR #191 declaration.
    privileges: bool = False
    #: Whether this engine can take a BOUNDED sample of a column's actual
    #: values — Phase 1 slice 10's `postgres_column_profile` (design §5.7,
    #: §5.8). Its own capability rather than a corollary of `column_stats`,
    #: because the two are genuinely independent: `pg_stats` gives a profile
    #: with no rows read, while data-class and reference-data matching need
    #: real values (design §5.1: "the only route for data-class and
    #: reference-data matching, which need actual values"). An engine could
    #: have either without the other.
    #:
    #: When False, `data_class_match`/`reference_data_match` are
    #: `not_established` for every column — never "no match found". That
    #: distinction is the reason this is a declared capability rather than an
    #: empty result set.
    value_sampling: bool = False
    #: Whether this engine can introspect what the CONNECTING CREDENTIAL
    #: itself can see and do — `credential_capability` (design: REPLY-
    #: DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §3/§4, replying to
    #: ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md #251). Catalog reads
    #: and privilege-check function calls only — `pg_namespace`/`pg_class`
    #: (unfiltered) compared against `has_schema_privilege`/
    #: `has_table_privilege`, `pg_has_role` for the statistics role, and
    #: `has_table_privilege(..., 'INSERT')` PROBED, never exercised, for
    #: write. Independent of `privileges` above (which audits OTHER roles'
    #: grants): this is about what THIS SESSION's own role can reach.
    credential_introspection: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


#: No capability beyond the generic information_schema reads every
#: DatabaseConnection subclass already does via get_schema_info(). The
#: default for any engine that has not declared otherwise.
NO_CAPABILITIES = EngineCapabilities()


@dataclass(frozen=True)
class ContainmentLevel:
    """One level of an engine's containment hierarchy ABOVE the table.

    REPLY-SCHEMA-AS-SUB-RESOURCE.md §5 (project owner, 2026-09-24): *"different
    databases have or do not have schemas, and their semantics differ...
    Containment is declared per engine, in the engine capability declaration
    design §5.1 already calls for on `DatabaseConnection`. Each level carries:
    its name in that engine's vocabulary, its Egeria technology type, semantic
    flags (`namespace`, `owner`, `security_boundary`, `physical_unit`), the
    default container, and the system containers to exclude."*

    Declared per engine rather than assumed, for exactly the reason
    `EngineCapabilities` above is: an engine that has not been taught to this
    codebase must produce an honest "nothing declared" rather than silently
    inheriting Postgres's hierarchy. "Schema" is the word Postgres uses; MySQL
    has no such level at all, Oracle's is an *owner* rather than a namespace,
    and DuckDB's parent is a file. Hardcoding `"schema"` anywhere downstream
    would make all four read alike.

    The four semantic flags are independent on purpose — §5's table has an
    engine for nearly every combination:

    - `namespace` — can a table be qualified by this level inside one
      connection (`schema.table`)? This is the flag the aggregation grain is
      derived from (see `EngineContainment.aggregation_grain`).
    - `owner` — is the level a *principal* rather than a container? Oracle's
      schema is a user; Postgres's is not.
    - `security_boundary` — does the level carry its own privilege? Postgres
      schemas do (`USAGE`), which is why the credential probe is per schema.
    - `physical_unit` — is the level a separate physical artifact (a file, a
      database with its own connection)?
    """

    #: The level's name in THIS engine's vocabulary — "schema" for Postgres,
    #: "catalog" for DuckDB, "owner" for Oracle. Never assumed by a caller.
    name: str
    #: Egeria's technology type for this level, so a `sub_resources` row (shape
    #: 2, not built here) and the native survey read-back agree on what they
    #: are naming. §5: *"Egeria's technology types already encode this per
    #: engine... so the declaration maps onto rule A rather than inventing a
    #: hierarchy."*
    egeria_technology_type: str = ""
    namespace: bool = False
    owner: bool = False
    security_boundary: bool = False
    physical_unit: bool = False
    #: The container every engine of this kind has by default — `public` for
    #: Postgres, `dbo` for SQL Server. Treated as an ORDINARY container, never
    #: special-cased away (REPLY §1: *"treat `public` as a schema like any
    #: other"*); declared so a reader can see which one it is, not so it can
    #: be skipped.
    default_container: str = ""
    #: Exact container names that are the engine's own plumbing and are
    #: excluded from analysis output by default.
    system_containers: tuple[str, ...] = ()
    #: Prefixes for the same, for engines that generate them (`pg_toast`,
    #: `pg_toast_temp_1`, `pg_temp_3`). A prefix rather than a pattern because
    #: every real case is a prefix and a regex here would be a licence to put
    #: matching logic in a declaration.
    system_container_prefixes: tuple[str, ...] = ()

    def is_system_container(self, name: str | None) -> bool:
        """Is `name` one of this level's system containers?

        The single place that question is answered, so a call site cannot
        drift into its own hardcoded `('pg_catalog', 'information_schema')`
        list — there were four such lists in `connection.py`'s own SQL before
        this declaration existed, and each is correct only for Postgres.
        """
        if not name:
            return False
        if name in self.system_containers:
            return True
        return any(name.startswith(p) for p in self.system_container_prefixes)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The engine has genuinely unprivileged catalog objects a credential can read
#: whatever its table grants — Postgres's `pg_class`/`pg_namespace`. This is
#: what `#257`'s catalog-only fallback already relies on, and what makes
#: "structure only" a reachable state for a schema with `USAGE` and no
#: `SELECT`.
STRUCTURAL_FLOOR_UNPRIVILEGED = "unprivileged"
#: A structural floor exists, but only with a specific elevated role or grant
#: — Oracle's `SELECT_CATALOG_ROLE` / `SELECT ANY DICTIONARY`, SQL Server's
#: `VIEW DEFINITION` at database scope. A database owner can grant it without
#: exposing row data, so it is a real ask to make; it is just not free.
STRUCTURAL_FLOOR_ROLE_GRANT = "role_grant"
#: No floor short of per-table grants — MySQL/MariaDB, whose
#: `information_schema` shows only objects the user already holds some
#: privilege on. There, an unreadable table is also an INVISIBLE one, so the
#: denominator ("of M tables") is not established either.
STRUCTURAL_FLOOR_NONE = "none"


@dataclass(frozen=True)
class EngineContainment:
    """An engine's containment levels above the table, outermost first.

    `levels` is ordered — `(database, schema)` for Postgres — so that
    `aggregation_grain` can be *derived* rather than declared twice. §5 point
    2: *"The aggregation grain is derived, not fixed: the lowest `namespace`
    level above table. Postgres → schema. MySQL → the database itself, so
    per-namespace output equals whole-database output and the interesting
    comparison moves up to the server."*

    An engine with no declaration gets `NO_CONTAINMENT`, whose
    `aggregation_grain` is `None`. That is not "this engine has one flat
    namespace" — it is "nobody has declared this engine's hierarchy", and the
    callers treat it as an absence (whole-database output only, labelled as
    such) rather than guessing.
    """

    engine: str = ""
    levels: tuple[ContainmentLevel, ...] = ()
    #: How much STRUCTURE this engine lets a credential see when it cannot
    #: read the data (architecture session, 2026-09-24). Postgres's
    #: "`information_schema` is privilege-filtered but `pg_class` is not"
    #: property — the one `#257`'s catalog-only fallback and the
    #: `structure_only` credential state both rest on — does NOT generalize:
    #: Oracle and SQL Server have a floor only behind a role grant, and MySQL
    #: has none at all. Declared per engine so a future engine says which of
    #: the three it is instead of silently inheriting Postgres's.
    #:
    #: The empty default is deliberate on `NO_CONTAINMENT`: an engine nobody
    #: has declared has not been found to have no floor — nobody looked.
    structural_floor: str = ""

    @property
    def declared(self) -> bool:
        return bool(self.levels)

    @property
    def aggregation_grain(self) -> ContainmentLevel | None:
        """The innermost `namespace` level above table, or None if undeclared.

        Derived, per §5 point 2. For Postgres the database level is NOT a
        namespace (you cannot write `database.schema.table` in one Postgres
        connection), so this resolves to `schema`; for a hypothetical MySQL
        declaration the database level would be the namespace and this would
        resolve to it, which is the correct answer there — per-namespace output
        then equals whole-database output, honestly rather than by accident.
        """
        for level in reversed(self.levels):
            if level.namespace:
                return level
        return None

    def level(self, name: str) -> ContainmentLevel | None:
        for level in self.levels:
            if level.name == name:
                return level
        return None

    def is_system_container(self, name: str | None) -> bool:
        """System-container test at the aggregation grain (no grain → False).

        False when nothing is declared is deliberate: with no declaration
        there is no grain to group by either, so no name can be excluded at a
        level that does not exist. The caller's own "is there a grain" check is
        what stops it from reporting per-container output — not this.
        """
        grain = self.aggregation_grain
        return bool(grain and grain.is_system_container(name))

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "structural_floor": self.structural_floor,
            "levels": [level.as_dict() for level in self.levels],
            "aggregation_grain": (
                self.aggregation_grain.name if self.aggregation_grain else None
            ),
        }


#: Nothing declared. The honest default for an engine this codebase has not
#: been taught, and NOT a claim that the engine is flat.
NO_CONTAINMENT = EngineContainment()

#: A Postgres database: a physical unit with its own connection and its own
#: `CONNECT` privilege, but NOT a namespace — one connection cannot qualify a
#: table with it, which is exactly why cross-database references need a
#: foreign server or `dblink` (§5 point 4) and why the grain below is `schema`.
POSTGRES_DATABASE_LEVEL = ContainmentLevel(
    name="database",
    egeria_technology_type="PostgreSQL Relational Database",
    namespace=False,
    owner=False,
    security_boundary=True,
    physical_unit=True,
)

#: A Postgres schema: §5's table, row 1 — *"a namespace with its own privilege
#: (`USAGE`); `public` default; extensions and `pg_toast` own schemas"*.
#: `owner=False` distinguishes it from Oracle, where the same level IS a user;
#: a Postgres schema has an owner but is not itself a principal.
POSTGRES_SCHEMA_LEVEL = ContainmentLevel(
    name="schema",
    # Egeria's own technology type for the level (its open-metadata type is
    # `DeployedDatabaseSchema`, which REPLY §5 names as already separate from
    # the database). Only the database-level type above is exercised by this
    # codebase today — the schema one is declared for shape 2's
    # `sub_resources` rows and the native read-back, neither of which is built
    # in this slice.
    egeria_technology_type="PostgreSQL Relational Database Schema",
    namespace=True,
    owner=False,
    security_boundary=True,
    physical_unit=False,
    default_container="public",
    system_containers=("pg_catalog", "information_schema"),
    # `pg_toast`, `pg_toast_temp_1`, `pg_temp_3` — generated, one per backend.
    system_container_prefixes=("pg_toast", "pg_temp"),
)

POSTGRES_CONTAINMENT = EngineContainment(
    engine="postgresql",
    levels=(POSTGRES_DATABASE_LEVEL, POSTGRES_SCHEMA_LEVEL),
    # `pg_class`/`pg_namespace` are readable by any connected role regardless
    # of `USAGE`/`SELECT`, which is what `get_credential_capability()`'s own
    # docstring establishes against a live instance and what `#257`'s
    # catalog-only fallback recovers tables through. Stated here rather than
    # assumed, because it is a Postgres property and not a database one.
    structural_floor=STRUCTURAL_FLOOR_UNPRIVILEGED,
)

#: `DatabaseEntity.db_type` spellings that mean Postgres. Postgres is the ONLY
#: engine declared in this slice, deliberately (REPLY §5 gives the shape for
#: eight more; building them without a live instance to check against would
#: declare semantics nobody verified). Anything else resolves to
#: `NO_CONTAINMENT` and reports whole-database output labelled as
#: "containment not declared for this engine" — never Postgres's hierarchy
#: applied to an engine that does not have it.
_CONTAINMENT_BY_ENGINE: dict[str, EngineContainment] = {
    "postgresql": POSTGRES_CONTAINMENT,
    "postgres": POSTGRES_CONTAINMENT,
    "pgsql": POSTGRES_CONTAINMENT,
}


def containment_for_engine(engine: str | None) -> EngineContainment:
    """Resolve a `DatabaseEntity.db_type` to its containment declaration.

    Takes the engine NAME rather than a connection because the one caller that
    needs it most — `db_derived`, the zero-fetch step — has no connection by
    construction and must still be able to group by the right level for a
    database whose credentials are gone.
    """
    return _CONTAINMENT_BY_ENGINE.get((engine or "").strip().lower(), NO_CONTAINMENT)


class DatabaseConnection(ABC):
    """Abstract base class for database connections."""

    @abstractmethod
    def connect(self) -> Any:
        """Establish connection to the database."""

    @abstractmethod
    def execute_query(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return results as list of dicts."""

    @abstractmethod
    def get_schema_info(self) -> dict:
        """Get database schema information (schemas, tables, columns)."""

    @abstractmethod
    def get_statistics(self) -> dict:
        """Get database statistics (row counts, sizes, etc.)."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection."""

    @property
    def capabilities(self) -> EngineCapabilities:
        """This engine's capability declaration (design §5.1).

        Not abstract: an engine that adds no capability beyond the schema
        read needs no boilerplate override, and a caller that has not been
        taught about a given engine gets an honest "nothing declared" rather
        than an AttributeError.
        """
        return NO_CAPABILITIES

    @property
    def containment(self) -> EngineContainment:
        """This engine's containment levels above the table (REPLY-SCHEMA-AS-
        SUB-RESOURCE.md §5).

        Not abstract, for the same reason `capabilities` is not: an engine
        nobody has declared reports `NO_CONTAINMENT` — "the hierarchy is not
        declared" — rather than raising, or inheriting Postgres's.
        """
        return NO_CONTAINMENT


class PostgreSQLConnection(DatabaseConnection):
    """PostgreSQL-specific connection implementation."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._conn = None

    def connect(self) -> Any:
        """Establish PostgreSQL connection."""
        try:
            import psycopg2
        except ImportError as e:
            raise ImportError(
                "psycopg2 is required for PostgreSQL connections. "
                "Install it with: pip install psycopg2-binary"
            ) from e

        self._conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
        )
        return self._conn

    def execute_query(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return results as list of dicts."""
        if not self._conn:
            raise RuntimeError("Not connected to database")

        with self._conn.cursor() as cur:
            cur.execute(query, params)
            if cur.description:
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
            return []

    def get_schema_info(self) -> dict:
        """Get PostgreSQL schema information.

        `information_schema.schemata` is privilege-filtered by Postgres
        itself, to schemas the connected role owns or holds ANY grant on —
        a schema with zero privilege (not even `USAGE`) never appears here
        at all, and everything below is keyed off this list, so that
        schema's tables were never even attempted. This is the identical
        gap `get_credential_capability()`'s own docstring documents having
        hit and fixed for the credential-visibility PROBE (an 8-vs-6
        schema undercount, `coco_pharma`) by reading `pg_namespace`
        directly instead — a fix that was never carried back to this
        enumeration, so the probe and this inventory could (and did,
        `coco_pharma` 2026-09-26: 61 vs. 56 tables) disagree on the
        database's own totals. Design ruling (security-model.md
        §2.1/§3.4): on an engine whose structural floor is unprivileged
        (Postgres), every enumeration reads that floor, never a
        privilege-filtered view, so there is exactly one denominator.

        Fixed by adding a second pass below: any schema `_enumerate_relations`
        (the same unprivileged `pg_namespace`/`pg_class` floor
        `get_credential_capability` uses) sees that the privileged loop
        above missed is read the same way `_catalog_only_fallback` already
        reads a table `information_schema` couldn't see WITHIN an
        already-known schema — `pg_class`/`pg_attribute` are catalog
        metadata, not privilege-filtered, so table names, relation kinds,
        and column names/types ARE visible with zero grants; only row data
        and comments are not. Each such table is tagged `source:
        "catalog_fallback"`, the same marker `database_rows_from_survey_data`
        (`result_materializer.py`) already recognizes and stores as
        `STATE_CATALOG_ESTIMATE` — no downstream change needed for the
        table-level honesty; a zero-privilege schema now appears with real,
        named tables in "structure only" state rather than not appearing at
        all.
        """
        errors: dict[str, str] = {}
        schemas_query = """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY schema_name
        """
        try:
            schemas = self.execute_query(schemas_query)
        except Exception as exc:
            errors["schemas"] = str(exc)
            schemas = []
        schema_descriptions = self._get_schema_descriptions()

        result = {"schemas": [], "total_tables": 0, "total_columns": 0}
        seen_schema_names: set[str] = set()
        for schema in schemas:
            schema_name = schema["schema_name"]
            seen_schema_names.add(schema_name)
            tables = self._get_tables_for_schema(schema_name)
            result["schemas"].append({
                "name": schema_name,
                "description": schema_descriptions.get(schema_name, ""),
                "tables": tables,
            })
            result["total_tables"] += len(tables)
            result["total_columns"] += sum(len(t["columns"]) for t in tables)

        try:
            floor_schemas, _floor_tables = self._enumerate_relations()
        except Exception as exc:
            errors["enumeration_floor"] = str(exc)
            floor_schemas = []
        for s in floor_schemas:
            schema_name = s.get("schema_name")
            if not schema_name or schema_name in seen_schema_names:
                continue
            seen_schema_names.add(schema_name)
            try:
                catalog_tables = self._catalog_table_summary(schema_name)
            except Exception as exc:
                errors[f"schema:{schema_name}"] = str(exc)
                catalog_tables = {}
            tables = []
            for table_name, info in catalog_tables.items():
                tables.append({
                    "name": table_name,
                    "type": info.get("table_type") or "",
                    "description": "",
                    "columns": self._catalog_columns_for_table(schema_name, table_name),
                    "source": "catalog_fallback",
                    "row_count_estimate": info.get("reltuples"),
                    "row_count_basis": "estimated",
                })
            result["schemas"].append({
                "name": schema_name,
                "description": schema_descriptions.get(schema_name, ""),
                "tables": tables,
                # Distinguishes "found via the floor, zero USAGE grant" from
                # an ordinary schema above -- not yet rendered anywhere (no
                # per-schema view exists until slice 22), but present so
                # that view can tell the two apart without re-deriving it.
                "access": "no_usage",
            })
            result["total_tables"] += len(tables)
            result["total_columns"] += sum(len(t["columns"]) for t in tables)

        if errors:
            result["_errors"] = errors
        return result

    def _enumerate_relations(self) -> tuple[list[dict], list[dict]]:
        """The unprivileged floor: every non-system schema and relation in
        this database, read straight from `pg_namespace`/`pg_class` —
        readable by any connected role regardless of `USAGE`/`SELECT`
        grants (`get_credential_capability()`'s own docstring establishes
        this). The single enumeration both the credential-capability probe
        and `get_schema_info()`'s inventory read from, so the two can no
        longer independently drift on how many schemas or tables this
        database has (design ruling, security-model.md §2.1/§3.4,
        2026-09-26 — see `get_schema_info()`'s docstring for the incident
        that prompted it). Raises on failure; callers record the error on
        their own section rather than this shared helper silently
        defaulting, since what "no rows" should mean differs per caller.
        """
        schemas = self.execute_query("""
            SELECT n.nspname AS schema_name,
                   has_schema_privilege(current_user, n.nspname, 'USAGE') AS usage_granted
            FROM pg_namespace n
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY n.nspname
        """)
        tables = self.execute_query("""
            SELECT n.nspname AS schema_name, c.relname AS table_name, c.relkind,
                   has_table_privilege(current_user, c.oid, 'SELECT') AS can_select,
                   has_table_privilege(current_user, c.oid, 'INSERT') AS can_insert
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'v', 'm', 'f', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY n.nspname, c.relname
        """)
        return schemas, tables

    def _get_schema_descriptions(self) -> dict[str, str]:
        """Return {schema_name: description} from pg_namespace."""
        try:
            rows = self.execute_query("""
                SELECT n.nspname AS schema_name,
                       obj_description(n.oid, 'pg_namespace') AS description
                FROM pg_namespace n
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                ORDER BY n.nspname
            """)
            return {r["schema_name"]: r.get("description") or "" for r in rows}
        except Exception:
            return {}

    def _get_tables_for_schema(self, schema_name: str) -> list[dict]:
        """Get tables and columns for a schema, including PK/FK info and pg_description comments.

        `information_schema.tables`/`.columns` (and the PK/FK/comment
        lookups below, which also go through `information_schema`/
        `obj_description()`/`col_description()`) are privilege-filtered by
        Postgres: a role needs `SELECT` on a table before that table shows up
        here at all. Confirmed live against a real `coco_pharma` incident
        (design: ASK/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md): a
        credential with `USAGE` on `coco_ods` but no `SELECT` grant on any
        table inside it got **zero** tables back for that schema, even
        though the tables genuinely exist.

        `pg_class`/`pg_attribute`/`pg_namespace` are catalog metadata, not
        privilege-filtered — any connected role can read them regardless of
        grants (the same fact `get_credential_capability()` relies on). So
        after the normal enumeration below, `_catalog_only_fallback()` fills
        in any table the catalog knows about that `information_schema` did
        not return — table name, column names, Postgres type names, and
        `pg_class.reltuples` as an ANALYZE-time row estimate. This is a
        fallback, not a replacement: a schema where `information_schema`
        already sees every table is returned exactly as before, with the
        richer exact data (real PK/FK, `is_nullable`, `column_default`,
        exact comments) that only that path can supply.
        """
        # Primary/foreign keys: read from `pg_constraint`/`pg_index` via
        # `_catalog_keys_for_schema()`, not `information_schema.
        # table_constraints`/`key_column_usage`/`constraint_column_usage`.
        #
        # This used to be two separate `information_schema` queries here,
        # promoted to the fallback-only `_catalog_keys_for_schema()` in
        # Slice 21b and now promoted again to be the ONLY path (2026-09-27,
        # BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §A). `constraint_column_usage`
        # is not keyed per column: when a referenced table (e.g.
        # `person.businessentity`) carries several referencing FK
        # constraints, the join multiplies or collapses rows depending on
        # constraint shape, and the same defect shape hit the PK query.
        # Verified live against `laz_local_adventureworks` on 2026-09-27:
        # this path recovered 91/91 FK columns and 181/181 PK columns
        # versus 71/99 from the retired queries above.
        #
        # `pg_lookup`/`fk_lookup` may come back `None` (not `{}`) if their
        # own catalog query failed — see `_catalog_keys_for_schema()`'s
        # docstring for why that distinction matters and must be preserved
        # here rather than collapsed into a confident `False`/absent.
        pk_lookup, fk_lookup = self._catalog_keys_for_schema(schema_name)

        # Main query: tables + columns with descriptions
        query = """
            SELECT
                t.table_name,
                t.table_type,
                obj_description(
                    (quote_ident(t.table_schema)||'.'||quote_ident(t.table_name))::regclass,
                    'pg_class'
                ) AS table_description,
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.column_default,
                c.ordinal_position,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                col_description(
                    (quote_ident(t.table_schema)||'.'||quote_ident(t.table_name))::regclass,
                    c.ordinal_position
                ) AS column_description
            FROM information_schema.tables t
            LEFT JOIN information_schema.columns c
                ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE t.table_schema = %s
            ORDER BY t.table_name, c.ordinal_position
        """
        rows = self.execute_query(query, (schema_name,))

        tables: dict[str, dict] = {}
        for row in rows:
            table_name = row["table_name"]
            if table_name not in tables:
                tables[table_name] = {
                    "name": table_name,
                    "type": row["table_type"],
                    "description": row.get("table_description") or "",
                    "columns": [],
                    "source": "information_schema",
                }
            if row["column_name"]:
                col_name = row["column_name"]
                # `None` means the catalog PK/FK query itself failed for
                # this schema — propagate that as "not established" rather
                # than a confident `False`/absent (see
                # `_catalog_keys_for_schema()`'s docstring).
                is_pk = (
                    None if pk_lookup is None
                    else col_name in pk_lookup.get(table_name, set())
                )
                fk_entries = (
                    None if fk_lookup is None
                    else fk_lookup.get((table_name, col_name))
                )
                # A column normally carries at most one FK; the rare
                # legal case of two FKs on one column keeps the full list
                # under `foreign_keys` while `foreign_key` stays the first
                # entry for existing single-FK consumers.
                fk = fk_entries[0] if fk_entries else None

                # Build a human-friendly type display
                data_type = row["data_type"] or ""
                max_len = row.get("character_maximum_length")
                num_prec = row.get("numeric_precision")
                num_scale = row.get("numeric_scale")
                if max_len:
                    type_display = f"{data_type}({max_len})"
                elif num_prec and num_scale:
                    type_display = f"{data_type}({num_prec},{num_scale})"
                elif num_prec:
                    type_display = f"{data_type}({num_prec})"
                else:
                    type_display = data_type

                tables[table_name]["columns"].append({
                    "name": col_name,
                    "type": type_display,
                    "base_type": data_type,
                    "nullable": row["is_nullable"] == "YES",
                    "default": row.get("column_default"),
                    "position": row["ordinal_position"],
                    "description": row.get("column_description") or "",
                    "is_primary_key": is_pk,
                    "foreign_key": fk,
                    "foreign_keys": fk_entries if fk_entries and len(fk_entries) > 1 else None,
                    "source": "information_schema",
                })

        self._catalog_only_fallback(schema_name, tables)
        return list(tables.values())

    def _catalog_only_fallback(self, schema_name: str, tables: dict[str, dict]) -> None:
        """Fill in, in place, any table `information_schema` did not return
        for this schema but `pg_class` says exists.

        Only ever ADDS entries `tables` is missing — a schema where
        `information_schema` already saw every table is untouched, so a
        credential with full access keeps getting exactly today's richer
        data. See `_get_tables_for_schema()`'s docstring for why this is
        possible at all (pg_class/pg_attribute/pg_namespace are not
        privilege-filtered) and what it can and cannot supply.
        """
        try:
            catalog = self._catalog_table_summary(schema_name)
        except Exception:
            return
        pk_lookup, fk_lookup = self._catalog_keys_for_schema(schema_name)
        for table_name, info in catalog.items():
            if table_name in tables:
                continue
            columns = self._catalog_columns_for_table(
                schema_name, table_name, pk_lookup, fk_lookup)
            tables[table_name] = {
                "name": table_name,
                "type": info.get("table_type") or "",
                "description": "",
                "columns": columns,
                "source": "catalog_fallback",
                # pg_class.reltuples is an ANALYZE-time estimate, never a
                # live count — kept in its own field, and paired with an
                # explicit basis, rather than written straight into a plain
                # "row_count" that every other caller reads as exact.
                "row_count_estimate": info.get("reltuples"),
                "row_count_basis": "estimated",
            }

    def _catalog_table_summary(self, schema_name: str) -> dict[str, dict]:
        """{table_name: {table_type, reltuples}} from `pg_class`/`pg_namespace`
        for one schema — catalog metadata, readable by any connected role
        regardless of `USAGE`/`SELECT` grants (same fact
        `get_credential_capability()` relies on). `reltuples` is the row
        estimate from the last `ANALYZE`, not a live count.
        """
        query = """
            SELECT c.relname AS table_name, c.relkind, c.reltuples
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relkind IN ('r', 'p', 'v', 'm')
        """
        rows = self.execute_query(query, (schema_name,))
        kind_to_type = {
            "r": "BASE TABLE", "p": "BASE TABLE",
            "v": "VIEW", "m": "MATERIALIZED VIEW",
        }
        out: dict[str, dict] = {}
        for r in rows:
            name = r.get("table_name")
            if not name:
                continue
            reltuples = r.get("reltuples")
            out[name] = {
                "table_type": kind_to_type.get(r.get("relkind"), ""),
                "reltuples": (
                    int(reltuples) if reltuples is not None and reltuples >= 0 else None
                ),
            }
        return out

    def _catalog_keys_for_schema(self, schema_name: str) -> tuple[dict | None, dict | None]:
        """`(pk_lookup, fk_lookup)` — as of 2026-09-27
        (BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §A) this is the PRIMARY PK/FK
        source for `_get_tables_for_schema()`, not only the catalog-only
        fallback path. Shape: `{table_name: {column_name, ...}}` /
        `{(table_name, column_name): [{foreign_schema, foreign_table,
        foreign_column}, ...]}` — a list per column, not a single dict,
        because a column may legally carry more than one FK constraint.
        Read from `pg_constraint`/`pg_attribute` rather than
        `information_schema.table_constraints`/`key_column_usage`/
        `constraint_column_usage`: the latter is not keyed per column and
        multiplies or collapses rows when a referenced table carries
        several referencing constraints (verified live: 71/99 recovered
        vs. 91/181 true on `laz_local_adventureworks`, 2026-09-27) —
        besides being the exact privilege-filtered view this fallback
        originally existed to route around.

        `pg_constraint` is catalog metadata like `pg_class`/`pg_attribute`/
        `pg_namespace` (see `_get_tables_for_schema()`'s own docstring) —
        readable by any connected role regardless of `USAGE`/`SELECT`
        grants, so a fallback table's PK/FK no longer needs to default to
        `None`: unlike `is_nullable`/`column_default`/comments (which
        genuinely have no catalog-only source), key membership is exactly
        as available here as the table/column names already are. Composite
        keys are matched position-by-position (`WITH ORDINALITY`) so a
        multi-column FK pairs each local column with its correct referenced
        column rather than a cross product.

        Returns `None` (not `{}`) for either half when its own query
        failed — an empty dict here would be indistinguishable from "the
        query ran and genuinely found no keys," and a caller reading
        `pk_lookup.get(table, set())` against a silently-failed empty dict
        would report every column as confidently NOT a primary key: the
        exact confident-wrong-answer shape `_catalog_columns_for_table`'s
        own docstring already refuses for `is_nullable`/`default`.
        """
        pk_lookup: dict[str, set] | None = {}
        try:
            pk_query = """
                SELECT c.relname AS table_name, a.attname AS column_name
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN unnest(con.conkey) AS ck(attnum) ON true
                JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ck.attnum
                WHERE n.nspname = %s AND con.contype = 'p'
            """
            for r in self.execute_query(pk_query, (schema_name,)):
                pk_lookup.setdefault(r["table_name"], set()).add(r["column_name"])
        except Exception:
            pk_lookup = None

        fk_lookup: dict[tuple, list] | None = {}
        try:
            fk_query = """
                SELECT c.relname AS table_name, a.attname AS column_name,
                       fn.nspname AS foreign_schema, fc.relname AS foreign_table,
                       fa.attname AS foreign_column
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_class fc ON fc.oid = con.confrelid
                JOIN pg_namespace fn ON fn.oid = fc.relnamespace
                JOIN unnest(con.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
                JOIN unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = ck.ord
                JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ck.attnum
                JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = fk.attnum
                WHERE n.nspname = %s AND con.contype = 'f'
            """
            for r in self.execute_query(fk_query, (schema_name,)):
                # Appended, not assigned: a column that carries two FK
                # constraints (rare, legal) keeps both entries instead of
                # the second silently clobbering the first.
                fk_lookup.setdefault((r["table_name"], r["column_name"]), []).append({
                    "foreign_schema": r["foreign_schema"],
                    "foreign_table": r["foreign_table"],
                    "foreign_column": r["foreign_column"],
                })
        except Exception:
            fk_lookup = None

        return pk_lookup, fk_lookup

    def _catalog_columns_for_table(
        self, schema_name: str, table_name: str,
        pk_lookup: dict | None = None, fk_lookup: dict | None = None,
    ) -> list[dict]:
        """Column names and Postgres type names from `pg_attribute`, for the
        catalog-only fallback path.

        Deliberately does NOT attempt `is_nullable`, `column_default`, or a
        comment for these columns — this codebase's default/comment lookups
        go through `information_schema`/`col_description()`, which are
        exactly the privilege-filtered paths this fallback exists because
        of, and Postgres genuinely has no catalog-only source for them.
        Reporting `nullable` as a guessed `False` here would be a confident
        wrong answer of the same shape this whole change exists to avoid,
        so that field stays `None`/absent rather than defaulted.

        `is_primary_key`/`foreign_key` are the exception (fixed 2026-09-26,
        Slice 21b — previously always `None`/absent here too): `pk_lookup`/
        `fk_lookup`, from `_catalog_keys_for_schema()`'s catalog-only
        `pg_constraint` read, are exactly as available in this path as the
        column names themselves already are — see that function's own
        docstring for why. A caller that omits them, or whose
        `_catalog_keys_for_schema()` call itself failed (passed through here
        as `None`, not `{}` — see that function's docstring), gets the
        pre-fix `None`/absent behaviour rather than a confidently-wrong
        `False`: `pk_lookup is None` is checked explicitly below rather than
        folding a failed lookup into an empty one.
        """
        query = """
            SELECT a.attname AS column_name,
                   a.attnum  AS ordinal_position,
                   format_type(a.atttypid, a.atttypmod) AS data_type
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """
        try:
            rows = self.execute_query(query, (schema_name, table_name))
        except Exception:
            return []
        columns = []
        for r in rows:
            name = r.get("column_name")
            if not name:
                continue
            data_type = r.get("data_type") or ""
            is_pk = None if pk_lookup is None else (name in pk_lookup.get(table_name, set()))
            fk_entries = None if fk_lookup is None else fk_lookup.get((table_name, name))
            fk = fk_entries[0] if fk_entries else None
            columns.append({
                "name": name,
                "type": data_type,
                "base_type": data_type,
                "nullable": None,
                "default": None,
                "position": r.get("ordinal_position"),
                "description": "",
                "is_primary_key": is_pk,
                "foreign_keys": fk_entries if fk_entries and len(fk_entries) > 1 else None,
                "foreign_key": fk,
                "source": "catalog_fallback",
            })
        return columns

    def list_databases(self) -> list[dict]:
        """List all databases on this server that the current user can connect to."""
        query = """
            SELECT
                d.datname                                    AS name,
                pg_size_pretty(pg_database_size(d.datname)) AS size_pretty,
                pg_database_size(d.datname)                 AS size_bytes,
                d.datdba::regrole::text                     AS owner,
                shobj_description(d.oid, 'pg_database')     AS description,
                pg_encoding_to_char(d.encoding)             AS encoding
            FROM pg_database d
            WHERE d.datistemplate = false
            AND has_database_privilege(d.datname, 'CONNECT')
            ORDER BY d.datname
        """
        try:
            rows = self.execute_query(query)
            result = []
            for r in rows:
                result.append({
                    "name": r["name"],
                    "size_pretty": r.get("size_pretty") or "",
                    "size_bytes": int(r.get("size_bytes") or 0),
                    "owner": r.get("owner") or "",
                    "description": r.get("description") or "",
                    "encoding": r.get("encoding") or "",
                })
            return result
        except Exception as e:
            raise RuntimeError(f"Could not list databases: {e}") from e

    @property
    def capabilities(self) -> EngineCapabilities:
        """Postgres declares the capabilities this and the prior slice
        extract: `query_stats` (`pg_stat_statements`) stays False
        deliberately — see EngineCapabilities' docstring; nothing in this
        class reads it yet, so declaring True would be a promise this code
        does not keep.
        """
        return EngineCapabilities(
            column_stats=True,
            tuple_counters=True,
            index_stats=True,
            replication_status=True,
            resilience=True,
            external_dependencies=True,
            privileges=True,
            # Phase 1 slice 10: `TABLESAMPLE SYSTEM`/`BERNOULLI` with
            # `REPEATABLE (seed)`, which is what `sampling.py` builds and what
            # design §5.8 names specifically in preference to
            # `ORDER BY random() LIMIT n`.
            value_sampling=True,
            # `credential_capability` probe — pg_namespace/pg_class,
            # has_schema_privilege/has_table_privilege and pg_has_role all
            # exist on every Postgres this codebase supports.
            credential_introspection=True,
        )

    @property
    def containment(self) -> EngineContainment:
        """server → database → schema, per REPLY-SCHEMA-AS-SUB-RESOURCE.md §5's
        first table row. The one engine declared in this slice."""
        return POSTGRES_CONTAINMENT

    def get_column_stats(self) -> list[dict]:
        """Per-column `pg_stats` — populated only after `ANALYZE` has run.

        Design §5.1: null_frac, n_distinct, most_common_vals,
        most_common_freqs, histogram_bounds, avg_width, correlation — column
        profiling without sampling.

        A column with no matching row here has not been measured as "having
        no values" — it has never been analyzed. That distinction is made by
        the caller, which knows the full column catalog from
        `get_schema_info()` and can tell "in the catalog, absent from
        pg_stats" from "genuinely profiled". This method only reports what
        pg_stats has; it never fabricates a row for a column ANALYZE has not
        reached.

        Joins in `pg_class.reltuples` — the row-count estimate from the SAME
        `ANALYZE` run that produced `n_distinct` — because resolving
        `n_distinct`'s negative-ratio form needs the row count *as of that
        analyze*, not a live count read separately (design review round 2,
        2026-09-21: `pg_stat_user_tables.n_live_tup` drifts from
        `pg_class.reltuples` between analyzes, so multiplying a ratio
        computed at analyze time by a row count read at survey time is an
        internally inconsistent number, even once the sign is fixed).
        """
        query = """
            SELECT
                s.schemaname, s.tablename, s.attname,
                s.null_frac, s.n_distinct, s.avg_width, s.correlation,
                s.most_common_vals::text  AS most_common_vals,
                s.most_common_freqs::text AS most_common_freqs,
                s.histogram_bounds::text  AS histogram_bounds,
                c.reltuples
            FROM pg_stats s
            LEFT JOIN pg_namespace n ON n.nspname = s.schemaname
            LEFT JOIN pg_class c ON c.relname = s.tablename AND c.relnamespace = n.oid
            WHERE s.schemaname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY s.schemaname, s.tablename, s.attname
        """
        try:
            return self.execute_query(query)
        except Exception:
            return []

    def get_table_activity(self) -> list[dict]:
        """Per-table tuple counters, live/dead rows, scan counts and
        vacuum/analyze recency from `pg_stat_user_tables` (design §5.1) —
        the full set `database_table_activity` has columns for, not just the
        row-count/last-analyzed subset `_get_table_row_stats` already feeds
        into `schema_info` for display.

        A table absent from this result (present in the catalog but missing
        here) has not been reported as inactive — Postgres has not
        accumulated a statistics-collector row for it yet, which is rare but
        distinct from "zero activity" (a real row with all-zero counters).
        """
        query = """
            SELECT
                schemaname, relname AS tablename,
                n_tup_ins, n_tup_upd, n_tup_del, n_tup_hot_upd,
                n_live_tup, n_dead_tup,
                seq_scan, idx_scan,
                last_vacuum, last_autovacuum, last_analyze, last_autoanalyze,
                n_mod_since_analyze AS pending_changes
            FROM pg_stat_user_tables
            ORDER BY schemaname, relname
        """
        try:
            rows = self.execute_query(query)
        except Exception:
            return []
        result = []
        for r in rows:
            result.append({
                "schemaname": r.get("schemaname", ""),
                "tablename": r.get("tablename", ""),
                "rows_inserted": r.get("n_tup_ins"),
                "rows_updated": r.get("n_tup_upd"),
                "rows_deleted": r.get("n_tup_del"),
                "hot_updates": r.get("n_tup_hot_upd"),
                "live_tuples": r.get("n_live_tup"),
                "dead_tuples": r.get("n_dead_tup"),
                "seq_scan": r.get("seq_scan"),
                "idx_scan": r.get("idx_scan"),
                "last_vacuum": str(r["last_vacuum"]) if r.get("last_vacuum") else "",
                "last_autovacuum": str(r["last_autovacuum"]) if r.get("last_autovacuum") else "",
                "last_analyze": str(r["last_analyze"]) if r.get("last_analyze") else "",
                "last_autoanalyze": str(r["last_autoanalyze"]) if r.get("last_autoanalyze") else "",
                "pending_changes": r.get("pending_changes"),
            })
        return result

    def get_stats_reset(self) -> str:
        """When `pg_stat_database` last reset this database's counters.

        The evidence `database_table_activity.stats_reset` exists to carry
        (see `result_materializer.py`'s identical comment on the native
        path) — a change comparator (design §9.1, Phase 1 slice 14) needs
        this to tell a real rate from the negative delta a reset produces.
        """
        try:
            rows = self.execute_query(
                "SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()"
            )
            value = rows[0].get("stats_reset") if rows else None
            return str(value) if value else ""
        except Exception:
            return ""

    def get_index_stats(self) -> list[dict]:
        """Per-index usage from `pg_stat_user_indexes`, joined to `pg_index`
        for uniqueness/primary-key — design §5.1's "index usage and
        unused-index detection".

        `idx_scan == 0` is the unused-index signal, with the same staleness
        caveat as the tuple counters: it is a count since the last stats
        reset (`get_stats_reset()`), not since the index was created.
        """
        query = """
            SELECT
                s.schemaname, s.relname AS tablename, s.indexrelname,
                s.idx_scan, s.idx_tup_read, s.idx_tup_fetch,
                i.indisunique  AS is_unique,
                i.indisprimary AS is_primary,
                pg_relation_size(s.indexrelid) AS index_size_bytes
            FROM pg_stat_user_indexes s
            JOIN pg_index i ON i.indexrelid = s.indexrelid
            ORDER BY s.schemaname, s.relname, s.indexrelname
        """
        try:
            return self.execute_query(query)
        except Exception:
            return []

    # ── Phase 1 slice 8: postgres_operations (design §5.1, §5.4, §5.5, §5.7) ──
    # Six new reads backing `privilege_audit`, `db_activity_signals` (which
    # reuses get_table_activity()/get_stats_reset() above rather than
    # querying pg_stat_user_tables a second way), `db_resilience` and
    # `db_external_dependencies`. Each method never raises — a query
    # failure (missing catalog view, insufficient privilege) yields an
    # empty result, and it is the CALLER (gated on `capabilities` above)
    # that says whether "empty" here means "measured, none" or "could not
    # be measured" — same division of responsibility as get_column_stats()
    # and get_table_activity() already establish.

    def get_privilege_audit(self) -> dict:
        """Roles, table grants and default ACLs (design §5.1's catalog-source
        table; §5.4's `privilege_audit` row) — "who can read and write
        what", including the PUBLIC grants `postgres_operations` raises an
        RFA on.
        """
        # Each field below is an independent read: a failure on one (e.g.
        # insufficient privilege on pg_default_acl) must not take out the
        # other two, so each gets its own try/except with an explicit
        # fallback re-assignment in the except body (never a bare `pass`) —
        # that fallback keeps the failure's default visible in the code, in
        # the shape `tests/test_no_silent_success.py`'s ratchet expects of a
        # handler in a value-returning function.
        errors: dict[str, str] = {}
        roles: list[dict] = []
        try:
            # `execute_query` always calls `cursor.execute(query, params)`
            # with a params tuple, even the default empty `()` -- which
            # still switches psycopg2 into printf-style query substitution.
            # A literal `%` that isn't part of a `%s`/`%(name)s` placeholder
            # then reads as a malformed format spec and raises (here,
            # `IndexError: tuple index out of range`) -- caught by this
            # very `except`, silently turning "the query is broken" into
            # "there are no roles", live-confirmed on `coco_pharma`
            # (2026-09-26): every survey's `privilege_audit.roles` was
            # empty, though `pg_roles` genuinely has 13 real rows there.
            # `%%` is the literal-percent escape psycopg2's substitution
            # expects; `pg\_%` (one escaped underscore, one literal
            # trailing wildcard) becomes `pg\_%%`. Recorded on `_errors`
            # (collector-honesty rule, design ruling 2026-09-26) now too,
            # so a future regression of this exact class is caught by the
            # headline reader rather than only by luck or a live incident.
            roles = self.execute_query("""
                SELECT rolname, rolsuper, rolcreaterole, rolcreatedb,
                       rolcanlogin, rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname NOT LIKE 'pg\\_%%'
                ORDER BY rolname
            """)
        except Exception as exc:
            roles = []
            errors["roles"] = str(exc)

        table_grants: list[dict] = []
        try:
            # NOT information_schema.role_table_grants — found live while
            # verifying grant_change against a real PUBLIC grant on coco_ods
            # (2026-09-22): that view only shows grants where the CURRENT
            # connecting role is the grantor or grantee (or a member of one),
            # per its own Postgres documentation. Confirmed directly: as
            # `egeria_user` (RE's stored, non-superuser credential — the
            # ordinary case, not a special one), it returned egeria_user's
            # own SELECT and nothing else, hiding both the table owner's
            # grants AND a real `GRANT SELECT ... TO PUBLIC` on the same
            # table — exactly the design §9.1 done-test case
            # ("especially to PUBLIC") this survey exists to catch. pg_class
            # ACLs are catalog metadata, visible to any connected role
            # regardless of what that role itself was granted, so this reads
            # the real ACL as stored rather than only the slice of it that
            # happens to involve the connecting role.
            table_grants = self.execute_query("""
                SELECT
                    n.nspname AS table_schema,
                    c.relname AS table_name,
                    CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                         ELSE acl.grantee::regrole::text END AS grantee,
                    acl.privilege_type,
                    acl.is_grantable
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN LATERAL aclexplode(c.relacl) AS acl
                WHERE c.relkind IN ('r', 'v', 'm', 'f', 'p')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                  AND c.relacl IS NOT NULL
                ORDER BY table_schema, table_name, grantee, privilege_type
            """)
        except Exception as exc:
            table_grants = []
            errors["table_grants"] = str(exc)

        default_acl: list[dict] = []
        try:
            default_acl = self.execute_query("""
                SELECT
                    n.nspname                    AS schema_name,
                    a.defaclrole::regrole::text  AS role_name,
                    a.defaclobjtype              AS object_type,
                    a.defaclacl::text            AS acl
                FROM pg_default_acl a
                LEFT JOIN pg_namespace n ON n.oid = a.defaclnamespace
                ORDER BY schema_name NULLS FIRST, role_name
            """)
        except Exception as exc:
            default_acl = []
            errors["default_acl"] = str(exc)

        result = {"roles": roles, "table_grants": table_grants, "default_acl": default_acl}
        if errors:
            result["_errors"] = errors
        return result

    def get_credential_capability(self) -> dict:
        """What THIS credential can see and do, as distinct from what the
        database contains — the `credential_capability` probe (design: REPLY-
        DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §0/§3/§4).

        `pg_namespace` and `pg_class` are catalog metadata, readable by any
        connected role regardless of `USAGE`/`SELECT` grants — confirmed
        directly against the incident that prompted this (a real `coco_pharma`
        database, connected as `egeria_user`): `information_schema.schemata`
        showed 6 of the database's real 8 schemas, and `has_table_privilege`
        found `SELECT` on only 3 of `coco_ods`'s real tables despite `USAGE`
        on the schema itself. So the unfiltered pg_* counts here are the
        honest denominator ("of M"), not an under-count — the blind spot has a
        known size.

        Every read is catalog metadata or a privilege-CHECK function call.
        `has_table_privilege(..., 'INSERT')` is called for every visible
        table to answer "could this credential write", but no write is ever
        attempted — `write_capable` is a probe result, reported honestly as
        such, never an exercised capability.

        The schema/table enumeration itself now goes through
        `_enumerate_relations()`, shared with `get_schema_info()`'s own
        floor pass, rather than duplicating the same two queries — see that
        method's docstring for why the two needing to agree is the point.
        A caught exception here is recorded on `_errors` (collector-honesty
        rule, design ruling 2026-09-26) rather than only silently degrading
        to empty defaults, so a reader can tell "measured, and it's zero"
        from "the read itself failed."
        """
        errors: dict[str, str] = {}
        connected_as = ""
        try:
            rows = self.execute_query("SELECT current_user AS connected_as")
            connected_as = rows[0].get("connected_as") or "" if rows else ""
        except Exception as exc:
            connected_as = ""
            errors["connected_as"] = str(exc)

        try:
            schemas, tables = self._enumerate_relations()
        except Exception as exc:
            schemas, tables = [], []
            errors["enumeration"] = str(exc)

        stats_role = False
        try:
            # pg_monitor (PG 10+) is the standard predefined role for read
            # access to monitoring views/functions (pg_stat_*, pg_read_all_
            # stats implied). MEMBER (not USAGE) is the correct third
            # argument for a role-membership check.
            rows = self.execute_query(
                "SELECT pg_has_role(current_user, 'pg_monitor', 'MEMBER') AS has_role"
            )
            stats_role = bool(rows[0].get("has_role")) if rows else False
        except Exception as exc:
            stats_role = False
            errors["stats_role"] = str(exc)

        by_schema: dict[str, dict] = {}
        for s in schemas:
            by_schema[s["schema_name"]] = {
                "usage_granted": bool(s.get("usage_granted")),
                "table_total": 0,
                "table_select": 0,
            }
        for t in tables:
            sc = by_schema.setdefault(
                t["schema_name"],
                {"usage_granted": False, "table_total": 0, "table_select": 0},
            )
            sc["table_total"] += 1
            if t.get("can_select"):
                sc["table_select"] += 1

        result = {
            "connected_as": connected_as,
            "schema_total": len(schemas),
            "schema_visible": sum(1 for s in schemas if s.get("usage_granted")),
            "table_total": len(tables),
            "table_select": sum(1 for t in tables if t.get("can_select")),
            "by_schema": by_schema,
            "stats_role": stats_role,
            #: Always True: this method never skips the write probe, it only
            #: ever skips the write itself.
            "write_probed": True,
            "write_capable": any(t.get("can_insert") for t in tables),
        }
        if errors:
            result["_errors"] = errors
        return result

    def get_replication_status(self) -> dict:
        """Whether this connection is a standby, and — if it is a primary —
        which replicas are attached and how far behind (design §5.5).

        `is_in_recovery` is `None` only if `pg_is_in_recovery()` itself could
        not be queried (should not happen on a reachable Postgres server);
        that is a genuinely different, worse case than "queried and it said
        false", so the two are kept distinguishable rather than both
        defaulting to `False`. `replicas` gets the same collector-honesty
        floor (`_errors`, design ruling 2026-09-26) a bare `[]` cannot carry
        on its own -- a query failure and "genuinely no replicas attached"
        must not read identically to a reader.
        """
        errors: dict[str, str] = {}
        is_in_recovery: bool | None = None
        try:
            rows = self.execute_query("SELECT pg_is_in_recovery() AS in_recovery")
            is_in_recovery = bool(rows[0]["in_recovery"]) if rows else None
        except Exception as exc:
            is_in_recovery = None
            errors["is_in_recovery"] = str(exc)

        replicas: list[dict] = []
        try:
            rows = self.execute_query("""
                SELECT
                    application_name,
                    client_addr::text AS client_addr,
                    state,
                    sync_state,
                    EXTRACT(EPOCH FROM replay_lag) AS replay_lag_seconds
                FROM pg_stat_replication
                ORDER BY application_name
            """)
            for r in rows:
                replicas.append({
                    "application_name": r.get("application_name") or "",
                    "client_addr": r.get("client_addr") or "",
                    "state": r.get("state") or "",
                    "sync_state": r.get("sync_state") or "",
                    "replay_lag_seconds": (
                        float(r["replay_lag_seconds"])
                        if r.get("replay_lag_seconds") is not None else None
                    ),
                })
        except Exception as exc:
            replicas = []
            errors["replicas"] = str(exc)

        result = {"is_in_recovery": is_in_recovery, "replicas": replicas}
        if errors:
            result["_errors"] = errors
        return result

    def get_wal_archiving_status(self) -> dict:
        """Is WAL archiving on, and is it succeeding (design §5.5). A caught
        exception is recorded on `_errors` (collector-honesty rule, design
        ruling 2026-09-26) rather than only degrading to the empty
        defaults, which otherwise read identically to "archiving is off"."""
        errors: dict[str, str] = {}
        archive_mode = ""
        try:
            rows = self.execute_query("SHOW archive_mode")
            archive_mode = str(rows[0].get("archive_mode", "")) if rows else ""
        except Exception as exc:
            archive_mode = ""
            errors["archive_mode"] = str(exc)

        archived_count = None
        failed_count = None
        last_archived_time = ""
        last_failed_time = ""
        try:
            rows = self.execute_query("""
                SELECT archived_count, failed_count,
                       last_archived_time::text AS last_archived_time,
                       last_failed_time::text   AS last_failed_time
                FROM pg_stat_archiver
            """)
            if rows:
                r = rows[0]
                archived_count = r.get("archived_count")
                failed_count = r.get("failed_count")
                last_archived_time = r.get("last_archived_time") or ""
                last_failed_time = r.get("last_failed_time") or ""
        except Exception as exc:
            archived_count, failed_count = None, None
            last_archived_time, last_failed_time = "", ""
            errors["archiver_stats"] = str(exc)

        result = {
            "archive_mode": archive_mode,
            "archived_count": archived_count,
            "failed_count": failed_count,
            "last_archived_time": last_archived_time,
            "last_failed_time": last_failed_time,
        }
        if errors:
            result["_errors"] = errors
        return result

    def get_backup_tool_signals(self) -> dict:
        """Presence of a known backup-tool extension (design §5.5: "partly"
        observable). Detecting the extension does not prove a backup is
        configured or succeeding — only that the tooling is installed —
        which is exactly the "partial signal" design §5.5 says this is, not
        the last-backup-date/restore-test-date questions it explicitly
        marks as not machine-observable at all.
        """
        known_tool_markers = ("pgbackrest", "pg_backrest", "wal-g", "wal_g", "barman")
        detected: list[str] = []
        result: dict = {}
        try:
            rows = self.execute_query("SELECT extname FROM pg_extension ORDER BY extname")
            for r in rows:
                name = (r.get("extname") or "")
                if any(marker in name.lower() for marker in known_tool_markers):
                    detected.append(name)
        except Exception as exc:
            detected = []
            result["_errors"] = {"detected_extensions": str(exc)}
        result["detected_extensions"] = detected
        return result

    def get_clustering_info(self) -> dict:
        """Citus clustering catalogs, when the extension is present (design
        §5.5: "partly" observable). Patroni-via-REST and managed-service HA
        are deliberately out of scope for a connection-layer catalog read —
        see `DB-OPERATIONS-STEP-IMPLEMENTED.md`'s scoped-out section.
        """
        citus_detected = False
        citus_version = None
        result: dict = {}
        try:
            rows = self.execute_query(
                "SELECT extversion FROM pg_extension WHERE extname = 'citus'"
            )
            if rows:
                citus_detected = True
                citus_version = rows[0].get("extversion")
        except Exception as exc:
            citus_detected, citus_version = False, None
            result["_errors"] = {"citus_detected": str(exc)}
        result["citus_detected"] = citus_detected
        result["citus_version"] = citus_version
        return result

    def get_external_dependencies(self) -> dict:
        """What this database depends on outside itself (design §5.4):
        extensions, foreign data wrappers/servers/tables, and logical
        replication publications/subscriptions. Each caught exception is
        recorded on `_errors` (collector-honesty rule, design ruling
        2026-09-26) so a real query failure never reads identically to
        "this database genuinely depends on nothing".
        """
        errors: dict[str, str] = {}
        extensions: list[dict] = []
        foreign_servers: list[dict] = []
        foreign_tables: list[dict] = []
        publications: list[dict] = []
        subscriptions: list[dict] = []
        try:
            extensions = self.execute_query(
                "SELECT extname, extversion FROM pg_extension ORDER BY extname"
            )
        except Exception as exc:
            extensions = []
            errors["extensions"] = str(exc)
        try:
            foreign_servers = self.execute_query("""
                SELECT fs.srvname, fdw.fdwname
                FROM pg_foreign_server fs
                JOIN pg_foreign_data_wrapper fdw ON fdw.oid = fs.srvfdw
                ORDER BY fs.srvname
            """)
        except Exception as exc:
            foreign_servers = []
            errors["foreign_servers"] = str(exc)
        try:
            foreign_tables = self.execute_query("""
                SELECT n.nspname AS schema_name, c.relname AS table_name, fs.srvname
                FROM pg_foreign_table ft
                JOIN pg_class c ON c.oid = ft.ftrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_foreign_server fs ON fs.oid = ft.ftserver
                ORDER BY schema_name, table_name
            """)
        except Exception as exc:
            foreign_tables = []
            errors["foreign_tables"] = str(exc)
        try:
            publications = self.execute_query(
                "SELECT pubname FROM pg_publication ORDER BY pubname"
            )
        except Exception as exc:
            publications = []
            errors["publications"] = str(exc)
        try:
            # Only visible to a superuser/subscription-owning role on the
            # subscriber database — a permission error here is swallowed to
            # empty by the same try/except as every other read in this
            # method, which means "no subscriptions" and "not permitted to
            # see pg_subscription" are not distinguished per-item. That is a
            # known simplification (see DB-OPERATIONS-STEP-IMPLEMENTED.md);
            # the whole-method `external_dependencies` capability gate is
            # what distinguishes "this engine can't do this at all". The
            # `_errors` entry at least says a permission error happened,
            # even if it can't say for which reason relative to the other
            # two.
            subscriptions = self.execute_query(
                "SELECT subname FROM pg_subscription ORDER BY subname"
            )
        except Exception as exc:
            subscriptions = []
            errors["subscriptions"] = str(exc)
        result = {
            "extensions": extensions,
            "foreign_servers": foreign_servers,
            "foreign_tables": foreign_tables,
            "publications": publications,
            "subscriptions": subscriptions,
        }
        if errors:
            result["_errors"] = errors
        return result

    def get_statistics(self) -> dict:
        """Get database statistics."""
        stats = {
            "database_size": self._get_database_size(),
            "table_stats": self._get_table_statistics(),
            "row_stats": self._get_table_row_stats(),
            "column_stats": self.get_column_stats(),
            "table_activity": self.get_table_activity(),
            "index_stats": self.get_index_stats(),
            "stats_reset": self.get_stats_reset(),
        }
        return stats

    def _get_database_size(self) -> dict:
        """Get database size information."""
        query = """
            SELECT 
                pg_database_size(current_database()) as size_bytes,
                pg_size_pretty(pg_database_size(current_database())) as size_pretty
        """
        result = self.execute_query(query)
        return result[0] if result else {"size_bytes": 0, "size_pretty": "0 bytes"}

    def _get_table_statistics(self) -> list[dict]:
        """Get statistics for all tables."""
        query = """
            SELECT 
                schemaname,
                tablename,
                pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(tablename)) as total_bytes,
                pg_size_pretty(pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(tablename))) as total_size
            FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
              AND has_schema_privilege(schemaname, 'USAGE')
            ORDER BY total_bytes DESC
            LIMIT 100
        """
        return self.execute_query(query)

    def _get_table_row_stats(self) -> list[dict]:
        """Get row counts and last-activity timestamps from pg_stat_user_tables.

        `n_live_tup` is maintained incrementally by DML tracking, not only
        by `ANALYZE` — but that tracking is exactly what a fresh restore
        from a dump loses (a plain SQL dump/restore carries table DATA, not
        `pg_stat_user_tables`'s runtime counters), and a table nobody has
        ever `ANALYZE`d or written to since restore reads `n_live_tup == 0`
        indistinguishably from a table that is genuinely empty. Found live
        on `coco_pharma` (2026-09-26): every table in two entire schemas
        showed `n_live_tup = 0` with `last_analyze`/`last_autoanalyze` both
        `NULL` — reported downstream as "measured, 0 rows", a confident
        wrong answer for tables that plainly hold real data (their own
        names — `orders`, `customers`, `order_details` — are not those of
        empty tables) design §5.1a already has a name for this exact
        state (`REASON_NEVER_ANALYZED` in `database_surveyor.py`) for a
        sibling case (`n_distinct` resolution); this reader gets the same
        treatment: `row_count` is `None`, not `0`, when the stats
        collector shows zero activity AND no `ANALYZE` has ever run — a
        real, non-zero `n_live_tup` is trusted regardless of `ANALYZE`
        history, since DML tracking alone would have produced it.
        """
        query = """
            SELECT
                schemaname,
                relname                                              AS tablename,
                n_live_tup                                          AS row_count,
                GREATEST(last_analyze, last_autoanalyze)            AS last_analyzed,
                GREATEST(last_vacuum,  last_autovacuum)             AS last_vacuumed,
                n_mod_since_analyze                                  AS pending_changes
            FROM pg_stat_user_tables
            ORDER BY schemaname, relname
        """
        try:
            rows = self.execute_query(query)
            # Cast timestamps to ISO strings so they survive JSON serialisation
            result = []
            for r in rows:
                live_tup = r.get("row_count")
                last_analyzed = str(r["last_analyzed"]) if r.get("last_analyzed") else ""
                never_analyzed_zero = (not live_tup) and not last_analyzed
                result.append({
                    "schemaname": r.get("schemaname", ""),
                    "tablename":  r.get("tablename", ""),
                    "row_count":  None if never_analyzed_zero else int(live_tup or 0),
                    "last_analyzed": last_analyzed,
                    "last_vacuumed": str(r["last_vacuumed"]) if r.get("last_vacuumed") else "",
                    "pending_changes": int(r.get("pending_changes") or 0),
                })
            return result
        except Exception:
            return []

    def close(self) -> None:
        """Close the PostgreSQL connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


@contextmanager
def server_connection(host: str, port: int, user: str, password: str, db_type: str = "postgresql"):
    """Context manager for connecting to a database server (uses 'postgres' DB to list databases).

    Args:
        host: Server hostname
        port: Server port
        user: Database user
        password: Database password
        db_type: Database type (only 'postgresql' supported)

    Yields:
        PostgreSQLConnection connected to the 'postgres' system database
    """
    if db_type == "postgresql":
        conn = PostgreSQLConnection(
            host=host,
            port=port,
            database="postgres",  # system DB to list all databases
            user=user,
            password=password,
        )
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    try:
        conn.connect()
        yield conn
    finally:
        conn.close()


@contextmanager
def database_connection(db_entity: DatabaseEntity, credentials: dict):
    """Context manager for database connections.
    
    Args:
        db_entity: DatabaseEntity with connection details
        credentials: Dict with 'user' and 'password' keys
        
    Yields:
        DatabaseConnection instance
        
    Example:
        with database_connection(db_entity, {"user": "admin", "password": "secret"}) as conn:
            schema = conn.get_schema_info()
    """
    if db_entity.db_type == "postgresql":
        if not credentials.get("user") or not credentials.get("password"):
            raise ValueError(
                "Database credentials are required to connect ('user'/'password' both "
                "missing or empty) — there is no other fallback (env vars, config) inside "
                "this function; callers must resolve credentials themselves first "
                "(e.g. from DatabaseEntity.db_user/db_password)."
            )
        conn = PostgreSQLConnection(
            host=db_entity.host,
            port=db_entity.port,
            database=db_entity.database_name,
            user=credentials["user"],
            password=credentials["password"],
        )
    else:
        raise ValueError(f"Unsupported database type: {db_entity.db_type}")

    try:
        conn.connect()
        yield conn
    finally:
        conn.close()


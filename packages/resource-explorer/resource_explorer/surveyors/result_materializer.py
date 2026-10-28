"""Turn survey results into the structured DB/FS detail rows.

Two inputs, one output shape. That is the whole point of this module.

**Native Egeria surveys.** `survey-postgres-database` and `survey-folder` run
on Egeria's engine host and leave their findings as annotations on a
SurveyReport. `egeria_survey_reader.py` walks those back out; until this
module existed they stayed raw blobs, so a native survey's results could not
be queried, diffed or trended the way a local survey's could. Here they become
the same rows `postgres_schema_and_stats` writes, tagged `source='egeria'`
instead of `source='local'` (design doc §3 rule D: "RE keeps a local copy of
every result, whoever ran the survey"; Egeria stays the catalog of record,
RE's store is the operational history).

**RE's own `survey_data` blobs.** The same conversion from the other side, for
the local surveys that predate the structured tables. Used by
`scripts/backfill_structured_tables.py`.

Both paths write through `ProjectRegistry.write_detail_rows`, which is keyed
`(slug, surveyed_at, source)` — so a native and a local run of the same day
coexist rather than overwrite.

## Annotation types and metric keys are copied, not invented

Design §2.2: "Every finding RE publishes for a database or file must use the
annotation type and metric keys Egeria's own service would use for the same
finding." The constants below were read on 2026-09-20 from the Egeria Java
checkout rather than guessed or recalled:

- `SurveyDatabaseAnnotationType` / `SurveyFolderAnnotationType`
  (`frameworks/open-survey-framework/.../controls/`) — the `annotationType`
  strings, which are human-readable sentences, not enum names.
- `RelationalDatabaseMetric`, `RelationalSchemaMetric`, `RelationalTableMetric`,
  `RelationalColumnMetric`, `FileDirectoryMetric`, `FileMetric`
  (`.../measurements/`) — the `resourceProperties` keys.
- `PostgresDatabaseStatsExtractor.java` — which annotation carries which
  metric set, and the qualified-name construction this module has to reverse.

**Every `resourceProperties` value arrives as a string**, including the
numbers: the extractor writes `Long.toString(...)` / `Boolean.toString(...)`
into a `Map<String,String>`. Hence `_as_int` / `_as_bool` below.

## Absence

A metric key that is not present in an annotation becomes `None`, never 0.
The design doc (§5.1) and Egeria's own connector documentation both insist on
this: missing schemas in a native Postgres survey mean the survey userId
lacked permission, and an empty `pg_stats` means ANALYZE never ran. Neither is
"we looked, and there was nothing". Section-level coverage rows carry that
distinction where a missing *row* cannot — see `SECTION_*` and `STATE_*` in
`registry.py`.

One consequence worth stating: a native Postgres survey reports no
null-fraction and no histogram, so `database_column_profiles.null_fraction` is
NULL on every `source='egeria'` row. That is not a gap in this module — it is
the native service genuinely not measuring it, and it is why design §5.4 keeps
`pg_stats` as RE's own route to the same question.
"""
from __future__ import annotations

import logging

from resource_explorer.registry import (
    SECTION_COLUMNS,
    SECTION_COLUMN_PROFILES,
    SECTION_DATA_FILES,
    SECTION_ENTRIES,
    SECTION_GRANTS,
    SECTION_SCHEMAS,
    SECTION_SQL_OBJECTS,
    SECTION_TABLE_ACTIVITY,
    SECTION_TABLES,
    SOURCE_EGERIA,
    SOURCE_LOCAL,
    STATS_SOURCE_DATABASE,
    STATS_SOURCE_RESOURCE_EXPLORER,
    STATE_CATALOG_ESTIMATE,
    STATE_MEASURED,
    STATE_NOT_MEASURED,
)

log = logging.getLogger(__name__)


# ── Native annotation types (SurveyDatabaseAnnotationType / SurveyFolderAnnotationType)

ANN_DATABASE_MEASUREMENTS = "Capture Database Measurements"
ANN_SCHEMA_MEASUREMENTS = "Capture Database Schema Measurements"
ANN_TABLE_MEASUREMENTS = "Capture Database Table Measurements"
ANN_COLUMN_MEASUREMENTS = "Capture Database Column Measurements"
ANN_COLUMN_VALUES = "Capture Frequent Values for Column"
ANN_SCHEMA_LIST = "Capture List of Schemas"
ANN_TABLE_LIST = "Capture List of Tables"
ANN_COLUMN_LIST = "Capture List of Table Columns"
ANN_TABLE_SIZES = "Capture Database Table Sizes"
ANN_SCHEMA_TABLE_SIZES = "Capture Table Sizes for a Database Schema"

ANN_FILE_COUNTS = "Capture File Counts"
ANN_PROFILE_FILE_EXTENSIONS = "Profile File Extensions"
ANN_PROFILE_FILE_NAMES = "Profile File Names to External Log"
ANN_PROFILE_FILE_TYPES = "Profile File Types"
ANN_PROFILE_ASSET_TYPES = "Profile Asset Types"
ANN_PROFILE_DEP_IMPL_TYPES = "Profile Deployed Implementation Types"
ANN_MISSING_REF_DATA = "Missing File Reference Data"
ANN_INACCESSIBLE_FILES = "Inaccessible files"

# ── Metric keys (Relational*Metric, File*Metric) ───────────────────────────

M_DB_NAME = "Database Name"
M_DB_SCHEMA_COUNT = "Number of schemas"
M_DB_TABLE_COUNT = "Number of tables"
M_DB_COLUMN_COUNT = "Number of columns"
M_DB_SIZE = "Data size"
M_LAST_STATS_RESET = "Last statistics reset"

M_SCHEMA_QNAME = "Qualified Schema Name"
M_SCHEMA_NAME = "Schema Name"
M_SCHEMA_TOTAL_TABLE_SIZE = "Total size of tables"
M_VIEW_COUNT = "Number of views"
M_MAT_VIEW_COUNT = "Number of materialized views"

M_TABLE_QNAME = "Table Qualified Name"
M_TABLE_NAME = "Table Name"
M_TABLE_TYPE = "Table Type"
M_TABLE_OWNER = "Resource Owner"
M_TABLE_SIZE = "Table Size"
M_ROWS_INSERTED = "Number Of Rows Inserted"
M_ROWS_UPDATED = "Number Of Rows Updated"
M_ROWS_DELETED = "Number Of Rows Deleted"
M_IS_POPULATED = "Is Populated"
M_HAS_INDEXES = "Has Indexes"
M_HAS_RULES = "Has Rules"
M_HAS_TRIGGERS = "Has Triggers"
M_HAS_ROW_SECURITY = "Has Row Security"
M_QUERY_DEFINITION = "Query Definition"

M_COLUMN_QNAME = "Column qualified name"
M_COLUMN_NAME = "Column name"
M_COLUMN_SIZE = "Column size"
M_COLUMN_TYPE = "Column data type"
M_COLUMN_NOT_NULL = "Not Null"
M_AVERAGE_WIDTH = "Average Column Width"
M_DISTINCT_VALUES = "Number Of Distinct Values"
M_MOST_COMMON_VALUES = "Most Common Values"
M_MOST_COMMON_FREQS = "Most Common Values Frequency"

M_FILE_SIZE = "fileSize"
M_FILE_CAN_READ = "canRead"
M_FILE_CAN_WRITE = "canWrite"
M_FILE_CAN_EXECUTE = "canExecute"
M_FILE_IS_SYMLINK = "symLink"
M_FILE_IS_HIDDEN = "hidden"
M_FILE_CREATION_TIME = "creationTime"
M_FILE_LAST_MODIFIED = "lastModifiedTime"
M_FILE_LAST_ACCESSED = "lastAccessedTime"
M_FILE_RECORD_COUNT = "recordCount"
M_FILE_ASSET_TYPE = "assetTypeName"


# Egeria's survey keys `resourceProperties` by each metric's DISPLAY NAME
# (`PostgresDatabaseStatsExtractor` puts `Metric.getDisplayName()`; live-confirmed
# in PROBES-2026-09-21.md as "Number Of Rows Inserted"). The M_* keys above are
# those exact strings. `LEGACY_PROPERTY_NAMES` maps the camelCase propertyNames
# RE used to read, so older stored/fixture shapes still resolve.
LEGACY_PROPERTY_NAMES = {
    "databaseName": "Database Name",
    "schemaCount": "Number of schemas",
    "tableCount": "Number of tables",
    "columnCount": "Number of columns",
    "dataSize": "Data size",
    "lastStatisticsReset": "Last statistics reset",
    "qualifiedSchemaName": "Qualified Schema Name",
    "schemaName": "Schema Name",
    "totalTableSize": "Total size of tables",
    "viewCount": "Number of views",
    "materializedViewCount": "Number of materialized views",
    "tableQualifiedName": "Table Qualified Name",
    "tableName": "Table Name",
    "tableType": "Table Type",
    "tableOwner": "Resource Owner",
    "tableSize": "Table Size",
    "numberOfRowsInserted": "Number Of Rows Inserted",
    "numberOfRowsUpdated": "Number Of Rows Updated",
    "numberOfRowsDeleted": "Number Of Rows Deleted",
    "isPopulated": "Is Populated",
    "hasIndexes": "Has Indexes",
    "hasRules": "Has Rules",
    "hasTriggers": "Has Triggers",
    "hasRowSecurity": "Has Row Security",
    "queryDefinition": "Query Definition",
    "columnQualifiedName": "Column qualified name",
    "columnName": "Column name",
    "columnSize": "Column size",
    "columnDataType": "Column data type",
    "columnNotNull": "Not Null",
    "averageColumnWidth": "Average Column Width",
    "numberOfDistinctValues": "Number Of Distinct Values",
    "mostCommonValues": "Most Common Values",
    "mostCommonValuesFrequency": "Most Common Values Frequency",
}


def native_props(props) -> dict:
    """A native annotation's resourceProperties keyed by Egeria's display
    names. A legacy camelCase propertyName key is renamed; a display-name key
    already present wins."""
    out = dict(props or {})
    for old, new in LEGACY_PROPERTY_NAMES.items():
        if old in out and new not in out:
            out[new] = out[old]
    return out

#: The views a native survey reports as tables. `tableType` comes from
#: `information_schema.tables.table_type`, so these are its vocabulary.
_VIEW_TABLE_TYPES = {"VIEW", "MATERIALIZED VIEW"}


# ── value coercion ─────────────────────────────────────────────────────────


def _as_int(value) -> int | None:
    """Parse a native metric value to int, or None if it was not measured.

    None rather than 0, always. A native Postgres survey that omits
    `numberOfRowsDeleted` has not told us the table saw no deletes; it has told
    us nothing, and a 0 here would be read as the former.
    """
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _blob_flag(value) -> int | None:
    """A survey-blob boolean as 1/0; absent or None stays None (not read)."""
    return None if not isinstance(value, bool) else (1 if value else 0)


def _as_bool(value) -> int | None:
    """Parse Boolean.toString() output to 0/1, or None if absent."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in ("true", "t", "1", "yes"):
        return 1
    if text in ("false", "f", "0", "no"):
        return 0
    return None


def _text(value) -> str:
    return "" if value is None else str(value)


def _split_qualified(qualified: str, leaf: str, depth: int) -> list[str]:
    """Split a native qualified name into its parts, right to left.

    Native qualified names nest as `database.schema`, `database.schema.table`,
    `database.schema.table.column` (`PostgresDatabaseStatsExtractor.java:754,
    960, 1147`). `depth` is how many trailing parts to return.

    The leaf name is supplied separately by the same annotation, so it is
    stripped by exact suffix match rather than by splitting — a column named
    `first.name` would otherwise be torn in half. Remaining parts are split on
    `.`, which stays wrong for a *schema* or *database* whose name contains a
    dot; that is rare enough to accept and too rare to have a test fixture, so
    it is recorded here rather than silently assumed away.
    """
    if not qualified:
        return []
    remainder = qualified
    if leaf and remainder.endswith("." + leaf):
        remainder = remainder[: -(len(leaf) + 1)]
    elif leaf and remainder == leaf:
        remainder = ""
    parts = remainder.split(".") if remainder else []
    if leaf:
        parts = parts + [leaf]
    return parts[-depth:] if depth else parts


def _schema_of_table(props: dict) -> str:
    table_name = _text(props.get(M_TABLE_NAME))
    parts = _split_qualified(_text(props.get(M_TABLE_QNAME)), table_name, 2)
    return parts[0] if len(parts) == 2 else ""


def _schema_table_of_column(props: dict) -> tuple[str, str]:
    column_name = _text(props.get(M_COLUMN_NAME))
    parts = _split_qualified(_text(props.get(M_COLUMN_QNAME)), column_name, 3)
    if len(parts) == 3:
        return parts[0], parts[1]
    return "", ""


def _annotations_of_type(annotations: list[dict], annotation_type: str) -> list[dict]:
    return [a for a in annotations if a.get("annotation_type") == annotation_type]


# ── native database survey → rows ──────────────────────────────────────────


def database_rows_from_annotations(annotations: list[dict]) -> dict[str, list[dict]]:
    """Convert native `survey-postgres-database` annotations to detail rows.

    Returns a mapping of table name to rows. Pure — takes the list
    `egeria_survey_reader.get_annotations_by_report_guid` returns and touches
    no registry, so it is testable against a captured annotation shape with no
    database and no live Egeria.
    """
    schemas: list[dict] = []
    tables: list[dict] = []
    columns: list[dict] = []
    profiles: list[dict] = []
    activity: list[dict] = []
    sql_objects: list[dict] = []

    # The counters on each table measurement are cumulative since the last
    # statistics reset, and that reset timestamp is reported once, at database
    # level (`RelationalDatabaseMetric.LAST_STATISTICS_RESET`). It is carried
    # onto every activity row so a later comparator can tell a real change
    # rate from the negative difference a reset produces — see the column
    # comment on `database_table_activity.stats_reset`.
    stats_reset = None
    for ann in _annotations_of_type(annotations, ANN_DATABASE_MEASUREMENTS):
        value = (native_props(ann.get("resource_properties"))).get(M_LAST_STATS_RESET)
        if value:
            stats_reset = _text(value)
            break

    for ann in _annotations_of_type(annotations, ANN_SCHEMA_MEASUREMENTS):
        props = native_props(ann.get("resource_properties"))
        schema_name = _text(props.get(M_SCHEMA_NAME))
        if not schema_name:
            continue
        schemas.append({
            "schema_name": schema_name,
            "qualified_schema_name": _text(props.get(M_SCHEMA_QNAME)),
            "table_count": _as_int(props.get(M_DB_TABLE_COUNT)),
            "view_count": _as_int(props.get(M_VIEW_COUNT)),
            "mat_view_count": _as_int(props.get(M_MAT_VIEW_COUNT)),
            "column_count": _as_int(props.get(M_DB_COLUMN_COUNT)),
            "total_table_size_bytes": _as_int(props.get(M_SCHEMA_TOTAL_TABLE_SIZE)),
            "state": STATE_MEASURED,
        })

    for ann in _annotations_of_type(annotations, ANN_TABLE_MEASUREMENTS):
        props = native_props(ann.get("resource_properties"))
        table_name = _text(props.get(M_TABLE_NAME))
        if not table_name:
            continue
        schema_name = _schema_of_table(props)
        table_type = _text(props.get(M_TABLE_TYPE))
        tables.append({
            "schema_name": schema_name,
            "table_name": table_name,
            "qualified_table_name": _text(props.get(M_TABLE_QNAME)),
            "table_type": table_type,
            "table_owner": _text(props.get(M_TABLE_OWNER)),
            "column_count": _as_int(props.get(M_DB_COLUMN_COUNT)),
            # Native reports no row count: the table measurement carries
            # tuple *deltas* (inserted/updated/deleted), not a live count.
            # Left NULL deliberately rather than derived from the deltas,
            # which would be a fabricated number.
            "row_count": None,
            "size_bytes": _as_int(props.get(M_TABLE_SIZE)),
            "is_populated": _as_bool(props.get(M_IS_POPULATED)),
            "has_indexes": _as_bool(props.get(M_HAS_INDEXES)),
            "has_rules": _as_bool(props.get(M_HAS_RULES)),
            "has_triggers": _as_bool(props.get(M_HAS_TRIGGERS)),
            "has_row_security": _as_bool(props.get(M_HAS_ROW_SECURITY)),
            "query_definition": _text(props.get(M_QUERY_DEFINITION)),
            "state": STATE_MEASURED,
        })

        inserted = _as_int(props.get(M_ROWS_INSERTED))
        updated = _as_int(props.get(M_ROWS_UPDATED))
        deleted = _as_int(props.get(M_ROWS_DELETED))
        if inserted is not None or updated is not None or deleted is not None:
            activity.append({
                "schema_name": schema_name,
                "table_name": table_name,
                "rows_inserted": inserted,
                "rows_updated": updated,
                "rows_deleted": deleted,
                "stats_reset": stats_reset,
                "state": STATE_MEASURED,
            })

        # A view's defining SQL rides on the table measurement rather than
        # having an annotation of its own, so this is where sql_objects gets
        # its native rows. RE's own `sql_analysis` adds dependencies and
        # lineage the native survey does not compute (design §3 rule C).
        query_definition = _text(props.get(M_QUERY_DEFINITION))
        if table_type in _VIEW_TABLE_TYPES or query_definition:
            sql_objects.append({
                "schema_name": schema_name,
                "object_name": table_name,
                "object_type": (
                    "materialized_view"
                    if table_type == "MATERIALIZED VIEW"
                    else "view"
                ),
                "definition": query_definition,
                "depends_on_json": None,
                "column_lineage_json": None,
                "complexity": None,
                "state": STATE_MEASURED,
            })

    # Frequent-value annotations are per column and separate from the column
    # measurement, so collect them first and fold them into the profile row.
    frequent_values: dict[tuple[str, str, str], dict] = {}
    for ann in _annotations_of_type(annotations, ANN_COLUMN_VALUES):
        props = native_props(ann.get("resource_properties"))
        schema_name, table_name = _schema_table_of_column(props)
        column_name = _text(props.get(M_COLUMN_NAME))
        if not column_name:
            continue
        value_list = ann.get("value_list") or []
        value_count = ann.get("value_count") or {}
        if not value_list and value_count:
            value_list = list(value_count.keys())
        frequent_values[(schema_name, table_name, column_name)] = {
            "values": value_list,
            "counts": value_count,
            "range_from": ann.get("value_range_from", ""),
            "range_to": ann.get("value_range_to", ""),
        }

    for ann in _annotations_of_type(annotations, ANN_COLUMN_MEASUREMENTS):
        props = native_props(ann.get("resource_properties"))
        column_name = _text(props.get(M_COLUMN_NAME))
        if not column_name:
            continue
        schema_name, table_name = _schema_table_of_column(props)
        not_null = _as_bool(props.get(M_COLUMN_NOT_NULL))
        columns.append({
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            "qualified_column_name": _text(props.get(M_COLUMN_QNAME)),
            # Native gives no ordinal position. NULL, not 0 — 0 would sort
            # every native column to the front as if it were first.
            "ordinal_position": None,
            "data_type": _text(props.get(M_COLUMN_TYPE)),
            "base_type": _text(props.get(M_COLUMN_TYPE)),
            "column_size": _as_int(props.get(M_COLUMN_SIZE)),
            "is_nullable": None if not_null is None else (0 if not_null else 1),
            "description": "",
            # Native does not report keys; leave is_primary_key absent rather
            # than defaulting to 0, which would assert "not a key".
            "is_primary_key": None,
            "foreign_key_json": None,
            "state": STATE_MEASURED,
        })

        mcv = props.get(M_MOST_COMMON_VALUES)
        mcf = props.get(M_MOST_COMMON_FREQS)
        freq = frequent_values.get((schema_name, table_name, column_name), {})
        profiles.append({
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
            # The native Postgres survey does not measure a null fraction,
            # a histogram or a correlation. NULL here means the native
            # service does not compute it, not that the column has no nulls.
            "null_fraction": None,
            "distinct_count": _as_float(props.get(M_DISTINCT_VALUES)),
            "average_width": _as_int(props.get(M_AVERAGE_WIDTH)),
            "correlation": None,
            "most_common_values_json": (
                freq.get("values") if freq.get("values") else _split_values(mcv)
            ),
            "most_common_freqs_json": (
                freq.get("counts") if freq.get("counts") else _split_values(mcf)
            ),
            "histogram_bounds_json": None,
            "min_value": _text(freq.get("range_from")),
            "max_value": _text(freq.get("range_to")),
            # Whose numbers these are. Not "egeria": the native survey reads
            # them out of the database's own pg_stats (distinct counts,
            # average widths and frequent values are all ANALYZE output), so
            # Egeria transported them rather than computing them. That it
            # arrived via a native survey is already recorded in `source`.
            #
            # `stats_computed_at` is unknown rather than the survey time: the
            # native report carries no `last_analyze`, so how old these
            # numbers are cannot be established from it. NULL says exactly
            # that, and saying "computed at survey time" would be false.
            "stats_source": STATS_SOURCE_DATABASE,
            "stats_computed_at": None,
            "sample_strategy": "",
            "sample_rows": None,
            "sample_seed": None,
            "state": STATE_MEASURED,
        })

    return {
        "database_schemas": schemas,
        "database_tables": tables,
        "database_columns": columns,
        "database_column_profiles": profiles,
        "database_table_activity": activity,
        "database_sql_objects": sql_objects,
    }


def _split_values(value) -> list | None:
    """Native most-common-values arrive as one Postgres array-ish string.

    Returns None when absent, so "not reported" stays distinct from "reported
    as empty" — an empty list is a real answer and None is not.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text:
        return []
    return [part.strip().strip('"') for part in text.split(",")]


def materialize_database_report(
    registry,
    slug: str,
    surveyed_at: str,
    annotations: list[dict],
    source: str = SOURCE_EGERIA,
) -> dict[str, int]:
    """Write a native database survey report's annotations as detail rows.

    Returns row counts per table. Idempotent: `write_detail_rows` replaces
    this `(slug, surveyed_at, source)`'s rows, so reading the same report back
    twice does not double them.
    """
    rows = database_rows_from_annotations(annotations)
    sections = {
        "database_schemas": SECTION_SCHEMAS,
        "database_tables": SECTION_TABLES,
        "database_columns": SECTION_COLUMNS,
        "database_column_profiles": SECTION_COLUMN_PROFILES,
        "database_table_activity": SECTION_TABLE_ACTIVITY,
        "database_sql_objects": SECTION_SQL_OBJECTS,
    }
    written: dict[str, int] = {}
    for table, table_rows in rows.items():
        written[table] = registry.write_detail_rows(
            table,
            slug,
            surveyed_at,
            source=source,
            rows=table_rows,
            coverage_section=sections[table],
            coverage_detail=_native_absence_note(table, table_rows),
        )
    return written


def _native_absence_note(table: str, rows: list[dict]) -> str:
    """The sentence a consumer needs when a native section comes back empty.

    Egeria's own Postgres connector documentation says missing schemas,
    tables or columns indicate the survey userId lacks permission rather than
    that none exist. RE cannot tell the two apart from the report alone, so it
    records the ambiguity instead of resolving it in either direction — which
    is the honest answer and the one design §5.1 asks for.
    """
    if rows:
        return ""
    if table in ("database_schemas", "database_tables", "database_columns"):
        return (
            "Native survey reported none. Egeria's Postgres connector "
            "documents this as possibly meaning the survey user lacks "
            "permission rather than that none exist — not resolvable from "
            "the report alone."
        )
    if table == "database_column_profiles":
        return "Native survey reported no column measurements."
    return ""


# ── native folder survey → rows ────────────────────────────────────────────


def filesystem_rows_from_annotations(annotations: list[dict]) -> dict[str, list[dict]]:
    """Convert native `survey-folder` annotations to filesystem detail rows.

    The folder survey's shape differs from the database one in a way that
    matters: it reports *aggregates and profiles* (counts by extension, by
    file type, by asset type) rather than one annotation per file. Per-file
    detail only exists when `survey-folder-and-files` ran, which produces a
    `survey-data-file` report per file with `FileMetric` measurements.

    So a native folder survey usually yields zero `filesystem_entries` rows
    with a real measurement behind it. That is recorded as coverage state
    rather than as an empty inventory — see `materialize_filesystem_report`.
    """
    entries: list[dict] = []

    for ann in _annotations_of_type(annotations, ANN_INACCESSIBLE_FILES):
        # An inaccessible file is a measured fact about a path: it exists and
        # could not be read. Recorded as an entry whose state says so, rather
        # than omitted — omitting it is how an unreadable file becomes
        # indistinguishable from a file that is not there.
        for path in (ann.get("value_list") or []):
            entries.append({
                "entry_path": path,
                "entry_name": path.rsplit("/", 1)[-1],
                "entry_type": "file",
                "size_bytes": None,
                "is_readable": 0,
                "state": "not_permitted",
            })

    for ann in annotations:
        props = native_props(ann.get("resource_properties"))
        if not props or M_FILE_SIZE not in props:
            continue
        # A per-file measurement from `survey-data-file`, reached when the
        # caller passed the file reports' annotations in alongside the
        # folder's.
        path = _text(ann.get("display_name") or ann.get("qualified_name"))
        if not path:
            continue
        entries.append({
            "entry_path": path,
            "entry_name": path.rsplit("/", 1)[-1],
            "entry_type": "file",
            "size_bytes": _as_int(props.get(M_FILE_SIZE)),
            "asset_type": _text(props.get(M_FILE_ASSET_TYPE)),
            "is_hidden": _as_bool(props.get(M_FILE_IS_HIDDEN)),
            "is_symlink": _as_bool(props.get(M_FILE_IS_SYMLINK)),
            "is_executable": _as_bool(props.get(M_FILE_CAN_EXECUTE)),
            "is_writable": _as_bool(props.get(M_FILE_CAN_WRITE)),
            "is_readable": _as_bool(props.get(M_FILE_CAN_READ)),
            "created_at": _text(props.get(M_FILE_CREATION_TIME)),
            "modified_at": _text(props.get(M_FILE_LAST_MODIFIED)),
            "accessed_at": _text(props.get(M_FILE_LAST_ACCESSED)),
            "record_count": _as_int(props.get(M_FILE_RECORD_COUNT)),
            "state": STATE_MEASURED,
        })

    return {"filesystem_entries": entries, "filesystem_data_files": []}


def materialize_filesystem_report(
    registry,
    slug: str,
    surveyed_at: str,
    annotations: list[dict],
    source: str = SOURCE_EGERIA,
) -> dict[str, int]:
    """Write a native folder survey report's annotations as detail rows."""
    rows = filesystem_rows_from_annotations(annotations)
    has_folder_measure = bool(_annotations_of_type(annotations, ANN_FILE_COUNTS))

    written: dict[str, int] = {}
    written["filesystem_entries"] = registry.write_detail_rows(
        "filesystem_entries",
        slug,
        surveyed_at,
        source=source,
        rows=rows["filesystem_entries"],
        coverage_section=SECTION_ENTRIES,
        coverage_state=(
            None
            if rows["filesystem_entries"]
            # A folder survey that measured the directory but listed no files
            # did not find an empty directory — it ran without the
            # `-and-files` variant, so per-file rows were never in scope.
            else (STATE_NOT_MEASURED if has_folder_measure else None)
        ),
        coverage_detail=(
            ""
            if rows["filesystem_entries"]
            else (
                "Folder survey measured directory-level counts only; per-file "
                "rows need the survey-folder-and-files variant."
                if has_folder_measure
                else ""
            )
        ),
    )
    # The native folder survey never profiles data files (design §1.4: the
    # CSV variant does, per file, in its own report). Recorded as
    # not-measured so an empty table does not read as "no data files".
    written["filesystem_data_files"] = registry.write_detail_rows(
        "filesystem_data_files",
        slug,
        surveyed_at,
        source=source,
        rows=[],
        coverage_section=SECTION_DATA_FILES,
        coverage_state=STATE_NOT_MEASURED,
        coverage_detail=(
            "The native folder survey does not profile data files; "
            "survey-csv-file / RE's own data_file_profiling does."
        ),
    )
    return written


# ── RE's own survey_data blobs → rows (the back-fill path) ─────────────────


def database_rows_from_survey_data(survey_data: dict) -> dict[str, list[dict]]:
    """Convert a local `database_surveys.survey_data` blob to detail rows.

    The blob's shape is what `database_surveyor.py` writes: `schema_info`
    (schemas → tables → columns, enriched with row counts and sizes),
    `statistics`, and `views`.
    """
    schema_info = (survey_data or {}).get("schema_info") or {}
    statistics = (survey_data or {}).get("statistics") or {}
    operations = (survey_data or {}).get("operations") or {}

    schemas: list[dict] = []
    tables: list[dict] = []
    columns: list[dict] = []
    activity: list[dict] = []
    sql_objects: list[dict] = []

    for schema in schema_info.get("schemas") or []:
        schema_name = schema.get("name") or ""
        if not schema_name:
            continue
        schema_tables = schema.get("tables") or []
        # Brief D 7f.2: the schema's size is the sum of its tables' sizes, but
        # only when EVERY table in it was sized. One unread table makes any
        # sum a confident wrong total, so the whole schema stays NULL then
        # (the table rows beside it still carry what was read).
        # A plain view owns no storage, so it is neither summed nor counted
        # against the total.
        table_sizes = [
            _blob_int(t.get("size_bytes")) for t in schema_tables
            if str(t.get("type") or "").upper().replace("_", " ") != "VIEW"
        ]
        schema_size = (
            sum(table_sizes)
            if table_sizes and all(s is not None for s in table_sizes) else None
        )
        schemas.append({
            "schema_name": schema_name,
            "description": schema.get("description") or "",
            "table_count": len(schema_tables),
            "column_count": sum(len(t.get("columns") or []) for t in schema_tables),
            # The local surveyor does not separate views from tables at the
            # schema level. NULL, not 0.
            "view_count": None,
            "mat_view_count": None,
            "total_table_size_bytes": schema_size,
            "state": STATE_MEASURED,
        })

        for table in schema_tables:
            table_name = table.get("name") or ""
            if not table_name:
                continue
            table_columns = table.get("columns") or []
            # A table `_catalog_only_fallback()` (connection.py) supplied
            # because `information_schema` came back thin for its schema —
            # see STATE_CATALOG_ESTIMATE's docstring (registry.py). Its row
            # count, when it has one at all, is `pg_class.reltuples` (an
            # ANALYZE-time estimate carried in `row_count_estimate`, never
            # the plain `row_count` key a fully-measured table would carry),
            # unless a later enrichment pass (database_surveyor.py's
            # `_store_results`, reading `pg_stat_user_tables`) found a real
            # one for it after all — that pass writes straight into
            # `row_count` when it does, which is why `row_count` is still
            # checked first below.
            is_catalog_fallback = table.get("source") == "catalog_fallback"
            row_count = table.get("row_count")
            if row_count is None and is_catalog_fallback:
                row_count = table.get("row_count_estimate")
            tables.append({
                "schema_name": schema_name,
                "table_name": table_name,
                "table_type": table.get("type") or "",
                "description": table.get("description") or "",
                "column_count": len(table_columns),
                "row_count": _blob_int(row_count),
                "size_bytes": _blob_int(table.get("size_bytes")),
                "table_bytes": _blob_int(table.get("table_bytes")),
                "index_bytes": _blob_int(table.get("index_bytes")),
                #: Egeria's Has Triggers / Has Rules / Has Row Security and the
                #: materialized-view Is Populated / Has Indexes: NULL = not read.
                "is_populated": _blob_flag(table.get("is_populated")),
                "has_indexes": _blob_flag(table.get("has_indexes")),
                "has_rules": _blob_flag(table.get("has_rules")),
                "has_triggers": _blob_flag(table.get("has_triggers")),
                "has_row_security": _blob_flag(table.get("has_row_security")),
                "state": STATE_CATALOG_ESTIMATE if is_catalog_fallback else STATE_MEASURED,
            })

            last_analyzed = table.get("last_analyzed") or ""
            last_vacuumed = table.get("last_vacuumed") or ""
            pending = _blob_int(table.get("pending_changes"))
            if last_analyzed or last_vacuumed or pending is not None:
                activity.append({
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "last_analyze": last_analyzed,
                    "last_vacuum": last_vacuumed,
                    "pending_changes": pending,
                    # The old blob carried no tuple counters at all; the
                    # extension that reads pg_stat_user_tables is design
                    # §5.7's job, not this back-fill's. NULL throughout.
                    "state": STATE_MEASURED,
                })

            for column in table_columns:
                column_name = column.get("name") or ""
                if not column_name:
                    continue
                foreign_key = column.get("foreign_key")
                # connection.py's catalog-only fallback (`_catalog_columns_
                # for_table`) deliberately leaves `nullable`/`is_primary_key`
                # as None rather than guessing — those lookups need
                # information_schema/pg_description, which is exactly what
                # was unavailable. Preserve that as NULL in the stored row
                # (not a coerced False/0), so a reader cannot mistake "not
                # established" for "confirmed not nullable / not a PK".
                nullable = column.get("nullable")
                is_pk = column.get("is_primary_key")
                columns.append({
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "column_name": column_name,
                    "ordinal_position": _blob_int(column.get("position")),
                    "data_type": column.get("type") or "",
                    "base_type": column.get("base_type") or "",
                    "is_nullable": None if nullable is None else (1 if nullable else 0),
                    "column_default": _text(column.get("default")),
                    "description": column.get("description") or "",
                    "is_primary_key": None if is_pk is None else (1 if is_pk else 0),
                    "foreign_key_json": foreign_key if foreign_key else None,
                    "state": (
                        STATE_CATALOG_ESTIMATE
                        if column.get("source") == "catalog_fallback"
                        else STATE_MEASURED
                    ),
                })

    for view in (survey_data or {}).get("views") or []:
        name = view.get("name") or view.get("view_name") or ""
        if not name:
            continue
        sql_objects.append({
            "schema_name": view.get("schema") or view.get("schema_name") or "",
            "object_name": name,
            #: blobs written before materialized views were read have no kind
            "object_type": "materialized_view" if view.get("kind") == "materialized_view" else "view",
            "definition": view.get("definition") or view.get("view_definition") or "",
            "depends_on_json": view.get("depends_on") or view.get("dependencies") or None,
            "column_lineage_json": view.get("column_lineage") or None,
            "complexity": _blob_int(view.get("complexity")),
            "state": STATE_MEASURED,
        })

    # `statistics` in the old blob is a bag whose shape varies by surveyor
    # version. Rather than guess at keys that may not be there, the back-fill
    # takes only what the blob demonstrably carries and marks the rest
    # not-measured through coverage — see backfill_database_survey.
    _ = statistics

    result = {
        "database_schemas": schemas,
        "database_tables": tables,
        "database_columns": columns,
        "database_table_activity": activity,
        "database_sql_objects": sql_objects,
    }

    # `database_grants` — found missing while building the grant_change
    # comparator (Phase 1 slice 14 follow-up, 2026-09-22): `record_database_
    # survey`'s caller already stores `results["operations"]` in the blob
    # (database_surveyor.py, postgres_operations/privilege_audit), but this
    # function never read it back out, so `_DATABASE_BLOB_UNMEASURED` marked
    # every local survey's grants as never-measured even when privilege_audit
    # genuinely ran and had grants to report — a database_grants row was
    # written by NO code path at all, native or local, confirmed by a live
    # query against the shared registry rather than assumed. Only added to
    # the result dict when this survey actually ran privilege_audit
    # (`operations["privilege_audit"]` present) — a survey that only ran
    # schema_inventory must still fall through to the NOT_MEASURED marker
    # below, not report an empty grants list as "measured, none".
    privilege_audit = operations.get("privilege_audit")
    if privilege_audit is not None:
        grants: list[dict] = []
        for g in privilege_audit.get("table_grants") or []:
            object_name = g.get("table_name") or ""
            if not object_name:
                continue
            grants.append({
                "schema_name": g.get("table_schema") or "",
                "object_name": object_name,
                "object_type": "table",
                "grantee": g.get("grantee") or "",
                # information_schema.role_table_grants carries a `grantor`
                # column, but connection.py's get_privilege_audit() query
                # does not select it — left blank rather than guessed.
                "grantor": "",
                "privilege_type": g.get("privilege_type") or "",
                # connection.py's get_privilege_audit() query (pg_class ACLs
                # via aclexplode) returns a real boolean; a stale/cached blob
                # from before that fix, or a native survey's own shape,
                # could still carry the information_schema-style 'YES'/'NO'
                # text — accept either rather than assuming one.
                "is_grantable": 1 if g.get("is_grantable") in (True, "YES", "yes") else 0,
                "state": STATE_MEASURED,
            })
        result["database_grants"] = grants

    return result


def filesystem_rows_from_survey_data(survey_data: dict) -> dict[str, list[dict]]:
    """Convert a local `filesystem_surveys.survey_data` blob to detail rows."""
    inventory = (survey_data or {}).get("inventory") or []
    entries: list[dict] = []
    data_files: list[dict] = []

    for item in inventory:
        path = item.get("file_path") or ""
        if not path:
            continue
        name = item.get("file_name") or path.rsplit("/", 1)[-1]
        entries.append({
            "entry_path": path,
            "entry_name": name,
            "entry_type": "file",
            "size_bytes": _blob_int(item.get("file_size_bytes")),
            "file_extension": name.rsplit(".", 1)[-1].lower() if "." in name else "",
            "file_type": item.get("format") or "",
            "is_hidden": 1 if item.get("is_hidden") else 0,
            "is_symlink": 1 if item.get("is_symlink") else 0,
            "is_executable": 1 if item.get("is_executable") else 0,
            "is_writable": 1 if item.get("is_writable") else 0,
            # The local walk records a file it could stat, so it was
            # readable; files it could not are in `inaccessible_files`.
            "is_readable": 1,
            "modified_at": item.get("modified_at") or "",
            "state": STATE_MEASURED,
        })
        if item.get("is_data_file"):
            # The walk classifies a data file; profiling is a separate,
            # more expensive step. Some blobs carry its results merged into
            # the inventory item and some do not, so take them when present
            # rather than assuming either way.
            row_count = _blob_int(item.get("row_count"))
            column_count = _blob_int(item.get("column_count"))
            columns = item.get("columns")
            profiled = (
                row_count is not None or column_count is not None or bool(columns)
            )
            data_files.append({
                "file_path": path,
                "format": item.get("format") or "",
                "file_size_bytes": _blob_int(item.get("file_size_bytes")),
                "row_count": row_count,
                "column_count": column_count,
                "schema_json": columns or None,
                # Only claim a stats source when something was actually
                # profiled. An unprofiled data file has no statistics, and
                # naming a source for numbers that do not exist would make
                # the card assert a provenance it cannot support.
                "stats_source": STATS_SOURCE_RESOURCE_EXPLORER if profiled else "",
                "stats_computed_at": (
                    (survey_data or {}).get("surveyed_at") or None
                    if profiled
                    else None
                ),
                "state": STATE_MEASURED,
            })

    for item in (survey_data or {}).get("inaccessible_files") or []:
        path = item.get("path") or ""
        if not path:
            continue
        entries.append({
            "entry_path": path,
            "entry_name": path.rsplit("/", 1)[-1],
            "entry_type": "file",
            "size_bytes": None,
            "is_readable": 0,
            "state": "not_permitted",
        })

    return {"filesystem_entries": entries, "filesystem_data_files": data_files}


def _blob_int(value) -> int | None:
    """Parse a blob value to int, preserving the absent/zero distinction."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── back-fill drivers ──────────────────────────────────────────────────────
#
# These write one historical survey's rows. `scripts/backfill_structured_
# tables.py` iterates every stored survey through them. Kept here rather than
# in the script so the conversion is importable and testable without running
# a CLI, and so the native and blob paths stay side by side — they must agree
# on row shape or the whole point of the exercise is lost.

#: Sections a local database survey blob can never answer, whatever it holds.
#: Recorded per run so an empty `database_grants` after a back-fill reads as
#: "this survey never looked" and not as "there are no grants" — the exact
#: collapse the structured tables were introduced to make visible.
_DATABASE_BLOB_UNMEASURED = {
    "database_column_profiles": (
        SECTION_COLUMN_PROFILES,
        "Local surveys predating the structured tables did not profile "
        "columns; pg_stats reading is design §5.7's postgres_schema_and_stats "
        "extension.",
    ),
    "database_grants": (
        "grants",
        "This survey did not run privilege_audit, so grants were not read — "
        "it runs as its own analysis (postgres_operations). A survey that "
        "DID run it gets real database_grants rows instead, via the "
        "`operations.privilege_audit` branch in "
        "database_rows_from_survey_data() above (added 2026-09-22 for the "
        "grant_change comparator — see that function's own comment for the "
        "gap this closed).",
    ),
    "database_settings": (
        "settings",
        "Local surveys predating the structured tables did not read "
        "pg_settings.",
    ),
}


def _section_already_measured(registry, table: str, slug: str, surveyed_at: str,
                              source: str, section: str) -> bool:
    """Did an earlier write under this exact key already record `section` as a
    real attempt (measured or empty), or leave rows in `table`?"""
    try:
        if registry.query_detail_rows(table, slug, surveyed_at, source):
            return True
        cov = registry.get_section_coverage("database", slug, surveyed_at, source)
    except Exception:
        return False
    entry = cov.get(section)
    return bool(entry) and entry.get("state") != STATE_NOT_MEASURED


def backfill_database_survey(
    registry,
    slug: str,
    surveyed_at: str,
    survey_data: dict,
    source: str = SOURCE_LOCAL,
    merge: bool = False,
) -> dict[str, int]:
    """Materialise one stored database survey blob into the detail tables.

    `merge=True` (Brief D 7f.1): this key already has a snapshot, because the
    steps of one scan share a `surveyed_at`. `write_detail_rows` REPLACES per
    key, so a later step that collected nothing for a section (activity,
    profiles, grants, settings) must not delete what an earlier step wrote nor
    overwrite its coverage with a "not measured" marker. The sections the blob
    carries in full (schemas, tables, columns, views) are rewritten as before.
    """
    rows = database_rows_from_survey_data(survey_data)
    sections = {
        "database_schemas": SECTION_SCHEMAS,
        "database_tables": SECTION_TABLES,
        "database_columns": SECTION_COLUMNS,
        "database_table_activity": SECTION_TABLE_ACTIVITY,
        "database_sql_objects": SECTION_SQL_OBJECTS,
        # Present in `rows` only when this survey's blob actually ran
        # privilege_audit (see database_rows_from_survey_data) — otherwise
        # "database_grants" is absent from `rows` entirely and falls through
        # to the NOT_MEASURED loop below, same as before this was added.
        "database_grants": SECTION_GRANTS,
    }
    written: dict[str, int] = {}
    for table, table_rows in rows.items():
        if merge and not table_rows and _section_already_measured(
                registry, table, slug, surveyed_at, source, sections[table]):
            continue
        written[table] = registry.write_detail_rows(
            table,
            slug,
            surveyed_at,
            source=source,
            rows=table_rows,
            coverage_section=sections[table],
        )
    for table, (section, detail) in _DATABASE_BLOB_UNMEASURED.items():
        if merge and table not in rows and _section_already_measured(
                registry, table, slug, surveyed_at, source, section):
            continue
        if table in rows:
            # Actually measured this run (currently only possible for
            # database_grants, when privilege_audit ran) — do not overwrite
            # the real rows just written above with a NOT_MEASURED marker.
            continue
        written[table] = registry.write_detail_rows(
            table,
            slug,
            surveyed_at,
            source=source,
            rows=[],
            coverage_section=section,
            coverage_state=STATE_NOT_MEASURED,
            coverage_detail=detail,
        )
    return written


def backfill_filesystem_survey(
    registry,
    slug: str,
    surveyed_at: str,
    survey_data: dict,
    source: str = SOURCE_LOCAL,
) -> dict[str, int]:
    """Materialise one stored filesystem survey blob into the detail tables."""
    rows = filesystem_rows_from_survey_data(survey_data)
    sections = {
        "filesystem_entries": SECTION_ENTRIES,
        "filesystem_data_files": SECTION_DATA_FILES,
    }
    written: dict[str, int] = {}
    for table, table_rows in rows.items():
        written[table] = registry.write_detail_rows(
            table,
            slug,
            surveyed_at,
            source=source,
            rows=table_rows,
            coverage_section=sections[table],
        )
    return written

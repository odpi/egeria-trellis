"""Tests for the structured DB/FS detail tables, the Egeria read-back
materialiser and the survey_data back-fill.

Design: `docs/multi-resource-questions-design.md` §5.7 (database tables), §6
(filesystem tables), §3 rule D (keyed by (slug, surveyed_at, source), local
copy of every result whoever ran the survey).

The native-annotation fixtures below are built to the shape read out of the
Egeria Java source on 2026-09-20 — `SurveyDatabaseAnnotationType` for the
annotationType strings and `PostgresDatabaseStatsExtractor.java` for which
`Relational*Metric` keys land on which annotation, including the detail that
every `resourceProperties` value is a *string* because the extractor writes
`Long.toString(...)` into a `Map<String,String>`. They have NOT been checked
against a live run (that is probe 9), so they pin the shape this code was
written against rather than proving the live shape matches.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import (
    DatabaseEntity,
    FileSystemEntity,
    ProjectRegistry,
    SECTION_GRANTS,
    SECTION_TABLES,
    SOURCE_EGERIA,
    SOURCE_LOCAL,
    STATS_SOURCE_DATABASE,
    STATS_SOURCE_RESOURCE_EXPLORER,
    STATE_EMPTY,
    STATE_MEASURED,
    STATE_NOT_MEASURED,
    STATE_NOT_PERMITTED,
    _DETAIL_TABLE_SPECS,
)
from resource_explorer.surveyors.result_materializer import (
    ANN_COLUMN_MEASUREMENTS,
    ANN_COLUMN_VALUES,
    ANN_DATABASE_MEASUREMENTS,
    ANN_FILE_COUNTS,
    ANN_SCHEMA_MEASUREMENTS,
    ANN_TABLE_MEASUREMENTS,
    backfill_database_survey,
    backfill_filesystem_survey,
    database_rows_from_annotations,
    database_rows_from_survey_data,
    filesystem_rows_from_survey_data,
    materialize_database_report,
    materialize_filesystem_report,
)

SURVEYED_AT = "2026-09-20T10:00:00"
EARLIER = "2026-09-19T10:00:00"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="coco_ods", display_name="Coco ODS", db_type="postgresql",
        host="localhost", port=5442, database_name="coco_ods",
    ))
    r.register_filesystem(FileSystemEntity(
        slug="drop_zone", display_name="Drop Zone",
        local_mount_point="/tmp/drop",
    ))
    return r


# ── fixtures shaped like real payloads ─────────────────────────────────────


def native_database_annotations() -> list[dict]:
    """A native `survey-postgres-database` report, as egeria_survey_reader
    returns it. Values are strings throughout, as the Java extractor writes
    them."""
    return [
        {
            "annotation_type": ANN_DATABASE_MEASUREMENTS,
            "summary": "Database measurements",
            "resource_properties": {
                "databaseName": "coco_ods", "schemaCount": "1",
                "tableCount": "2", "columnCount": "3", "dataSize": "163840",
                "lastStatisticsReset": "2026-09-01T00:00:00",
            },
        },
        {
            "annotation_type": ANN_SCHEMA_MEASUREMENTS,
            "resource_properties": {
                "qualifiedSchemaName": "coco_ods.public", "schemaName": "public",
                "totalTableSize": "163840", "tableCount": "2",
                "viewCount": "1", "materializedViewCount": "0",
                "columnCount": "3",
            },
        },
        {
            "annotation_type": ANN_TABLE_MEASUREMENTS,
            "resource_properties": {
                "tableQualifiedName": "coco_ods.public.patient",
                "tableName": "patient", "tableType": "BASE TABLE",
                "tableOwner": "coco", "tableSize": "81920", "columnCount": "2",
                "numberOfRowsInserted": "1200", "numberOfRowsUpdated": "30",
                "numberOfRowsDeleted": "0", "isPopulated": "true",
                "hasIndexes": "true", "hasRules": "false",
                "hasTriggers": "false", "hasRowSecurity": "false",
            },
        },
        {
            "annotation_type": ANN_TABLE_MEASUREMENTS,
            "resource_properties": {
                "tableQualifiedName": "coco_ods.public.patient_summary",
                "tableName": "patient_summary", "tableType": "VIEW",
                "tableOwner": "coco", "columnCount": "1",
                "queryDefinition": "SELECT id FROM patient",
            },
        },
        {
            "annotation_type": ANN_COLUMN_MEASUREMENTS,
            "resource_properties": {
                "columnQualifiedName": "coco_ods.public.patient.id",
                "columnName": "id", "columnDataType": "integer",
                "columnSize": "4", "columnNotNull": "true",
                "averageColumnWidth": "4", "numberOfDistinctValues": "1200",
            },
        },
        {
            "annotation_type": ANN_COLUMN_MEASUREMENTS,
            "resource_properties": {
                "columnQualifiedName": "coco_ods.public.patient.status",
                "columnName": "status", "columnDataType": "text",
                "columnNotNull": "false", "averageColumnWidth": "6",
                "numberOfDistinctValues": "3",
                "mostCommonValues": "{active,discharged,pending}",
                "mostCommonValuesFrequency": "{0.7,0.2,0.1}",
            },
        },
        {
            "annotation_type": ANN_COLUMN_VALUES,
            "resource_properties": {
                "columnQualifiedName": "coco_ods.public.patient.status",
                "columnName": "status",
            },
            "value_list": ["active", "discharged", "pending"],
            "value_count": {"active": 840, "discharged": 240, "pending": 120},
            "value_range_from": "active",
            "value_range_to": "pending",
        },
    ]


def local_database_blob() -> dict:
    """An old-style `database_surveys.survey_data` blob, as
    `database_surveyor.py` writes it."""
    return {
        "schema_info": {
            "total_tables": 1, "total_columns": 2,
            "schemas": [{
                "name": "public", "description": "standard public schema",
                "tables": [{
                    "name": "patient", "type": "BASE TABLE",
                    "description": "one row per patient",
                    "row_count": 1200, "size_bytes": 81920,
                    "last_analyzed": "2026-09-18T00:00:00",
                    "last_vacuumed": "", "pending_changes": 0,
                    "columns": [
                        {"name": "id", "type": "integer", "base_type": "integer",
                         "nullable": False, "default": None, "position": 1,
                         "description": "", "is_primary_key": True,
                         "foreign_key": None},
                        {"name": "ward_id", "type": "integer",
                         "base_type": "integer", "nullable": True,
                         "default": None, "position": 2, "description": "",
                         "is_primary_key": False,
                         "foreign_key": {"foreign_schema": "public",
                                         "foreign_table": "ward",
                                         "foreign_column": "id"}},
                    ],
                }],
            }],
        },
        "statistics": {},
        "annotation_count": 4,
        "views": [{"name": "patient_summary", "schema": "public",
                   "definition": "SELECT id FROM patient",
                   "depends_on": ["public.patient"]}],
    }


def local_filesystem_blob() -> dict:
    return {
        "surveyed_at": SURVEYED_AT,
        "total_files": 2, "total_data_files": 1, "total_size_bytes": 2048,
        "inventory": [
            {"file_path": "data/patients.csv", "file_name": "patients.csv",
             "file_size_bytes": 1024, "is_data_file": True, "format": "CSV",
             "is_hidden": False, "is_symlink": False, "is_executable": False,
             "is_writable": True, "modified_at": "2026-09-19T00:00:00"},
            {"file_path": "README.md", "file_name": "README.md",
             "file_size_bytes": 1024, "is_data_file": False,
             "format": "Unknown", "is_hidden": False, "is_symlink": False,
             "is_executable": False, "is_writable": True,
             "modified_at": "2026-09-18T00:00:00"},
        ],
        "inaccessible_files": [{"path": "private/secret.bin",
                                "error": "Permission denied"}],
    }


def _required_columns_by_table() -> dict[str, set[str]]:
    """NOT NULL columns per table, read out of the DDL.

    Derived rather than listed so this test keeps testing the real
    constraints if a column's nullability changes.
    """
    import re

    from resource_explorer.registry import _DB_FS_DETAIL_TABLE_DDL

    out: dict[str, set[str]] = {}
    for ddl in _DB_FS_DETAIL_TABLE_DDL:
        name = re.search(r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)", ddl).group(1)
        out[name] = {
            m.group(1)
            for m in re.finditer(r"^\s*([a-z_][a-z0-9_]*)\s+[A-Z].*NOT NULL", ddl,
                                 re.MULTILINE)
        }
    return out


# ── 1. schema / insert / query round-trip, every table ─────────────────────


class TestTableRoundTrip:
    def test_every_declared_table_exists_and_round_trips(self, registry):
        """Each of the ten tables accepts a row and returns it.

        Driven off `_DETAIL_TABLE_SPECS` rather than a hand-written list, so a
        table added to the DDL without a test fails here instead of shipping
        untested.
        """
        assert len(_DETAIL_TABLE_SPECS) == 10, sorted(_DETAIL_TABLE_SPECS)
        required = _required_columns_by_table()
        for table, spec in _DETAIL_TABLE_SPECS.items():
            slug = "coco_ods" if spec.resource_type == "database" else "drop_zone"
            # Every nullable column None on purpose: this is also the check
            # that "not measured" is storable in every column that allows it.
            row = {c: None for c in spec.value_columns}
            for col in required[table]:
                if col in row:
                    row[col] = f"x_{col}"
            row["state"] = STATE_MEASURED
            written = registry.write_detail_rows(table, slug, SURVEYED_AT, rows=[row])
            assert written == 1, table
            back = registry.query_detail_rows(table, slug, SURVEYED_AT)
            assert len(back) == 1, table
            assert back[0]["source"] == SOURCE_LOCAL, table
            assert back[0]["surveyed_at"] == SURVEYED_AT, table

    def test_rejects_unknown_table(self, registry):
        with pytest.raises(ValueError):
            registry.write_detail_rows("not_a_table", "coco_ods", SURVEYED_AT, rows=[])

    def test_rewrite_replaces_rather_than_appends(self, registry):
        """Re-materialising the same report must not double its rows."""
        rows = [{"schema_name": "public", "table_name": "patient"}]
        registry.write_detail_rows("database_tables", "coco_ods", SURVEYED_AT, rows=rows)
        registry.write_detail_rows("database_tables", "coco_ods", SURVEYED_AT, rows=rows)
        assert len(registry.query_detail_rows("database_tables", "coco_ods")) == 1

    def test_local_and_egeria_rows_of_same_run_coexist(self, registry):
        """Rule D: keyed by (slug, surveyed_at, source), so a native and a
        local run on the same timestamp do not overwrite each other."""
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, source=SOURCE_LOCAL,
            rows=[{"schema_name": "public", "table_name": "local_only"}])
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, source=SOURCE_EGERIA,
            rows=[{"schema_name": "public", "table_name": "egeria_only"}])
        local = registry.query_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, SOURCE_LOCAL)
        egeria = registry.query_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, SOURCE_EGERIA)
        assert [r["table_name"] for r in local] == ["local_only"]
        assert [r["table_name"] for r in egeria] == ["egeria_only"]

    def test_latest_run_wins_when_no_timestamp_given(self, registry):
        registry.write_detail_rows(
            "database_tables", "coco_ods", EARLIER,
            rows=[{"schema_name": "public", "table_name": "old"}])
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT,
            rows=[{"schema_name": "public", "table_name": "new"}])
        latest = registry.query_detail_rows("database_tables", "coco_ods")
        assert [r["table_name"] for r in latest] == ["new"]
        assert registry.get_detail_run_timestamps("database_tables", "coco_ods") == [
            SURVEYED_AT, EARLIER,
        ]

    def test_json_columns_round_trip_as_python(self, registry):
        registry.write_detail_rows(
            "database_columns", "coco_ods", SURVEYED_AT,
            rows=[{"schema_name": "public", "table_name": "patient",
                   "column_name": "ward_id",
                   "foreign_key_json": {"foreign_table": "ward"}}])
        row = registry.query_detail_rows("database_columns", "coco_ods")[0]
        assert row["foreign_key_json"] == {"foreign_table": "ward"}

    def test_booleans_stored_as_integers(self, registry):
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT,
            rows=[{"schema_name": "public", "table_name": "patient",
                   "has_indexes": True, "has_rules": False}])
        row = registry.query_detail_rows("database_tables", "coco_ods")[0]
        assert row["has_indexes"] == 1
        assert row["has_rules"] == 0


# ── 2. absence is a result ─────────────────────────────────────────────────


class TestAbsenceIsAResult:
    def test_measured_zero_is_not_not_measured(self, registry):
        """The core distinction. A table whose row count was measured as 0 and
        one whose row count was never measured must not be the same row."""
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, rows=[
                {"schema_name": "public", "table_name": "measured_empty",
                 "row_count": 0},
                {"schema_name": "public", "table_name": "never_counted"},
            ])
        rows = {r["table_name"]: r for r in
                registry.query_detail_rows("database_tables", "coco_ods")}
        assert rows["measured_empty"]["row_count"] == 0
        assert rows["never_counted"]["row_count"] is None

    def test_empty_section_records_coverage_as_empty(self, registry):
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, rows=[],
            coverage_section=SECTION_TABLES)
        coverage = registry.get_section_coverage("database", "coco_ods")
        assert coverage[SECTION_TABLES]["state"] == STATE_EMPTY
        assert coverage[SECTION_TABLES]["row_count"] == 0

    def test_unmeasured_section_is_distinct_from_empty_one(self, registry):
        """Two empty tables, two different reasons, two different states."""
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, rows=[],
            coverage_section=SECTION_TABLES)
        registry.record_section_coverage(
            "database", "coco_ods", SURVEYED_AT, SECTION_GRANTS,
            STATE_NOT_PERMITTED, detail="survey user lacks pg_roles")
        coverage = registry.get_section_coverage("database", "coco_ods")
        assert coverage[SECTION_TABLES]["state"] == STATE_EMPTY
        assert coverage[SECTION_GRANTS]["state"] == STATE_NOT_PERMITTED
        assert "pg_roles" in coverage[SECTION_GRANTS]["detail"]

    def test_section_never_recorded_is_absent_not_empty(self, registry):
        """A section nothing claimed to have tried is missing from the mapping
        entirely — weaker than not_measured, and must not read as 'none'."""
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, rows=[],
            coverage_section=SECTION_TABLES)
        coverage = registry.get_section_coverage("database", "coco_ods")
        assert SECTION_GRANTS not in coverage

    def test_coverage_is_per_source(self, registry):
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, source=SOURCE_LOCAL,
            rows=[{"schema_name": "public", "table_name": "t"}],
            coverage_section=SECTION_TABLES)
        registry.write_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, source=SOURCE_EGERIA,
            rows=[], coverage_section=SECTION_TABLES)
        local = registry.get_section_coverage(
            "database", "coco_ods", SURVEYED_AT, SOURCE_LOCAL)
        egeria = registry.get_section_coverage(
            "database", "coco_ods", SURVEYED_AT, SOURCE_EGERIA)
        assert local[SECTION_TABLES]["state"] == STATE_MEASURED
        assert egeria[SECTION_TABLES]["state"] == STATE_EMPTY


# ── 3. the materialiser: native annotations → rows tagged source='egeria' ──


class TestNativeMaterialiser:
    def test_converts_annotations_to_rows(self):
        rows = database_rows_from_annotations(native_database_annotations())
        assert [s["schema_name"] for s in rows["database_schemas"]] == ["public"]
        schema = rows["database_schemas"][0]
        assert schema["table_count"] == 2
        assert schema["view_count"] == 1
        assert schema["total_table_size_bytes"] == 163840

        tables = {t["table_name"]: t for t in rows["database_tables"]}
        assert set(tables) == {"patient", "patient_summary"}
        assert tables["patient"]["schema_name"] == "public"
        assert tables["patient"]["size_bytes"] == 81920
        assert tables["patient"]["has_indexes"] == 1
        assert tables["patient"]["has_rules"] == 0

    def test_view_becomes_a_sql_object(self):
        rows = database_rows_from_annotations(native_database_annotations())
        views = {v["object_name"]: v for v in rows["database_sql_objects"]}
        assert "patient_summary" in views
        assert views["patient_summary"]["object_type"] == "view"
        assert views["patient_summary"]["definition"] == "SELECT id FROM patient"

    def test_tuple_counters_become_activity_rows(self):
        rows = database_rows_from_annotations(native_database_annotations())
        activity = {a["table_name"]: a for a in rows["database_table_activity"]}
        assert activity["patient"]["rows_inserted"] == 1200
        assert activity["patient"]["rows_updated"] == 30
        # Measured as zero, not missing — the native survey did report it.
        assert activity["patient"]["rows_deleted"] == 0
        # The view reported no counters at all, so it gets no activity row.
        assert "patient_summary" not in activity

    def test_column_qualified_name_splits_into_schema_and_table(self):
        rows = database_rows_from_annotations(native_database_annotations())
        columns = {c["column_name"]: c for c in rows["database_columns"]}
        assert columns["id"]["schema_name"] == "public"
        assert columns["id"]["table_name"] == "patient"
        assert columns["id"]["data_type"] == "integer"
        # columnNotNull true → is_nullable 0
        assert columns["id"]["is_nullable"] == 0
        assert columns["status"]["is_nullable"] == 1

    def test_frequent_values_annotation_feeds_the_profile(self):
        rows = database_rows_from_annotations(native_database_annotations())
        profiles = {p["column_name"]: p for p in rows["database_column_profiles"]}
        assert profiles["status"]["distinct_count"] == 3
        assert profiles["status"]["most_common_values_json"] == [
            "active", "discharged", "pending"]
        assert profiles["status"]["min_value"] == "active"

    def test_most_common_values_parsed_from_postgres_array_text(self):
        """A column with no separate frequent-values annotation still gets its
        values, parsed out of the `{a,b,c}` string the extractor writes."""
        annotations = [a for a in native_database_annotations()
                       if a["annotation_type"] != ANN_COLUMN_VALUES]
        rows = database_rows_from_annotations(annotations)
        profiles = {p["column_name"]: p for p in rows["database_column_profiles"]}
        assert profiles["status"]["most_common_values_json"] == [
            "active", "discharged", "pending"]

    def test_unmeasured_native_fields_are_none_not_zero(self):
        """The native Postgres survey measures no null fraction, no histogram,
        no ordinal position and no row count. Each must be None — a 0 would be
        read as a measurement."""
        rows = database_rows_from_annotations(native_database_annotations())
        profile = rows["database_column_profiles"][0]
        assert profile["null_fraction"] is None
        assert profile["histogram_bounds_json"] is None
        assert profile["correlation"] is None
        column = rows["database_columns"][0]
        assert column["ordinal_position"] is None
        assert column["is_primary_key"] is None
        tables = {t["table_name"]: t for t in rows["database_tables"]}
        assert tables["patient"]["row_count"] is None
        # The view annotation carries no tableSize at all. None, not 0 — a 0
        # here would state that the view occupies no bytes, which nothing
        # measured. This assertion is what fails if the metric parser ever
        # starts defaulting an absent value to zero.
        assert tables["patient_summary"]["size_bytes"] is None
        assert tables["patient"]["size_bytes"] == 81920
        # Likewise the view reported no column measurements of its own.
        profiles = {p["column_name"]: p for p in rows["database_column_profiles"]}
        assert profiles["id"]["most_common_values_json"] is None

    def test_materialize_writes_rows_tagged_egeria(self, registry):
        """The stream's done-test: a native report read back lands as rows
        with source='egeria'."""
        written = materialize_database_report(
            registry, "coco_ods", SURVEYED_AT, native_database_annotations())
        assert written["database_tables"] == 2
        assert written["database_columns"] == 2

        rows = registry.query_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, SOURCE_EGERIA)
        assert {r["table_name"] for r in rows} == {"patient", "patient_summary"}
        assert all(r["source"] == SOURCE_EGERIA for r in rows)
        # And nothing was written under the local source.
        assert registry.query_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, SOURCE_LOCAL) == []

    def test_materialize_is_idempotent(self, registry):
        for _ in range(2):
            materialize_database_report(
                registry, "coco_ods", SURVEYED_AT, native_database_annotations())
        rows = registry.query_detail_rows(
            "database_tables", "coco_ods", SURVEYED_AT, SOURCE_EGERIA)
        assert len(rows) == 2

    def test_empty_native_report_records_the_permission_ambiguity(self, registry):
        """A native survey reporting no schemas may mean the survey user
        cannot see them. The coverage detail must say so rather than letting
        an empty table read as 'this database has no schemas'."""
        materialize_database_report(registry, "coco_ods", SURVEYED_AT, [])
        coverage = registry.get_section_coverage(
            "database", "coco_ods", SURVEYED_AT, SOURCE_EGERIA)
        assert coverage["schemas"]["state"] == STATE_EMPTY
        assert "permission" in coverage["schemas"]["detail"]

    def test_annotations_without_resource_properties_are_skipped(self):
        """Older or non-measurement annotations must not produce half-rows."""
        rows = database_rows_from_annotations([
            {"annotation_type": ANN_TABLE_MEASUREMENTS, "summary": "no props"},
            {"annotation_type": ANN_DATABASE_MEASUREMENTS,
             "resource_properties": {}},
        ])
        assert rows["database_tables"] == []


class TestNativeFilesystemMaterialiser:
    def test_folder_only_survey_marks_entries_not_measured(self, registry):
        """A `survey-folder` run without the `-and-files` variant lists no
        files. That is not an empty directory, and must not read as one."""
        annotations = [{
            "annotation_type": ANN_FILE_COUNTS,
            "resource_properties": {"fileCount": "42", "totalFileSize": "8192"},
        }]
        materialize_filesystem_report(
            registry, "drop_zone", SURVEYED_AT, annotations)
        coverage = registry.get_section_coverage(
            "filesystem", "drop_zone", SURVEYED_AT, SOURCE_EGERIA)
        assert coverage["entries"]["state"] == STATE_NOT_MEASURED
        assert "and-files" in coverage["entries"]["detail"]

    def test_native_folder_survey_never_profiles_data_files(self, registry):
        materialize_filesystem_report(registry, "drop_zone", SURVEYED_AT, [])
        coverage = registry.get_section_coverage(
            "filesystem", "drop_zone", SURVEYED_AT, SOURCE_EGERIA)
        assert coverage["data_files"]["state"] == STATE_NOT_MEASURED

    def test_inaccessible_files_become_entries_not_omissions(self, registry):
        annotations = [{
            "annotation_type": "Inaccessible files",
            "value_list": ["private/secret.bin"],
        }]
        materialize_filesystem_report(
            registry, "drop_zone", SURVEYED_AT, annotations)
        rows = registry.query_detail_rows(
            "filesystem_entries", "drop_zone", SURVEYED_AT, SOURCE_EGERIA)
        assert len(rows) == 1
        assert rows[0]["entry_path"] == "private/secret.bin"
        assert rows[0]["state"] == STATE_NOT_PERMITTED
        assert rows[0]["is_readable"] == 0


class TestRemovalCleansUpChildren:
    """Every new table carries a real FK to its parent, so removal has to
    delete them first. Missing one does not strand rows the way an
    unguarded slug column would — it makes the parent delete fail outright,
    which is louder but just as broken. Found by the full suite, not by the
    new tests, which is why it is pinned here."""

    def test_remove_database_with_detail_rows(self, registry):
        backfill_database_survey(
            registry, "coco_ods", SURVEYED_AT, local_database_blob())
        assert registry.query_detail_rows("database_tables", "coco_ods")
        registry.remove_database("coco_ods")
        assert registry.get_database("coco_ods") is None
        assert registry.query_detail_rows("database_tables", "coco_ods") == []
        assert registry.get_section_coverage("database", "coco_ods") == {}

    def test_remove_filesystem_with_detail_rows(self, registry):
        backfill_filesystem_survey(
            registry, "drop_zone", SURVEYED_AT, local_filesystem_blob())
        assert registry.query_detail_rows("filesystem_entries", "drop_zone")
        registry.remove_filesystem("drop_zone")
        assert registry.query_detail_rows("filesystem_entries", "drop_zone") == []
        assert registry.get_section_coverage("filesystem", "drop_zone") == {}

    def test_coverage_rejects_an_unknown_resource_type(self, registry):
        """dataset and model are in the plan but have no coverage table yet.
        Failing loudly beats writing their coverage nowhere."""
        with pytest.raises(ValueError):
            registry.record_section_coverage(
                "dataset", "x", SURVEYED_AT, "schemas", STATE_MEASURED)


# ── 3b. whose statistics, and as of when (designer review 2026-09-20) ──────


class TestStatsProvenance:
    """`sample_strategy` says how much was looked at. These say whose numbers
    they are and when they were computed — without which a card can show a
    three-month-old null fraction beside a two-minute-old row count and
    indicate nothing about the difference."""

    def test_native_column_stats_are_attributed_to_the_database(self):
        """A native survey transports pg_stats output; it does not compute it.
        Attributing the numbers to Egeria would be the wrong provenance —
        which engine ran the survey is already in `source`."""
        rows = database_rows_from_annotations(native_database_annotations())
        profile = rows["database_column_profiles"][0]
        assert profile["stats_source"] == STATS_SOURCE_DATABASE
        # The native report carries no last_analyze, so the age of these
        # numbers is unknown. NULL, not the survey time — claiming the survey
        # time would assert they were computed then, which is false.
        assert profile["stats_computed_at"] is None

    def test_run_analyze_state_is_expressible_without_a_special_case(
        self, registry
    ):
        """"No statistics collected, run ANALYZE" is exactly
        stats_source=database with stats_computed_at NULL — not a separate
        flag or a sentinel value."""
        registry.write_detail_rows(
            "database_column_profiles", "coco_ods", SURVEYED_AT, rows=[{
                "schema_name": "public", "table_name": "patient",
                "column_name": "id",
                "stats_source": STATS_SOURCE_DATABASE,
                "stats_computed_at": None,
                "null_fraction": None,
            }])
        row = registry.query_detail_rows(
            "database_column_profiles", "coco_ods")[0]
        assert row["stats_source"] == STATS_SOURCE_DATABASE
        assert row["stats_computed_at"] is None
        assert row["null_fraction"] is None

    def test_stats_computed_at_survives_the_round_trip(self, registry):
        registry.write_detail_rows(
            "database_column_profiles", "coco_ods", SURVEYED_AT, rows=[{
                "schema_name": "public", "table_name": "patient",
                "column_name": "id",
                "stats_source": STATS_SOURCE_DATABASE,
                "stats_computed_at": "2026-06-01T00:00:00",
            }])
        row = registry.query_detail_rows(
            "database_column_profiles", "coco_ods")[0]
        # Deliberately older than SURVEYED_AT: the whole point is that the
        # statistics and the survey reporting them have different ages.
        assert row["stats_computed_at"] == "2026-06-01T00:00:00"
        assert row["stats_computed_at"] < SURVEYED_AT

    def test_stats_reset_is_recorded_on_activity_rows(self):
        """Tuple counters are cumulative since the last reset. Without the
        reset timestamp per snapshot, a reset between two snapshots yields a
        negative difference that a chart would draw as '−40,000 inserts'."""
        rows = database_rows_from_annotations(native_database_annotations())
        activity = {a["table_name"]: a for a in rows["database_table_activity"]}
        assert activity["patient"]["stats_reset"] == "2026-09-01T00:00:00"

    def test_stats_reset_absent_when_the_survey_did_not_report_it(self):
        annotations = [a for a in native_database_annotations()
                       if a["annotation_type"] != ANN_DATABASE_MEASUREMENTS]
        rows = database_rows_from_annotations(annotations)
        activity = {a["table_name"]: a for a in rows["database_table_activity"]}
        # None, not "" — unknown rather than "never reset".
        assert activity["patient"]["stats_reset"] is None

    def test_a_moved_stats_reset_is_detectable_between_two_snapshots(
        self, registry
    ):
        """The consumer-facing point of the column: two snapshots whose reset
        timestamps differ are not a rate interval, and the stored rows are
        enough to establish that."""
        registry.write_detail_rows(
            "database_table_activity", "coco_ods", EARLIER, rows=[{
                "schema_name": "public", "table_name": "patient",
                "rows_inserted": 40000, "stats_reset": "2026-08-01T00:00:00",
            }])
        registry.write_detail_rows(
            "database_table_activity", "coco_ods", SURVEYED_AT, rows=[{
                "schema_name": "public", "table_name": "patient",
                "rows_inserted": 12, "stats_reset": "2026-09-19T00:00:00",
            }])
        prev = registry.query_detail_rows(
            "database_table_activity", "coco_ods", EARLIER)[0]
        curr = registry.query_detail_rows(
            "database_table_activity", "coco_ods", SURVEYED_AT)[0]
        naive_rate = curr["rows_inserted"] - prev["rows_inserted"]
        assert naive_rate < 0                       # the misleading number
        assert curr["stats_reset"] != prev["stats_reset"]   # and why it is wrong

    def test_unprofiled_data_file_claims_no_stats_source(self):
        """A data file that was classified but never profiled has no
        statistics, so naming a source for them would assert a provenance
        nothing supports."""
        rows = filesystem_rows_from_survey_data(local_filesystem_blob())
        data_file = rows["filesystem_data_files"][0]
        assert data_file["stats_source"] == ""
        assert data_file["stats_computed_at"] is None

    def test_profiled_data_file_is_attributed_to_resource_explorer(self):
        blob = local_filesystem_blob()
        blob["inventory"][0]["row_count"] = 500
        blob["inventory"][0]["column_count"] = 4
        rows = filesystem_rows_from_survey_data(blob)
        data_file = rows["filesystem_data_files"][0]
        assert data_file["stats_source"] == STATS_SOURCE_RESOURCE_EXPLORER
        assert data_file["stats_computed_at"] == SURVEYED_AT
        assert data_file["row_count"] == 500


# ── 4. the back-fill: survey_data blobs → the same rows ────────────────────


class TestBackfill:
    def test_database_blob_converts(self):
        rows = database_rows_from_survey_data(local_database_blob())
        assert [s["schema_name"] for s in rows["database_schemas"]] == ["public"]
        assert rows["database_schemas"][0]["table_count"] == 1
        tables = rows["database_tables"]
        assert tables[0]["table_name"] == "patient"
        assert tables[0]["row_count"] == 1200
        assert tables[0]["size_bytes"] == 81920
        columns = {c["column_name"]: c for c in rows["database_columns"]}
        assert columns["id"]["is_primary_key"] == 1
        assert columns["id"]["ordinal_position"] == 1
        assert columns["ward_id"]["foreign_key_json"]["foreign_table"] == "ward"

    def test_database_blob_unmeasured_fields_stay_none(self):
        """The old blob carried no view counts. None, not 0. (The schema size
        stopped being unmeasured in Brief D 7f.2: it is the sum of the tables'
        sizes -- see test_schema_size_is_a_sum_only_when_every_table_was_sized.)"""
        rows = database_rows_from_survey_data(local_database_blob())
        schema = rows["database_schemas"][0]
        assert schema["view_count"] is None
        assert schema["mat_view_count"] is None

    def test_schema_size_is_a_sum_only_when_every_table_was_sized(self):
        blob = local_database_blob()
        rows = database_rows_from_survey_data(blob)
        assert rows["database_schemas"][0]["total_table_size_bytes"] == 81920
        blob["schema_info"]["schemas"][0]["tables"][0]["size_bytes"] = None
        rows = database_rows_from_survey_data(blob)
        assert rows["database_schemas"][0]["total_table_size_bytes"] is None

    def test_grants_are_not_measured_when_privilege_audit_did_not_run(self):
        """No `operations` key in the blob at all (a schema_inventory-only
        local survey) — database_grants must be absent from the converted
        rows so the caller's NOT_MEASURED marker applies, never an empty
        'measured, none' list."""
        rows = database_rows_from_survey_data(local_database_blob())
        assert "database_grants" not in rows

    def test_grants_are_extracted_when_privilege_audit_ran(self):
        """Phase 1 slice 14 follow-up (2026-09-22): found live that
        database_grants was written by NO survey path at all — this blob
        (with operations.privilege_audit present, as database_surveyor.py's
        `_survey_operations` stores it) must now convert to real rows."""
        blob = dict(local_database_blob())
        blob["operations"] = {
            "privilege_audit": {
                "roles": [],
                "table_grants": [
                    {"table_schema": "public", "table_name": "patient",
                     "grantee": "app_user", "privilege_type": "SELECT",
                     "is_grantable": "NO"},
                    {"table_schema": "public", "table_name": "patient",
                     "grantee": "PUBLIC", "privilege_type": "SELECT",
                     "is_grantable": "NO"},
                ],
                "default_acl": [],
            },
        }
        rows = database_rows_from_survey_data(blob)
        grants = rows["database_grants"]
        assert len(grants) == 2
        by_grantee = {g["grantee"]: g for g in grants}
        assert by_grantee["PUBLIC"]["privilege_type"] == "SELECT"
        assert by_grantee["PUBLIC"]["object_name"] == "patient"
        assert by_grantee["PUBLIC"]["object_type"] == "table"
        assert by_grantee["app_user"]["is_grantable"] == 0

    def test_backfill_writes_real_grant_rows_when_privilege_audit_ran(self, registry):
        blob = dict(local_database_blob())
        blob["operations"] = {
            "privilege_audit": {
                "roles": [], "default_acl": [],
                "table_grants": [
                    {"table_schema": "public", "table_name": "patient",
                     "grantee": "PUBLIC", "privilege_type": "SELECT",
                     "is_grantable": "NO"},
                ],
            },
        }
        written = backfill_database_survey(registry, "coco_ods", SURVEYED_AT, blob)
        assert written["database_grants"] == 1

        rows = registry.query_detail_rows("database_grants", "coco_ods")
        assert len(rows) == 1
        assert rows[0]["grantee"] == "PUBLIC"

        coverage = registry.get_section_coverage("database", "coco_ods")
        assert coverage["grants"]["state"] == STATE_MEASURED

    def test_backfill_writes_rows_and_marks_what_it_cannot_know(self, registry):
        written = backfill_database_survey(
            registry, "coco_ods", SURVEYED_AT, local_database_blob())
        assert written["database_tables"] == 1
        assert written["database_columns"] == 2
        assert written["database_sql_objects"] == 1

        rows = registry.query_detail_rows("database_tables", "coco_ods")
        assert rows[0]["table_name"] == "patient"
        assert rows[0]["source"] == SOURCE_LOCAL

        coverage = registry.get_section_coverage("database", "coco_ods")
        # Sections the blob could never answer say so explicitly.
        assert coverage["column_profiles"]["state"] == STATE_NOT_MEASURED
        assert coverage["grants"]["state"] == STATE_NOT_MEASURED
        assert coverage["settings"]["state"] == STATE_NOT_MEASURED
        assert coverage["tables"]["state"] == STATE_MEASURED

    def test_backfill_of_an_empty_blob_writes_nothing(self, registry):
        written = backfill_database_survey(registry, "coco_ods", SURVEYED_AT, {})
        assert written["database_tables"] == 0

    def test_filesystem_blob_converts(self, registry):
        written = backfill_filesystem_survey(
            registry, "drop_zone", SURVEYED_AT, local_filesystem_blob())
        assert written["filesystem_entries"] == 3   # two files + one unreadable
        assert written["filesystem_data_files"] == 1

        entries = {e["entry_path"]: e for e in registry.query_detail_rows(
            "filesystem_entries", "drop_zone")}
        assert entries["data/patients.csv"]["size_bytes"] == 1024
        assert entries["data/patients.csv"]["file_extension"] == "csv"
        assert entries["README.md"]["state"] == STATE_MEASURED
        # The unreadable file is present and labelled, not silently dropped.
        assert entries["private/secret.bin"]["state"] == STATE_NOT_PERMITTED
        assert entries["private/secret.bin"]["size_bytes"] is None

    def test_filesystem_data_file_is_classified_but_not_profiled(self):
        rows = filesystem_rows_from_survey_data(local_filesystem_blob())
        data_file = rows["filesystem_data_files"][0]
        assert data_file["format"] == "CSV"
        # The walk classifies without profiling — None, not 0 rows.
        assert data_file["row_count"] is None
        assert data_file["column_count"] is None

    def test_recording_a_survey_materialises_rows_automatically(self, registry):
        """New surveys must not need the back-fill script. record_database_survey
        is the single seam every database survey path funnels through."""
        registry.record_database_survey(
            slug="coco_ods", schema_count=1, table_count=1, column_count=2,
            survey_data=local_database_blob(),
        )
        rows = registry.query_detail_rows("database_tables", "coco_ods")
        assert [r["table_name"] for r in rows] == ["patient"]

    def test_recording_a_filesystem_survey_materialises_rows(self, registry):
        registry.add_filesystem_survey(
            "drop_zone", SURVEYED_AT, local_filesystem_blob())
        rows = registry.query_detail_rows("filesystem_entries", "drop_zone")
        assert len(rows) == 3

    def test_backfill_is_rerunnable(self, registry):
        for _ in range(2):
            backfill_database_survey(
                registry, "coco_ods", SURVEYED_AT, local_database_blob())
        assert len(registry.query_detail_rows("database_columns", "coco_ods")) == 2

    def test_native_and_blob_paths_agree_on_row_shape(self):
        """Both converters must produce rows the same table accepts. If they
        drift, one of the two silently stops populating a column."""
        native = database_rows_from_annotations(native_database_annotations())
        blob = database_rows_from_survey_data(local_database_blob())
        for table in ("database_schemas", "database_tables", "database_columns"):
            spec = _DETAIL_TABLE_SPECS[table]
            for source_rows in (native[table], blob[table]):
                for row in source_rows:
                    unknown = set(row) - set(spec.value_columns)
                    assert not unknown, f"{table}: {unknown}"


# ── 5. get_database_diff reads rows, not blobs ─────────────────────────────


class TestDatabaseDiffReadsRows:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
        )
        from resource_explorer.web.app import app
        return TestClient(app)

    def _record(self, registry, surveyed_at, table_names):
        """Record a survey row plus its structured rows, bypassing the blob so
        the test proves the route reads rows and not JSON."""
        with registry._conn() as conn:
            conn.execute(
                """INSERT INTO database_surveys
                   (database_slug, surveyed_at, egeria_report_guid, schema_count,
                    table_count, column_count, survey_data, source)
                   VALUES (?, ?, '', 1, ?, 0, ?, 'local')""",
                ("coco_ods", surveyed_at, len(table_names),
                 json.dumps({"deliberately": "not the source of truth"})),
            )
        registry.write_detail_rows(
            "database_tables", "coco_ods", surveyed_at,
            rows=[{"schema_name": "public", "table_name": t} for t in table_names],
            coverage_section=SECTION_TABLES,
        )

    def test_diff_comes_from_structured_rows(self, registry, client):
        self._record(registry, EARLIER, ["patient", "ward"])
        self._record(registry, SURVEYED_AT, ["patient", "visit"])
        body = client.get("/api/databases/coco_ods/diff").json()
        assert body["new_tables"] == ["public.visit"]
        assert body["removed_tables"] == ["public.ward"]
        assert body["table_diff_state"] == STATE_MEASURED

    def test_fewer_than_two_runs_returns_empty(self, registry, client):
        self._record(registry, SURVEYED_AT, ["patient"])
        assert client.get("/api/databases/coco_ods/diff").json() == {}

    def test_run_without_structured_rows_is_not_reported_as_no_change(
        self, registry, client
    ):
        """The bug the old implementation had: a blob it could not parse
        produced an empty set, so the answer was a confident '±0, nothing
        added, nothing removed'. Now it says it cannot tell."""
        with registry._conn() as conn:
            for surveyed_at in (EARLIER, SURVEYED_AT):
                conn.execute(
                    """INSERT INTO database_surveys
                       (database_slug, surveyed_at, egeria_report_guid,
                        schema_count, table_count, column_count, survey_data, source)
                       VALUES (?, ?, '', 1, 2, 4, '{}', 'local')""",
                    ("coco_ods", surveyed_at),
                )
        body = client.get("/api/databases/coco_ods/diff").json()
        assert body["table_diff_state"] == "not_comparable"
        assert body["new_tables"] == []
        assert body["removed_tables"] == []
        assert "backfill_structured_tables" in body["table_diff_note"]

    def test_genuinely_empty_run_is_comparable(self, registry, client):
        """A run measured as having no tables is a real answer, and diffing
        against it is legitimate — unlike a run with no rows at all."""
        self._record(registry, EARLIER, [])
        self._record(registry, SURVEYED_AT, ["patient"])
        body = client.get("/api/databases/coco_ods/diff").json()
        assert body["table_diff_state"] == STATE_MEASURED
        assert body["new_tables"] == ["public.patient"]

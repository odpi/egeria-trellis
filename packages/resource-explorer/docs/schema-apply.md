# `resource-explorer schema apply` and `RE_SCHEMA_MODE` (TR-112b)

Why: `ProjectRegistry.__init__` runs about 440 DDL statements (some take ACCESS EXCLUSIVE locks) once per process per
database URL. On Kubernetes several pods starting together would contend. The schema step becomes its own Job.

* `resource-explorer schema apply` runs the setup against the configured registry database and exits 0, printing
  `schema ready · N statements · <elapsed>`. It resolves the database URL exactly as `web` does, including
  `?options=-csearch_path%3D...`; the marker lives in that schema.
* It writes a one-row marker table, `schema_version` (`schema_hash`, `statements`, `applied_at`). The hash is of the
  schema-defining code (`_init_schema` and everything it reaches; comments and docstrings do not change it).
* A second run finds the marker for this code, sends no DDL, and prints the same line (the stored count).
  A Postgres advisory lock makes concurrent runs take turns.
* `RE_SCHEMA_MODE=auto` (default) is today's behaviour: construction runs the setup. Compose is unchanged.
  `RE_SCHEMA_MODE=external`: construction never runs DDL. It checks the marker and raises `SchemaNotReady`
  ("the RE schema is not at this version: run `resource-explorer schema apply` (the chart's schema Job) first") when it is
  missing or for other code.
* Container: `docker run ... trellis/resource-explorer schema-apply` (entrypoint role `schema-apply`).
* **Not covered (read this):** `external` mode governs `ProjectRegistry` only. Four stores still run their own DDL on first use,
  so a pod in external mode can still issue DDL against those: the metrics collector, `trellis_feedback`, `trellis_vectorstore`
  (including `CREATE EXTENSION vector`) and `trellis_artifact_tree`. The code fix is TR-115 (TR-112b part 2), due before the
  Portal and RE charts (Phase 2).
* **Rolling upgrades rely on additive migrations.** The schema Job runs first, so for a short time old pods run against the
  new schema. That is safe only because every migration is additive (new tables, nullable or defaulted columns, `IF NOT EXISTS`).
  A migration that drops, renames or tightens something breaks old pods mid-upgrade and needs a different rollout.
* The version hash follows `_init_schema` and everything it reaches, including code it imports from other
  `resource_explorer` modules (`open_data.schema`, `survey_report.ANNOTATION_TYPES_REGISTRY`, `org_importer._url_to_slug`, ...).
  See `resource_explorer/schema_fingerprint.py`. Changing the SQL, a type, a constant or a helper moves it; comments and docstrings do not.

Tests: `tests/test_schema_apply.py`; Postgres cases need `RE_SCHEMA_TEST_PG_URL` (a throwaway database, no search_path) and skip otherwise.

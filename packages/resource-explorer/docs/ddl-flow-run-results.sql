-- TR-21 (docs/ddl-flow-run-results.md): the one targeted statement. Never apply it by a full schema init.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = resource_explorer;
CREATE TABLE IF NOT EXISTS flow_run_results (
    flow_run_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    slug        TEXT NOT NULL,
    step_key    TEXT NOT NULL,
    step_run_id INTEGER,
    output      TEXT NOT NULL,
    norm_hash   TEXT NOT NULL,
    written_at  TEXT NOT NULL
);
COMMIT;

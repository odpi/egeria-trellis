"""The three registry tables of slice OD1. Deployment record: docs/ddl-open-data-od1.md.

New tables only; no existing table is altered. Applied by `ProjectRegistry._migrate_open_data_tables` at the end of
`_init_schema`, the first time a process running this code opens the shared registry.
"""
from __future__ import annotations

OPEN_DATA_TABLES = ("open_data_portals", "open_data", "portal_directories", "investigation_data_requests")

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS open_data_portals (
        slug            TEXT PRIMARY KEY,
        display_name    TEXT NOT NULL,
        api_kind        TEXT NOT NULL,
        base_url        TEXT NOT NULL,
        secret_ref      TEXT NOT NULL DEFAULT '',
        origin          TEXT NOT NULL DEFAULT 'hand',
        registered_by   TEXT NOT NULL DEFAULT '',
        registered_at   TEXT NOT NULL,
        adapter         TEXT NOT NULL DEFAULT '',
        calls_per_page  INTEGER,
        page_size       INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS open_data (
        slug             TEXT PRIMARY KEY,
        identifier       TEXT NOT NULL UNIQUE,
        identifier_kind  TEXT NOT NULL,
        portal_slug      TEXT NOT NULL,
        portals_json     TEXT NOT NULL DEFAULT '[]',
        title            TEXT NOT NULL,
        publisher        TEXT NOT NULL,
        licence_id       TEXT NOT NULL,
        attribution      TEXT NOT NULL,
        share_alike      TEXT NOT NULL,
        commercial       TEXT NOT NULL,
        modified         TEXT NOT NULL,
        formats_json     TEXT NOT NULL,
        size_bytes       TEXT NOT NULL,
        record_json      TEXT NOT NULL,
        retrieved_at     TEXT NOT NULL,
        datacite_json    TEXT NOT NULL DEFAULT '',
        imported_by      TEXT NOT NULL DEFAULT '',
        imported_at      TEXT NOT NULL,
        work_list_slug   TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS portal_directories (
        slug           TEXT PRIMARY KEY,
        display_name   TEXT NOT NULL,
        api_kind       TEXT NOT NULL,
        base_url       TEXT NOT NULL,
        registered_by  TEXT NOT NULL DEFAULT '',
        registered_at  TEXT NOT NULL,
        last_run_at    TEXT NOT NULL DEFAULT ''
    )""",
    # Approved (project owner, 2026-10-30): docs/ddl-open-data-data-request.md. Applied at the next owner-approved
    # 8813 restart after merge; until then code reads a missing table as "no data request stated".
    """CREATE TABLE IF NOT EXISTS investigation_data_requests (
        investigation_slug  TEXT PRIMARY KEY,
        subjects_json       TEXT NOT NULL DEFAULT '[]',
        period              TEXT NOT NULL DEFAULT '',
        places_json         TEXT NOT NULL DEFAULT '[]',
        grain               TEXT NOT NULL DEFAULT '',
        need_commercial     TEXT NOT NULL DEFAULT '',
        need_share_alike    TEXT NOT NULL DEFAULT '',
        formats_json        TEXT NOT NULL DEFAULT '[]',
        updated_by          TEXT NOT NULL DEFAULT '',
        updated_at          TEXT NOT NULL
    )""",
)

# DDL record: open data slice OD1, three new registry tables (re/od1-open-data)

**Status:** branch pushed, not merged. This is the deployment record for three NEW tables in RE's shared registry.
**Decision (project owner, 2026-10-28):** "start OD1 after the runs, approve the DDL" (the three tables below).
Design: `design-notes/DESIGN-OPEN-DATA-SEARCH-AND-SURVEY.md` §3, §9 (OD1 row), §11.

Nothing in this branch has run against the shared registry (localhost:5442). The tables are created only by RE's own
schema init, the first time a process running this code opens the shared registry, and only after the owner's word
for that restart. Merging is not enough.

## The exact Postgres DDL

`ProjectRegistry._migrate_open_data_tables` in `resource_explorer/registry.py` runs these three statements at the end
of `_init_schema`, each in its own short transaction (the statements live in `resource_explorer/open_data/schema.py`):

```sql
CREATE TABLE IF NOT EXISTS open_data_portals (
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
);

CREATE TABLE IF NOT EXISTS open_data (
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
);

CREATE TABLE IF NOT EXISTS portal_directories (
    slug           TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    api_kind       TEXT NOT NULL,
    base_url       TEXT NOT NULL,
    registered_by  TEXT NOT NULL DEFAULT '',
    registered_at  TEXT NOT NULL,
    last_run_at    TEXT NOT NULL DEFAULT ''
);
```

SQLite runs the same text. No existing table is altered, indexed or referenced by a foreign key.

### Lock behaviour

`CREATE TABLE IF NOT EXISTS` on a name that does not exist takes no lock on any table another process is using; on a
second start it is a no-op. No data is moved.

## What the columns hold

- `open_data_portals.secret_ref` is the NAME of a secrets-store collection (`<slug>::Open Data Portal Key`), never a
  key. The key itself is written to the `.omsecrets` projection (`omsecrets_store.write_credential`); with no store
  configured a registration that carries a key is refused rather than silently dropping it.
- `open_data.attribution / share_alike / commercial` hold `true`, `false` or the text `not_reported`: the licence
  flags as the adapter read them, and "the source did not say" is never an empty string.
- `open_data.record_json` is the publisher's record verbatim; `retrieved_at` is when RE read it (UTC ISO).
  `datacite_json` is DataCite's resolved record (publisher, related identifiers) or '' when the row has no DOI or
  DataCite could not be reached.
- `open_data.identifier` is the DOI (lower case, bare) when the record carries one, else `<portal slug>:<dataset id>`.
  It is UNIQUE: a second import of the same dataset is "already in the catalog", never a second row.
  `portals_json` lists every portal a collapsed dataset was found on (first = `portal_slug`).
- `open_data_portals.origin` is `hand` or `directory:<slug>`.

## Rollback

Additive and unreferenced: `DROP TABLE open_data, open_data_portals, portal_directories;` removes the slice's data and
nothing else. Work-list members of kind `open_data` would then point at no row (the list keeps living, as for any
deleted resource).

## Verification

- `tests/test_open_data.py::test_the_three_tables_exist_on_a_fresh_registry`
- the whole `tests/test_open_data.py` runs on a fresh SQLite registry (no live network, no Egeria, no Postgres).

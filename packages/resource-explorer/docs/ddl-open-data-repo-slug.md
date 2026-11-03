# DDL record: `open_data.repo_slug` and the `repository` pseudo-portal (open data from repositories)

**Status:** specified by the Architect's brief (BRIEF-OPEN-DATA-REPO-DATASETS-AND-ZENODO-2026-11-02, "DDL"); to be applied by
RE's schema init (`ProjectRegistry._migrate_open_data_tables`) at the next owner-approved 8813 restart after this branch
merges. Merging alone applies nothing.

## The change

```sql
ALTER TABLE open_data ADD COLUMN IF NOT EXISTS repo_slug TEXT DEFAULT '';   -- Postgres
ALTER TABLE open_data ADD COLUMN repo_slug TEXT DEFAULT '';                 -- SQLite (no IF NOT EXISTS; one writer)
```

Additive and nullable. The default is a constant, so Postgres rewrites no row. The migration first reads the table's columns
and runs the statement only when `repo_slug` is absent; on Postgres it sets a bounded `lock_timeout` (the registry's
`_DDL_LOCK_TIMEOUT`) so a table another process holds fails fast. Fresh databases get the column from the
`CREATE TABLE IF NOT EXISTS open_data` text in `open_data/schema.py`.

What the column holds: the registered repository's slug for a dataset imported from it (`POST /api/open-data/import-from-repository`);
`''` or NULL for every other row ("not imported from a repository"). No foreign key and no index.

## The reserved pseudo-portal row (data, not DDL)

After the statements, the migration inserts one row into `open_data_portals` when none has slug `repository`:
`slug 'repository'`, `api_kind 'repository'`, `base_url 'repository://'`, `origin 'system'`, no adapter, no cost shape.
Datasets imported from a repository carry `portal_slug 'repository'`. It is not a portal: `GET /api/open-data/portals`
and the Suggested portals list do not show it, a search or price request against it is refused with a sentence, and the
scout/profile/refresh steps report it as "not a portal: nothing to re-read".

## Rollback

`ALTER TABLE open_data DROP COLUMN repo_slug;` and `DELETE FROM open_data_portals WHERE slug = 'repository';` (after
deleting or re-pointing any `open_data` row filed under it). Rolling back the code without these is safe: the column is
ignored by older code, and the row is an ordinary-looking portal row older code would list as "no adapter yet".

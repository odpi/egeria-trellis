# DDL record (APPROVED): the data request on an investigation (OD2 brief Part B4)

**Status:** APPROVED (owner, 2026-10-30). Not yet applied. **Decision (project owner, 2026-10-30):** "approve the table"
(the new table, as recommended below, not the column alternative). It is applied by RE's schema init
(`ProjectRegistry._migrate_open_data_tables`, statement in `open_data/schema.py`) at the NEXT owner-approved 8813
restart after this branch merges; merging alone applies nothing. Until then the code treats a missing table as
"no data request stated" and refuses a save with a sentence.
Design: `design-notes/DESIGN-OPEN-DATA-SEARCH-AND-SURVEY.md` §1 (step 1, the data request) and §2; the Architect's
walk of 2026-10-29, §2 row 1 and §4.2 (the "Data request" band).

## Why a table and not a column

An investigation has no place today for a person's statement of the data they need (no lens, no request fields). The
change needed is one record per investigation, with six dimensions, edited as a unit. A new table, keyed by the
investigation's slug, follows the OD1 precedent (`ddl-open-data-od1.md`): `CREATE TABLE IF NOT EXISTS` takes no lock on
`investigations`, which every request path reads, and alters nothing a running process uses. Adding columns to
`investigations` would be six `ALTER TABLE ... ADD COLUMN` statements on a hot table; this is the smaller blast radius.
The alternative, if the owner prefers it, is one nullable `data_request_json TEXT` column on `investigations`
(see "Alternative" below).

## The exact DDL

```sql
CREATE TABLE IF NOT EXISTS investigation_data_requests (
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
);
```

SQLite runs the same text. No foreign key, no index beyond the primary key, no existing table altered.

## Nullable or not

Every column is `NOT NULL` with a default; none is nullable. "Not stated" is never a NULL or a bare empty string
standing in for a measurement:

- `subjects_json`, `places_json`, `formats_json`: a JSON list; `[]` means the person has not stated any (the screen
  reads "not stated").
- `period`, `grain`: text; `''` means not stated. A stated period is text such as `2019-2023`.
- `need_commercial`: `''` (no need stated) or `'1'` (commercial use needed).
- `need_share_alike`: `''` (no need stated) or `'0'` (no share-alike licences). These are the two licence needs the
  search form already offers, so a request feeds the search unchanged.
- `updated_by` is the signed-in person, `updated_at` is UTC ISO. Both are always written together with the row.

## What old rows read

Every existing investigation has NO row in this table. That reads as "no data request stated" (all six dimensions
"not stated"). Nothing is backfilled and no existing row is touched; an investigation gets a row the first time a
person saves a request on it. Deleting an investigation does not cascade here (no FK); its request row is removed by
the same code that deletes the investigation, in the slice that wires this table.

## Reads and writes (when approved and applied)

- Read: `SELECT ... WHERE investigation_slug = ?`; no row means all six "not stated".
- Write: one upsert per Save, by the signed-in person. No Egeria call, no external call.
- Used by: the "Data request" band on the investigation page, and the lens-terms route
  (`POST /api/open-data/lens-terms`) so a search started from the band carries the request.

## Rollback

`DROP TABLE investigation_data_requests;` loses only the requests typed since it was created; no other table
references it, and code that finds the table absent reads every investigation as "no data request stated". Rolling
back the code without dropping the table is also safe: the table is then ignored.

## Alternative (if the owner prefers one column)

```sql
ALTER TABLE investigations ADD COLUMN data_request_json TEXT;
```

Nullable (NULL on every old row = not stated). One statement, but on a table every request path reads, and an
`ALTER TABLE` takes a short exclusive lock on it. Recommendation: the new table.

## What the code does about it

The statement is the fourth entry of `open_data/schema.py` `STATEMENTS`. `OpenDataStore.get_data_request` reads a
missing table or a missing row as `None` ("no data request stated"); `save_data_request` on a missing table raises
`DataRequestTableMissing` and the route answers 409 with "The data request cannot be stored yet: its table is applied
at the next owner-approved restart." `GET /api/open-data/investigations/{slug}/data-request` reports `stored: false`
while the table is absent. The band on the investigation page, `lens-terms` and the winnow read it.

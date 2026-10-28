# Find databases dialog, slice 1: implemented (2026-10-01)

Slice 1 of REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md (drawing:
wireframes/FindAndImport.dc.html). Branch `re/find-databases-dialog`, off
origin/main aa7169da.

Resolved checkout for every test run:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-find-databases/packages/resource-explorer/resource_explorer/__init__.py`
(`uv run python -c "import resource_explorer; print(resource_explorer.__file__)"`).

## What each tab does

The sidebar's Find action, with the Databases kind selected, opens one dialog
titled "Find databases" with three tabs. Every tab that produces candidates ends
in the same candidate table and the same confirm.

**Saved sources.** The registered database servers, each a row with Run (or Run
again), Test and Remove, plus "+ Register a server" (registering a server is
configuration, so it lives here). A row shows `run MM-DD HH:MM · n found` or
"never run". A server with no stored credential shows "no credentials stored"
and its Run is disabled (it never calls the server). Run is
`POST /api/db-servers/{slug}/run`: it discovers on the server, compares with the
source's stored previous candidate set, and stores this run's set. The line above
the table reads "First run of X: n found. Nothing to compare with yet.", then
"n new since MM-DD" (another day) or "n new since HH:MM" (same day, so an
immediate re-run reads "0 new since 14:05"). A first run never says "new": there
is no baseline, and `new_count` is null, not 0. A failed run stores nothing, so
the previous run stays the baseline. A refused (401) load of the sources is a
message plus "Try again", never "No saved sources yet".

**Discover on a server.** Host, port, username, password; Discover calls
`POST /api/db-servers/_discover-inline` (nothing registered, nothing stored). The
password is held in memory for the dialog, set on the input as a property (never
an attribute), sent for that one connection, and dropped when the server is
saved. "Save as a source" (slug and display name) is the ordinary registration
call; until it is done the confirm is disabled with "Save this server as a source
above to register databases from it", because registering a database needs a
registered server.

**From a file.** One sentence: "Loading databases from a file is coming in the
next slice." No table, no confirm. (Slice 2 builds the CSV.)

**Candidate table (shared).** Columns: Database (with its server beneath, and a
`new` mark on a database that appeared since the last run), Size, Owner role,
Description, Connect with this credential, Prior verdict, "activity · after
registration". Row states:

| state | what shows |
|---|---|
| connectable | size, owner, description, "yes", checkbox |
| cannot CONNECT | listed, not filtered: "? not readable with this credential" for size, "? can't connect with this credential" for the mark, checkbox disabled and unchecked, not dimmed; a note says it stays a candidate until a credential that can connect is given |
| size not read | "? not readable with this credential" (never 0) |
| owner not read | "not read" |
| description measured empty | "none set" |
| description never returned (null) | "not reported by this source" |
| already registered | dimmed (opacity-50), pre-checked, disabled, "already registered" |
| prior verdict | the disposition and reason, keyed by `resource_key('database','host:port/name')`; for an already-registered row, the verdict under its slug; none decided reads "undecided" |

**Shared confirm.** Group select (defaults to the source's group), Investigation
select (defaults to the sidebar's investigation; closed investigations are not
offered), and one button: "Add these N to <investigation>" when an investigation
is chosen, otherwise "Register these N". It registers each selected database from
its saved source (`POST /api/db-servers/{slug}/add-database`, a local registry
write), applies the group (`POST /api/projects/{slug}/group`) and, if an
investigation is chosen, adds it to that investigation's scope
(`POST /api/investigations/{inv}/members`, entity_type `database`, rationale
"Found by <source> on <date>"). Partial failures are listed, not counted away; a
401 says "sign in". Nothing is written to Egeria.

**Scope API, not work list.** The investigation scope API exists
(`web/routes/investigations.py` `add_member`, `ProjectRegistry.add_working_set_member`,
idempotent upsert on `(working_set, entity_type, entity_slug)`), so the confirm
uses it. The work-list API (`/api/work-lists/`) was read and not used: a work list
is a homogeneous, named set of already-registered slugs, which is a different act
from putting a resource in an investigation's scope.

## list_databases() and the response shape

`PostgreSQLConnection.list_databases()` no longer filters on CONNECT. It returns
every non-template database with `can_connect` (the measured
`has_database_privilege(..., 'CONNECT')`). `size_pretty` / `size_bytes` are null
when the credential cannot read them (the SQL guards `pg_database_size()` with a
CASE on CONNECT or `pg_read_all_stats` membership, since it raises otherwise);
`owner` and `encoding` are null only if the row carries none; `description` is
`''` when the server was asked and none is set (`shobj_description` is world
readable, so empty is a measurement). `DiscoveredDatabase` gains `key`, `address`,
`server_slug`, `can_connect`, `registered_slug`, `verdict`, `is_new` and its
`size_*`, `owner`, `description`, `encoding` become nullable. `database_count` on
the Test routes now counts every listed database; `connectable_count` is added.
Registered detection is by `resource_key` across all registered databases, not
only the server's own.

## Migration (first start migrates the shared registry: needs a peer check)

Additive and nullable, idempotent (the column list is read first, in the same
transaction, as `_add_author_column` does), run from the `db_servers` section of
`ProjectRegistry._init_schema`. The SQL is identical on both dialects:

```sql
-- SQLite (column list read with PRAGMA table_info(db_servers))
ALTER TABLE db_servers ADD COLUMN last_run_at TEXT DEFAULT NULL;
ALTER TABLE db_servers ADD COLUMN last_run_candidates TEXT DEFAULT NULL;

-- Postgres (column list read with
--   SELECT column_name FROM information_schema.columns WHERE table_name = 'db_servers')
ALTER TABLE db_servers ADD COLUMN last_run_at TEXT DEFAULT NULL;
ALTER TABLE db_servers ADD COLUMN last_run_candidates TEXT DEFAULT NULL;
```

`ADD COLUMN ... DEFAULT NULL` is metadata-only on Postgres (no rewrite). NULL
means "never run"; a stored `'[]'` means "ran and found nothing".
`last_run_candidates` is a JSON array of resource_keys, replaced on every
successful Run. **The first process that opens the shared Postgres registry after
this merges runs these two ALTERs. Ask every live peer session before restarting
the shared server on this build** (coordinate-shared-writes). The Postgres SQL was
checked against a fake connection, never a real server.

## Admin rename

"Discovery Sources" is now "Repository discovery sources" in
`next/admin/index.js` (the tab label and its comment), `next/admin/discovery_sources.js`
(the pane heading), and Classic's `index.html` (tab label, pane h1, and the
pointer text in the Scouting view). No existing test pinned the old label; the new
pins are in `tests/test_find_databases_dialog.py` and the harness.

## Deviations from the designer's reply

1. **Non-connectable rows are not registrable in slice 1.** The reply says
   they are "importable as a candidate that needs a different credential". There
   is no per-row credential entry in the dialog yet and `add-database` copies the
   server's own credential, so registering one would create a database that
   cannot be surveyed. They are listed, marked and unchecked, with the wireframe's
   sentence. The per-row "choose a credential" is deferred to slice 2 with the CSV
   `server` / `connection_ref` column.
2. **Server registration stays in the dialog.** The reply (§5) says Admin keeps
   "server registration with its stored credential". Admin has no database pane
   today, and the old Find dialog already had Register/Test/Remove, so they stay on
   the Saved sources tab rather than moving to a pane that does not exist. Moving
   them is a separate decision.
3. **Prior verdict is displayed, not set, in this dialog.** The reply keys the
   verdict by address so "ignored" is remembered across runs; the key and the read
   are built and tested, and the existing entity-generic route
   (`POST /api/discovery/disposition/database/{resource_key}`) writes it. A control
   to set it from a candidate row (the repo dialog's emoji buttons) is not in this
   slice.
4. **A saved source stores the set of candidate keys, not the candidate rows.**
   Enough for "n new since"; "which ones were new" is recomputed against the
   stored keys on the next Run only, so the previous run's *new* marks are not
   re-displayable later.
5. **One-off with no save cannot register.** Registration needs a registered
   server (`add-database` is keyed on one), so the one-off tab asks for "Save as a
   source" first. The reply does not say how a one-off confirm should register.

## Deferred

- Per-row credential for a non-connectable candidate (slice 2).
- Setting a verdict from a candidate row; reconciling a verdict set under a
  candidate key onto the slug at registration (as `_reconcile_disposition_on_import`
  does for repos).
- Already-registered candidates cannot be added to an investigation's scope from
  here (they are excluded from N); "add the registered ones too" is not built.
- "Saved sources" for repos (still in Admin) and the file-system Find dialog
  (still the old-UI stub; its tab will be "From a file" only, per the owner).
- A `last_run_candidates` size cap: a server with thousands of databases stores
  thousands of keys in one TEXT cell.
- Timestamps are stored in UTC and shown in the browser's local time.

## Not verified

- No real browser render: the dialog is exercised only in jsdom, so layout,
  the 960px width, the sticky table header and the look of the "marked" row are
  unseen.
- No real Postgres server: `list_databases()` is tested against a stubbed
  `execute_query`, so the SQL itself (the CASE guard around `pg_database_size`,
  `pg_has_role(current_user, 'pg_read_all_stats', 'MEMBER')`, `regrole` cast) has
  not been run by any server, and the case it exists for (a role without CONNECT
  on another database) has not been seen. The Postgres migration was checked
  against a fake connection only.
- The first start against the shared registry, as above.
- The investigation scope write (`add_member`) was exercised only against a stub
  in the harness and by the existing route code, not end to end in this change.

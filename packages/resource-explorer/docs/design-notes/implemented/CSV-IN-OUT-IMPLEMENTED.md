# CSV in and out, slice 2: implemented (2026-10-03)

Slice 2 of REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md (sections 3, 4, 5 and
"Slices" item 2). Databases first; file systems are not built here (owner ruling).
Branch `re/csv-in-out`, off origin/main b31530f7.

Resolved checkout for every test run:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-csv-in-out/packages/resource-explorer/resource_explorer/__init__.py`
(`uv run python -c "import resource_explorer; print(resource_explorer.__file__)"`).

## The column contract (`batch_io.py`)

Intent columns (read on import), then `status_` columns (written on export, never
read):

| intent | meaning |
|---|---|
| `resource_type` | `repo`, `database`, `filesystem` (required) |
| `address` | the natural key (required); a database is `host:port/name` |
| `display_name`, `group`, `subpath`, `disposition`, `disposition_reason`, `notes` | as before |
| **`server`** (new) | databases: the slug of a REGISTERED db server. The credential comes from that server |
| **`connection_ref`** (new) | databases: a reference NAME for a secrets entry. A plain name only (`[A-Za-z0-9][A-Za-z0-9_.-]*`); anything with `@`, `:`, `/`, `=` or a space is refused as "looks like a connection string or a value" |

`status_registered, status_slug, status_cataloged, status_egeria_link,
status_last_surveyed_at, status_last_published_at, status_lifecycle,
status_indexed` as before, plus two new words columns: **`status_in_scope`**
(`in scope` / `not in scope` / `no investigation named`) and **`status_fit`** (see
deviation 2).

### Credentials never appear in a CSV, in or out

Enforced by shape, in three places:

* Nothing reads one. `_build_row` copies only `INTENT_COLUMNS` out of a record; a
  column whose NAME looks like a credential (`passw|pwd|passphrase|secret|
  credential|token|api key|private key|connection string|dsn`, `connection_ref`
  excepted by exact name) is named in the preview, once, and its cells are never
  looked at. The preview payload and the import result are tested not to contain
  the cell value.
* Nothing writes one. `assert_no_credential_columns(ALL_COLUMNS)` runs at import
  time and again inside `write_csv` and `rows_to_csv_text`; adding a
  credential-named column to the contract makes the module fail to load. Exports
  carry `server` (the slug) and `connection_ref` (the name), never `db_user` or
  `db_password`; a test registers a database with a stored password and asserts it
  is in no export.
* The browser does not even send one. `next/csv-guard.js` blanks the cells under a
  credential-like header before the file text is kept or posted (header kept, so
  the server still names the column; line numbers do not move; embedded newlines
  and quoting survive). A test pins its pattern to the Python one and asserts no
  request body contains the planted cell.

## The preview (`POST /api/discovery/from-file/preview`)

`batch_io.preview_file` = `plan_import`'s five counts, each opening its lines:

> **9 rows** · 3 new (⚠ 1 need a person) · 1 already registered · 1 duplicate in
> the file · 2 invalid · 2 of a kind not importable here

* **new**: importable (a registered `server`, or a `connection_ref`) plus
  **needs a person**: a database row naming neither is a candidate "⚠ needs a
  person: name a server or a credential", has no checkbox, and is not importable
  until a server is chosen in the preview (a select, same-host servers marked;
  the choice re-plans on the server via `server_choices`).
* **already registered**, by `resource_key` (so a re-import is idempotent: the
  same file again gives 0 new). Intent on such a row that differs from the
  registry (`group`, `disposition`) is listed as a **proposed change** ("1 row
  would change group"), unticked, applied only if ticked at the confirm; a
  proposed group that does not exist is shown but blocked.
* **duplicate in the file**, **invalid** (each with its file line number and
  reason: no address, unknown type or disposition, not `host:port/name`, server
  not registered, address not on that server, unknown group, slug collision with a
  different database, `connection_ref` that is not a name).
* **of a kind not importable here**: file system rows (no import path), repo rows
  (they load through Find repos' own "load from file"), counted with line numbers
  and a reason, not dropped.
* Columns: unknown ones are accepted and named once ("Ignored columns: region");
  `status_` ones ignored and said once ("1 status_ column ignored: those are
  written by RE, never read"); credential-like ones named once (above).
* **Refused**: a missing `resource_type` or `address` column refuses the file
  (HTTP 200, `refused` set), naming the missing column(s) and showing the header
  row found. An empty file is refused too. Line numbers are the file's own
  physical lines (comment and blank lines count).

## The confirm (`POST /api/discovery/from-file/import`)

The browser sends the file text again plus the ticked `lines`, `server_choices`,
`group`, `investigation`, `accept_changes`; the server re-plans from the text (no
rows travel). New database rows are registered (credential copied from their
server by `build_database_entity`, the one builder now shared with the Find
dialog's add-database route; or, with only a `connection_ref`, registered with the
reference recorded and no stored credential), put in the group (row cell, else
the dialog's), given their disposition, and, when an investigation is chosen,
added to its scope. **Already-registered rows are added to the investigation's
scope too** (that is what makes an exported scope load into a second
investigation); nothing else about them changes. Per-row failures are reported by
line, never counted away. Local registry writes only; nothing goes to Egeria and
nothing is read from it.

## The dialog (`next/db-server-discovery.js`)

"From a file" is now the real door: file chooser, the counts line (zero counts are
plain text, not buttons), the opened lines, the proposed-changes box, and the SAME
confirm bar the candidate table uses (`confirmBarHtml`, extracted so both tabs
render the identical Group / Investigation / "Add these N to <investigation>"
control; `Register these N` with no investigation; `Apply these N changes` when
only changes are ticked). N counts ticked importable rows plus, once an
investigation is chosen, ticked already-registered rows. After a confirm the
preview is re-run (so it reads 0 new), the sidebar refreshes, and an open
investigation page is refreshed through `refreshOpenInvestigation` (#429).

**The door on the investigation page.** The Members section of an investigation
now has a "＋ add…" control (there was none) offering "from a file", which opens
this dialog on the From-a-file tab with that investigation preset
(`openFindDbServersDialog({tab:'file', investigation})`; imported on use because
the dialog imports the investigation module back).

## Exports and filenames

`re-<what>-<name>-<date>.csv`, built by `batch_io.export_filename` and sent as the
`Content-Disposition` of every export:

| export | where | filename |
|---|---|---|
| a source's candidates | `POST /api/discovery/candidates.csv` (the rows the table shows) via "Export CSV" in the candidate table | `re-candidates-regional-pg-2026-10-03.csv` |
| an investigation's scope | `GET /api/investigations/{slug}/scope.csv` via "Export CSV" beside "＋ add…" | `re-scope-<slug>-2026-10-03.csv` |
| a work list | `GET /api/work-lists/{slug}/export.csv` via "export CSV" in the work-list actions | `re-work-list-<name>-2026-10-03.csv` |
| the inventory | `GET /api/discovery/inventory.csv` (existing) | `re-inventory-2026-10-03.csv` |

Downloads are fetched (bearer auth rides the patched fetch; a plain link would not
carry it) and handed to the browser by `next/download.js`.

The scope export writes **in-scope members only**, so loading it into another
investigation can never promote a candidate or excluded member to in scope. A
member whose resource is no longer registered is written with no address, so
re-importing names it as invalid by line instead of dropping it.

**Round trip.** Exporting a scope and importing it into a second investigation
adds the resources (as `in-scope`), carries no fit (`status_fit` is read by no
code; the member rows hold no fit or lens text; tested), and loading it into the
same investigation again changes nothing. Export-then-import of the whole
inventory is a no-op (0 new, no proposed changes).

## What is refused, in one place

* a file without `resource_type` or `address` columns, or empty;
* per row (kept in the preview as invalid, never written): see the invalid list
  above;
* a `connection_ref` that is not a plain name;
* import into an investigation or group that does not exist (HTTP 400);
* a credential in any form: column ignored, cells never read or sent.

## Deviations from the designer's reply

1. **`connection_ref` alone registers a database that cannot be surveyed yet.**
   The reply says a row naming a server "or `connection_ref`, naming a secrets
   entry" is importable. No resolver from a `connection_ref` name to a credential
   exists in this build (the field exists on `DatabaseEntity`, nothing consumes
   it for connecting), so such a row is registered with the name recorded and no
   stored credential (`credential_status` none). The preview says
   "reference <name>", not "credential found".
2. **`status_fit` cannot carry the fit headline yet.** The headline needs a data
   lens bound to the investigation; no lens exists on main (the work-lists + lens
   branch is unmerged). The column says `no data lens applied` for scope and
   work-list members and `no investigation named` elsewhere, never a number.
   `batch_io.fit_headline` is the one place the real headline lands.
3. **"Five counts" plus a sixth fact.** Needs-a-person rows are inside "new"
   (they are candidates), shown as "(⚠ k need a person)", not a sixth count.
4. **Repo rows are "not importable here"** in the databases dialog's preview
   (their import path is Find repos' own load from file, which is unchanged). The
   command line still registers repos only (`plan_import(..., importable=("repo",))`).
5. **Proposed changes are unticked by default** and shown with the confirm; the
   reply says "applied only on confirm", this makes the confirm opt-in per change.
6. **A database slug quirk fixed (slice 1 defect).** The registry stores slugs
   with `_` for `-`; `add-database` returned the dashed slug it was asked for, so
   the dialog put `regional-pg-x` in an investigation's scope while the database
   is `regional_pg_x`. The route now returns the stored slug (test pinned), and
   the CSV import uses the stored slug throughout.
7. **Candidate export is the dialog's table, not a stored set** (a POST of the
   rows on screen); facts that are not part of the column contract (size, owner,
   CONNECT) are not exported.
8. `ImportRow`/`ImportPlan` gain `server`, `connection_ref`, `needs_person` and
   `proposed_changes`; `plan_import` takes `importable`, `server_choices` and
   `not_importable_note`. `INTENT_COLUMNS` gained two entries, so the inventory
   CSV is two columns wider (still every column every time).

## Deferred

* **File-system import** (owner: databases first) and any file-system lister.
* **Per-row credentials** and a "choose a credential" control for a database that
  cannot CONNECT (slice 1 deferred it to this slice; still only `server` /
  `connection_ref`, per the credentials ruling).
* **The fit headline** in `status_fit` (needs the lens).
* A stored "which candidates were new" for a saved source, unchanged.
* Spreadsheet formula injection: cell values beginning `=`, `+`, `-`, `@` are
  exported as they are (as the inventory always did).

## Evidence

* Python: `tests/test_csv_in_out.py` (new, 53 tests, red on main by ImportError at
  collection since `NO_INVESTIGATION` etc. do not exist there; green with the
  change), known-negatives included (zero-count, no investigation, excluded
  members not exported, the command line still repo-only, credentials in no
  export). Every test file begins with an autouse fixture that points the
  environment at a temp SQLite file and `PGVECTOR_PORT=1`, and the registry fixture
  asserts a `sqlite:///` URL under `tmp_path`.
* Harness: `frontend-build/test-harness/csv-in-out.test.mjs` (14 tests; the preview
  payloads are `csv-in-out.fixture.json`, generated from `preview_file`, not
  hand-written). With the four edited front-end files reverted (new files left in
  place): 12 fail, 2 pass (the guard module test and the sidebar known-negative);
  with the change 14 / 14. `find-databases-dialog.test.mjs`'s "From a file is one
  sentence" test was replaced by "the real door" (the sentence is gone by design).
* Full harness on Node 20.11.0: 288 / 288. pytest over test_csv_in_out.py,
  test_batch_io.py, test_find_databases_dialog.py, test_next_*.py (50 files),
  test_tailwind_next_class_coverage.py, test_tailwind_next_freshness.py,
  test_static_js_syntax.py, plus test_resource_types_vocabulary.py and
  test_import_batch_resilience.py: 845 + 23 passed. `npm run build:css:next`
  produces an unchanged `tailwind-next.css` (no new classes).

## Not verified

* **No real browser and no real file picker.** The file is chosen in jsdom by
  setting `input.files`; the picker, `File.text()` on a real `File`, the Blob
  download and the dialog layout (the lines panel, the proposed-changes box, the
  sticky header) have never been seen on a screen.
* No real Postgres, no real database server, no Egeria, no Prefect: every registry
  was a temp SQLite file and no test connects to anything. The import was never
  run against the shared registry.
* `connection_ref` was never resolved to a credential (none can be).
* The `＋ add…` menu and the Export CSV buttons have not been seen in a browser.
* Large files: the whole text is posted twice (preview, import) and parsed in
  Python each time; not measured.
* A pre-existing oddity seen and not touched: `addMemberFormHtml()` in
  `stages/investigation.js` renders the entity-type options twice in its select.

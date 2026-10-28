# Curate: what gets catalogued, slice A (implemented)

2026-10-04, branch `re/curate-scope-a`, built on `origin/main` 45f4671b. Slice A of the
catalogue-scope work: everything up to the commit button, with **no Egeria write of any kind**.
Slice B (the commit that compiles the scope into Egeria's cataloguer lists, and retires Classic's
Publish button for databases) is not started.

Environment proof: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"`
printed `/Users/dwolfson/localGit/egeria-v6/trellis-re-scope-a/packages/resource-explorer/resource_explorer/__init__.py`,
a path inside the builder's own worktree.

**Decision (project owner, 2026-10-04):** the scope is a declared, signed, dated choice stored in RE,
not in Egeria. A commit compiles it into Egeria's lists every time, so a reset of Egeria does not
lose it. Slice A stores it and draws it; it compiles nothing.

## What changed

| File | Change |
|---|---|
| `resource_explorer/catalogue_scope.py` (new) | the pure logic and the registry-backed reads and writes: the merged scope view, the three proposal rules, the four observation states, inheritance, baseline and "new since", the name-conflict check, depth |
| `resource_explorer/web/routes/catalogue_scope.py` (new), `web/app.py` | `/api/catalogue-scope/{slug}` routes (below); every write resolves the author from the session and answers 401 when signed out |
| `resource_explorer/registry.py` | two additive tables and four append/list methods; `remove_database` also clears them |
| `web/static/next/stages/curate-scope.js` (new) | the section: header, depth line, tree, row states, conflict and new-since lines |
| `web/static/next/stages/curate-bands.js`, `curate.js`, `web/static/re-api.js` | the scope is the first section of a database's band 2; the two waiting sections' sentences gain "and the tables to be catalogued first (above)"; API wrappers |
| `web/static/next/tailwind-next.css` | regenerated on Node 20.11 (literal classes only; the dynamic-class tripwire is untouched) |
| `tests/test_catalogue_scope.py`, `frontend-build/test-harness/curate-catalogue-scope.test.mjs` (new) | 42 Python and 21 harness tests |

`curate_plan.py`, `curate_commit.py`, `catalogue_depth_offer.py` and Classic's database Publish are untouched.

## Routes

| Route | Does |
|---|---|
| `GET /api/catalogue-scope/{slug}` | the scope merged with the Schema Inventory tree (open when signed out) |
| `PUT .../depth` | store the depth (one of four) |
| `PUT .../node` | set a schema or table to `catalogue` or `leave_out`; if a rule is proposing for that node right now the event records the same choice as a confirmation and the opposite as an override |
| `POST .../node/confirm`, `.../node/override`, `.../node/clear` | confirm or override the live proposal (409 when none), clear a choice (409 when none) |
| `POST .../redeclare` | re-baseline: "new since" counts from now |
| `POST .../resolve` | resolve one name conflict with the same explicit choice on every decided table of that name |
| `GET .../conflicts`, `.../new-since`, `.../history` | `scope_conflicts()`, `new_since_declared()` and the full event and declaration history, for slice B |

## Tables (shared registry state; additive, idempotent, SQLite and Postgres)

Both are `CREATE TABLE IF NOT EXISTS` inside `_init_schema`, with `INTEGER PRIMARY KEY AUTOINCREMENT`
(translated to `SERIAL` on Postgres by the existing translator), no foreign keys, append-only.

`catalogue_scope_events` (id, database_slug, node_kind, schema_name, table_name, choice, action,
source, proposal_rule, proposal_choice, reason, measured_at, measured_json, author, changed_at).
Index `idx_catalogue_scope_events_node` on (database_slug, node_kind, schema_name, table_name, id).
`node_kind` is `schema`, `table` or `depth`. The current choice of a node is its newest row; a cleared
choice is a row with `choice = ''`. `source` is `person` or the id of the rule that was confirmed;
the proposal's rule, choice, reason and the measurement under it stay on the row so an overridden
proposal can be shown struck through and a later survey can be compared with it.

`catalogue_scope_baselines` (id, database_slug, kind, baseline_json, survey_at, declared_by,
declared_at). Index `idx_catalogue_scope_baselines_slug` on (database_slug, id). One row at the first
declaration (any first choice, confirmation or depth) and one per re-declaration; the newest is the
baseline "new since" is measured against. `baseline_json` is `{"schemas": [...], "tables": ["schema.table", ...]}`
of the non-system nodes the latest survey knew at that moment.

## What each rule does

- Proposals (designer section 2, one function, `propose_for_node`): `empty_schema` (0 tables,
  measured, never no-access) proposes leave out; `no_writes` (a schema whose every table `db_change_rates`
  measured idle, or one idle table) proposes leave out with "no writes since 09-27 (survey of 10-02)";
  `data_lens_match` (a table name that matches the lens's subject terms) proposes catalogue. Two rules
  that disagree propose nothing and the row says so.
- Not proposals: PII data classes (a mark, "PII · 3 columns"), a staging-looking name (a note),
  no access ("? not established"), a verdict.
- States: proposed, confirmed, overridden, "survey now disagrees" (both values and the survey date; the
  choice does not move). A person's own choice with no proposal behind it reads as set by them.
- Undecided: a table inherits its schema's explicit choice ("catalogue (from schema)", muted; "differs
  from its schema" in ink); a schema with no choice "keeps what's in Egeria now"; an unconfirmed proposal
  is still undecided and counts for nothing.
- New since: nodes in the latest survey but not in the baseline and not since decided. `N new schemas
  (M tables) not in your scope`, plus new tables in schemas already known, reported separately.
- Conflicts: across effective (explicit or inherited) choices, a table name that is `catalogue` in one
  schema and `leave_out` in another. Both rows are marked; `scope_conflicts()` returns `{count, names, pairs}`.
- Depth (stored, changes only what the tree shows): the database only, schemas, schemas and tables,
  tables and columns; column rows appear only at the last. A node's provenance line names the level the
  depth excludes ("tables excluded via include list").

## Verification

- Python: `tests/test_catalogue_scope.py`, 42 tests, temp SQLite (the fixture asserts the registry URL is
  the temp file). Guards made to fail on purpose and watched failing: PII proposing, a staging name proposing,
  no-access proposing, a verdict proposing, an unconfirmed proposal counting as a choice, the baseline never
  stored, conflicts ignoring inheritance.
- Frontend: Node 20.11, `node --test test-harness/*.test.mjs`, 407 of 407 (386 before; 21 new, all red on
  the old code). Two guards failed on purpose: proposal controls left enabled when signed out, and a status
  written from the click instead of the re-read.
- `tests/test_tailwind_next_*`: pass after regenerating the CSS. The source-reading sweep: see the report.

## Interpretations and gaps (read before slice B)

1. **No stored data lens exists on this build**, so `data_lens_for()` returns None and the lens proposal
   rule is live in code and tests (through the `lens` argument) but fires on no real database yet.
2. **Data classes have no local store** (`data_class_match` results are not kept), so the data classes
   column reads "not established" and the PII mark is wired and tested but empty on real data.
3. **Default depth** is "schemas and tables" until a person chooses (the wireframe's selected radio, "tables").
4. **Database-only depth** is worded "no catalog target, nothing below the database is catalogued" (the
   architect's ruling) although the lever findings say it can also be expressed as two sentinel include lists.
5. **Header numerator and denominator**: undeclared, the numbers are Egeria's native survey row
   (`schema_count`, `table_count` of the newest `source = egeria` row); declared, "7 of 29" is catalogued
   schemas of the schemas the tree offers (non-system), so the denominator can be checked on screen.
6. **Conflict count** is the number of conflicting table names; undecided versus decided is not a conflict.
7. The classification "no tables" is read from the tree; a schema with no tables appears there only when the
   credential probe knows it, which is the existing Schema Inventory behaviour.

## Left for slice B

The commit (a curation record: RE publish, the catalog target with the compiled lists, the native survey, the
manifest of the difference), per-node proof rows from read-back, the disabled Catalogue button with the conflict
count as its reason, the "will be removed from Egeria" preview, retiring Classic's database Publish, "catalogue
scope" on the investigation's scope grid, and the architect's open questions (ownership and zone on
cataloguer-created elements, whether the cataloguer adopts RE's database asset).

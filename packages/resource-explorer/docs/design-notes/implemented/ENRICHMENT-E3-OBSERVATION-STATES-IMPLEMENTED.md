# Enrichment E3 — implemented

Authoritative gate and slice: `BRIEF-ENRICHMENT-E3-OBSERVATION-STATES.md`
(branch `re/brief-enrichment-e3-observation-states`, commit 263c7ee0). Not
restated here; this note records what was built, the rulings that shaped it,
and what the gate still needs a person for.

## Environment provenance

- Worktree: `/Users/dwolfson/localGit/egeria-v6/trellis-re-enrichment-e3`, branch `re/enrichment-e3-observation-states`, off origin/main 5f152c74.
- `uv sync --all-packages --extra dev` run in the worktree.
- `python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
  `/Users/dwolfson/localGit/egeria-v6/trellis-re-enrichment-e3/packages/resource-explorer/resource_explorer/__init__.py`
  (inside this worktree, not main's checkout), before any test run.
- Signed commit check: a `git commit -s -S` test commit verified `G`; every commit since is `-s -S`.
- Harness runs use Node 20 (`nvm use 20`); the default node 18 fails all harness tests at load.
- Nothing here touched the shared Postgres registry or live Egeria. Every Python test uses a temp SQLite registry or fakes.

## What was built

1. **Evidence fetch by kind** (`stages/enrichment.js` `fetchEnrichmentEvidence`).
   Passes `apiEntityType(state.resourceType)` and intersects `ENRICHMENT_EVIDENCE`
   with `listAnalyses(kind)` (the catalog's `resource_types`). Nothing inapplicable
   is requested or drawn. All 8 evidence analyses are repo-only today, so a database
   and a filesystem rail read "no enrichment evidence is catalogued for databases yet"
   (one line, `data-rail-empty`). If the catalog itself cannot be read the rail says so
   rather than guessing. `preliminary_fit` (the lens read) was left alone.
2. **Four observation states, one pure function** (`next/observation-state.js`,
   `observationState({field, measurement})`), table-tested in
   `frontend-build/test-harness/observation-state-table.test.mjs` (21 cases).
   The disagreement rule compares the LATEST measurement with the STORED measured
   value, never with the person's value (owner's precision, 2026-09-30). Table rows
   cover: override + same re-measure stays overridden (no disagree); override +
   different re-measure disagrees; confirmed + same re-run unchanged; legacy rows;
   spelling-only differences; "cleared only by a person choosing".
3. **Server stamps the measurement** (`web/routes/context.py`): `EnrichmentField`
   gains `measured_value` / `measured_at`, stamped in `save_field` from the server's
   own fact layer for observations with a proposing pair (`PROPOSING_ANALYSES`,
   only `licence <- license_classification`, only where the catalog says the analysis
   applies). The client cannot assert them. Every choice (accept, override, "keep mine")
   re-stamps, which is the only thing that clears "⚠ review".
4. **Licence as an observation row, every kind** (`fieldRowHtml`): repositories draw
   the four states (proposed: muted, "proposed by license_classification · time", no
   person line, no check mark; confirmed: signed; overridden: signed with the measured
   value beside it; disagrees: "⚠ review — survey now measures X (was Y)" with
   "accept measured" / "keep mine"). Databases: "no survey measures this for databases",
   typed value becomes confirmed and signed at once. The old "from survey: … confirm"
   line is gone. Wording reuses the existing "⚠ review —" pattern and `text-state-warn`.
5. **Database owner as material on the owner judgement row**: "database owner role:
   NAME (measured)", never a button and never pre-filling the input. Before any survey:
   "database owner role: not measured yet · run a survey". Read from `schema_inventory`'s
   `value.database_owner`.
6. **`datdba` measurement** (Option (a), owner's ruling). `schema_inventory` is the
   step that actually connects (`db_fingerprint` reads stored rows, no connection), and
   the always-running schema step already holds the connection, so
   `PostgreSQLConnection.get_database_owner()` runs there, stored as the survey row's
   top-level `database_owner` (preserve-prior, non-fatal, engine-optional) and surfaced
   by its own reader (see "Follow-up" below). Existing databases read "not measured yet" until
   re-surveyed. **`db_server_profile` (queued in a future database-analysis batch) is
   the eventual consolidated home for server-level facts like this; this extension of
   the schema step is the minimal home for now, not the final architecture.**
7. **Scouting's licence question** (`app.js`): the row stays (still catalogued), but
   for databases it no longer shows ◌. With no Context value: "no survey measures this
   for databases · record it on Context ›" (glyph ⚠ needs you). With a value:
   "recorded on Enrichment › Context ›" (glyph ✓). The link routes to Enrichment › Context.
   Repositories are untouched. Other surfaces that call `rowState` directly (work-list
   digest, legend counts) still classify it as `no_reader`; flagged below.

## Tests

- Python: `tests/test_database_owner_measured.py` (5), `tests/test_enrichment_observation_stamp.py` (12).
- Node harness (routing level, real strip click -> `loadPane` -> `renderContext`):
  `enrichment-e3-observation-states.test.mjs` (12), `observation-state-table.test.mjs` (21);
  `context-parity-routing.test.mjs` updated (catalog stub, new "proposed by" wording).
  Harness total 103 passing.
- Revert-and-confirm, each restored afterwards:
  datdba in the schema step removed -> 3 owner tests fail; stamping removed -> 3 stamp
  tests fail; old unfiltered/repo-default fetch -> gate 1 fails; disagree compared against
  the person's value -> the override-same-re-measure routing test and its table row fail;
  Scouting override removed -> the Scouting test fails.

## Follow-up (design acceptance, 2026-09-30): two gaps fixed before serving

1. **One state, one word, everywhere.** `next/context-recorded.js` is the single
   mapping (question -> Context key -> `answered`/`human`). Every consumer reads it:
   the Questions row, the Questions KEY/legend counts and the markdown copy (through
   `effectiveRowState` in `app.js`), and the work list's grid cells, narrow list and
   digest (through `contextCellState`, reading each member's own Context once when the
   list opens, only when such a column exists; an unreadable member's cell says `?`).
   No surface says `◌ no reader yet` for the database licence question any more. Note:
   the work list's cells for this question were `unclassified` (empty `analysis_ids`),
   not `no_reader`, before this change; now they match the Questions pane.
   Harness test: "one state everywhere" (Context, KEY, work-list cells/digest).
2. **The owner is its own fact.** `database_owner` is registered as an analysis fact key
   (results map, headline, re-step map -> the schema step) with its own `measured_at`,
   and `schema_inventory` no longer carries it. The owner line fetches only
   `database_owner`; a `schema_inventory` fact with an empty tables list changes
   nothing. Test: a zero-table survey with a successful owner read is `measured`;
   never surveyed is `never_run` ("not measured yet"). It deliberately has no per-card
   Run (not in `DATABASE_SURVEYOR_STEP_MAP`): any survey produces it.
3. **Equal-value case** says "survey now agrees · time" on the material line; the
   case/whitespace normalisation is documented in `observation-state.js`.

Revert-verified: work-list wiring removed, KEY wiring removed, and owner read from
`schema_inventory` each fail a test, and pass when restored.

## Gate status

Gate 5, final wording: before the survey the owner line reads "not measured yet · run a
survey"; the project owner runs a survey on coco_pharma; the line then shows the measured
role as material, never as a proposal. Verified for both sides against stubs and a fake
connection; the real `pg_database` query
(`SELECT datdba::regrole::text ... WHERE datname = current_database()`) has not run against
a live server -- the owner's own survey is that proof. Gates 1-4 and 6 are covered by
routing-level tests; no live 8813 walk was done here.

## Flagged, not fixed

- `worklist.js` `loadGrid` calls `getQuestions(slug, {phase, perspectives})` without an
  `entityType`, so a database work list reads the repo question path. Pre-existing; the
  licence question's wording is cross-type, so this slice's behaviour is unaffected.
- `value`-less fact: `database_owner`'s fact has no `last_run_at` (live-read); its time is
  `value.measured_at`.

# Slice 21a — level-aware headlines — implemented

**Coordinator brief:** owner-approved next slice after the three-defect
follow-up (`re/followup-3-copy-fixes`, #TBD) — the first concrete piece of
slice 21.
**PR:** #TBD (`re/slice21a-level-headlines`).
**Live gate:** port 8812.

## The bug

`FactLayer._headline_for(analysis_id, slug)` had no notion of "level" at
all — every question backed by the same analysis rendered the identical
resource-level sentence, regardless of the question's own declared
`levels`. Found live, owner's question, 2026-09-26: "How big is this
database" (`levels: [resource, container]`) and "Which schemas carry the
data, and which are system, empty or staging?" (`levels: [container]`
alone) both rendered `schema_inventory`'s identical resource-level
sentence — "8 schema(s), 7 with tables..." — so the second question ticked
✓ on a line that names schemas but classifies none of them.

## Fix

### Backend: level-aware headline dispatch

- **`FactLayer._primary_level(question)`** (new, `facts.py`): the single
  level to build a question's facts/headline at. Rule: `"resource"` wins if
  present in `question["levels"]` (so multi-level questions like "How big"
  render the resource summary, unchanged); otherwise the first declared
  sub-level (container/member/field) wins; otherwise `"resource"` (every
  question authored before the Level column existed).
- **`FactLayer._headline_for(analysis_id, slug, level="resource")`**
  (rewritten): when `level != "resource"`, tries the NEW
  `analysis_container_headline_map` provider first, falling back to the
  ordinary resource reader (`analysis_headline_map`/`analysis_kinds()
  .results.headline_reader`) on any absence or failure — the "no
  regression" default the coordinator asked for. The ordinary 2-arg
  `_headline_for` behaviour, and its 3 OTHER call sites
  (`projects.py`'s dashboard tiles, `workflows/analysis.py`'s dashboard
  builder, `facts.py` itself for resource-level questions), are unchanged.
- **`ResourceTypeAdapter.analysis_container_headline_map`** (new field,
  `survey_definition_executor.py`): `() -> {analysis_id:
  container_level_headline_reader}`, a SEPARATE map from
  `analysis_headline_map` rather than a change to that map's value shape —
  `analysis_headline_map` is called as a plain 2-arg function at 3 existing
  call sites with no notion of level, and changing its shape would break
  all three. `None` (undeclared) means no resource type offers a
  level-aware reading for anything — the default for every type except
  database's `schema_inventory` today.
- **`FactLayer._check_level`** (stricter branch, sub-resource questions
  only): a fact's non-empty `.headline` only satisfies the gate when it
  came from a GENUINE container reader
  (`_level_specific_headline_exists(analysis_id, level)`), not
  `_headline_for`'s resource-fallback text — otherwise "no regression,
  fallback still renders" would incorrectly grant a ✓ to an analysis with
  no real per-level answer. The coordinator's own regression case: "a
  question at container level whose analysis has only a resource reader
  still renders the resource line but does NOT tick." When the question's
  primary level IS `"resource"` (even alongside declared sub-levels), the
  OLD, simpler rule applies unchanged (any non-empty headline satisfies
  it) — this preserves "How big"'s exact existing behaviour.

### `schema_inventory`'s container-level reader

**`_schema_inventory_container_rows(registry, slug)`** (new,
`survey_definition_adapter.py`) — the structured per-schema classification,
factored out so both of its two consumers (see below) and any future third
one (Backlog: slice 22's per-schema VIEW) read the exact same
classification rather than risking disagreement. Reuses existing,
previously-unwired infrastructure rather than reimplementing it:
`schema_scope.py`'s `container_scope_states()` (the credential-capability
probe's per-schema `SCOPE_READABLE`/`SCOPE_STRUCTURE_ONLY`/
`SCOPE_NOT_VISIBLE`/`SCOPE_EMPTY` states — built ahead of its own wiring
for slice 20's prep, now consumed for the first time here) and
`connection.py`'s `POSTGRES_CONTAINMENT.is_system_container()`.

Classification priority (design §18.4: "always broken down by containment
level — never a rollup without its parts; system schemas folded away"):

1. **system** (`pg_catalog`, `information_schema`, `pg_toast*`,
   `pg_temp*`) — folded to a trailing count, never named individually.
2. **no access** / **structure only** — from the credential-capability
   probe's per-schema scope state.
3. **staging** — name heuristic (`_STAGING_NAME_MARKERS = ("stg",
   "staging", "tmp", "temp", "scratch", "sandbox")`), explicitly marked "by
   name" since it's a guess, not a measurement.
4. **empty** — zero tables, or every table has a measured row count of
   exactly zero.
5. **data** — everything else, sorted by total rows descending.

Two consumers, both new:

- **`_schema_inventory_container_headline`** — the one-sentence rendering
  for the container-level question's headline: `"coco_ods 22 table(s) ·
  2,100 row(s) (est.); eu_sales 9 table(s) · 0 row(s) — empty; demo 4
  table(s) — no access · 2 system schema(s) folded"`.
- **`_schema_inventory_container_measurements`** (point 4, below) — one row
  per schema for the "numbers behind this" evidence table.

Both registered on new `DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP` /
`DATABASE_ANALYSIS_CONTAINER_RESULTS_MAP` dicts, wired into `_ADAPTER` via
`analysis_container_headline_map=lambda: ...` /
`analysis_container_results_map=lambda: ...`.

### Point 4: the evidence table becomes level-aware too

"The numbers behind this" (`build_measurements()` in `stage_page.py`) was
not level-aware at all — a container-level question's evidence panel
showed the exact same flat resource scalars a resource-level question's
did. Fixed the same way as the headline: a new
`ResourceTypeAdapter.analysis_container_results_map` field (`() ->
{analysis_id: container_level_results_reader}`), consulted by
`_build_measurements_via_results_reader` only when the caller's `level`
names a sub-resource level AND a reader is registered for that
`analysis_id` — falls through to the existing resource-level reader
otherwise (no reader registered, or `level="resource"`), unchanged for
every other analysis and both `entity_type`s that don't apply here.

Threaded end-to-end:

- `build_measurements(registry, slug, analysis_id, entity_type="repo",
  level="resource")` — new `level` parameter, default preserves every
  pre-existing caller's behaviour.
- `GET /api/projects/{slug}/analyses/{analysis_id}/measurements` gained a
  `level` query param (default `"resource"`).
- `re-api.js`'s `getMeasurements(slug, analysisId, entityType, level)` —
  new 4th parameter, appended to the query string.
- `app.js`: new `primaryQuestionLevel(entry)` helper, mirroring
  `FactLayer._primary_level`'s exact rule client-side from the question
  catalog entry's own `levels` array (already present on every entry via
  `QuestionCatalogEntry.to_dict()` — no new backend plumbing needed for
  this half). `provenanceLine`'s "the numbers behind this" button now
  carries `data-numbers-level="<primary level>"`; `toggleMeasurementsInPlace`
  passes it through to `getMeasurements`.

The evidence table's existing generic row shape (`{name, value, opens,
note}`) needed no frontend rendering change — a per-schema row's `name` is
the schema name, `value` is `"N table(s) · M row(s) (est.) · B bytes"`, and
`note` carries the classification (`"empty"`, `"no access"`, etc.),
rendered by the exact same `<span class="text-ink-muted">` the resource
scalar table already uses for `note`. A folded system-schema count is a
single trailing row (`name: "N system schema(s)"`, `value: "folded"`,
`note: "system"`) rather than one row apiece.

`bytes_total` is new to `_schema_inventory_container_rows` — the headline
sentence never rendered bytes (already long enough without it), but the
design's own spec for the per-schema breakdown named "table count, row
total ... bytes" as the full set, and the evidence table has room for it.

## A fourth defect, found live gating this branch: `levels` never reached the frontend at all

`app.js`'s `primaryQuestionLevel(entry)` was written on the assumption
(recorded before this live gate) that `entry.levels` was "very likely
already available client-side... via `QuestionCatalogEntry.to_dict()`."
True of `question_catalog_reader.get_questions()` itself, but the ONLY
route reaching the frontend — `GET /api/databases/{slug}/questions` →
`workflows/scouting.py::build_question_checklist` — builds its OWN,
separate per-question dict from those entries and never copied `levels`
across. Live gate on 8812, `coco_pharma`: fetching that route directly
showed every question missing `levels` entirely, so
`primaryQuestionLevel` always silently fell back to `"resource"` — the
container-level evidence table this slice built would never actually have
been reached through the real UI, despite every backend piece and every
unit test being correct in isolation.

**Fixed:** `build_question_checklist` now copies `e.get("levels") or
["resource"]` onto each question dict, same default
`question_catalog_reader`'s own `to_dict()` already uses. New test:
`tests/test_db_fs_results_and_questions.py::
TestBuildQuestionChecklistIsGeneralized::
test_levels_are_carried_through_to_each_question` — pins the container-only
question's `levels == ["container"]`, the multi-level "How big" question
still carries `"resource"`, and every question has a non-empty `levels`
list.

**Live-confirmed after the fix**, `coco_pharma` on port 8812:
- `GET /api/databases/localhost_docker_coco_pharma/questions?phase=scouting`
  now returns `"levels": ["container"]` for "Which schemas carry the
  data...?".
- `GET /api/analyses/facts/localhost_docker_coco_pharma/answer?question=...`
  for that question returns a genuinely different headline from "How big is
  this database": `"eu_sales 1 table(s) · 0 row(s); ... coco_ods 23
  table(s) — structure only; coco_sus 30 table(s) — structure only; demo 1
  table(s) — no access; demo_auth 4 table(s) — no access"` vs. "How big"'s
  unchanged `"8 schema(s), 7 with tables (...), 6 visible to this
  credential · 61 table(s) (58 base, 3 view) · 479 column(s)."` — the exact
  two-different-headlines behaviour this slice was written for, now
  confirmed reachable end-to-end, not just at the unit level.
- `GET /api/projects/localhost_docker_coco_pharma/analyses/schema_inventory
  /measurements?entity_type=database&level=container` returns the
  per-schema table (7 rows: `eu_sales`/`target_sales`/`us_sales` with row
  and byte totals, `coco_ods`/`coco_sus` marked "structure only",
  `demo`/`demo_auth` marked "no access") — the `entity_type=database`
  (no `level`) call is unchanged, still the flat 8-scalar resource shape.

## Not touched

`db_derived`'s catalog-only-fallback key population, `not_established`
coverage-threshold states, and the 7 remaining headline readers named in
the coordinator's slice 21b brief — queued as their own, separate slice,
not started here.

Collection is untouched throughout — everything here is derivable from
already-stored `database_tables` rows and the existing credential-
capability probe; no new survey step, no new stored field.

## Tests

- `tests/test_headline_level_awareness.py` (new, 7 tests): `_primary_level`
  directly (resource wins alongside a sub-level; container-only defaults
  correctly; no-levels defaults to resource); `_headline_for`'s level
  dispatch (same analysis, two levels, two different headlines; fallback to
  resource when no container reader registered for that analysis_id;
  fallback when the whole resource type has no container map declared at
  all); the coordinator's own regression case —
  `TestCheckLevelRequiresAGenuineLevelReaderNotTheFallback`: a
  resource-only reader's fallback text renders but `_check_level` still
  withholds the ✓ (`env.level_mismatch is True`).
- `tests/test_schema_inventory_container_headline.py` (new, 13 tests):
  system folding (`pg_catalog`/`information_schema`/`pg_toast*`/`pg_temp*`),
  data/empty classification with the estimate caveat, credential-scope
  classification (no-access/structure-only), staging name-heuristic
  matching, ordering (data desc by rows → empty → staging → shortfall →
  system-folded-last), and `None` when there are no tables at all.
- `tests/test_slice17_question_level_gate.py` (modified): `_fact_layer()`
  helper gained `fl._maps_cache = {}` (needed once `_check_level` started
  calling `self._map(...)`) and an optional `container_headline_analyses`
  parameter, used by 2 pre-existing synthetic tests
  (`grant_change`/`row_count_snapshot`) that inject a headline directly
  without a real container reader — updated, not reverted, since they test
  a legitimate "if a genuine reader existed" scenario the new stricter gate
  would otherwise correctly-but-unexpectedly flag. All 19 tests in this
  file pass.
- `tests/test_stage_page.py` — new
  `TestBuildMeasurementsIsLevelAwareForADatabase` (3 tests): default level
  is unchanged resource scalars; container level returns a per-schema row
  shape (not the resource scalar shape) with system folding and an `empty`
  note; container level falls back to the resource reading for an analysis
  with no container reader registered (`row_count_snapshot`).
- Full suite: 6517 passed, 104 skipped, 0 failed.

## CI timeout on the first push was a runner flake, not this diff

Run 36290350357 (tip `6e4ce6f7`) was cancelled at CI's 30-minute job
timeout. Investigated at length (see `re/tests-fail-fast-without-egeria`'s
own IMPLEMENTED doc for the full trace) and diffed against this slice's
commit — no new network I/O anywhere in it. Confirmed 2026-09-27: CI on
`3e3c0b12` (Slice 21b, which contains all of this slice) passed with the
test job at 15.7 minutes, squarely in the normal band. The original cancel
was a runner-level flake, unrelated to this diff.

## Report

Branch `re/slice21a-level-headlines`, tip: see PR.

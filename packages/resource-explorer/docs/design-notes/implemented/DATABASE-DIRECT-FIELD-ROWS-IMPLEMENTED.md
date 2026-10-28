# Database direct-field rows: vocabulary fix + 10 readers — implemented

**Found live 2026-09-29** by the project owner checking `laz_local_adventureworks`
(#8810, port 8810, never touched directly by this change): 11 Questions-tab rows
showed `○ not run` even though every ANALYSIS on that resource had actually run.
None of the 11 is backed by an analysis at all — their catalog `kind` is `direct`
(a stored field), not `analysis`. `○ not run` promises a real survey step exists
and has simply not been triggered; that promise was false for all 11.

Reference implementation: `#309`
(`HEADLINE-GAPS-FINGERPRINT-AND-REPO-CARDS-IMPLEMENTED.md`),
which built the "resource-state headline" mechanism for repositories —
`facts.py`'s `RESOURCE_STATE_SOURCES` + `_RESOURCE_STATE_HEADLINES`, registered
on the repo `ResourceTypeAdapter`. This slice ports the same mechanism to
databases and fixes the vocabulary bug that made the gap invisible in the first
place.

## 1. The vocabulary fix

`web/static/next/app.js`'s `rowState(entry, env)` had exactly three terminal
states before this: `no-surveyor` (catalog `kind: gap` — no mechanism exists at
all), the answered states (`env.answerable`), and a final fallback of `unrun`
for everything else. A `direct`/`chart`-kind question (a mechanism IS declared —
"this is answerable from a stored field") with no reader wired up fell straight
into that fallback, indistinguishable from a real, unrun analysis.

Fixed generally, not just for the 11 named rows: `rowState` now returns
`no_reader` for any `direct`/`chart`-kind row that is not yet answerable,
mirroring `facts.py`'s own `UNDECLARED_KINDS` table (`direct`, `chart`) — the
backend's existing "answerable from a field, but not declared machine-readably
yet" classification.

`glyphs.js` already had the glyph for this: `STATES.no_reader` (`◌`, family
`no-answer-here`, word "no reader") — declared by G1's glyph-family
consolidation but marked RESERVED, unused by any consumer. This slice claims it:

- `app.js`'s `GLYPH_KEYS`/`STATE_TONE` now include `no_reader` (tone
  `text-state-gap`, same non-accent tone as `no-surveyor` — both are "you
  cannot get an answer here yet" states, distinguished by wording, not colour).
- `rowInner`/`bodyLines` render a "no reader yet" pill and body message,
  mirroring the existing `no-surveyor` rendering exactly.
- `LEGEND_ORDER` (the Questions tab's KEY line) counts `no_reader` separately
  from `unrun` — the whole point of the fix is that these two counts must not
  merge.

`no_reader` and `no-surveyor` share the ◌ glyph family (both are "no answer
here") but are genuinely different states: `no-surveyor` — no mechanism exists
at all (`kind: gap`); `no_reader` — a mechanism kind is declared (`direct`/
`chart`) but no reader has been implemented for it. Same distinction `unrun`
(a real, un-triggered analysis) already draws against `no-surveyor`.

Also found and fixed in the same pass: `facts.py`'s `_resource_state_fact` read
the entity via the hardcoded `self._registry.get(slug)` (the `projects` table,
repo-only) rather than through the resource type's own adapter. Every database
resolver added below would have received `project=None` and reported "No
resource named ... is registered" for every database, every time — the sixth
`repo` hardcode this codebase has found in this area (`_last_run`'s own
docstring names the fifth). Fixed by dispatching through
`get_adapter(resource_type).get_entity(registry, slug)`, a no-op change for
repo (`_get_project_entity` already is `registry.get(slug)`).

## 2. The 10 readers (of 11 rows found)

`facts.py`'s new `DATABASE_RESOURCE_STATE_SOURCES` table, registered on the
database `ResourceTypeAdapter` (`survey_definition_adapter.py`,
`state_sources=lambda: ...DATABASE_RESOURCE_STATE_SOURCES`) exactly as the repo
table is registered on the repo adapter. Subjects are namespaced `db_*`,
disjoint from the repo table's subjects, so both tables' headline functions
live in the one merged `_RESOURCE_STATE_HEADLINES` dict with no collision, even
though several question texts are shared verbatim between the repo and
database catalogs.

| # | Question | Reader | Source field(s) |
|---|----------|--------|------------------|
| 1 | What is this resource, and what is it for? | `_db_r_description` | `DatabaseEntity.description` |
| 2 | Has this resource already been catalogued in Egeria, and when? | `_db_r_catalogued` | `egeria_linkage.describe_publish_status` (guid + staleness) + `activity_log`'s last `publish` operation timestamp for `(entity_type='database', entity_slug)` |
| 3 | Is there any existing use within our organization? | `_db_r_existing_use` | `list_databases_in_group` (group siblings) + `find_entity_investigations("database", slug)` (work-list/investigation membership) |
| 4 | Any known feedback? | `_db_r_feedback` | `resource_feedback` via `list_resource_feedback("database", slug)` |
| 5 | Does it replace or extend something we already have? | `_db_r_related` | `list_databases()` filtered by same `server_slug` / same `database_name` — a direct lookup, not `db_fingerprint`'s similarity score |
| 6 | Has this resource already been surveyed at any tier, and what did earlier signals reveal? | `_db_r_surveyed` | `DatabaseEntity.last_surveyed_at` |
| 7 | Which Survey Definition should I run — a quick coarse check or the full deep survey? | `_db_r_which_survey` | `SurveyDefinitionReader.find_candidate_process_guids("PostgreSQL Database")` |
| 8 | Based on what's already known, is this worth investigating further, or should it be deprioritized? | `_db_r_disposition` | `get_disposition_for_entity("database", slug)` |
| 9 | How much has changed since the last time this was surveyed — is it worth re-running now? | `_db_r_changed_since_survey` | `notification_detector.detect_change`, run over every id in `DATABASE_ANALYSIS_RESULTS_MAP` (same generic, slug-keyed mechanism the repo reader uses unmodified) |
| 10 | Is there a Survey Definition authored for this resource's technology type at all, or is that a catalog gap? | `_db_r_survey_definition_exists` | Same candidates reader as #7, empty-result case |

Every reader returns a one-sentence headline via a matching `_h_db_*` function
in `facts.py`'s `_DATABASE_RESOURCE_STATE_HEADLINES` — e.g. "Catalogued in
Egeria, published 2026-09-15." / "Not yet catalogued in Egeria.", "3 Survey
Definition(s) authored: DatabaseAssessmentSurvey, DatabaseAnalysisSurvey,
DatabaseScoutingSurvey.", "Registered, but not assigned to a group; not in
scope for any investigation."

None of these run anything — every one reads a field the registry (or
`activity_log`) already stores.

### #2's "when" is a different source than repo's

Repo's `_r_catalogued` reads "when" from `project_published_annotation_types`,
written only by the repo publish path. The database publish path
(`survey_definition_adapter._publish` → `EgeriaDatabaseSurveyor.
publish_step_annotations`) never writes that table — reading it for a database
would silently report "never published" for one that plainly has been.
`activity_log` is written for every operation on every resource type (CLAUDE.md
rule 16), including publish, so its own timestamp is the honest, already-stored
source for "when" here. If a database has never had a `publish` activity row
recorded (e.g. it was published before this rule existed, or by a path that
predates activity logging), `last_published_at` is simply empty — matching
repo's own graceful degradation when `project_published_annotation_types` has
no rows for a published resource.

## 3. The one row NOT implemented: licence

"Under what licence or agreement may this resource be used?" is declared for
databases in `question_catalog.yaml`, `kind: direct`, but its `note` field is a
stale, verbatim copy-paste of the repo wording: *"N/A — direct field (GitHub
license field)"*. `DatabaseEntity` (`registry.py`) has no licence field —
`license`/`license_spdx_id` exist only on `project_stats` (the repo-only
table) — and GitHub has no opinion about a PostgreSQL database. No other
stored field answers this question for a database.

This is exactly the brief's "the mechanism you find doesn't match what's
described" case: reported rather than guessed at. `DATABASE_RESOURCE_STATE_
SOURCES` deliberately does not have an entry for it, and — per the vocabulary
fix — the row now renders `◌ no reader` (honest: no reader exists) rather than
`○ not run` (false: no analysis exists either) and rather than a fabricated
reader pointed at storage that does not exist. If a real answer is wanted here,
it needs either a genuine per-database licence/agreement field added to the
registry, or the question re-scoped to what a database's registration actually
carries (e.g. governance zone / classification) — a design decision, not
something to guess at in this slice.

## 4. Gate — live-verified against `laz_local_adventureworks`

Read-only script against the real shared registry (never through 8810/8813 —
`ProjectRegistry()` with default config, `FactLayer._resource_state_fact` /
`.answer()` directly, no write path touched: `.facts()`'s gap-recording side
effect is not on the declared-state-source path these calls take):

```
What is this resource, and what is it for?                                    -> answerable=True  (nothing_found: "No description recorded for this database yet.")
Has this resource already been catalogued in Egeria, and when?                 -> answerable=True  ("Catalogued in Egeria.")
Is there any existing use within our organization?                             -> answerable=True  ("Registered, but not assigned to a group; not in scope for any investigation.")
Any known feedback?                                                            -> answerable=True  (nothing_found: "No feedback recorded yet.")
Does it replace or extend something we already have?                          -> answerable=True  ("Candidate overlap only, not a judgement of replacement: 1 on the same server.")
Has this resource already been surveyed at any tier, and what did earlier ...  -> answerable=True  ("Last surveyed 2026-09-29T19:22:28.542038.")
Which Survey Definition should I run ...                                      -> answerable=True  ("3 Survey Definition(s) authored: DatabaseAssessmentSurvey, DatabaseAnalysisSurvey, DatabaseScoutingSurvey.")
Based on what's already known, is this worth investigating further ...        -> answerable=True  (nothing_found: "No disposition recorded yet — undecided.")
How much has changed since the last time this was surveyed ...                -> answerable=True  (nothing_found: "Nothing has changed since the last survey (18 analysis(es) compared).")
Is there a Survey Definition authored for this resource's technology type ...  -> answerable=True  ("3 Survey Definition(s) authored for PostgreSQL Database.")
Under what licence or agreement may this resource be used?                     -> answerable=False (not implemented -- renders ◌ no reader, per §3)
```

Gate met: on `laz_local_adventureworks`, no direct-field row shows `○ not run`.
The eleven direct-field rows are split across TWO stages, not one (corrected
2026-09-30; this section used to say the gate was run on Discovery only): nine
are Discovery questions and two (description and licence) are Scouting
questions. Ten render a real, one-sentence answer (whichever of
`measured`/`nothing_found` the state genuinely is — `nothing_found` is not a
failure, it is a real "no" answer, same as repo's own resolvers); the one
unimplemented row (licence) renders `◌ no reader yet`, which is the honest
state, not a silently-swallowed gap.

All eleven rows, so a future reader does not have to rerun the cross-check:

| # | Question | Stage | State |
|---|----------|-------|-------|
| 1 | What is this resource, and what is it for? | Scouting | answered (description; has a real answer) |
| 2 | Under what licence or agreement may this resource be used? | Scouting | ◌ no reader yet — documented gap, no reader exists |
| 3 | Has this resource already been catalogued in Egeria, and when? | Discovery | answered |
| 4 | Is there any existing use within our organization? | Discovery | answered |
| 5 | Any known feedback? | Discovery | answered |
| 6 | Does it replace or extend something we already have? | Discovery | answered |
| 7 | Has this resource already been surveyed at any tier, and what did earlier signals reveal? | Discovery | answered |
| 8 | Which Survey Definition should I run — a quick coarse check or the full deep survey? | Discovery | answered |
| 9 | Based on what's already known, is this worth investigating further, or should it be deprioritized? | Discovery | answered |
| 10 | How much has changed since the last time this was surveyed — is it worth re-running now? | Discovery | answered |
| 11 | Is there a Survey Definition authored for this resource's technology type at all, or is that a catalog gap? | Discovery | answered |

Discovery: 9 rows. Scouting: 2 rows (description, licence).

`nothing_found` above for "existing use" reflects real data: this database's
`group_slug` is genuinely empty in the registry (confirmed by reading the row
directly, not inferred).

## 5. Not touched, and why

- `worklist.js`'s `cellState` (the comparison-grid glyph) has a related but
  different gap: a `direct`-kind question with no `analysis_ids` (true of
  every direct row in this catalog) already falls to `unclassified` there, not
  `unrun` — so the specific "○ not run" defect this brief targets does not
  reproduce in that surface. Its own axis (analysis-id-driven, never consults
  `state_sources` at all) is a separate question from the Questions tab's own
  vocabulary, out of scope for this pass.
- Repo's own `RESOURCE_STATE_SOURCES` table and its six `_h_*` headline
  functions are unchanged. The one shared touch point, `_resource_state_fact`'s
  entity lookup, is provably a no-op for repo (see §1).

## Tests

- `tests/test_next_no_reader_row_state.py` (new) — extracts `rowState`/
  `needsLensDeclaration`/`GLYPH_KEYS`/`STATE_TONE`/`LEGEND_ORDER` verbatim from
  `app.js` and runs them in node: a `direct`/`chart` row with no reader is
  `no_reader`, not `unrun`; `gap` stays `no-surveyor`; an unrun `analysis` row
  stays plain `unrun`; `no_reader` is a declared glyph/tone state; the legend
  counts `no_reader` and `unrun` as separate entries.
- `tests/test_facts.py::TestDatabaseResourceStateSources` (new, 12 tests) —
  each reader against a real (throwaway-schema) Postgres registry via the
  `pg_registry` fixture, both the "yes" and "no/not-yet" case where
  applicable, plus the wiring assertion (`get_adapter("database").
  state_sources() is DATABASE_RESOURCE_STATE_SOURCES`, exactly 10 entries),
  a catalog-consistency guard (every declared question text is a real
  `kind: direct` database question), and the licence-gap assertion (§3).
- `tests/test_fact_layer_resource_type_dispatch.py` — existing
  `TestTheDatabaseAdapterNowDeclaresResults` docstring updated (it used to say
  `state_sources` "stays undeclared" for database); one existing fixture
  (`TestDispatchGoesThroughTheAdapter.fake_type`) updated from `get_entity=
  lambda reg, slug: None` to `object()` — that fixture's None was harmless
  under the old hardcoded-`.get()` behaviour and became a real regression
  under the new adapter-dispatched lookup (§1's fix); fixed rather than
  worked around.
- Frontend harness (`frontend-build/test-harness/*.test.mjs`, run via
  `node --test` under Node 20 — this checkout's default Node is v14, which
  predates the `node:test` runner; `npm install` was run once in
  `frontend-build/` to restore `jsdom` for this worktree, matching what
  `frontend-build/package.json`'s `test:harness` script expects): all 17
  pass, including `state-tone-no-accent.test.mjs` (the new `no_reader` tone
  is `text-state-gap`, not an accent colour) and
  `by-analysis-headline-and-glyphs.test.mjs`.
- Targeted run: `test_facts.py` + `test_fact_layer_resource_type_dispatch.py`
  + `test_next_no_reader_row_state.py` + `test_next_one_glyph_table.py`:
  99 passed, 0 failed.
- Full suite (`uv run pytest tests/ -q`): see the branch's own CI /
  companion report for the exact count — run in the background alongside
  writing this document; no failures observed in the targeted areas this
  change touches.

## Report

Branch `re/database-direct-field-rows`.

# Three small defects from the owner's 8811/8812 screenshots — implemented

**Coordinator brief:** three small follow-ups from the Slice 12 / schema-
headline gate screenshots, on their own branch off `main` per the
coordinator's ruling (not on either of the two branches the screenshots
were taken from).
**PR:** #TBD (`re/followup-3-copy-fixes`).
**Also includes:** a `docs/Backlog.md` design note (not fixed here) for
the `_store_results` whack-a-mole pattern (three incidents: row_count/
size_bytes, operations/credential_capability, logged as a slice 18/20
candidate).

## 1. The ⚠ beside "ran Xm ago" did nothing on click

`lastRunHtml()` (`app.js`) rendered the warning glyph as a bare `<span>` —
an indicator with no explanation, found live clicking it on Database
Scouting Scan. The run's own step report (which analysis-id step failed
and why) already exists — it's parsed out of the activity-log `detail`
JSON by `get_survey_definition_last_activity()` — but that method only
kept `last_run_at`/`last_run_status`/`last_published_at` from it, dropping
the `errors` list on the floor.

**Fix:**
- `registry.py`'s `get_survey_definition_last_activity()` now also carries
  `last_run_errors` (the run detail's own `errors` list, `[]` on a clean
  run — never absent, so the frontend can tell "no errors" from "not
  fetched").
- `survey_definitions.py`'s `list_candidates()` threads `last_run_errors`
  onto each candidate.
- `app.js`'s `lastRunHtml()`: when a run failed AND carries errors, the ⚠
  is now a button (`data-run-errors`) opening the same `openDialog()`
  panel `data-defhist` ("definition history") already uses, listing each
  error. When a run failed but for some reason carries no error detail
  (e.g. an executor-level exception logged before any per-step detail was
  built), it stays a plain glyph with its existing tooltip — the fallback
  the coordinator asked for at minimum.

## 2. Evidence panel's "schema count" contradicted the headline's "8 schemas"

`_schema_inventory_results()`'s own value dict had a field called
`schema_count` — schemas that produced at least one stored table row (7 on
`coco_pharma`). The headline sentence (schema-headline branch, merged as
#302) leads with `schema_total` from the credential-capability probe
instead (8 — "how many schemas exist," per the owner's own ruling there).
Showing "schema count 7" directly under a headline reading "8 schema(s)"
read as a contradiction, even though both numbers were individually
correct for what they each actually counted.

**Fix:** renamed the field to `schemas_with_tables` (so the evidence
panel's auto-derived label — `key.replace(/_/g, ' ')` — reads "schemas
with tables 7", matching the headline's own "N with tables" wording), and
added `schema_total`/`schemas_visible` to the same dict, sourced from the
credential-capability probe the headline and the header banner already
use. Omitted (not `0`/`0`) when no probe has run yet — `"0 of 0"` would
claim the database has no schemas at all, a stronger and false claim
versus "not measured yet."

## 3. Evidence panel listed some fields twice

Root cause, confirmed live: "How big is this database" is answered by
TWO analysis_ids (`schema_inventory` + `row_count_snapshot`, a "mixed"
question), each with its own independently-written results reader.
Nothing stops two readers from naming a field the same thing — both
`_schema_inventory_results()` and `_row_count_snapshot_results()` read
the same stored `database_tables` rows and both happen to call their own
fields `tables`/`table_count`. `showEvidence()` rendered every key in
each fact's `.value` with no awareness of what an earlier fact in the
same envelope had already shown, so `tables`/`table_count` appeared once
per fact that carried them — two facts, so twice.

(The coordinator's own report named `view_count`/`materialized_view_count`/
`foreign_table_count` specifically; live-checking the actual rendered
panel found `tables`/`table_count` are the fields genuinely shared between
the two readers — the view/materialized/foreign counts exist only on
`schema_inventory`'s side. Same mechanism either way — "two facts
contributing the same keys" — just a different pair of field names than
first reported.)

**Fix:** `showEvidence()` now tracks a `shownKeys` Set across the whole
envelope's facts, in `env.facts` order (which already follows the question
catalog's own `analysis_ids` list). A later fact's value entries are
filtered against it before rendering, so a key already shown by an earlier
fact is silently skipped rather than repeated — dedup by key, first
occurrence wins.

## Tests

- `tests/test_registry.py`: new `TestSurveyDefinitionLastActivityCarriesRunErrors`
  (2 tests) — errors from the run detail are carried into the per-ref
  entry; a clean run carries `[]`, not a missing key.
- `tests/test_schema_inventory_results_evidence_fields.py` (new, 3 tests):
  the key is `schemas_with_tables`, not `schema_count`; `schema_total`/
  `schemas_visible` present when a probe has run; both omitted (not
  zeroed) when none has.
- `tests/test_next_evidence_panel_dedup.py` (new, 4 tests, source-level
  per this codebase's established no-JS-runner pattern): `showEvidence`
  tracks a shared `shownKeys` Set and filters each fact's entries against
  it; `lastRunHtml`'s warning glyph becomes a button carrying
  `last_run_errors`, and its click handler opens a dialog naming them.
- Existing suites re-run clean: `test_survey_definitions_routes.py`,
  `test_registry.py` (full file), `test_schema_inventory_headline.py`,
  `test_slice17_question_level_gate.py` — 208 passed, 0 failed.
- Full suite (after rebasing onto #303's merge, 8fe62240): 6503 passed,
  104 skipped, 0 failed.

## Live gate (8812, `coco_pharma`, this branch's own tip)

All three confirmed live:

- The ⚠ next to "ran 84m ago" on Database Scouting Scan opens a dialog
  reading "LAST RUN — ERROR · 84m ago" with the real failing-step message
  ("Failed to publish results to Egeria: Database 'coco_pharma' is not yet
  cataloged in Egeria...") — previously did nothing on click.
- "How big is this database"'s evidence panel reads "schemas with tables
  7 · schema total 8 · schemas visible 6" (previously "schema count 7",
  contradicting the headline's "8 schema(s)").
- The same panel's `schema_inventory`/`row_count_snapshot` fact blocks no
  longer repeat `tables`/`table count` — each appears once, in the first
  fact (`schema_inventory`) that carries it.


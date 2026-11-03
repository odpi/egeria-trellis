# Curate scope UX slice, implemented (2026-10-06)

Branch `re/curate-scope-conflicts-layout-selection`. Parts: (1) name conflicts removed, (2) layout,
(3) filter and multi-select, (4) one read per write and a render error banner, (5) the repository
plan's non-blocking loading state, (6) toggle pins, commit button, choice feedback.

## Part 5: the repository Curate plan (perf row for the backlog)

`GET /api/projects/<slug>/curate/plan` took 25,127 ms on `egeria_git`. The stage used to show
"Assembling what the catalogue would learn…" with no clock; it now draws at once with
`plan loading · n s` (seconds from a clock, a fake one in the tests), the other bands load on their
own and the rest of the page stays usable, the plan fills in when it arrives, and a failure shows
its cause and the elapsed time.

Cause, measured on a temp-SQLite fixture (no Egeria, no shared registry), `build_plan` under cProfile:

- **One number: 6 of the 6 fact reads each call `record_gaps_for`, and that is 36 of 40 ms (90%) of
  `build_plan` on a small fixture.** One plan issues 669 SQL statements and 1,294 connection
  checkouts (about 111 statements per gap pass: `collect_gaps` -> `_disagreement_gaps` ->
  `_r_changed_since_survey` -> 37 `detect_change` calls per pass). `layer.facts()` is called once per
  analysis id (`_fact`), so the gap pass runs 6 times; passing the six ids to one `facts()` call
  would run it once.
- The size-dependent part is the sub-resource survey: 20,000 findings moved the same plan from 41 ms
  to 466 ms, dominated by reading that analysis twice (`_sub_resource_survey_results` and its headline;
  145 `query_findings` calls, 40,465 row objects).
- No Egeria call is made by `build_plan` (nothing in `curate_plan.py` reaches Egeria).
- NOT reproducible here: the 25 s. On SQLite the statements are microseconds; 669 statements over 25 s
  is about 37 ms each, which fits a Postgres round trip plus pool checkout under load, but that is an
  inference from the arithmetic, not a measurement. The next measurement belongs on the gate server:
  time `record_gaps_for` once inside `_fact`.

## Part 6: the dead triangle, the commit button, choice feedback

- Cause of the dead coco_sus triangle (reproduced on unchanged origin/main in
  `curate-scope-toggle.test.mjs`, tests 4 and 5 fail there): the render computed
  `open = openSchemas.has(name) || tables.some(conflict)` and wrote the result back into
  `openSchemas`. While any table of the schema carried a name conflict (23 on coco_pharma once coco_ods
  was left out next to coco_sus) the click removed a flag the render re-added: a dead toggle. After the
  conflicts went away the schema stayed open from the remembered flag until a second click: "started
  working again". Part 1 removed the conflicts; the tests pin that open state is only what the person set.
- The commit control is a filled primary `Catalog · n schemas` button on its own row under the manifest
  and the refresh box, with the reason beside it when off; `Read Egeria again` is a separate secondary link.
- A choice write shows `saving…`, then `saved in Resource Explorer · who · when · not yet cataloged in
  Egeria` for six seconds (the clause becomes `Egeria changes when you press Catalog` for a node already
  in Egeria). The per-row verbs are unchanged.

## Part 7: RE's own survey report step, and long surveys

- `RE's own survey report · done · report not found · 76 annotations` was wrong twice. In
  `publish_local_survey` the SurveyReport element is created first and the annotations under it; a failure
  of that create was only a `log.warning`, `report_guid` stayed empty, and `annotation_count` is
  `len(annotations)` BUILT locally, so 76 never meant 76 published. There is no "annotations without a
  report element" path: an empty guid means the report was not confirmed. The surveyor now returns
  `report_error`; the step says `report <id> · n annotations published · from the 10-03 survey` only with a
  guid, `failed` with Egeria's word when the create raised, `n annotations from the 10-03 survey · Egeria
  returned no report element id, so the report itself is not confirmed` when nothing says why, and
  `report not found` only when the publish returned nothing at all. The proof row's empty `element_guid`
  on coco_pharma (76 annotations, 2026-10-03T18:07:08) therefore most likely records a swallowed create
  failure, not a success; the next commit shows which.
- A step line no longer says its state twice (`submitted · submitted · 10-06 13:25`).
- A real database survey takes minutes (engine action 63547d8a was IN_PROGRESS seven minutes after start
  on a 2-schema scope, 6 annotations so far). The step reads `running in Egeria · IN_PROGRESS · N annotations
  so far`, with the hint `check again in a minute with Read Egeria again`, no spinner; the leave-out
  in-progress block will matter for the same reason.

## Part 6 addendum: what a choice press looks like (owner feedback 2026-10-06)

The owner pressed `leave out` twice because the only trace was text scrolled off the right edge. Now,
in the visible left part of the row (the choice cell and the row's left strip), at the moment of the press:

Before (undecided row), markup:
`<div class="flex items-baseline gap-s2 border-b border-rule border-l-[6px] pl-[4px] py-[3px] text-caveat border-l-transparent" data-scope-effective="">`
choice cell: `○` (`data-scope-mark="none"`, `role="img" aria-label="undecided"`), `undecided · keeps what’s in Egeria now`, `catalogue · leave out`.

After pressing `leave out`, before the server answers (same render as the press):
row class `… border-l-rule-strong`, name cell `text-ink-muted line-through opacity-70`, choice cell
`aria-busy="true"`: `⊘ left out` (`data-scope-mark="leave_out"`, `aria-label="left out"`, badge
`data-scope-badge`), `leave out · set by <who> <date>`, `saving…`, both verbs `disabled` (a second press
on the row is ignored; one bulk action at a time).
When the PUT answers (before the scope is read again): `saving…` becomes `saved`
(`title="saved in Resource Explorer · who · when · not yet cataloged in Egeria"`) and the row gets
`bg-paper-surface` for 1.5 s. If the PUT fails the row returns to its previous markup and shows
`✕ not saved · <cause>`.
Catalog is `●` with `border-l-ink`; the legend above the table reads `● catalog · ⊘ left out (struck
through) · ○ undecided`. State uses shape, weight, strike-through and opacity only (no accent colour).

## Round 3 (2026-10-06): the designer's panels and the owner's two real commits on coco_pharma

Built to `REPLY-DESIGNER-STATE-AS-VISUAL-CUE.md` and its addendum. Panels A (selector), B (numbered steps), C
(manifest table); cue vocabulary: fill = chosen, lowered row = not included, strike-through = superseded text
only, left-edge rule = needs you, no accent for state. (The round-2 marks, strips and badge were replaced: they
were the first reading of the owner's feedback, not the designer's drawing.)

- **Survey report (Part 7 cause, from 8813's log).** Both commits (bd32f1a0, 0ccd9c32) logged
  `Failed to publish SurveyReport ... relatedHTTPCode=409 ... OMAG-COMMON-409-001 ... qualifiedName
  SurveyReport::PostgreSQL::localhost_docker_coco_pharma::2026-10-03T18:07:08.668511 is not available for
  use`. The report's qualifiedName is keyed on the survey RUN's timestamp, so republishing the same 10-03 run
  meets a 409; `publish_local_survey` logged a warning and carried on with no guid (the 76 is RE's local count).
  Fix: on that 409 the surveyor looks the report up by its exact name (`_find_element_guid`, not a search),
  reuses it, counts the annotations Egeria holds under it (`get_asset_by_guid` graph depth 1) and publishes
  only the missing ones; `publish_annotations` already looks each annotation's qualifiedName up before it
  creates (`annotation_props.publish_annotations`), so a replay never duplicates. The step reads `already in
  Egeria · report <id> · from the 10-03 survey · <k> annotations in Egeria` (`· built locally: <n>` only when
  they differ); every other create failure FAILS the step (`report not published · <cause> · 76 annotations
  built, none published`) and the proof row records the failure. A 409 whose report cannot be read fails with
  `Egeria refused the report as a duplicate but no report with that name could be read`.
- **Minor failure, kept on record (the refresh step).** `refresh the cataloguer · failed · TIMEOUT_ERROR_408 =>
  Request timed out for endpoint .../integration-daemon/integration-connectors/refresh` while the connector_read
  proof row said REFRESHING: the daemon was already on its own pass, the elements WERE created and read back.
  A 408 while the connector reports REFRESHING (before or after) is now `skipped · the cataloguer was already
  refreshing · elements arrive on its pass`, not counted as failed; a 408 with any other or unreadable status,
  and any non-timeout refusal, stays a failure with Egeria's word first. INFO lines now bracket the refresh
  (`asking the daemon to refresh <connector> (status <s>)`; `refresh finished in <t> s` / `timed out after
  <t> s ...` / `refused: <first sentence>`).
- **Measured survey duration.** A real database survey in Egeria takes 15-22 minutes (engine action 63547d8a:
  15 min for 2 schemas; 42448ea2: 22 min for 3), not the seconds a toy takes. The running step reads `running in
  Egeria · started <time> · <n> min · <k> annotations so far · usually takes about 15-25 minutes ... · check
  again · checking every 60 s`; while the page is open it reads the survey's status every 60 s (a read-back, never
  a write, one in flight, stopped at an end or when the page is hidden). A page reload loses the commit it was
  watching (the record is not looked up again): that is not built.
- **Attach step**: `1 attached · 2 already attached` from the proof rows' `mechanism`.
- **Page re-reads on step change**: whenever a polled commit record's step states change the scope is read again
  (one GET) and the tree and header marker redrawn, so no row is older than a step on the same page (live finding:
  `not committed yet` next to an attach step that was already done).
- **Manifest sentence** names both counts: `RE's survey report is published whole; it describes the <m> schemas RE's
  own <date> survey could read (Egeria's survey counts <M>); elements are created for the <n> you chose.`
- **Collision**: a soft delete is the state `deleted` (`deleted in Egeria · <when>`), no glyph (the `removed`
  glyph entry is gone from `glyphs.js`), the row lowers, the preview says `will delete` beside `will archive`.
  The stored proof kind keeps the name `removed` (existing rows carry it; renaming would orphan them).
- **401**: on a choice the row returns to its previous look and shows `✕ unsaved · your session expired · sign in
  again` with `save again` (re-sends the same choice: nothing is lost) and `sign in`; on a bulk press and on the
  commit the status line / button reason says the same with a sign-in link.
- **build_plan** runs the gap pass once per plan (a test counts it); the 25 s stays on the backlog with the
  measured 669 statements.

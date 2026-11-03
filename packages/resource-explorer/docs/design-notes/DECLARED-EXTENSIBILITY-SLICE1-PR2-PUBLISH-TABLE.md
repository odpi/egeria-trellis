# Declared extensibility, slice 1 PR 2 (the Curate publish table): what was built, choices, gaps (2026-10-27)

Companion to `DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md` (§5 the publish table, §7 the gate) and to
PR 1's `DECLARED-EXTENSIBILITY-SLICE1-PR1-GAPS.md`. Records what the design note leaves open and what this PR chose,
rather than guessing.

## 1. What was built

- **The table** (`resource_explorer/publish_table.py`, `web/routes/publish_table.py`,
  `web/static/next/stages/publish-table.js`): on a database's Curate "Describe and publish" tab, between What people
  say and the Publish band. A row per declared measure of every database analysis with a declaration, except
  `proposal` kinds. Columns: analysis, measure with unit, value with unit, as-of, confidence form with its basis or
  method, scope, writes as (the declared type, or "generic measure · no native type declared"), Publish | Keep in RE,
  and the publish state from proof rows ("local · not published", "published · read back <time>", "changed since
  published", "sent · not read back"). An absent value shows the declared absence sentence, never a zero.
- **The choice**: saved at once, signed and dated, append-only, newest wins.
- **The press** is the database's one press, the Catalogue commit on Contents. Its manifest gains a line naming how
  many chosen measures go (`declared_measures`, from the same function the press sends from). Its step 3 (RE's own
  survey report) carries the chosen measures as annotations after the stored verdicts, reads the report back, and
  records one proof row per measure; the step's words gain "N declared measures read back".
- **The two routes**: generic (ResourceMeasureAnnotation, value in `resourceProperties` under the measure's name,
  `additionalProperties.declaredRoute: generic` and the label, unit, as-of, confidence basis, scope) and native
  (the declared type with the declared property map; `declaredRoute: native`).
- **The as-of seam** (PR 1 gap §2.1): `_db_derived_field_reader` now carries the run beside the payload as
  `_run: {read_snapshot, surveyed_at}`; `Fact.as_of` is `_run.read_snapshot` when present, else the run time, and is
  in `as_dict()`. `_run` is envelope, not content, in both `_has_content` and `results_have_data`. On screen, an
  answer whose facts have no run time but an as-of says "as of <time>" rather than "run time not recorded".

## 2. Choices this PR made, beyond the note

1. **No new table.** The choice is a row in `resource_scope_events` (kind `measure`, locator `<analysis>/<measure>`,
   choice `publish` | `keep`); `current_resource_scope` reads scope kinds only, so these never count as scope. Proof
   rows go in `catalogue_commit_proofs` (node_kind `measure`, schema_name = analysis id, table_name = measure,
   proofs `measure_read_back` / `measure_not_read_back`); every existing reader of that table filters by node kind or
   proof kind. No DDL.
2. **Not chosen is Keep in RE.** No row is ever published by default.
3. **Measures ride in RE's own survey report**, keyed `declared_measure.<analysis>.<measure>` under the report's
   prefix, so they need a measured survey of RE's own (the manifest says so when there is none) and a later survey's
   publish writes a new annotation rather than updating the old one, as every annotation in that report does.
4. **Only the commit asks for them.** `publish_local_survey(declared_measures=False)` is the default, so the retired
   publish route and the hybrid surveyor never send chosen measures without recording proofs.
5. **Native writing is ResourceMeasureAnnotation only.** Any other declared type (SchemaAnalysis, DataClass,
   ResourceProfile, Classification, ...) is shown as what the row writes as, with Publish disabled and the reason:
   "Resource Explorer does not write <type> from a declaration yet". Building those types from a declaration needs a
   per-type construction (a profile's value counts, a classification's name) that PR 1 says no declaration carries
   yet (PR 1 gaps §2.4).
6. **A row cannot publish** without a value, with a non-scalar value, or without an as-of ("a number never travels
   without its as-of"); nor when the confidence form is `generated` (the declaration already forbids it an
   annotation).
7. **Changed since published** compares the proof row's value and as-of with the current ones. It is a word on the
   row; nothing is re-sent until the next press.

## 3. Not built

- **File systems.** Their rows are listed by the route but not offered on screen, and cannot publish
  ("publishing from this table is built for databases only so far"): a file system's Publish attaches its root folder
  only and has no survey report of RE's for the measures to ride in.
- **Native types other than ResourceMeasureAnnotation** (§2.5).
- **The engine-host and native-survey paths.** A library step's results already publish through the engine host when
  Egeria runs it, and some built-in measures (schema inventory, row counts) are already in RE's survey report under
  their own annotations. The table does not know that, so publishing such a row writes the same number a second time
  under the declared name. See owner question 1.
- **Cost on open.** The table reads every declared analysis through the fact layer; the seven `db_derived` analyses
  each recompute `run_db_derived` (it is not memoized across facts). Fine at coco_pharma's size; worth a memo if a
  large database makes Curate slow to open.
- **Library steps' value paths** use the record property names ("Writes Per Day"); the table reads those as whole keys
  first. Whether the results reader returns records in that shape was not checked against a live run.

## 4. Owner questions

The project owner answered all four on 2026-10-28: see §4a.

1. **Rows whose analysis already publishes another way.** Should a row say "already in Egeria via <the survey report /
   the engine host>" and offer no Publish, rather than offering a second, declared copy of the same number?
2. **Native types next.** Which declared types should the publisher learn first? The PR 1 declarations name
   SchemaAnalysisAnnotation and ResourcePhysicalStatusAnnotation most often after ResourceMeasureAnnotation.
3. **File systems.** Should their chosen measures wait for a file-system survey report of RE's own, or ride on the
   root-folder publish?
4. **Choice lifetime.** A choice is kept across surveys, so a measure chosen once is sent by every later commit until
   someone chooses Keep in RE. Is that the intent, or should a choice be per survey?

## 4a. Owner answers (2026-10-28)

**Decision (project owner, 2026-10-28):** "A number already in Egeria another way: the row says 'already in Egeria
via <the survey report / the engine host>' and offers no Publish, so a number is never written twice."

**Decision (project owner, 2026-10-28):** "Native types next: SchemaAnalysisAnnotation first, then
ResourcePhysicalStatusAnnotation in a later slice."

**Decision (project owner, 2026-10-28):** "File systems: their chosen measures wait for a file-system survey report
of RE's own; they don't ride the root-folder publish."

**Decision (project owner, 2026-10-28):** "Choice lifetime: a choice lasts across surveys (today's rule), with
'changed since published' on the row."

The words above are the coordinator's relay of the owner's answers. What was built for each:

1. **Already in Egeria.** `publish_table.published_elsewhere` recognises three routes, none written per analysis:
   - **RE's survey report.** The commit's step 3 always writes the survey's database counts, its size and its views
     under their own annotations (`DatabaseSurveyor._create_schema_annotations`, `_create_statistics_annotations`,
     `_create_views_annotations`). Four declared measures say the same numbers (`IN_SURVEY_REPORT`):
     `schema_inventory` relations and columns, `row_count_snapshot` total_size, and `sql_analysis` views.
     - A test pins each pair to a measure the shipped catalogue declares, so renaming one cannot quietly make it
       publishable.
     - It is per measure: `schemas_with_tables` is not in the report and stays publishable.
   - **The engine host.** The newest `engine_action` run on this database for the analysis's step key (a library
     step's `provided_by.step`, else the catalogue id), whose stored outcome is `survey-completed` with a report GUID.
     - The row reads "already in Egeria via the engine host · report <time>".
     - A failed run, or another database's run, does not count.
     - Runs that cannot be read leave the row publishable: the table never guesses that a number is in Egeria.
   - **Egeria's own survey.** A catalogue entry with `source: egeria` (`egeria_db_survey`). This is the same rule
     applied to the third route the catalogue has; it is not in the owner's two examples.

   On such a row, Publish is disabled and Keep in RE is still offered. The row's state shows the route, and its
   reason reads "… publishing it here would write the same number twice". A Publish chosen earlier is no longer sent,
   because the press sends only publishable rows.
2. **SchemaAnalysisAnnotation, written natively.**
   - Its only typed fields are `schemaName` and `schemaType`. Each is filled only when the declaration's property map
     sends the value field to it; neither is ever invented.
   - The value, and the envelope (route, analysis, measure, unit, as-of, confidence, scope), go in `jsonProperties`,
     which every annotation type carries. `annotation_props` sends no `additionalProperties` for this type, and
     changing that would alter every schema annotation RE already publishes.
   - The rows this opens, beyond those already in RE's survey report or Egeria's own:
     - `db_external_dependencies` (five);
     - `db_relationship_graph`'s foreign_key_edges;
     - `schema_diff` (three);
     - `nested_column_profile`.
   - ResourcePhysicalStatusAnnotation still reads "Resource Explorer does not write … from a declaration yet" and
     offers Keep in RE only, until its slice.
3. **File systems.** The row's reason now says the measures wait: "a file system's measures wait for a survey report
   of Resource Explorer's own for file systems; its Publish attaches the root folder only". The report itself is the
   Files steps' work (steps 6-8), not this table's. When it exists, this table needs only to:
   - send chosen rows in it, as `annotations_for` does for a database;
   - read them back by qualified name;
   - and record `measure` proof rows keyed by the file system's slug.
4. **Choice lifetime.** This was already the behaviour. The row now also says so:
   - the choice line reads "will publish · <who> · <when> · sent by every commit until Keep in RE";
   - a published value that has since moved reads "changed since published · read back <time> · the next commit sends
     the new value".

Tests: `tests/test_publish_table.py` covers each route of answer 1 (including a stale choice no longer sent), a
SchemaAnalysisAnnotation published and read back through the real surveyor publish code against a fake Egeria, the
property map reaching `schemaType`, ResourcePhysicalStatusAnnotation still refused, and a standing choice sent by
two commits and ended by Keep in RE. The harness pins the "elsewhere" state and the standing words.

## 4b. After slice 2 (the Chat compile)

Slice 2 added three things to the declaration and the compiler, and the table now reads all three the same way the
Chat compile does:

- **Confidence per measure.** Each row's confidence is `Declaration.confidence_of(measure)`: the measure's own where
  it declares one, else the declaration's. The confidence sent with a published measure (`confidenceForm`,
  `confidenceBasis`) is that row's. A measure whose own confidence is `generated` writes as nothing.
- **Not built.** An analysis with `built: false` reads "Planned, not built yet: not measured." followed by its
  declared not-measured sentence, whatever the fact layer holds. It offers Keep in RE only, and a Publish choice is
  refused with that reason. Today that is `doc_source_ingestion`, `doc_evidence_check` and `semantic_suggestions`.
- **Library steps.** A library step's result is unwrapped once by `declared_context.library_rows`, as the Chat
  compile does. The database library steps (`change_rates`, `database_staleness`, `hub_tables`,
  `statistics_freshness`) are member-scoped, so their value fields name a column of a per-table row, not a value for
  the database. Such a row used to read "ran and found nothing". It now reads "one value per member, not one for the
  resource · see <extent>", has absence `per_member`, and cannot publish: there is no one number to send.

**Not done here.** The file-system library steps (`fs_activity` and the rest) are resource-scoped, but write one
record per measure, distinguished by the record's `measure` key. Picking the right record for a measure is left
for when a file system can publish (owner answer 3). Until then their rows still show the declared absence.
The Chat compile has the same gap, in `_measure_lines`.

Tests: five more in `tests/test_publish_table.py`, covering:
- a measure's own confidence on its row and on the annotation sent;
- a not-built analysis refusing a choice;
- the three shipped not-built analyses;
- `change_rates`, wrapped as the shared reader returns it;
- a resource-level field beside `measurement_rows` still resolving.

**Update: the file-system library steps.** The table and the Chat compile now share
`declared_context.measure_records(registry, entity_type, slug, step, dec, analysis_id)`. It reads each measure from
the key the host stored it under (`survey_step_host.measure_step_key`):
- the step's own measure is under the step key;
- each further measure is under `<step>:<measure>`.

For each measure it takes the value from `resourceProperties` and the state and reason from the envelope, as the
Files card's `derived_view` does. If a measure's record states no value, the row shows the record's state and the
step's reason. For example, `fs_access_activity` on a mount without access-time updates reads "access times not
recorded on this mount: …".

A first attempt looked for a `measure` key on rows read under the step key alone. Real storage never puts another
measure's rows there, and a test that faked that shape passed anyway. The tests now run `fs_activity` through RE's
own host on a walked tree and check both readers against `derived_view`.

File systems still cannot publish (owner answer 3): this changes only what their rows show.

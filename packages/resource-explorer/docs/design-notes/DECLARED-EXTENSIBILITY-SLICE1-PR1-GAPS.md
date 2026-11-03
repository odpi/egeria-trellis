# Declared extensibility, slice 1 PR 1 (the declaration): gaps and choices (2026-10-27)

Companion to `DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md`. PR 1 adds the declaration
(`trellis_analytics/declaration.py`), carries it on `StepManifest.declaration` and on every database and
filesystem entry of `analysis_catalog.yaml`, and validates both with the same model. Writing 36 real
declarations against the code turned up places where the design note is silent or where the code does not
yet say what a declaration needs. Each is recorded here rather than guessed. Nothing below changes rendering.

## 1. Choices this PR made, beyond the note

- **`annotation` is per measure, not per declaration.** §1 lists one `annotation` for the analysis. §5's
  publish table has a row per measure with its own "writes as", and `hub_tables` already publishes two
  measures as two types (ResourceMeasure and Classification). So each measure may declare
  `annotation: {type, properties}`; absent is "generic measure · no native type declared". A catalogue
  measure's type must be one of the entry's `annotation_types` (refused otherwise); a step measure's must be
  what the step publishes (`annotation_type_for`), and a step measure published as anything but
  ResourceMeasureAnnotation must declare it, so the table cannot say "generic" for a native write.
- **`value_field` and `as_of` are paths.** A dotted path names a nested field (`counts.stale`); a trailing
  `[]` declares the count of a list field (`columns_added[]`); `run.<field>` names a field of the run that
  produced the result, not of the result (see §2.1).
- **`confidence.method`.** An estimate whose basis is the same for every result (n_live_tup is
  PostgreSQL's estimate) declares it as a sentence; no result field carries it. `inferred` and `judged`
  still need a basis *field*, since their basis differs per result.
- **Confidence is one per declaration.** `database_staleness` reports an estimate (row counts) and an
  exact read (write counters); it declares `estimated`, the weaker, per §4's "weakest link" rule.
- **The four absence keys meet the envelope's states** as the model's docstring says: `measured_nothing`
  is `nothing_found`; `not_measured` and `not_permitted` are both `not_established`, told apart by the
  reason; `not_applicable` is itself.
- **`token_cost` is an authoring estimate** (about 8 tokens a field a row, plus 40), not a measurement.
- **A refused catalogue declaration does not take the catalogue down.** The entry still loads, is listed
  and runnable, logs the refusal, and carries `declaration_refused` in `to_dict()`.
  `test_analysis_declarations.py` fails on any refusal in the shipped file.

## 2. Gaps found in the code

1. **The as-of is dropped before the UI sees it.** db_derived payloads carry no time of their own; the run
   carries `surveyed_at` (compute time) and `read_snapshot` (the newest stored row read), and
   `_db_derived_field_reader` returns only `derived[field]`. Most survey-backed readers likewise carry none
   and rely on the row's `surveyed_at` or the fact's `last_run_at`. The declarations name `run.read_snapshot`
   or `run.surveyed_at`; a consumer must be handed the run's fields beside the payload for that to resolve.
   Which seam carries them (the fact envelope, the reader) is the renderer PR's decision.
2. **Three analyses have no results reader**, so their declared fields describe the step's output, not what
   the UI reads today: `nested_column_profile`, `sql_analysis`, `egeria_db_survey`.
3. **Three entries are not built** (`doc_source_ingestion`, `doc_evidence_check`, `semantic_suggestions`).
   They are declared from their descriptions, with value fields (`documents_ingested`, `evidence[]`,
   `suggestions[]`) that no code produces yet. Each building slice must hold its output to them or change
   them.
4. **Property maps are not declared for built-in analyses.** Their annotation construction lives per type in
   `annotation_props.py` and in the sub-surveyors. Each measure declares the type it writes as, with an empty
   property map.
5. **The engine-host Egeria declaration does not carry the declaration.** `declare.manifest_properties` and
   `generate_engine_host_declaration.py` are unchanged, so the promotion route of §1 (the Library's declare
   step) does not yet publish result kind, units or absences to Egeria.

Found in passing; out of this PR's scope, listed for whoever owns them:

- `result_materializer.py` (around line 863) reads `view.get("column_lineage")` where the surveyor writes
  `lineage`, and calls `_blob_int` on the `complexity` dict, so both columns are probably always NULL for
  local surveys.
- `filesystem_inventory` sums an unread `size_bytes` as 0.
- `privilege_audit` and `db_activity_signals` store "engine does not support this" as None; the reader's
  `or {}` turns that into an empty result, which reads as nothing found.

## 3. Owner questions

All four are answered: see §3a. The questions are kept as they were asked.

1. **Answers: one list or two?** A built-in analysis's `answers` repeats the question catalogue's
   `analysis_ids` binding; a test holds the two equal. Should the catalogue's `answers` be generated from the
   question catalogue instead, so there is one place to edit?
2. **Should a not-built analysis declare at all**, or be exempt until it is built (§2.3)?
3. **Per-measure confidence?** §4 makes confidence a property of the result; `database_staleness` shows a
   declaration can mix forms. Is "weakest link per declaration" the intended rule, or should a measure carry
   its own form?
4. **Repository analyses.** This PR declares database and filesystem entries only, as briefed. The four
   `TODO(declared-extensibility)` markers in `repo-analysis-views.js` and `declared-topology.js` wait on repo
   declarations.

## 3a. Owner answers (2026-10-28)

The project owner answered in the coordinator session; the coordinator relayed the answers on 2026-10-28. Each is
built in slice 2 (`DECLARED-EXTENSIBILITY-SLICE2-CHAT-COMPILE.md`), except the fourth, which is the next slice.

**Decision (project owner, 2026-10-28):** "GENERATE the declaration's `answers` from the question catalogue's
analysis_ids. One place to edit; the equality test becomes a staleness check." Built:
- `analysis_catalog_reader` fills a built-in entry's `answers` on load from `question_binding()`.
  `answers_questions: false` gives `[]`, and a library step keeps its manifest's questions.
- A hand-written `answers` in the catalogue is refused.
- `test_no_hand_written_entry_in_the_catalog_carries_answers` is the staleness check.

**Decision (project owner, 2026-10-28):** "DECLARE them, marked 'not built yet', so they read as 'planned, not
measured' rather than nothing." Built:
- `Declaration.built` defaults to true. `doc_source_ingestion`, `doc_evidence_check` and `semantic_suggestions`
  declare `built: false`.
- Chat says "Planned, not built yet: not measured." ahead of the declared not-measured sentence, and records the
  absence as `not_built`.

**Decision (project owner, 2026-10-28):** "PER MEASURE. Each measure carries its own form and basis, and the
declaration's headline confidence is the weakest of them." Built:
- `MeasureDeclaration.confidence` is optional. A measure without one inherits the declaration's confidence, and
  `Declaration.confidence_of(measure)` returns whichever applies.
- A headline that is not the weakest of the measures' forms is refused. So is a measure with a confidence of its
  own when other measures have none and the declaration declares none for them to inherit.
- The order is `CONFIDENCE_STRENGTH`: measured, judged, estimated, inferred, generated. Placing "judged" second was
  ruled by the project owner on 2026-10-28 (`DECLARED-EXTENSIBILITY-SLICE2-CHAT-COMPILE.md` §5).
- `row_count_snapshot.total_size` declares `measured` (pg_total_relation_size is exact) under an `estimated`
  headline.

**Decision (project owner, 2026-10-28):** "Repository analyses: declare them in the NEXT SLICE, clearing the four
TODO(declared-extensibility) markers in repo-analysis-views.js and declared-topology.js." Not built in slice 2:
it is the next slice.

## 4. What PR 2 (the Curate publish table) can rely on

Per measure: `name`, `unit`, `as_of`, `value_field`, and `annotation.type` or its absence ("generic measure ·
no native type declared"). Per declaration: `confidence.form` with `basis`, `method`, `score`, `calibrated`;
`scope`; and `result_kind`, where `proposal` rows stay off the table (§5). All are in
`AnalysisCatalogEntry.to_dict()["declaration"]` for every database and filesystem analysis, built-in or
library step.

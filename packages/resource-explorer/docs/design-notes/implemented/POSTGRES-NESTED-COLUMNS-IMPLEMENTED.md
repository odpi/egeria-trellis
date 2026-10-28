# postgres_nested_columns: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, Phase 1 slice #11 —
*"`postgres_nested_columns` | Sonnet | 10 (shares the inference core)"*.
Gate: slice 10 (`postgres_column_profile`, `data_class_match`,
`reference_data_match`), merged to `main` at `6f2e024a`.

**Implements:** `multi-resource-questions-design.md` §5.4's
`nested_column_profile` row and §5.7's `postgres_nested_columns` row.

**Branch:** `re/postgres-nested-columns`, from `main` at `6f2e024a` (the
slice 10 merge).

**Tests:** 53 new tests across `tests/test_nested_schema_inference.py` (the
reusable core, tested with zero database coupling) and
`tests/test_postgres_nested_columns.py` (the Postgres glue), plus two small
updates to existing tests that needed to know about the new analysis id
(`tests/test_database_surveyor_steps.py`) and a mechanical regeneration of
`question_catalog.yaml` (see §5 below — the CSV itself was not touched).
Full suite: **5,601 passed, 103 skipped, 1 failed** — the one failure
(`test_egeria_live_smoke.py::TestTheByNameFallbackWorks`) is pre-existing and
needs a live Egeria platform (confirmed by running it in isolation before
this slice touched anything; same failure noted in slice 10's own
write-up).

---

## 1. What "shares the inference core" means, concretely

Two new files, split the same way slice 10 split `column_matching.py` out of
`column_profile_step.py`:

| File | New? | Coupling |
|---|---|---|
| `resource_explorer/surveyors/nested_schema_inference.py` | new | **zero** — no pyegeria, no psycopg2, no `resource_explorer.registry` import. Pure Python over already-parsed values. |
| `resource_explorer/surveyors/database/nested_columns_step.py` | new | Postgres-specific: identifies JSON/XML columns, drives sampling, builds annotations. |

`nested_schema_inference.py` lives at the top level of `surveyors/`, not
under `surveyors/database/`, because §5.4's own row says it is "reused by
§6's `nested_schema_profile`" — the filesystem path (Phase 2, not this
slice) needs the identical "given these parsed JSON documents / XML trees,
what's the schema" logic against files on disk, with no database anywhere
in the loop. Putting it under `database/` would have made that reuse an
import across a package boundary that implies coupling which does not
exist.

**What is reused, not reimplemented, from slice 10** (the gate this slice
sits behind): `sampling.py`'s `resolve_sampling_config`/`SamplingConfig`/
`SampleProvenance`/`not_sampled_provenance`; `column_profile_step.py`'s
`sample_column_values` and `SamplingBudget` (byte/time budget accounting,
the `values is None` vs `values == []` distinction); and
`column_matching.py`'s `type_family` (to find JSON/JSONB/XML columns in the
schema catalog in the first place — the same classifier slice 10 uses for
its own `UNMATCHABLE_TYPE_FAMILIES` gate). No second sampling
implementation, and no second type-family map, exist anywhere in this
slice.

---

## 2. A documented deviation from the design doc's literal SQL-function wording

§5.4's mechanism cell reads: *"JSONB: `jsonb_object_keys`, `jsonb_typeof`
over a sample, key frequency, depth; XML: root element and xpath key
sampling."* This slice does **not** call `jsonb_object_keys()`,
`jsonb_typeof()`, or `xpath()` in SQL. Instead:

- Raw column values are sampled with slice 10's *existing* single-column
  SQL builder (`build_column_sample_sql` — the same `SELECT "col" AS value
  FROM ... TABLESAMPLE ...` shape `data_class_match` already uses), with no
  new SQL shape introduced.
- All key-frequency, type-consistency, depth, root-element and
  element/attribute-name inference happens in **Python**, over the sampled
  values, in `nested_schema_inference.py`.

Three reasons, in order of weight:

1. **The task's own item 4 requires the inference core to have "no
   Postgres-specific coupling."** A SQL-side implementation of
   `jsonb_object_keys`/`jsonb_typeof` is inherently Postgres-only — it
   cannot be handed a parsed JSON *file* in §6 with no database in the loop.
   Doing the inference in Python, over values that arrived as ordinary
   Python objects, is what makes the same function usable for a database
   column and a filesystem file identically. The XML side reinforces this
   further: `infer_xml_schema` uses the stdlib's
   `xml.etree.ElementTree`, not `xpath()`, for the identical reason — a
   file-based XML profile in §6 needs no database round trip to get the
   same answer.
2. **No live Postgres exists in this build environment** (same constraint
   slice 10 documented). A recursive-CTE or multi-round-trip SQL
   implementation of nested-key/type/depth extraction cannot be verified
   against a real server here, whereas the single-value sampling SQL is
   already characterized by slice 10's own tests (SQL-shape assertions,
   no live database needed) and this slice adds no new untested SQL
   surface.
3. **Reuse over reinvention** (the brief's own instruction, echoing the
   `resolve_n_distinct` duplication lesson): `sample_column_values` already
   fetches exactly the raw values this step needs. A second SQL path built
   specifically to push key/type extraction server-side would duplicate
   the sampling/budget/provenance machinery slice 10 built, for a
   marginal efficiency gain that cannot be measured here anyway.

This is a documented judgement call, not an oversight — flagged here the
same way slice 10 flagged its SYSTEM-vs-BERNOULLI choice. If a live
Postgres deployment later measures that pushing key extraction into SQL
materially reduces the bytes transferred for very large JSONB columns, that
would be a real, separate follow-up (logged to `docs/Backlog.md`), not a
correction of this choice — the reusable-core requirement stands regardless
of where the sampling happens.

---

## 3. The reusable core (`nested_schema_inference.py`)

Two independent shapes, because JSON and XML do not profile the same way:

**JSON** — `infer_json_schema(values)` walks a bag of already-parsed
Python values (`dict`/`list`/scalar) and returns:

- `scalar_count` / `array_count` / `object_count` — the TOP-LEVEL shape of
  each sampled value, which is what decides the column-level label (see
  §4);
- `document_count` — objects, plus every dict found inside a top-level
  array, unrolled — the denominator every key's presence fraction is
  stated against;
- `keys: tuple[KeyProfile, ...]` — one entry per dot/bracket-joined path
  (`"items[].sku"` for a key inside an array of objects), each carrying
  `presence_count`/`presence_fraction`, `observed_types` (a JSON-type ->
  count map), `type_consistent` (ignoring `null` — a nullable string field
  is still "a string field"), and `dominant_type`;
- `max_depth`, bounded (`DEFAULT_MAX_DEPTH = 6`, matching §5.8's spirit of
  a bound that keeps the cheap tier cheap) and a `truncated` flag when the
  distinct-key cap (`DEFAULT_MAX_KEYS = 500`) is hit.

The inconsistent-types case the task brief names specifically — a key like
`age` stored as a number in most rows and a string in a few — is exactly
what `observed_types`/`type_consistent` exist for:
`test_inconsistent_types_for_the_same_key_is_a_real_finding` pins that it
reports BOTH types and `type_consistent=False` rather than raising, picking
one, or dropping the key.

**XML** — `infer_xml_schema(raw_values)` parses each value with
`xml.etree.ElementTree` (accepting text, bytes, or an already-parsed
`Element` directly — the last option is what lets a file-based caller in
§6 skip re-serialising) and returns `root_element_counts`,
`dominant_root_element`, `parsed_count`/`unparseable_count`, and
`names: tuple[ElementNameProfile, ...]` — element AND attribute local
names (namespace prefixes stripped, matching `xpath()`'s own `name()`
behaviour), with presence measured per-document (a name occurring five
times in one document counts once, not five — the useful cross-row signal
for a schema question).

**Classification, shared vocabulary.** `classify_json_sample`/
`classify_xml_sample` reduce a built schema to one of five labels:
`LABEL_EMPTY`, `LABEL_SCALAR_ONLY` (JSON only), `LABEL_UNPARSEABLE` (XML
only), `LABEL_MIXED`, `LABEL_STRUCTURED`. These are NOT `column_matching`'s
`MATCH_*` vocabulary — there is no known element to compare against in a
profiling question, only a value shape to describe, so that vocabulary
genuinely does not fit (checked, not assumed, per the task's own
instruction to check before inventing a fourth one). What IS reused is
slice 7's `STATE_MEASURED`/`STATE_EMPTY`/`STATE_NOT_COLLECTED`/
`STATE_NOT_SUPPORTED` "did we get a sample" layer
(`resource_explorer.registry`) — the label sits *inside* `STATE_MEASURED`
as a finding, and only `LABEL_EMPTY` maps outside it (to `STATE_EMPTY`).

---

## 4. Absence discipline — the states, and what each one means

| State (`resource_explorer.registry`) | Label | Means | A real finding? |
|---|---|---|---|
| `STATE_NOT_SUPPORTED` | — | The engine declares no `value_sampling` capability | No — not established |
| `STATE_NOT_COLLECTED` | — | `catalog_stats_only`, an exhausted byte/time budget, or a failed sample query — `sample_column_values` returned `None` | No — not established |
| `STATE_EMPTY` | — | The sample ran; every value was NULL | Yes — a measurement, licensed to say "no non-NULL values" |
| `STATE_MEASURED` | `scalar_only` | Every sampled JSON value was a scalar, not an object/array | **Yes** — the column may be misclassified as needing nested profiling |
| `STATE_MEASURED` | `unparseable` | Every sampled XML value failed to parse as well-formed XML | **Yes** — a data-quality finding: the column is typed `xml` and holds non-XML content |
| `STATE_MEASURED` | `mixed` | Some sampled values were scalar/unparseable, some structured | **Yes** — inconsistent use of the column across rows |
| `STATE_MEASURED` | `structured` | Every sampled value was structured; the schema fields are populated | Yes — the ordinary case |

Every one of these renders a `SchemaAnalysisAnnotation`, including the
`not_established` ones (`STATE_NOT_SUPPORTED`/`STATE_NOT_COLLECTED`, which
carry `LABEL_EMPTY` with `schema=None` in the row and a
`not_established_reason` in the annotation's `json_properties`). Omitting
the annotation for a column nobody could profile would recreate the exact
defect this discipline exists to avoid: it would look identical to a
column that WAS profiled and found to hold no nested structure.
`TestAbsenceStates.test_every_column_still_gets_a_schema_analysis_annotation`
pins this.

**The §5.8 provenance envelope is on every claim, of both annotation
kinds this step emits** — the `ResourceMeasureAnnotation` (sample
provenance, mirroring slice 10's `_measure_annotation`) and the
`SchemaAnalysisAnnotation` itself, whose `json_properties["sample"]`
always carries the same `SampleProvenance.as_dict()`, `envelope` string
included, even for a `not_established` column (where the envelope reads
"no sample taken: ..."). `TestSampleProvenanceEnvelope` pins this on both
annotation kinds and on the not-established case specifically.

---

## 5. Registration and the mechanical catalog regeneration

`postgres_nested_columns` is registered in `survey_definition_adapter.py`'s
`_ADAPTER.re_analysis_steps`/`re_analysis_step_info`, the same pattern
slices 7–10 used, and dispatched through `DatabaseSurveyor.survey(steps=
["nested_columns"])` — a new opt-in step, NOT in `_ALL_STEPS`, for the same
reason `"column_profile"` is not: design §5.7 prices it `api_heavy /
medium`, the only other step in the family (besides `column_profile`) that
reads actual table data.

`nested_column_profile` (the *question* id, distinct from the *step* id —
the same naming split slice 10 has between `data_class_match`/
`reference_data_match` and `postgres_column_profile`) was added to
`analysis_catalog.yaml`'s `database_analyses`, and to
`DATABASE_ANALYSIS_STEP_MAP` (`["schema", "statistics", "nested_columns"]`
— needing `"statistics"` for the same reason `data_class_match`/
`reference_data_match` do: the sample's provenance is stated against the
per-table row counts `"statistics"` collects).

Adding the id to `analysis_catalog.yaml` made `question_catalog.yaml`
stale, exactly as slice 10 documented for its own two ids: the CSV
(`docs/dr-egeria/resource_questions.csv`, **not touched by this slice** —
its row already read `GAP: nested_column_profile (proposed)`, anticipating
this slice, per stream 4's own note) now resolves that GAP note to a real
id the generator recognises. The committed `question_catalog.yaml` is a
**generated** file (`test_question_catalog_generator_guard.py` fails loudly
if it drifts from what the CSV generates), so the fix — required for the
full suite to pass, and mechanical rather than a content edit — was to
re-run the generator:

```bash
python scripts/csv_to_question_catalog_yaml.py docs/dr-egeria/resource_questions.csv \
    --output resource_explorer/configdata/question_catalog.yaml
```

The resulting diff is two lines: the GAP row's `analysis_ids: []` becomes
`analysis_ids: [nested_column_profile]`. No CSV row, no question wording,
and no `facts.py` were touched — the task's restriction on those three is
observed to the letter; only the generated artifact was refreshed, the same
way slice 10 refreshed it and for the identical reason.

`tests/test_database_surveyor_steps.py::TestDatabaseAnalysisStepMap::
test_maps_all_local_survey_ids` is an exact-set assertion over
`DATABASE_ANALYSIS_STEP_MAP`'s keys and needed a one-line update to include
the new id; a mirrored `test_nested_column_profile_also_needs_statistics`
was added alongside slice 10's own `test_column_profile_backed_ids_also_
need_statistics`.

---

## 6. What is scoped out, and why

- **No new structured storage table.** §5.7's table list for this phase
  (`database_schemas`, `database_tables`, `database_columns`,
  `database_column_profiles`, `database_table_activity`,
  `database_grants`, `database_sql_objects`, `database_settings`) does not
  include a nested-columns detail table, and the task's own "what to
  produce" list asks for the annotation, not a new registry row family.
  This slice's output lives entirely in annotations plus a step-result
  dict (`nested_columns.columns`), matching `sql_analysis`/`db_derived`'s
  footprint rather than `postgres_column_profile`'s (which DOES have its
  own row family, because it merges into `database_column_profiles`
  alongside slice 7's numbers).
- **No DRAFT proposal / RFA path.** §5.4's `nested_column_profile` row
  does not ask for one (unlike `data_class_match`/`reference_data_match`,
  which explicitly propose new Data Classes/Valid Value Sets). A
  `scalar_only`/`unparseable`/`mixed` finding is surfaced as a distinct
  annotation label, which is what the task brief asks for ("worth its own
  state") — it does not ask for an action request, and inventing one would
  be scope creep beyond a Sonnet-scoped mechanical slice.
- **No `nested_schema_profile` (filesystem) implementation.** Explicitly
  named in the task as "not part of this slice." `nested_schema_inference.py`
  is built so that work is a glue module away, not a rewrite — it imports
  nothing this slice's Postgres glue owns.
- **XML depth beyond the stdlib parser's own recursion bound is not a
  second sampling budget.** `max_depth`/`max_names` on `infer_xml_schema`
  reuse the same defaults as the JSON side (`DEFAULT_MAX_DEPTH`,
  `DEFAULT_MAX_KEYS`) rather than a separate XML-specific budget — §5.8
  does not ask for one and adding it would be an unrequested knob.

---

## 7. What could not be tested

**No live Postgres or live Egeria exists in this build environment**
(unchanged from slice 10's own note). Specifically not verified against a
real server:

- The actual `TABLESAMPLE`/SQL shape for JSONB and XML columns — this is
  unchanged from slice 10's own machinery (`build_column_sample_sql`), so
  it inherits slice 10's own SQL-shape tests rather than needing new ones,
  but neither slice has run against a real Postgres instance.
- Whether a real JSONB driver hands back already-deserialised Python
  objects or raw text in this codebase's actual `execute_query` path.
  `_parse_json_value` handles both (`isinstance` check, then
  `json.loads` for text), and both branches are covered by
  `TestScalarOnlyIsARealFinding.test_already_parsed_dict_values_are_
  accepted_directly` and the ordinary string-value tests — but which
  branch a real run actually takes was not observable here.
- Whether a real `xml` column's stored bytes always parse cleanly with
  `xml.etree.ElementTree` — Postgres's own `xml` type validates
  well-formedness at write time for `xml.CONTENT` documents but not
  necessarily for `xml.CONTENT(CONTENT)` fragments, so a live column could
  in principle produce more `LABEL_UNPARSEABLE`/`LABEL_MIXED` findings
  than a synthetic test sample would suggest. This does not change the
  code path — that is exactly the state the label exists for — but it was
  not observed against a real deployment.
- The full-suite pre-existing failure
  (`test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
  test_a_cataloged_database_is_findable_by_name`) needs live Egeria and is
  unrelated to this slice — confirmed unrelated by running it in isolation
  before this slice's changes were made.

---

## 8. Where the code lives

| File | New? | What |
|---|---|---|
| `resource_explorer/surveyors/nested_schema_inference.py` | new | The reusable JSON/XML schema-inference core — zero database coupling |
| `resource_explorer/surveyors/database/nested_columns_step.py` | new | The Postgres-specific glue: column discovery, sampling, annotation building |
| `resource_explorer/surveyors/database/database_surveyor.py` | +1 step branch | Opt-in `"nested_columns"` step, delegating to the above |
| `resource_explorer/surveyors/database/survey_definition_adapter.py` | +1 entry | `postgres_nested_columns` as a `re_analysis_step` |
| `resource_explorer/configdata/analysis_catalog.yaml` | +1 entry | `nested_column_profile` |
| `resource_explorer/configdata/question_catalog.yaml` | regenerated | Mechanical — see §5 |
| `tests/test_nested_schema_inference.py` | new | The reusable core, 30 tests |
| `tests/test_postgres_nested_columns.py` | new | The Postgres glue, 23 tests |
| `tests/test_database_surveyor_steps.py` | +2 tests, 1 updated | The new analysis id in `DATABASE_ANALYSIS_STEP_MAP` |

## 9. Worth logging to `docs/Backlog.md`

- If a live deployment later shows pushing `jsonb_object_keys`/
  `jsonb_typeof` extraction into SQL materially reduces bytes transferred
  for very large JSONB columns compared to sampling raw values and parsing
  in Python (§2's documented deviation), that would be a real follow-up —
  logged here rather than acted on, since it cannot be measured without a
  live Postgres instance at realistic scale.
- No DataHub/curator-facing surface yet renders a `scalar_only`/
  `unparseable`/`mixed` finding distinctly from an ordinary `structured`
  one — same gap class slice 10 found for DRAFT proposals
  (`POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md` §4's "visibility finding"). Not
  fixed here because it is a UI change across `web/routes/databases.py`'s
  `EgeriaAnnotationItem` and `index.html`'s `renderAnnotations`, which is
  out of scope for a Sonnet-scoped step-implementation slice — flagged for
  a follow-up the same shape as that one.

# postgres_column_profile, data_class_match, reference_data_match: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, Phase 1 slice #10 —
*"`postgres_column_profile` with sampling config (design §5.8),
`data_class_match`, `reference_data_match`, RFA proposal convention (support
doc §3)."* Gate: probes 4 and 5 (both run 2026-09-21, both clear —
`PROBES-2026-09-21.md`) and slice 7 (`postgres_schema_and_stats`, merged).

**Implements:** `multi-resource-questions-design.md` §5.4 (the
`data_class_match` and `reference_data_match` rows), §5.7 (the
`postgres_column_profile` step), §5.8 (sampling strategy as configuration,
and the two rules that follow from it), plus
`egeria-support-for-multi-resource.md` §3's proposal convention as revised by
the **corrected** probe 4.

**Branch:** `re/postgres-column-profile`, from `main` at `ca0388b9` (the
probes-4/5 merge). No PR stacking was needed — slice 7 had already merged.

**Tests:** 131 new tests in `tests/test_postgres_column_profile.py`, all
passing. Full suite: **5,477 passed, 103 skipped, 1 failed** — the one failure
is pre-existing and needs live Egeria (see "What could not be tested"). Three
existing tests were updated because they pinned states this slice supersedes.

---

## 1. Sampling is configuration (design §5.8)

`resource_explorer/surveyors/database/sampling.py` — new. §5.8 is a
configuration *surface*, not one knob, and it is implemented as one:

| §5.8 setting | Implemented as | Notes |
|---|---|---|
| `strategy` | `CATALOG_STATS_ONLY` / `HEAD` / `RANDOM` / `SYSTEMATIC` / `STRATIFIED` / `FULL` | All six. An unknown value **raises** rather than defaulting |
| defaults per use case | `DEFAULT_MEASURE_SAMPLING` (`catalog_stats_only`), `DEFAULT_MATCHING_SAMPLING` (`random`) | §5.8 gives one default *per use case*, so one step resolves **two** configs |
| `max_rows` / `max_bytes` / `max_values` | 10,000 / 64 MB / 1,000 | `max_bytes` is a step-wide budget (`SamplingBudget`), the other two are per column |
| `seed` | fixed per resource via `stable_seed_for(slug)` | SHA-256 of the slug, **not** `hash()` — which is salted per interpreter and would give a different sample every restart, the exact opposite of the setting's purpose |
| `time_budget` | `time_budget_seconds`, default 60, consumed by `SamplingBudget` | §5.8's own spelling `time_budget` is accepted as an alias so a settings file can use the design doc's name |
| scope | `resolve_sampling_config(global → resource_type → resource → run)` | The resolved config records **which scope** supplied the last override, so a surprising sample is attributable |

### `TABLESAMPLE`, and the SYSTEM-vs-BERNOULLI call

§5.8 names "`TABLESAMPLE SYSTEM`/`BERNOULLI`" and does not choose between
them, so this slice chose, once and explicitly (`choose_tablesample_method`):

- **BERNOULLI** below 50,000 rows, at or above a 5% requested fraction, or
  when the row count is unknown. It considers every row, so the sample is
  unbiased; on a small table, or when most pages would be read anyway, that
  costs almost nothing. An unknown size picks correctness over an
  unjustifiable saving.
- **SYSTEM** otherwise — the cheap path, and the whole reason §5.8 prefers
  `TABLESAMPLE` to `ORDER BY random() LIMIT n`: it reads a fraction of the
  *pages* rather than sorting the table to return a handful of rows.

`REPEATABLE (seed)` is always emitted when a seed is set. A test asserts that
`random()` appears nowhere in the generated SQL.

The requested percentage is `100 × max_rows × 3 / total_rows`, capped at 100.
The 3× oversample is a judgement (SYSTEM returns approximately that fraction
of pages, and NULLs are then filtered and a `LIMIT` applied) — and it is
recorded in the provenance as the percent that actually ran, so a real
deployment can see whether it was enough rather than having to read this
document.

### The envelope is a hard requirement, and it is one function

`SampleProvenance.describe()` is the only place §5.8's sentence is built:

> `from a random sample of 10,000 of 4,200,000 rows (TABLESAMPLE SYSTEM 0.714286%, seed 7, 2026-09-21)`

It refuses to imply a proportion it cannot support: an unknown total row count
reads *"of an unknown total number of rows"*, and `as_dict()` serialises it as
`"not_established"` rather than `0` — a sample "of 0 rows" is a measurement,
a sample of an unknown total is not.

And `ColumnMatch.describe()` carries it into every claim. There is no path
through that method producing the word "conforms" without a fraction **and**
the sample envelope in the same sentence; `TestStatementsAreAlwaysQualified`
asserts this over every verdict rather than case by case.

---

## 2. `data_class_match` (design §5.4)

`resource_explorer/surveyors/database/column_matching.py` — new, pure logic,
no pyegeria or psycopg2 import.

Scores every `DataClass` the platform holds on §5.4's three signals — column
name, type, value-pattern conformance — weighted 0.25 / 0.05 / 0.70. Value
evidence dominates because it is the only signal that observes the data; type
alone barely narrows anything, since half a schema is `text`.

The judgement calls, all named constants in one place:

| Constant | Value | Why this value |
|---|---|---|
| `VALUE_MATCH_THRESHOLD` | 0.95 | Not 1.0: real columns carry a handful of legacy or test rows, and one malformed address should not stop a column being recognised as email. Not 0.80: a Data Class match drives a PII decision, and a fifth of the column disagreeing is not a conformance — it is two populations in one column |
| `MIN_SAMPLE_VALUES_FOR_VERDICT` / `MIN_DISTINCT_VALUES_FOR_VERDICT` | 20 / 8 | Below this, any regex fitting the sample fits by luck |
| `NAME_AND_TYPE_ONLY_CONFIDENCE` | 60 | A cap, deliberately below `VALUE_MATCH_THRESHOLD × 100`. Name-and-type evidence is real (it is how `pii_scan` worked) and it is **not** conformance, so it must never be reported at the same confidence as a tested match |

Three rules that fall out and are each pinned by a test:

- **A declared, tested, failing value specification is disqualifying whatever
  the name says.** A column called `email` full of integers is not an email
  column — and the name is exactly the evidence that would otherwise carry it
  over the line.
- **`dataPatterns` is a list of alternatives, so conformance is computed over
  their union**, not as the max of the per-pattern fractions. A column half
  `+44…` and half `(555) …` fully conforms to a two-pattern phone class and
  scores 50% against each pattern alone.
- **A `contentStatus: DRAFT` class is excluded by default.** It is a proposal,
  quite possibly one an earlier run of this same step made. Matching against
  it would let RE ratify its own guess: run one proposes `email_like`, run two
  reports the column "conforms to email_like", and a guess has become a
  finding with no curator in the loop. `include_draft_classes=True` enables
  it, and the match then records `matched_element_is_draft` and says so in the
  sentence.

**"A class we do not have yet"** is answered by a 16-pattern library
(`VALUE_PATTERNS`: email, UUID, IPv4, URL, ISO date/timestamp, phone, payment
card, IBAN, currency/country code, UK postcode, US ZIP, semver, hex digest,
prefixed identifier), each anchored with `fullmatch`. `fullmatch` rather than
`search` is load-bearing: a `search`-based email pattern matches every
free-text comment that happens to mention an address, which would classify a
notes column as PII and bury the columns that are. Patterns flagged
`privacy_relevant` route a proposal to the Privacy perspective too.

---

## 3. `reference_data_match` (design §5.4)

`n_distinct` comes from **slice 7's stored
`database_column_profiles.distinct_count`**, resolved through
`resolve_n_distinct()` — not re-derived with a `COUNT(DISTINCT …)`, which
would cost a full scan and could disagree with the stored row.

**The sign convention, and a note on slice 9.** The brief said to reuse slice
9's `_resolve_n_distinct` rather than re-derive it. Slice 9 (`db_derived`) had
**not merged** when this slice started — `main` was at the probes-4/5 merge and
there is no `db_derived.py` in the tree — so there was nothing to import.
`resolve_n_distinct` is therefore implemented here, as a **public** name so
slices 9 and 11 can import it instead of adding a third copy. It handles all
three of Postgres's meanings for that one signed column: `> 0` is a count,
`< 0` is the negative of the distinct values' *fraction of the row count*, and
`0` is unknown (returned as `None`, never `0` — an all-NULL column is a
measurement and this is the absence of one). Reading `-1` literally says "one
distinct value" when it means "every value is distinct", which would put every
primary key into the reference-data candidate set;
`test_the_unique_key_is_not_treated_as_reference_data` pins that end to end.

> **If slice 9 lands its own `_resolve_n_distinct`, the two must be reconciled
> to one before both are in the tree.** Logged to `docs/Backlog.md`.

The low-cardinality gate is **both** conditions, not either: at most 64
distinct values **and** at most 5% of rows. 40 distinct values is
reference-data-shaped in a million-row table and is just "a small table" in a
60-row one. An unknown distinct count returns `None` from
`is_low_cardinality`, which gates the question rather than answering it.

Four outcomes, per §5.4:

| Coverage of the sampled distinct values | Verdict | Published as |
|---|---|---|
| 1.0 | `matched` | `RelationshipAnnotation` advising a `ValidValuesAssignment` |
| ≥ 0.60, < 1.0 | `partial_match` | Advice **plus** an RFA naming the values outside the set |
| < 0.60, or no set at all | `unmatched_patterned` | A proposed `ValidValueSet` with `contentStatus: DRAFT` |
| distinct list truncated at `max_values` | `inconclusive` | — see below |

**Why a truncated list is inconclusive rather than partial**: a coverage
fraction computed from an incomplete list is wrong in an *unknown direction* —
the values that were not fetched might all be inside the set or all outside
it. That is why `build_distinct_values_sql` requests `max_values + 1`: without
the +1, a column with exactly `max_values` distinct values and one with a
million are indistinguishable.

**"`ValidValuesAssignment` advice/annotation"** — Egeria has no
`ValidValuesAnnotation`; `ValidValuesAssignment` is a *relationship*. So the
advice is published as a `RelationshipAnnotation`
(`RelationshipAdviceAnnotationProperties`) naming that relationship type and
the set to bind to. That is exactly what a relationship-advice annotation is
for, and it keeps the finding a suggestion a curator accepts rather than a
binding RE made unilaterally.

---

## 4. `contentStatus: DRAFT` — the mechanism, and the visibility finding

### The mechanism

`egeria_reference_catalog.py` creates proposals as **normal elements with
`contentStatus: DRAFT` inside `properties`**, linked to their evidence
annotation via `AssociatedAnnotation`. This is §3's revised guidance, and the
corrected probe 4 is what makes it available: it round-trips as `DRAFT` while
the element's own `ElementStatus` stays `ACTIVE`.

`initialStatus` is sent **nowhere**, and a test asserts its absence. It sets
`ElementStatus`, means something else entirely, and is dropped silently by
pyegeria's `NewElementRequestBody` (ISSUE-113) — it is what probe 4's first
pass tested by mistake.

Field names were taken from pyegeria's own REST reference rather than
inferred, and two of my initial assumptions were wrong: it is
**`namespacePath`** (not `namespace`) and **`dataPatterns`** as a list (there
is no `valuePattern` on `DataClassProperties`). Two live bugs in RE's own
`bootstrap_data_classes.py` were found in passing and deliberately **not**
copied: its `create_data_class` body omits the `properties.class`
discriminator, and it calls `link_valid_value_definition` with no body, which
makes pyegeria synthesise one and POST it un-serialised. Both logged to
`docs/Backlog.md`.

Proposal identity is keyed on the **column**, not on the detected pattern, so
two runs converge on one element rather than creating a second proposal — the
pattern is exactly the thing a second run might decide differently.

### The visibility finding — this was the load-bearing question, and the answer was no

The peer review flagged, and `PROBES-2026-09-21.md` sharpened: *does any
consumer read path surface `contentStatus`, or does a draft render identically
to confirmed content?*

**Measured, not assumed: it did not, anywhere.** Every single occurrence of
`contentStatus` / `content_status` / `ContentStatus` in the package was an
outbound write (the two arch-recovery materializers) or a comment. Zero hits
in any reader, any API model, any template, any JS. `userDefinedContentStatus`
appeared only in a doc line. So slice 10's entire proposal mechanism would
have been **invisible** — a DRAFT candidate class rendered exactly like a
confirmed finding, which is worse than the RFA fallback it replaced.

It is also a **four-hop chain**, because every hop whitelists fields and two
of them drop an undeclared field with no error at all. So this slice surfaced
it, end to end:

1. `surveyors/egeria_survey_reader.py` — `get_annotations_by_report_guid`
   now carries `content_status` (and `user_defined_content_status`).
2. `web/routes/egeria.py`, `web/routes/databases.py`,
   `web/routes/filesystems.py` — all three copies of `EgeriaAnnotationItem`
   declare `content_status: str = ""`. All three, because they are read by
   **one** frontend renderer: a field in two of them shows a badge on two tabs
   and silently not on the third. Pydantic v2's `extra='ignore'` means
   skipping this step makes the whole change invisible with no error.
3. `web/static/index.html` — `renderAnnotations()` draws an amber `DRAFT`
   badge with a tooltip explaining it is an unconfirmed proposal. One edit
   covers the database, repo and filesystem tabs and the Survey-Definition run
   panel, since that is the only annotation renderer in the frontend.
4. Outbound: `Annotation.content_status` on the dataclass and two lines in
   `annotation_props.build_annotation_props`. Guarded on truthiness, so the
   wire payload is byte-identical for every caller that does not set it — and
   because `build_annotation_body` is the single shared builder, the direct
   path and the outbox cannot drift, so a retried proposal cannot lose its
   draft-ness.

**Verified on the surface the user reads**, not only where it was written: the
`renderAnnotations` function was extracted into a harness and rendered in a
browser. The DRAFT proposal shows an amber badge; the confirmed match and the
not-established row show none. An empty `contentStatus` deliberately draws
**no** badge — "not stated" is what every annotation published before this
slice carries, and it is not the same as "confirmed".

`TestContentStatusIsSurfaced` pins each hop so a future refactor cannot
quietly re-break the chain.

**Two known remaining gaps** (both logged to `docs/Backlog.md`, neither
blocking):

- The **older** `/{slug}/annotations` path (`surveyors/egeria_reader.py`'s
  `_parse_annotation` → `AnnotationItem` in `egeria.py`) still does not carry
  `contentStatus`. It is a separate model and a separate renderer.
- **No DataClass/ValidValueSet browsing surface exists at all.** A draft
  proposal is visible as an *annotation* now; the proposed *element* itself is
  only visible in Egeria's own UI, because RE has no screen that lists Data
  Classes or reference sets. The single real property read of a governance
  element anywhere in RE (`database_surveyor.py`'s PII-keyword lookup) never
  reaches a UI. A curator's accept/reject queue — design §11's review queue —
  is the natural home and is not built.

---

## 5. Absence discipline — the states, and how each is tested

Slice 7's `STATE_MEASURED` / `STATE_NOT_COLLECTED` / `STATE_NOT_SUPPORTED`
vocabulary is **extended, not replaced**: those three answer "did this
measurement happen" and still carry `database_column_profiles.state`. Matching
genuinely has more shapes, so `column_matching` adds a verdict vocabulary —
including the two the brief named specifically, "sampled but inconclusive" vs
"not sampled at all", as distinct constants.

| Verdict | Means | Established? | Test |
|---|---|---|---|
| `matched` | Values read, a candidate conformed | yes | `test_a_conforming_column_matches` |
| `partial_match` | Reference set covers some values, not all | yes | `test_a_partial_match_names_the_values_outside_the_set` |
| `no_match` | Values read, candidates existed, nothing conformed — **the only negative that is a finding** | yes | `test_no_match_when_candidates_exist_and_nothing_conforms` |
| `unmatched_patterned` | Nothing known matched, but the values are regular enough to propose one | yes | `test_an_unmatched_but_patterned_column_becomes_a_proposal` |
| `inconclusive` | Compared, and the sample cannot support a verdict — too few values, or a truncated list | **no** | `test_sampled_but_too_thin_is_inconclusive_not_no_match`, `test_a_truncated_distinct_list_is_inconclusive_not_partial` |
| `not_sampled` | No value read at all: `catalog_stats_only`, absent capability, exhausted budget, failed query | **no** | `test_not_sampled_is_never_no_match` |
| `no_candidates` | Values read, platform held nothing to compare against — **or could not be read** | **no** | `test_an_empty_platform_is_no_candidates_not_no_match`, `test_an_unread_platform_gives_no_candidates_not_no_match` |
| `not_applicable` | The question does not apply (binary column; cardinality outside the gate) | **no** | `test_binary_columns_are_not_applicable_not_unmatched` |

`NOT_ESTABLISHED_VERDICTS` groups the four, so a consumer gets the distinction
by importing one name instead of remembering it. Every unestablished verdict
publishes at **confidence 0** (slices 7 and 8's convention), so two columns'
annotations can be told apart by the number alone without parsing prose, and
`describe()` says *"not established … This is not a statement that no match
exists."*

Four separate causes of "no sample", each named in the record rather than
collapsed:

1. the engine declares no `value_sampling` capability — `STATE_NOT_SUPPORTED`
   on the row, plus one confidence-0 annotation saying so;
2. the strategy is `catalog_stats_only`, so no value was ever read;
3. the `max_bytes` or `time_budget` budget ran out — the remaining columns are
   `not_sampled` **with the budget named**, so a 30-column table with 12
   sampled and 18 not-established is legible, rather than 12 findings and 18
   silences;
4. the sample query itself failed (a permission error on one table) — reported
   as not-sampled with the error, never as an empty result. A failed sample is
   not an empty sample.

And `sample_column_values` returns `None` for "not sampled" but `[]` for "the
sample ran and the column had no non-NULL values" — a measurement. The two are
different and a test pins both.

**Egeria-side absence gets the same treatment**, which the design docs did not
ask for and which the `find-absence-as-answer` review made obvious:
`ReferenceCatalog.available` distinguishes *"the platform holds no Data
Classes"* (a fact, and a reason to propose) from *"we could not ask"* (neither).
An unread catalogue makes every verdict `no_candidates` with the failure's own
message in the sentence — a run that never asked what exists has established
nothing about whether a column matches something. The adapter's catalogue-read
failure is recorded in three observable places (every verdict, a confidence-0
annotation, and the step's `reference_catalog_error` output field), which is
also how it satisfies the `no_silent_success` ratchet rather than being added
to its baseline.

---

## 6. The RFA convention (support doc §3) — what DRAFT does *not* replace

§3's RFA path was described as the plan "until DRAFT works". DRAFT works, so
the RFA is no longer the proposal carrier — **except in two cases, and the
distinction was worth getting right:**

1. **A partial reference-data match keeps its RFA even with DRAFT
   available.** The set already exists and some sampled values fall outside
   it, so there is no candidate element to create — the decision is whether to
   extend the set or fix the data. §5.4 asks for this by name ("partial → RFA
   listing the unmatched values"), and the RFA lists them, truncated at 40
   with a count.
2. **A proposal that could not be created as a DRAFT element**, because no
   Egeria client was available to this run. The RFA is then genuinely §3's
   fallback, carrying the observed pattern or values so the proposal is not
   lost.

No RFA is raised for an unestablished verdict — that would be an action
request about nothing.

---

## 7. Where the code lives

| File | New? | What |
|---|---|---|
| `surveyors/database/sampling.py` | new | §5.8's configuration surface, the SQL builders, `SampleProvenance` |
| `surveyors/database/column_matching.py` | new | Both questions' logic, the verdict vocabulary, `resolve_n_distinct`, the pattern library |
| `surveyors/database/egeria_reference_catalog.py` | new | Reading the platform's Data Classes / Valid Value Sets; DRAFT proposal creation; `AssociatedAnnotation` |
| `surveyors/database/column_profile_step.py` | new | The step: sampling execution, budget, annotation building, the RFA decision |
| `surveyors/database/database_surveyor.py` | +1 step branch | Opt-in `"column_profile"` step, delegating to the above. Slice 7/8's code is otherwise untouched |
| `surveyors/database/survey_definition_adapter.py` | +1 entry | `postgres_column_profile` as a `re_analysis_step` |
| `surveyors/database/connection.py` | +1 capability | `value_sampling`, True on Postgres |
| `surveyors/survey_report.py`, `surveyors/annotation_props.py` | +`content_status` | The outbound half of the contentStatus chain |
| `surveyors/egeria_survey_reader.py`, 3 × `web/routes/*.py`, `web/static/index.html` | +`content_status` | The inbound half |
| `registry.py` | +1 column | `database_column_profiles.sample_total_rows`, plus its migration entry |
| `configdata/analysis_catalog.yaml` | +2 entries | `data_class_match`, `reference_data_match` |

The step logic is a module of its own rather than four more methods on
`DatabaseSurveyor` on purpose: slices 7 and 8 own that file, this slice
**consumes** their output (`schema_info`'s column catalog, `statistics`' row
counts, `column_profile_rows`' `pg_stats` `n_distinct`) rather than extending
their code, and `survey()` reaches it through one delegating branch.

`"column_profile"` is **not** in `_ALL_STEPS`, same as `"operations"`: design
§5.7 prices it `api_heavy / medium` and it is the only step in the family that
reads actual table data, so it must never ride along on a default survey.
Requesting it does force `"statistics"` to run, though, because without the
row counts and the stored `n_distinct` the step runs and establishes
nothing — a run that looks like it worked. Same invariant, same reasoning, as
`"schema"`.

### A generated file needed regenerating, not hand-editing

Adding the two ids to `analysis_catalog.yaml` made
`configdata/question_catalog.yaml` stale, exactly as in slice 8: the CSV
(stream 4's content, **not touched here**) already named both ids in its
`GAP: … (proposed)` notes, anticipating this slice. The fix was to re-run the
generator, per the guard test's own error message:

```
python scripts/csv_to_question_catalog_yaml.py docs/dr-egeria/resource_questions.csv \
    --output resource_explorer/configdata/question_catalog.yaml
```

A mechanical regeneration of a derived file — both source files are unchanged
by it — not a hand-edit of authored question content. 14 lines changed.

**Left as-is, not fixed here:** the regenerated rows' `note` text still reads
`GAP: data_class_match (proposed) — …` even though `analysis_ids` is now
populated. Rewording the CSV's prose is its owner's call (stream 4), not this
slice's to make unilaterally — the same call slice 8 made, and flagged in
`docs/Backlog.md`.

---

## 8. Scoped out, and why

- **`SemanticAnnotation` / `semantic_suggestions`.** Design §5.7 lists
  "Semantic" among this step's outputs, but `semantic_suggestions` is its own
  §5.4 analysis id and is not among this slice's three deliverables. It is
  also **unbuildable as specified right now**: `SemanticAnnotation` /
  `SemanticAnnotationProperties` does not exist anywhere in pyegeria (zero
  occurrences, checked against the whole checkout) and is not in RE's own
  `AnnotationType` enum. Probe 5 created one via the generic
  `create_annotation`'s `properties.class` discriminator, so the server
  accepts it — but wiring a net-new annotation type for an analysis id this
  slice does not own is a slice of its own. Logged.
- **`postgres_nested_columns`** (slice 11) — shares this slice's inference
  core per the coordinator brief and is explicitly the next slice. The pattern
  library, `SamplingConfig`, `SampleProvenance` and the verdict vocabulary are
  all public and reusable for it; nothing was built for JSON/XML here.
- **`data_class_match` × `privilege_audit`** (design §5.4/§5.6's
  sensitive-exposure composite). This slice produces the sensitivity half;
  crossing it with slice 8's privilege half is a composite, not either
  analysis.
- **A curator accept/reject queue** (design §11's review queue). A DRAFT
  proposal is created and is visible as an annotation; accepting it — clearing
  `contentStatus`, creating the `ValidValuesAssignment` for real — has no
  surface. This is the largest honest gap in the slice and is logged as such.
- **`ValidValueSet` as a real entity type is unverified.**
  `build_proposed_valid_value_set_body` passes `typeName: "ValidValueSet"`
  inside `properties`, because pyegeria has **no** `create_valid_value_set`
  and no `ValidValueSetProperties` — a set must be created through
  `create_valid_value_definition`. That `typeName` path is exercised nowhere
  in either repo, so it is unverified against a live server; the create
  succeeds without it, producing a plain definition rather than a set. Logged.
- **Non-Postgres engines.** `value_sampling` is declared True on Postgres
  only. DuckDB has `USING SAMPLE` rather than `TABLESAMPLE`, which is a
  different builder; it declares the capability False and every verdict is
  honestly `not_established`.
- **`stratified`'s strata column is caller-supplied**, not inferred. Picking a
  partition key or date column automatically is a real feature and a
  guess-prone one; `stratified` with no `strata_column` **raises** rather than
  degrading to `random`, because a downgraded strategy would be recorded
  truthfully in the provenance and still be the wrong answer to what was
  asked, with nothing to reveal it.

---

## 9. What could not be tested

**No live Postgres**, same limitation slices 7 and 8 both recorded. Every
query this slice builds is exercised only through
`_FakeSamplingConnection`, a duck-typed stand-in that records the SQL it was
asked to run. That characterises the step's logic end to end and **cannot**
confirm the statements parse and behave against a real server. Specifically
unverified:

- that `TABLESAMPLE SYSTEM (0.714286) REPEATABLE (237511)` is accepted as
  written, and that the 3× oversample factor actually fills `max_rows` —
  the reason the percent that ran is recorded in the provenance rather than
  only documented here;
- that `SYSTEMATIC`'s `row_number() OVER ()` over heap order behaves as
  assumed, and what it really costs;
- `STRATIFIED`'s window against a real partitioned table.

**No live Egeria.** The proposal path is tested against `_FakeDesigner` /
`_FakeRefManager` / `_FakeDiscovery`, with every request body asserted
field by field. The bodies' *shapes* are not guesses — they are taken from
pyegeria's own REST reference files and from the corrected probe 4's
live-verified result — but these specific calls have not been made:

- `create_data_class` with this exact proposal body (probe 4 verified
  `contentStatus: DRAFT` round-trips on a `DataClass`, so the mechanism is
  live-verified; this body is not);
- `create_valid_value_definition` with `typeName: "ValidValueSet"` (see
  §8 — unverified anywhere in either repo);
- `link_annotation_to_described_element` (`AssociatedAnnotation`). §3 read the
  type from Egeria's Java source and pyegeria wraps the endpoint, but no probe
  ever created one. **This is the one call in the slice whose endpoint has
  never been exercised from Python at all**, and it is worth its own probe
  before relying on the evidence links.

**What was verified for real:** the `contentStatus` badge, in a browser,
against the actual `renderAnnotations` function extracted from `index.html` —
because "built it, verified where it was written, reported done, and the
screen still showed nothing" has happened four times in this repo, and the
whole point of §4's chain is the screen.

**Full suite:** 5,477 passed, 103 skipped, 1 failed. The failure is
`tests/test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
test_a_cataloged_database_is_findable_by_name` — **confirmed pre-existing** by
stashing this slice's entire diff (`git stash push -- packages/resource-explorer`,
restored by SHA and dropped by tag) and re-running that one test on the clean
base, where it fails identically. It needs a live Egeria this environment does
not have and is unrelated to column profiling.

Three existing tests were updated, each because it pinned a state this slice
supersedes:

- `test_database_surveyor_steps.py::TestDatabaseAnalysisStepMap::
  test_maps_all_local_survey_ids` — the step map gained two ids. A new test,
  `test_column_profile_backed_ids_also_need_statistics`, pins **why**
  `"statistics"` is in their step lists.
- `test_no_silent_success.py`'s two ratchets — satisfied by making the
  adapter's catalogue-read failure observable (option 1), **not** by adding it
  to the baseline.

---

## 10. Found along the way — logged to `docs/Backlog.md`, not fixed here

1. **A real bug in this slice's own first draft**, worth recording because it
   was caught by a test rather than by reading: `name_similarity` lower-cased
   before tokenising, so `emailAddress` became the single token
   `emailaddress` and scored **0.0** against `email_address`'s two tokens.
   Both spellings are ordinary — Egeria's property names are camelCase,
   Postgres columns are snake_case — so this was the common case, not an edge
   one, and it would have silently suppressed the name signal on roughly half
   of all real matches. Fixed here (`_CAMEL_BOUNDARY_RE`).
2. `bootstrap_data_classes.py`'s `create_data_class` body omits the
   `properties.class` discriminator every other create path in both repos
   sets.
3. `bootstrap_data_classes.py` calls `link_valid_value_definition` with no
   body, which makes pyegeria synthesise one from its own internal `prop` hint
   and POST it un-serialised.
4. pyegeria's `link_annotation_to_described_element` and
   `attach_annotation_to_report` both pass a `prop` hint that is not a real
   Egeria properties class — harmless while a body IS passed, a malformed
   POST when one is not. This slice always passes an explicit body; the
   pyegeria-side issue is worth filing (**not** fixing in place — that repo's
   policy is log-and-wait).
5. The `reference_data_match`/`resolve_n_distinct` overlap with slice 9, above.
6. `contentStatus` is still not carried by the older `/{slug}/annotations`
   read path, and there is no DataClass/ValidValueSet browsing surface at all.
7. The regenerated question-catalog rows' `GAP: … (proposed)` prose is now
   stale for both of this slice's ids — the CSV owner's call.

# Implemented: type-agnostic question-catalog plumbing (stream 2)

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md` stream 2, which implements
`docs/multi-resource-questions-design.md` §13 **Phase 0 items 1–4** and the five
`repo` hardcodes its §1.1 enumerates.
**Branch:** `re/question-catalog-multi-type`, branched from `0acb2565`.
**Scope:** local plumbing only. No live Egeria work, no `registry.py`, no new
question rows.

---

## ⚠ CONTESTED — one change needs a ruling before it is settled

**The `Funnel Stage` vocabulary dropping `Automate` (item 4) is an open
disagreement, not a settled decision.** The coordinator relayed, mid-stream,
that a designer review of the multi-resource design doc objects to its §1/§2.1
on the grounds that `RULING-NAV-GROUPING.md` places Automate in the navigation.
The change was already made when that arrived, and the coordinator's
instruction was to keep it and flag it here rather than revert.

**Exactly what would be reverted**, in `docs/dr-egeria/resource_questions.csv`
— one row, identified by its question text:

> *"How much has changed since the last time this was surveyed — is it worth
> re-running now?"*

| Column | Before | After |
|---|---|---|
| `Funnel Stage` | `Automate` | `Discovery` |
| `Catalog History` | *(empty)* | a sentence recording the ruling and the date |
| `Resource Types` | *(column did not exist)* | `repo` |

It was the **only** row in the CSV using `Automate` as a Funnel Stage.
Reverting is a one-cell edit plus a regeneration of both generated outputs
(`question_catalog.yaml` and `docs/dr-egeria/questions/scouting-questions.md`,
which now say `Discovery` where they said `Automate`). Nothing else in this
branch depends on it; items 1, 2, 3, 5 and 6 are independent.

Two things worth separating if the objection is discussed, because the design
doc and the ruling may not actually conflict:

- `Automate` as a **funnel stage** — a phase a resource passes through, a value
  in the `Funnel Stage` CSV column and a `ScopedBy` link in Egeria. That is what
  this change removes.
- `Automate` as an **intent tab** — the UI surface where schedules and
  subscriptions are managed. Untouched by this branch, and the design doc §2.1
  says so explicitly ("The `automate` intent tab in the UI is unaffected").

The `Automate Change Detection` value in the `Answering Mechanism` column is
also untouched; it names *which engine answers*, not a stage.

---

## HANDOVER — four `RESOURCE_STATE_SOURCES` keys go stale when the authoring stream merges

`facts.RESOURCE_STATE_SOURCES` keys fourteen resolvers on **exact question
text**. The question-authoring stream (`re/db-questions-csv`) rewords four of
those questions to cross-type wording, so those keys will match nothing.

Verified 2026-09-20 by reading that branch's CSV directly
(`git show origin/re/db-questions-csv:…/resource_questions.csv`, at
`2b38e159`) rather than taking the report on trust — all four old texts are
gone from it and all five new texts are present.

| Resolver | Old key (this branch) | New key (after the authoring stream) |
|---|---|---|
| `_r_description` | `What does this repository do?` | `What is this resource, and what is it for?` |
| `_r_maintainers` | `Who maintains this repository?` | `Who owns this resource (accountable owner), and who administers it?` |
| `_r_catalogued` | `Has this repository already been catalogued in Egeria and when?` | `Has this resource already been catalogued in Egeria, and when?` |
| `_r_license` | `Are there any restrictions for use?` | `Under what licence or agreement may this resource be used?` |

**The keys are not updated on this branch, deliberately.** This branch's CSV
still carries the old wording; swapping them here would break this branch's
own tests and would break `main` for the window between this stream's merge
and the authoring stream's. They belong in the **same commit as the
rewording**.

**`_r_license`'s row splits into two, and only one of them gets a resolver.**
It maps to the licence row above, which is what the resolver actually does
(it reads `stats["license"]`). The other half — *"Are there any restrictions
for use beyond the licence — classification, zone or terms of use?"* — is a
new, unbuilt question and should get **no** entry. A question with no state
source is a normal state; pointing a licence-field read at it would answer a
governance question with a licence string.

This does not fail silently. `test_every_declared_question_is_in_the_real_catalog`
(`tests/test_facts.py`) already asserts every key is a real catalog question;
its failure message now names this handover and points here.

**Resolved 2026-09-21, in the same commit that merged `re/db-questions-csv`
into this stream's branch.** All four keys in `RESOURCE_STATE_SOURCES` were
updated to the new wording exactly as tabled above; the split-off "beyond the
licence" question got no entry, as planned. The generator's `Resource Types`
vocabulary check also caught a real mismatch while reconciling: five CSV rows
used a literal `file` resource type that was never part of `RESOURCE_TYPES`
(`repo`/`database`/`filesystem`/`dataset`/`model` — `filesystem` already
covers it, per this design doc's own title "database, filesystem/file, open
dataset, AI model"); fixed by dropping `file` from those rows' `Resource
Types` cells. `docs/dr-egeria/resource_questions.csv` also needed its own
merge (Stream 2's own branch had inserted a placeholder `Resource Types`
column at a different position, filled `repo` on every row, as a generator
test fixture) — resolved in favor of the authoring stream's real content,
since the generator reads by header name (`csv.DictReader`), not position.
`resource_explorer/configdata/question_catalog.yaml`,
`docs/dr-egeria/questions/scouting-questions.md` and the repo Survey
Definition documents under `docs/dr-egeria/survey-definitions/` were all
regenerated from the reconciled CSV; `test_question_catalog_multi_type.py`'s
and `test_question_catalog_reader.py`'s tests that hardcoded pre-authoring
assumptions (52 repo-only questions, `database` being unauthored) were
updated to test the same invariants without depending on the CSV staying
`repo`-only. The seven reworded rows and the one stage-moved row each got a
dated entry in the CSV's own `Catalog History` column, per the guide's
audit-trail convention for this exact kind of change.

**This PR does NOT retire the seven old Question terms' `ScopedBy` links on
the live Egeria platform, and nobody should assume it did.** The CSV's
`Question` text is the case- and punctuation-sensitive join key back to the
Egeria `Question` GlossaryTerm (see the guide); rewording it here authors a
*new* term the next time the questions batch runs, and does not touch
whatever Survey Definitions are still `ScopedBy` the *old* term on a live
platform. This is the same shape as the 2026-09-13 incident
`scripts/reconcile_survey_definition_scopes.py`'s own module docstring
documents (a Question term split, old `ScopedBy` links never removed) --
that script is the tool for the retirement step, run report-only first, and
it is a separate, deliberate action for whoever next publishes this CSV to
Egeria, not something this PR does for them.

---

## What shipped

### 1. The reader distinguishes "not authored" from "filtered to nothing"

`surveyors/question_catalog_reader.py` (design §1.1 item 3, §13 Phase 0 item 1).

`_load()` returned a fabricated `{"repo": [...]}` and nothing else, so
`get_questions("database")` was `[]` — the same value as
`get_questions("repo", perspectives=["Privacy"])` finding no matches. Same
length, opposite answers.

- `_load()` now discovers **every** `<resource_type>_questions` section in the
  YAML. A type is a key if and only if the catalog has a section for it; a
  missing config file yields `{}`, not `{"repo": []}`.
- `get_questions()` returns a `QuestionList` — a `list` subclass, so every
  existing caller (the API routes, `context_compile`, `survey_definition_reader`,
  the stage pages) iterates, slices and JSON-serialises it unchanged — carrying
  `.authored`, `.absence` and `.absence_reason`. Four states:
  `AUTHORED`, `NOT_AUTHORED`, `AUTHORED_BUT_EMPTY`, `FILTERED_TO_NOTHING`.
- `.as_envelope()` gives an API or UI caller the list plus its state in one dict.
- `is_authored()` / `authored_resource_types()` are the cheap forms, for a
  caller deciding whether to offer a Questions tab at all.

A list subclass rather than a breaking signature change is a deliberate trade:
the distinction is *available* to every caller and *forced on* none, so no
consumer had to be edited in a stream that does not own those files.

### 2. The generator reads every `*_analyses` section and emits one key per type

`scripts/csv_to_question_catalog_yaml.py` (design §1.1 items 1–2).

- `_load_known_analysis_ids()` reads every `*_analyses` section, deduped,
  first-seen order preserved. A database question naming `schema_inventory` (a
  real `database_analyses` entry) used to come out `kind: unknown` with no
  `analysis_ids`.
- `generate()` emits `{resource_type}_questions` per type named in the CSV,
  ordered by the `RESOURCE_TYPES` vocabulary. **It does not emit empty sections
  for types nobody has authored** — that would put item 1's bug straight back.
- A cross-type row is deep-copied per type. A shallow copy left the nested
  `perspectives`/`purposes`/`answering` objects shared, and `yaml.safe_dump`
  emitted `&id001`/`*id001` anchors, handing two resource types the same mutable
  entry. Caught by a test, not by reading.

### 3. `Resource Types` is excluded from both by-elimination perspective scans

`NON_PERSPECTIVE_COLUMNS` (`csv_to_question_catalog_yaml.py`) and
`OPTIONAL_LEAD_COLUMNS` (`csv_to_dr_egeria_questions.py`). Both identify
Perspectives by elimination; a column missing from either becomes a phantom
Perspective on every row, which is exactly what "Catalog History" did in
2026-09. `docs/dr-egeria/resource_questions_guide.md` now says adding a
non-perspective column is a two-file change, in those words.

Per the **Decision (project owner, 2026-09-20)** in design §1.1, the column is
`;`-separated with `*` for all. It is **not** published to Egeria: the Egeria
side of the question model is resource-type-agnostic by design, so a Question
term is the same term whichever resource type asks it.

### 4. `Funnel Stage` drops `Automate` — see the CONTESTED section above

### 5. `ResourceTypeAdapter` carries the fact maps; `FactLayer` dispatches

`surveyors/survey_definition_executor.py`, `facts.py`,
`surveyors/repo_survey_definition_adapter.py` (design §1.1 item 5, §13 Phase 0
item 3).

`ResourceTypeAdapter` gains four optional **providers** (zero-argument callables
returning a map, so a type whose maps live in another module can declare them
without an import cycle):

| Field | What it carries |
|---|---|
| `analysis_results_map` | `{analysis_id: (results_reader, trend_reader)}` |
| `analysis_source_steps` | `{analysis_id: [step_key, …]}`, for `can_run` |
| `analysis_kinds` | `{analysis_id: AnalysisKind}`, for `live_read`/`headline_reader` |
| `state_sources` | `{question_text: (resolver, subject)}` |

`None` means **not declared**, which is not the same as empty — a resource type
with no results map cannot have facts read from it at all, and `FactLayer` says
so in those words instead of reporting every analysis as never-run.

`FactLayer.__init__` takes `resource_type` (default `"repo"`, so every existing
caller is unchanged) and reads the four maps through
`get_adapter(resource_type)`. The repo adapter registers its own maps and
resolves `facts.RESOURCE_STATE_SOURCES` lazily; that table's docstring now says
it is the repository's table and why it must be per-type (its keys are literal
repo question strings).

**A sixth `repo` hardcode turned up here**, not in the design's §1.1 list:
`FactLayer._last_run` called `registry.get_analysis_last_run("repo", slug)`.
That function has always taken an `entity_type`; a database asking for its own
last run got a repository's activity rows, which is to say none. Now
`self.resource_type`. Found by a test, pinned by
`test_the_last_run_lookup_uses_this_resource_type`.

### 6. One `RESOURCE_TYPES` constant

New `resource_explorer/resource_types.py`. **Two constants, and the split is
load-bearing:**

- `RESOURCE_TYPES` — the vocabulary: `repo`, `database`, `filesystem`,
  `dataset`, `model`. The last two have no surveyor and are here deliberately
  (design §13 Phase 0 item 4: "added now so nothing else hardcodes three"), so a
  question row tagged `dataset` generates a section rather than failing.
- `SURVEYED_RESOURCE_TYPES` — derived, not re-declared: the subset with an
  adapter, a registry table and an analysis-catalog section.

Widening every call site to the five-value vocabulary would have been a
behaviour change, not a refactor: `web/routes/egeria.py` validates an
`entity_type` off the wire and would have turned a clean 422 into a lookup
against a table that does not exist. Six sites now import the shared constant:

| File | Was |
|---|---|
| `batch_io.py` | `RESOURCE_TYPES = ("repo", "database", "filesystem")` |
| `inventory_export.py` | `RESOURCE_KINDS = (...)` |
| `tier_resolution.py` | inline tuple literal |
| `surveyors/survey_definition_docs.py` | `_KNOWN_RESOURCE_TYPES = {...}` |
| `workflows/depth_offer.py` | `_ALL_RESOURCE_TYPES = (...)` |
| `web/routes/egeria.py` | inline tuple literal (a sixth site, not in §1.3's five) |

`parse_resource_types()` raises on an unknown value rather than dropping it: a
typo'd `databse` that silently produced no catalog section would look exactly
like a resource type nobody has authored for.

---

## Tests

New: `tests/test_resource_types_vocabulary.py`,
`tests/test_question_catalog_multi_type.py`,
`tests/test_fact_layer_resource_type_dispatch.py`.
Extended: `tests/test_question_catalog_reader.py`.

Three of them are worth naming because they pin something a diff cannot show:

- `test_no_module_still_types_the_tuple_out` — the grep that found the
  duplication, as a test. Comments are stripped and `resource_types.py` is
  exempt; tests keep their own inline tuples, which are fixtures, not a second
  declaration of the vocabulary.
- `TestTheCallSitesReadTheSharedConstant` uses `is`, not `==`. A module that
  re-typed the same three strings would pass an equality check while
  re-introducing exactly what the constant removed.
- `test_a_cross_type_row_is_not_a_shared_yaml_anchor` — found the shallow-copy
  bug above.

`test_schema_inventory_really_is_a_database_only_analysis` pins the *premise* of
the `*_analyses` test: if `schema_inventory` were also in `repo_analyses`, that
test would pass for the wrong reason.

---

## Scoped out

- **Stream 3's structured tables** (`database_tables`, `database_columns`, the
  read-back materialiser, the `survey_data` back-fill). `registry.py` was not
  touched.
- **Stream 4's row content.** The CSV's existing 52 rows were backfilled to
  `Resource Types: repo` and nothing was reworded. Rewording the eighteen
  cross-type rows to `*` (design §4) is stream 4's, and doing it here would have
  made `get_questions("database")` return one question rather than the
  `NOT_AUTHORED` state the brief's own done-test asks for.
- **Live Egeria.** No probe, no publish, no `ScopedBy` reconciliation. The
  `Automate` term still exists in `docs/dr-egeria/foundations/foundations.md`
  and was left there: retiring a published Funnel Stage term is an Egeria write
  and belongs with the ruling, not ahead of it.
- **The UI surface.** `web/routes/analyses.py` still returns the question list
  as a bare list. The `absence`/`absence_reason` state is available on the
  returned object and through `as_envelope()`, and rendering it is a change to a
  file this stream does not own. **This is the "open the thing the user opens"
  gap for this slice** — the honesty exists in the layer and is not yet on a
  screen. Naming it rather than claiming it.

## Not tested

- **The §4 cross-type rows rendering on the classic Questions tab for
  `coco_ods`**, which is the brief's fourth done-test for this stream. Those
  rows do not exist until stream 4 authors them and there is no `database`
  section to render. The plumbing is exercised instead against fixture rows
  (`TestAHypotheticalDatabaseRow`), end to end from a CSV row through the
  generator to the reader.
- **A database `ResourceTypeAdapter` with real fact maps.** The database adapter
  declares none yet, so the tested behaviour is the honest-absence path, not a
  populated one. A synthetic adapter (`TestDispatchGoesThroughTheAdapter`)
  covers the populated path.
- **Anything needing the shared Postgres.** The dispatch tests use a registry
  stub deliberately; they are about which map is consulted, not about storage.

## One judgment call

Design §13 Phase 0 item 2 reads "the one Automate row moves to Discovery **with
`*`**", which would also set that row's `Resource Types` to all five types. The
coordinator brief's own stream table scopes stream 2 to "columns and the one
Automate row's stage", gives row content to stream 4, and sets a done-test of
`get_questions("database")` returning a *not authored* state. Setting `*` would
have made `database` authored with one question and failed that done-test. The
stage moved; the `*` did not. That row is one of the eighteen cross-type rows
design §4 hands to stream 4, and it should get `*` there.

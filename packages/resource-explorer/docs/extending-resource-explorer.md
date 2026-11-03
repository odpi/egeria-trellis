# Extending Resource Explorer

**Status:** live. How to add a question, analysis step, annotation type or survey type.

> **Adding an analysis without changing RE's code?** Write a library step instead: an installable package with a
> declared manifest. Start with the [tutorial](../../trellis-analytics/docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md); the field-by-field reference is [extending-with-analyses.md](extending-with-analyses.md). This document is about RE's
> own built-in registries.

How to add a **question**, an **analysis step**, an **annotation type**, or a
**survey type** — and, more usefully, what breaks when you miss a step.

Measured against the code on 2026-09-02, not written from memory. Every count
below came from running the thing.

---

## The map: four registries, four vocabularies

Most of the confusion in this area is that four things are keyed differently
and named similarly. Getting this straight first saves the most time:

| registry | size | keyed by | what it decides |
|---|---|---|---|
| `STEP_REGISTRY` | 40 | **step key** (`repo_secret_scan`) | what runs, in what order, at what cost |
| `ANALYSIS_KINDS` | 33 | **analysis id** (`secret_scan`) | what a user can run and read results for |
| `repo_survey_types.csv` | 70 rows / 10 bundles | **survey kind + display name** | which steps make up a named Survey Definition |
| `question_catalog.yaml` | 51 | **question text** | what a user can ask, and what answers it |

### Where things live

Config and code are not symmetrical, and knowing which is which saves the most
time:

| what | defined in | kind | how it reaches Egeria |
|---|---|---|---|
| **Step** | `surveyors/repo_survey_definition_adapter.py:288` (`STEP_REGISTRY`) + a `BaseSurveyor` subclass in `sub_surveyors/` | **code** | as a `GovernanceActionProcessStep` per survey definition |
| **Analysis** | same file (`ANALYSIS_KINDS`) + `configdata/analysis_catalog.yaml` | code + config | not directly — it is RE's own unit of "runnable" |
| **Annotation type** | `surveyors/survey_report.py` (`ANNOTATION_TYPES_REGISTRY`) | **code** | mapped to a real Egeria `...AnnotationProperties` type |
| **Survey type** | `docs/dr-egeria/repo_survey_types.csv` | **config** | as a `GovernanceActionProcess` + chained steps, via Dr.Egeria |
| **Question** | `docs/dr-egeria/resource_questions.csv` | **config** | as a `Question` GlossaryTerm, via Dr.Egeria |
| **Check ref** | `configdata/check_registry.yaml` | config | not published |

**Steps and annotation types are code; survey types and questions are config.**
A step *is* executable behaviour, so it cannot be config — but note the
consequence: adding a step means editing a 3,760-line Python file, while
adding a survey type means adding rows to a CSV.

There is also `_RE_ANALYSIS_STEP_INFO` (line 1117, same file) — an older
UI-facing descriptions dict. It is not a second source of truth; do not add to
it.

### What a step exposes to Egeria

Per step, per survey definition:

    Display Name     Coarse Profile Survey — Repo File Inventory
    Qualified Name   GovActionProcessStep::RepoCoarseProfile::repo_file_inventory
    Description      (the StepInfo description, verbatim)
    Additional Properties
      executes_at                resource-explorer
      supported_technology_type  Git Repository
      re_analysis_step           repo_file_inventory

**The `step_key` is the contract, not the Python class name.** Nothing in
Egeria names `FileInventorySurveyor`, and it should stay that way — a class
name is refactorable and a published qualified name is not.

`executes_at` is the dispatch discriminator: `resource-explorer` means RE runs
it locally; the alternative is delegating the step to a real Egeria governance
engine (`surveyors/egeria_delegated_step.py`, case 4 of
`docs/survey-execution.md (§7)`).

**`STEP_REGISTRY` is execution order. `ANALYSIS_KINDS` is not.** This has
already caused one wrong bug report: a step's position was read from
`ANALYSIS_KINDS` (position 24) and reported as an ordering bug, when
`STEP_REGISTRY` had it at position 3. If you are reasoning about *when
something runs*, only `STEP_REGISTRY` answers that.

**Not every step has an analysis id.** Four steps are unreachable from any
analysis and run only as part of a survey bundle:

    repo_file_inventory   repo_file_size   repo_git_statistics   repo_homepage

That is legitimate — they produce inputs other steps consume — but it means
"add a step" and "add something a user can run" are different tasks.

---

## Adding a question

Fully covered in **`docs/dr-egeria/resource_questions_guide.md`**, including
the five-link chain from CSV row to rendered answer. Not repeated here. The
three things people get wrong:

1. **A blank `Answering Analysis` column produces a question that appears and
   never answers.** That column is the wiring.
2. **Restart the web server.** The catalog loader is
   `@functools.lru_cache(maxsize=1)`.
3. **Regenerate the Survey Definitions** —
   `scripts/generate_repo_survey_definition.py`. Question scope-links are
   derived from the catalog, so the committed markdown goes stale.
   `test_survey_definition_generator_guard.py` fails until you do.

---

## Adding an analysis step

A step is the unit of execution. Adding one that a user can also *run and read*
means touching several places; adding one that only feeds other steps means
fewer.

### 1. Write the surveyor

`resource_explorer/surveyors/sub_surveyors/<name>.py`, subclassing
`BaseSurveyor`, returning a list of `Annotation` objects from `run()`.

Two rules the existing surveyors follow and new ones should:

- **Emit an annotation on every terminal path, including the empty one.** A
  missing annotation is indistinguishable from the step never having run.
  Use `step_outcome.from_upstream_table(...)` to say *why* it is empty.
- **Persist findings via the generic tables** — `registry.upsert_finding`
  (`project_analysis_findings`) or `upsert_metric`
  (`project_analysis_metrics`), with your own `kind`. Do not add a table.

### 2. Register it in `STEP_REGISTRY`

`repo_survey_definition_adapter.py`. `StepInfo` fields:

    step_key  surveyor_cls  description  annotation_types  static_kwargs
    accepts_surveyed_at  accepts_scope_locator  accepts_fast
    requires_context  requires_resources  requires_views
    fetch_cost  compute_cost

**`fetch_cost` and `compute_cost` are independent axes.** Wall-clock blends
them, so do not derive either from a stopwatch: a step that is slow because it
downloads is a different problem from one that is slow because it computes.
Only steps with zero network `connects` give trustworthy compute evidence. The
ceilings are generous on purpose — they exist to catch order-of-magnitude
errors, not to police seconds.

**`requires_context`** is how a step declares what must already be true
(`{"has_file_inventory": ...}`). Prefer it to failing at runtime.

### 3. If a user should be able to run it: `ANALYSIS_KINDS`

Keyed by **analysis id**, which is *not* the step key. `AnalysisKind` carries
`id`, `step_keys` (a list — one analysis may drive several steps), `family`,
and `results`.

### 4. If it produces readable results: `AnalysisKindResults`

    results_reader   trend_reader   render   headline_reader

**This is where extensions most often go wrong, twice in one day on
2026-09-02.** The fact layer — which answers *questions* — reads **only** the
`results_reader`. An annotation is invisible to it. A metric is invisible to
it. Both were built, verified where they were written, and reported done,
while the question they were meant to answer still said "not established".

**If a user should be able to ask about it, it must come out of the results
reader.** Verify by calling `FactLayer.fact(slug, analysis_id)`, not by
looking at the annotation you just wrote.

`headline_reader` returns the analysis's own one-sentence summary, which is
what the chat leads with. All 33 analyses define one. Write it as a sentence a
person would say, not a label: `"maintained by a team"`, not
`"team participation"`.

### 5. Catalog entry: `configdata/analysis_catalog.yaml`

Gives it a display name, intent, perspectives, `run_time`, `target_shape`. The
`description` is user-facing and is read as a claim — if it says the analysis
does something, it must.

### 6. If questions reference its checks: `configdata/check_registry.yaml`

Question rows can name `analysis_id:check_name`. Unknown refs fail the
generator loudly rather than silently dropping the link.

---

## Adding an annotation type

Seven exist. `ANNOTATION_TYPES_REGISTRY` in
`surveyors/survey_report.py`, each entry:

    type  display_name  description  properties  egeria_type  python_class

`egeria_type` maps to a real Egeria `...AnnotationProperties` type — a new one
must exist in the Egeria type system or publishing fails. The registry seeds
the `annotation_types` table on first run, so it is the source of truth for
both RE's own UI and what gets published.

**Before adding a type, check whether an existing one fits.**
`ResourceMeasureAnnotation` (counts something), `ClassificationAnnotation`
(says what something *is*), `RequestForAction` (asks a person to do
something), `QualityScoreAnnotation`, `SchemaAnalysis`, `DataClassAnnotation`,
`RelationshipAnnotation`. The distinction that matters most:

- **A measure counts. A classification asserts.** Docstring coverage is a
  measure, not a classification — the number is presence, and presence is not
  quality. Emitting it as a classification would have made 55.8% read as a
  grade.
- **A RequestForAction asserts that something *should* be done.** Do not emit
  one where nobody knows that: a low coverage figure is not a demand.

### Linking annotations

`Annotation.evidence_of` (an index into the same run's result list) creates a
real Egeria `AnnotationExtension` relationship from evidence to summary.
Measured 2026-09-01: `AnnotationExtension` is **UNI_LINK** — creating the same
link twice returns the same GUID, so create-blind is safe and no pre-check is
needed. Use it for aggregate-plus-breakdown shapes; `data_profiler`,
`dependency` and the code-volume census all do.

---

## Adding a survey type

A "survey type" is a named, ordered bundle of steps published to Egeria as a
`GovernanceActionProcess` — this is what a user picks when they run a survey.

**Source of truth: `docs/dr-egeria/repo_survey_types.csv`** (70 rows, 10
bundles: Repo Scouting Scan, Coarse Profile Survey, Scouting Survey, Repo
Discovery Survey, Repo Architecture Discovery, Analysis Survey, Assessment
Survey, Compliance Survey, Refresh Survey, Full Survey). One row per *step
within a bundle*:

    survey_kind  survey_group  survey_display_name  description
    output_filename  step_key  step_order

To add one:

1. Add rows to the CSV — one per step, with `step_order` setting the chain
   order. Every `step_key` must exist in `STEP_REGISTRY`; a mismatch raises
   `SurveyTypesCsvError` rather than generating a broken definition.
2. Regenerate:
   `uv run python scripts/generate_repo_survey_definition.py`
3. Run the generated markdown through **Dr.Egeria** (VALIDATE, then PROCESS)
   so the `GovernanceActionProcess` and its steps exist in Egeria.
4. Commit the CSV, the regenerated `.md` files, and `.generated.json`
   together.

### The naming trap

Two things differ by one word and behave differently:

| | what it is | downloads the zipball | reachable from the UI |
|---|---|---|---|
| **Coarse Profile Survey** | a Survey Definition (bundle of steps) | no | **yes** |
| **Refresh Coarse Profile** | `repo_profile_refresh`, `action=profile` | yes | no — its HTTP route was retired 2026-08-20 |

Cost real time on 2026-09-01: a user was told to run the one that is not
clickable. When adding a bundle, check whether the name collides with an
existing *action*.

---

## Scaling past repos

Everything above describes **one resource type**. `STEP_REGISTRY` exists once,
in the repo adapter, with 40 entries; databases and filesystems have surveyors
but no equivalent registry. Adding them — and then whatever comes after —
runs into three things that are fine at 40 and are not fine at several
hundred.

### 1. One flat dict in one 3,760-line file

The registry is a module-level literal, and its **insertion order is the
execution order**. That is a lot of load for one dict: identity, ordering,
cost, preconditions, and the surveyor binding.

The obvious split is per-resource-type modules exporting the same shape
(`repo/steps.py`, `database/steps.py`, `filesystem/steps.py`), merged at
import. Step keys are already namespaced by prefix (`repo_*`), so the
collision problem is solved; what is not solved is that ordering is
positional, and merging two ordered dicts does not give you a meaningful
combined order. **Ordering probably has to become explicit** (a `after=` or a
per-bundle order, which `repo_survey_types.csv` already carries as
`step_order`) before the registry can be split at all.

A second option worth weighing: **declare the step on the surveyor itself**, via
a decorator, so the definition lives next to the code it describes rather than
in a distant literal. That removes the two-places-to-edit problem entirely and
is how this normally scales. It costs import-time discovery and makes the full
list harder to read in one place.

### 2. How a step reaches its implementation — resolved

**Resolved 2026-09-02**, from Egeria's own type archive and handler source
plus `docs/survey-execution.md (§7)`, which had already established most of
it on 2026-08-17. Three earlier readings in this document were wrong; this
one is verified and cites where.

**The dispatch chain.** A step does not point at a shared "action type"
object. It names a **request type on an engine**, and the engine maps that to
a service:

    GovernanceActionProcessStep
      -(GovernanceActionExecutor { requestType })-> GovernanceEngine
      -(SupportedGovernanceService { requestType, serviceRequestType })-> GovernanceService
      -(Connection/ConnectorType)-> the connector class that does the work

`GOVERNANCE_ACTION_EXECUTOR_RELATIONSHIP` and
`SUPPORTED_GOVERNANCE_SERVICE_RELATIONSHIP` are both defined in
`OpenMetadataTypesArchive2_6.java` (lines 594 and 367). **So the shared
implementation is the `GovernanceService`, reached by `requestType` through an
engine** — which is exactly the thing RE currently gestures at with an
`additionalProperties` entry (`re_analysis_step: repo_file_inventory`) and no
relationship at all.

**How work is dispatched at runtime.** An engine action is announced; engine
hosts poll their engine's claimable actions and the first to claim it wins.
`EngineActionHandler.claimEngineAction`'s entire gate, read from source:

    if ((status == ActivityStatus.APPROVED) && (processingEngineUserId == null))

**Nothing verifies the claimer is a registered Engine Host, or a JVM.** Any
caller that can reach the REST API and claim an APPROVED, unclaimed action
gets it. Completion (`recordCompletionStatus`) requires only that the
completing userId matches the claimer, and then calls
`initiateNextEngineActions` server-side — which walks
`NextGovernanceActionProcessStep`, evaluates guards, and queues the next step.
**Process chaining is therefore free to whoever completes a step**, which is
why a Python claimer is viable at all.

The five engine-facing endpoints are listed in
`docs/survey-execution.md (§7)` under "Native mechanism recap".

**What this settles for the 109-vs-40 question:**

- `GovernanceActionProcessStep` **is a subtype of** `GovernanceActionType`
  (`OpenMetadataTypesArchive2_6.java:474`) — inheritance, not reference. So
  adopting `Create Governance Action Type` as an intermediate layer would
  **not** deduplicate anything.
- `description` is inherited from `Referenceable` onto every step entity, so
  the seven copies of `repo_file_inventory`'s description exist because there
  are seven *entities*, and no type layer changes that.
- A step **can** legitimately belong to several processes:
  `GovernanceActionProcessFlow` has `ANY_NUMBER` on the process end and
  `NextGovernanceActionProcessStep` is `multiLink` with `ANY_NUMBER` both
  sides. **Reusing one step GUID across processes is the actual dedup lever**,
  and it is a generator change, not a model change.
- `Guard` is a runtime predicate only (`OpenMetadataProperty.GUARD`, example
  value `x>4`). It carries no process identity.

**Still open, and both are tooling rather than model:**

1. Whether Dr.Egeria's commands and the view-service handlers can attach an
   **existing** step GUID as a `firstStep` or `NextGovernanceActionProcessStep`
   target, or always create a new element. The type model permits reuse; the
   authoring path may not. Check `GovernanceActionProcessStepHandler` and
   `ActionAuthorRESTServices`.
2. Registering a governance service against an engine is a **server-side
   gap**: `registerGovernanceServiceWithEngine(...)` exists but is wired to no
   REST endpoint (`GovernanceConfigurationResource` is read-only). Egeria does
   it via content-pack archive writers. Recorded in
   `docs/survey-execution.md (§7)`.

**What this means for RE.** Today every step carries
`executes_at: resource-explorer` and RE dispatches locally. Making a step
genuinely Egeria-dispatchable means giving it a real `GovernanceActionExecutor`
relationship to an engine that supports a matching `requestType` — at which
point `re_analysis_step` stops being an informal label and becomes the
request type. That is the shape to aim at when the database and filesystem
step sets are authored, and it is blocked today by ISSUE-79 rather than by
anything in this design.

### 3. What is worth exposing, and what is not

Today a step publishes `executes_at`, `supported_technology_type` and
`re_analysis_step`. That is a deliberately small surface and the restraint is
right — the Python class name is *not* published, so surveyors can be renamed
freely.

Candidates for exposing later, each only if something would consume it:

- **`annotation_types`** — already on `StepInfo`. Lets a consumer know what a
  step can produce without running it.
- **`fetch_cost` / `compute_cost`** — lets a planner reason about scheduling
  and cost before dispatch. These are **independent axes**; publishing a single
  blended "cost" would destroy the distinction that makes them useful.
- **`requires_context`** — declared preconditions, which is what a remote
  orchestrator would need to sequence steps it did not author.

The thing *not* to expose is anything that is an implementation detail RE may
want to change: class names, module paths, function names. The step_key is
the contract precisely because it is meaningless to Python.

## The failure mode to design against

Four separate extensions on 2026-09-01–02 were built, verified, and reported
done while the surface a user reads showed nothing:

| built into | read by | what the user saw |
|---|---|---|
| `api_structure` metric | results reader | `relationship_count: 437` |
| FileStructure annotations | results reader | no line counts at all |
| `refresh_profile()` | nothing — route retired | an instruction they could not follow |
| activity log annotations | `GET /rfas` | RFAs that never reached the drawer |

**The check that would have caught every one:** after building, open the thing
a user opens. Call `FactLayer.answer()`. Load the drawer. Click the button. If
the extension is for a person, the person's surface is the one that counts —
the metric, the annotation and the log row are all intermediate.

## See also

- `docs/dr-egeria/resource_questions_guide.md` — questions, end to end
- `docs/dr-egeria/foundations/foundations.md` — Perspectives and Funnel Stages
- `docs/repo-analysis-funnel.md (§13)` — a worked example of adding
  measures, including what was got wrong
- `docs/egeria-operations.md` — when Egeria and RE disagree

# BRIEF — Analytic extensibility: the walking skeleton (2026-10-07)

Design session brief for the coordinator and one builder, at the owner's
word ("your candidates work, write the brief"). Sources:
`packages/resource-explorer/docs/design-notes/DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md`
(A1–A13, R1–R20, §6 the owner's review), `docs/context-spec-requirements.md`
(C1–C20), `docs/context-as-product-requirements.md` (P1–P13). This brief
builds the first real instance of each so the three documents are checked
against something that runs. It lives at the trellis root because it spans
packages.

## 0. Two owner rulings that shape it (2026-10-07)

- **Annotation type, interim:** use `ResourceMeasureAnnotation` for the
  user step's result, with the envelope's fields carried in
  `additionalProperties` where the type has no property for them
  (`resultState`, `resultStateReason`, `producingRun`, `measuredAt`,
  `producerVersion`, `scope`). When the Egeria leads extend the annotation
  types, the step's manifest changes one line and the envelope moves into
  real properties; nothing else does.
- **Reconcile with what exists; what exists can be changed.** The Portal's
  "Analytic Functions" are already a registry and an executor (below). The
  skeleton registers into them and extends them; it does not build a second
  registry.

## 1. What exists, read from code

| Piece | Where | What it is |
|---|---|---|
| Analytic function registry | `pyegeria/view/analytic_registry.py` | `AnalyticFunctionSpec`: a dotted import path, `generic` (what it counts is a parameter) or fixed, `params: [AnalyticParam]`, a `binding_note`; a dict registry with `register_analytic_functions`; loaded config-driven from `PYEGERIA_REPORT_SPEC_MODULES` |
| Report-spec executor | `pyegeria/view/format_set_executor.py` | `exec_report_spec` runs a `FormatSet`'s `ActionParameter` either by client method (`function`) or by `analytic_function` (importlib on the dotted path); `SERIES`/`BAR`/`PIE` wrap the result as a Vega-Lite chart; `_exec_analytic_series` for trends |
| Report specs as the dashboard element | Portal `report_specs_handler.py`, `OVERVIEW_REPORTING_MODEL.md` §13 | a `FormatSet` with `analytic_function` + `analytic_spec_params`; `Report` is a real Egeria asset naming a spec plus parameters; `Dashboard Sheet` is the user-authored dashboard (pyegeria-local JSON store) |
| Advisor report specs | `packages/egeria-advisor/advisor/report_spec_parser.py` | parses a Dr.Egeria markdown spec into the same `FormatSet`; validates the client class; registers it |
| Context packer | `packages/trellis-context` | `ContextSpec` + deterministic packer and manifest; no resolver; no LLM |
| Survey steps | `packages/resource-explorer/resource_explorer/surveyors/sub_surveyors/` | `STEP_REGISTRY`; a step is a class in RE's own package; results stored by RE; `analysis_catalog.yaml` declares it; no plugin protocol |
| The computation to lift | `resource_explorer/.../db_chart_data.survey_history(registry, slug, limit)` | the Understanding trend chart's data: schemas, tables, columns per survey run |

So: **a routine already has a declaration form, a registry, an executor and
two consumers (Portal report specs and Dashboard Sheets; the Advisor's
specs parse to the same object).** What it lacks is the honesty envelope,
provenance, an ODS source, and a home outside pyegeria's own modules. A
**step** has none of this yet: no external authoring, no manifest, no
registration, no envelope on its result.

## 2. The skeleton, four things

### 2a. The shared library: `trellis-analytics` (R19)

One new package in the monorepo beside `trellis-context`, published as a
wheel so a notebook can `pip install` it. Contents, minimal:

- **Readers**: `ods` (the RE registry's read side: survey rows, facts,
  annotations by type, through `ProjectRegistry` opened read-only from a
  URL) and `egeria` (pyegeria clients from an identity; read only).
- **Envelope types**: `Envelope(result_state, result_state_reason,
  producing_run, measured_at, producer_version, scope)`, with the four
  states (`measured`, `nothing_found`, `not_established`, `not_applicable`)
  and `to_additional_properties()` for the interim ruling.
- **Manifest**: `RoutineManifest` and `StepManifest`, both **subclasses of
  pyegeria's `AnalyticFunctionSpec`** (the change to what exists, made in
  egeria-python on the owner's word, as a PR: add `kind: routine | step`,
  `source: ods | egeria | resource`, `version`, `envelope: bool`,
  `annotation_type` for steps, `resource_types`). A manifest is discovered
  by a Python entry point (`trellis.analytics`) and registered into the
  **same** registry (`register_analytic_functions`), so the Portal's
  "Analytic Functions" tab lists Trellis routines and steps without a
  second catalog.
- **No executor** in the library. A routine is called directly; a step is
  run by a host (RE's orchestrator, Prefect) that imports the library.

### 2b. One routine: `curation_coverage` (owner, 2026-10-07: "good idea for the routine")

The first routine is **curation coverage**, analysis 1 of
`DESIGN-CURATE-BY-ANALYSIS.md`, in place of the survey trend: its value is
obvious on the Curate page the owner is looking at, and it is wanted in
three places at once. The survey trend stays a later routine.

`trellis_analytics.routines.curation_coverage(ods, resource_type, slug) ->
CoverageResult`, `family="Curation"`, `source=ods`, no credential, no
Egeria call. Inputs, all from the curation record in the ODS:
`catalogue_commit_proofs` (published elements by GUID, read-back rows
only), `catalogue_scope_events` and `resource_scope_events` (choices:
include, leave out, undecided, proposal source), the survey report's
annotation count and its publish proof, the Enrichment fields' publish
rows (once P1 exists; `not applicable` until then), the outbox's `kept,
not sent` and `superseded` rows. Output: the counts of analysis 1 with
their words ("published · 54 tables · 1 term · 1 report (76 annotations) ·
read back <when>"; "not yet published · 26 undecided · 1 left out by a
person · N confirmed not published · M proposals awaiting"; "blocked · K
(ISSUE-117 block, kept not sent)"), each count carrying the row ids it
counts, and an `Envelope` on the whole: `measured` when the registry
answered, `nothing_found` when the resource has no commit and no choice
("nothing published yet · <n> known to RE"), `not_established` when the
registry could not be read (reason named), `not_applicable` for a kind
with no curation record.

RE's Curate By-analysis tab calls the library function and renders its
rows (the tab's hide-with-sentence goes when this lands). **Proven from
three hosts** (A11's proof): a script in `examples/`; a papermill notebook
with `slug` as its parameter; a Portal tile as a `FormatSet` with
`analytic_function="trellis_analytics.routines.curation_coverage"`, output
a KPI row plus a `BAR` of published versus not-yet-published, loaded
through `PYEGERIA_REPORT_SPEC_MODULES`; the tile test runs
`exec_report_spec` on a fake ODS and asserts the counts and the envelope's
state rendered as the chart's subtitle. The live Portal check is the
owner's.

### 2c′. Three levels of a step (owner, 2026-10-07), and what each owns

The owner's framing, adopted: a step has three levels, and the skeleton
builds the middle one once so that the first is small and the third is
already done.

| Level | What it is | Who writes it | What it owns |
|---|---|---|---|
| **1. The code** | a plain Python callable with a known signature: `run(inputs) -> results` where `inputs` is what the wrapper hands it (an ODS reader, a connection, prior annotations) and `results` is a list of plain measurements | the user | the measurement and its reason; nothing about Egeria, storage, credentials or envelopes |
| **2. The wrapper** | the library's `SurveyStep` that gives the code the behaviour of an Egeria Survey Step: reads the manifest, resolves and enforces declared inputs (hands a connection, never a password), runs the code under the executor, puts the **envelope** on every result, stores results in the ODS by annotation type, publishes by the profile, stamps the version, refuses a result without an envelope | the library, once; the user fills in the **manifest** (the wrapper's declaration: id, resource types, inputs and tiers, prerequisites, annotation type, questions answered, family, version) | everything in the walk's items 2 to 10 |
| **3. The survey** | the Egeria Survey Definition: a governance action process chaining steps by `re_analysis_step` | **already authored with Dr.Egeria today** (`docs/dr-egeria/survey-definitions-*/`, generated by `scripts/generate_*_survey_definition.py`) | which wrapped steps run, in what order, for which resource kinds, under which qualified names |

Consequences for the skeleton: the `StepManifest` is level 2's, not the
code's; the user's package contains the callable and the manifest and
nothing else; `trellis-analytics new-step` writes both; the `ExternalStep`
adapter in RE **is** the wrapper's host side and should be named for it
(`SurveyStepHost`); the Dr.Egeria `Create Analytic Function` declaration is
the wrapper's element, and a Survey Definition references it exactly as it
references RE's built-in steps today, so a user step joins a survey by
editing the definition document, not RE's code. "Running in Egeria's
engine host" is then the question of where level 2 executes, and the
survey (level 3) is already Egeria's.

**Three decisions on steps (owner, 2026-10-07):**

- **Credential tier:** a user step may ask for **read-only on the resource**
  and nothing more; the wrapper refuses a manifest that declares a higher
  tier, with the sentence naming the tier, until a publication-style profile
  says otherwise. The host hands a connection opened at that tier, never a
  password.
- **Engine host: later, and by equivalence; a standing plan, not a new
  one.** The RE engine host has been planned for weeks (the owner,
  2026-10-07; the working-assumptions note's A6 and the engine-host
  executor already assume it). Egeria's engine host runs Java; the
  RE/Prefect engine runs Python. The goal, not for this quarter, is that
  **RE's engine appears to Egeria and behaves like a native engine host**: the Survey Definition is Egeria's governance action process, its
  steps are declared as governance action types, and Egeria dispatches a
  Python step to RE's engine the way it dispatches a Java one to its own.
  The skeleton keeps that door open by declaring the wrapper's element in
  the governance-action family and by running steps only through RE's
  queue, never inline, so an Egeria dispatch later lands on the same path.
- **Questions, perspectives and purpose in the manifest: yes.** A manifest
  declares the questions it answers (by stable question id, which commits
  the question-id slice before this brief's step ships), the perspectives
  the answers serve, and **purpose as a qualifier** ("this step answers Q
  for purpose Find", so the same question can be answered differently per
  purpose). The purposes already exist (owner: "we have already
  discussed purposes and can use the ones we have"): the registry's
  `VALID_PURPOSES` on an investigation are `Assess`, `Certify`, `Deploy`,
  `Explore`, `Learn`, `Maintain`, `Select`, `Share` (`registry.py`, the
  investigation framing design). The manifest's `purpose` qualifier takes
  those values, with no qualifier meaning "any purpose". Two rules from the
  owner: the set is a **valid value set, evolvable**, since the values
  cannot be known in advance, so it must stop being a tuple in code and
  become a value set RE reads (the registry today, Egeria's valid values
  when the leads give it a home), with a new value added by a person, not a
  release; and the wrapper validates a manifest's purposes against that set
  at load, naming an unknown value rather than refusing silently. One
  finding for the owner: the Find and Integrate purposes of
  `DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md` are not in the
  tuple (nearest: `Select` and `Deploy`); whether they are added as values
  or mapped onto existing ones is the first use of the set's evolvability. The reader
  binds a question to annotation type plus predicate (R6), and the
  manifest's declaration is what makes a user step askable without editing
  the CSV.

### 2c. One user step: `database_staleness`

A Python package **outside** RE (`examples/steps/database_staleness/` in the
library's repo, installable on its own) with a `StepManifest` entry point:
`kind=step`, `source=ods`, `resource_types=[database]`,
`annotation_type=ResourceMeasureAnnotation`, `version` from the package.
It reads the last two `row_count_snapshot` results for a database from the
ODS and produces one annotation per table: rows changed since the last
survey, with the envelope: `measured` (two snapshots, counts present),
`nothing_found` (two snapshots, no change), `not_established` (one
snapshot or counts unmeasured, reason named), `not_applicable` (a view).

RE runs it: the orchestrator discovers steps by entry point beside its
`STEP_REGISTRY` (an adapter, `ExternalStep`, that calls the package and
stores its annotations with the envelope in `additionalProperties`),
`analysis_catalog.yaml` gains the entry **generated from the manifest**,
not hand-written (a generator script plus a test that the generated entry
matches the manifest), and the Analysis pane shows its rows with the
state words from the envelope (one generic reader per annotation type,
R7's first instance: the reader derives the state from the envelope, never
from the value).

**Registered in Egeria by a Dr.Egeria document** (R2): the generator also
emits the markdown that creates the step's element (a
`GovernanceActionType`-shaped declaration, the same family the survey
definitions use) under the owner's hand; retirement is a status, never a
delete. Running it in Egeria is not in this brief.

### 2d. One spec: the presentation flavour

A `FormatSet` for the tile above is the spec (C-flavour `presentation`):
`analytic_function`, `analytic_spec_params={"slug": ..., "limit": 30}`,
output `SERIES`, plus the two fields the spec note adds and the owner's
review confirmed: `as_of` and the resolved routine's `version` recorded in
the result's manifest, so a tile can say "from trellis-analytics 0.1.0 ·
survey trend · as of <time>". Written as a Dr.Egeria report-spec document
so the Advisor's parser produces the same object (one test: parser output
equals the Python `FormatSet`).

## 3. Report specs and local dashboards: does this fit

Checked against §1's reading, for the owner's question:

- **Report specs** already reference a routine by dotted path and pass
  parameters; the skeleton changes nothing in that contract. What they
  gain: the registry lists Trellis routines beside pyegeria's; a result
  carries an envelope and a version, so a spec's output can show state
  words instead of an empty chart when the state is `nothing_found`. The
  executor needs one addition: when the function's result is an envelope
  type, render the state word and the reason as the chart's subtitle (one
  small change in `format_set_executor.py`, egeria-python PR, owner's
  word). Specs without envelopes keep working unchanged.
- **Local dashboards (Dashboard Sheets)** compose `Report`s, each naming a
  spec plus parameters; they consume routines only through specs, so they
  need nothing new to show the trend tile. The gap the reporting model's
  own §13 names (the Sheet store is pyegeria-local JSON, not Egeria) stays
  open and is not this brief's.
- **The Advisor** parses specs to the same object, so it gains the same
  routines for free; its agent path (`create_router`) is the `agent task`
  flavour and is out of scope here.
- **What changes in what exists**: `AnalyticFunctionSpec` gains the five
  fields (additive, defaults keep every existing spec valid); the registry
  loads entry-point manifests beside module registration; the executor
  renders envelopes. Three small egeria-python changes, one PR, logged in
  `PYEGERIA_ISSUES.md` first as the repo's rule requires and committed only
  on the owner's word.

## 3a. How a user extends the list (owner's question, 2026-10-07)

The skeleton's own step is written the way a user would write one, so the
path is the same; the brief makes each step of it a tested thing, not a
convention:

1. **Write it.** A Python package with a `StepManifest` or `RoutineManifest`
   (R1's first form). The library ships a `cookiecutter`-style template
   (`trellis-analytics new-step <name>`) that writes the package skeleton,
   the manifest, a test with the four envelope states, and the example
   notebook. Single-file and notebook forms come later (R1's order).
2. **Install it where it will run.** `pip install` into the host's
   environment: RE's venv (or the Prefect worker's) for a step; the Portal's
   or a notebook's for a routine. The manifest is an entry point in the
   package's `pyproject.toml` (`[project.entry-points."trellis.analytics"]`),
   so installation is registration: at startup the registry loads every
   entry point it finds. No file to edit, no call to make, no restart of
   anything but the host whose environment changed.
3. **See it.** It appears in the Portal's "Analytic Functions" tab (routines
   and steps alike, with `kind`, `source`, `version`, `family`), in RE's
   Analysis stage for the resource types it names (the catalog entry is
   generated from the manifest at load), and in `trellis-analytics list`.
4. **Declare it to Egeria, if wanted.** `trellis-analytics declare <name>`
   writes the Dr.Egeria document; a person with the right hand creates the
   element; retirement is a status, never a delete. Not required for
   running locally; required for a context pack or another tool to find it
   through Egeria.
5. **Use it.** A report spec names the routine by dotted path, as today; a
   Dashboard Sheet composes the report; RE runs the step from its Analysis
   stage or a Survey Definition that lists it.

Where user code runs (A6, unchanged): a routine runs in the caller's
process, which is the caller's choice; a step never runs in the web
process: RE's orchestrator runs `ExternalStep` through the run queue, so on
a deployment with a Prefect worker the user's package must be installed in
the worker's environment too, and the row says "step not installed on the
worker" when it is not (the honest sentence, with the package name).
Versioning (R3): two versions may be installed in different environments;
every result carries the version from the manifest; a reader never
reinterprets an old result under a new version.

**Organisation of functions** (owner's aside): one field on the manifest,
`family` (free text, the same word pyegeria's `FormatSet.family` already
uses, so "Analytic" and "Analytic Function Demo" keep their places), plus
`tags`. The Portal tab and `trellis-analytics list` group by `family`,
then `kind`, then `source`; RE's Analysis stage groups by `resource_type`
and `intent_tier` as today. In Egeria a family is a `Collection` the
declaration joins, so a pack or a search can ask for a family. No
hierarchy beyond that until there are enough functions to need one.

**Name (decided, owner 2026-10-07).** Package `trellis-analytics` (import
`trellis_analytics`), display name **"Analytic Library"**; it holds steps
as well as routines. The library and the example step live in trellis.

**CLI and Dr.Egeria commands, both, with a clear split (owner's question).**
Two registries exist by design and answer different questions: the local
registry answers *what can run here* (entry points found in this
environment), Egeria answers *what exists and who declared it* (findable by
other tools and by context packs). Neither should pretend to be the other.

| Act | Where | Command |
|---|---|---|
| create a step or routine skeleton | library CLI | `trellis-analytics new-step <name>` / `new-routine <name>` |
| validate a manifest and run its envelope tests | library CLI | `trellis-analytics validate <package>` |
| register locally | nothing to run: `pip install` is the registration (entry point) | — |
| list what can run here, by family | library CLI | `trellis-analytics list [--family]` |
| write the Egeria declaration document from the manifest | library CLI | `trellis-analytics declare <name>` → a Dr.Egeria markdown file |
| create, update, retire the declaration in Egeria; join a family Collection | **Dr.Egeria** | `Create Analytic Function`, `Update Analytic Function`, `Retire Analytic Function`, `Link Analytic Function to Family` (new Dr.Egeria commands, in egeria-python on the owner's word; the element shape is the §2c declaration) |
| list what Egeria knows | **Dr.Egeria** / pyegeria | `View Analytic Functions [family]` (and the Portal tab reads the same) |
| compare the two | library CLI | `trellis-analytics drift` prints, per function: "installed here · declared in Egeria", "installed here · not declared", "declared · not installed here (version X)", with no repair; the same shape as the credential drift check |

Why the split: installation is already registration locally, so a CLI
"register" command would be a second truth; Egeria writes go through
Dr.Egeria because that is the sanctioned, reviewable, signed-document way
every other declaration (survey definitions, report specs, dashboard
sheets) reaches Egeria, and it gives a person the hand on each element;
the `declare` command keeps the two from diverging in wording by generating
the document from the manifest rather than by hand; `drift` keeps them
honest about each other without ever repairing silently. Skeleton scope:
`new-step`, `validate`, `list`, `declare`, `drift`, and `Create Analytic
Function` only; `Update`/`Retire`/`Link`/`View` follow once a second
function exists.

## 3b. Visibility across hosts (owner's two questions, 2026-10-07)

**Yes to both, through the one registry; what differs by host is what can
run, and the words say so.**

- **Trellis sees pyegeria's analytic functions.** They are already in the
  registry the skeleton joins, so RE's and the Advisor's report specs and
  Portal tiles may name them by dotted path as today, and `trellis-analytics
  list` shows them beside Trellis routines with `source=egeria`. They carry
  no envelope, so a Trellis consumer renders them as "value only · no
  envelope" rather than inventing a state; a context spec that needs a
  state cannot bind to one until it gains an envelope (the five additive
  fields make that possible per function, not required).
- **pyegeria and the Portal see Trellis functions.** Installed Trellis
  routines appear in `get_report_registry()` through the entry point, so
  the Portal's "Analytic Functions" tab lists them and a report spec or
  Dashboard Sheet may run them wherever `trellis-analytics` is installed.
  Trellis **steps** are listed too but marked "runs in Resource Explorer
  (step) · not runnable here": a step needs an executor and a resource,
  which the Portal does not have; the Portal shows the declaration and the
  latest result's state words read from Egeria's annotations, never a Run
  button.
- **Egeria is the view that does not depend on installation.** A declared
  function is visible to every host through Egeria even where the package
  is absent; `drift` says "declared · not installed here", and a report
  spec naming an uninstalled routine fails with that sentence, not an
  import error.
- **One name, two hosts.** The dotted path is the identity in both
  directions; `family` groups across origins; the registry never
  distinguishes "ours" and "theirs" except by `source` and by the fields a
  function fills.

Test: with `trellis-analytics` installed, `get_report_registry()` lists a
pyegeria function and a Trellis routine and a Trellis step with the right
`kind` and `source`; `exec_report_spec` runs the first two and refuses the
step with the sentence.

## 4. What is deliberately not in the skeleton

A Prefect-hosted step; running a step in Egeria's engine host; the
derivation flavour (annotation-input steps); the context pack (P-note,
its own first slice); a second routine or step; any UI beyond the Analysis
rows and the Portal tile; the question-binding of the new annotations
(R6; the step's rows appear under "By analysis" only).

## 5. Tests that fail first

- Library: envelope round-trips through `additionalProperties`; `ods` reader
  opens a scratch SQLite registry only (the registry guard applies here as
  everywhere).
- Routine: output equals Curate's By-analysis rows for the fixture; three
  hosts (script, papermill notebook, `exec_report_spec`) produce the same
  numbers; the `nothing_found` and `not_established` states on a resource
  with no commits and an unreadable registry; every count carries the row
  ids it counts.
- Step: four envelope states from four fixtures; annotations stored with
  the envelope; the generated catalog entry equals the manifest; the
  Analysis row's state word derives from the envelope (change the value,
  not the state, and the word does not change).
- Registry: an entry-point manifest appears in `get_report_registry()`'s
  analytic listing; an old spec with no new fields still validates.
- Spec: the Dr.Egeria document parses to the Python `FormatSet`.

## 6. Gate (owner, by use)

1. Curate's By analysis on coco_pharma reads the published count equal to
   the gate's read-back, names the 26 undecided and the one left out, and
   counts the Sales Forecast assignment as confirmed; the same numbers as
   the routine prints.
2. In a notebook, `from trellis_analytics.routines import
   curation_coverage` runs against the ODS URL and prints those numbers
   with the envelope's state.
3. On the Portal, the "Analytic Functions" tab lists the Trellis routine
   and the step; a Dashboard Sheet shows the coverage tile with "from
   trellis-analytics 0.1.0 · as of <time>" and the state word as subtitle.
4. In RE, the step appears in the Analysis stage for coco_pharma, runs,
   and its rows read `measured` / `nothing found` / `not established` from
   the envelope; the Dr.Egeria document is generated and reads correctly
   (the owner creates the element if he wishes).

## 7. Order and ownership

One builder, in the order 2a → 2b → 2d → 2c, because the step is the
largest and the routine proves the library first; 2b is curation coverage,
read from coco_pharma's curation record. The egeria-python
changes are a separate PR the owner approves; until it merges the manifest
subclasses carry the five fields themselves and the registry registration
uses the existing call, so the skeleton runs on pyegeria 6.1.29 as pinned.

## 8. Questions

**Owner:** whether Find and Integrate become purpose values or map onto `Select`/`Deploy`. Everything else is decided.

**Egeria leads:** the annotation-type extension's timing (interim ruling
stands until then); whether a step's declaration as a
`GovernanceActionType` is the right element for a step Egeria does not run.

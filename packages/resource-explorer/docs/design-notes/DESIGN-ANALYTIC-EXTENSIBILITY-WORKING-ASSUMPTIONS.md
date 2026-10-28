# DESIGN — Analytic extensibility across Trellis and the Egeria Portal: working assumptions (2026-10-02)

**Status: brainstorm captured, nothing decided, nothing built.** From the
design session with the project owner, 2026-10-02. The owner's framing:
*users are going to want to add and modify their own analytic steps within
surveys; we need to run them, generate annotations from them, answer
questions about the results, and display results*, some through the Egeria
Portal's local dashboards, which use the report-spec mechanism, Dr.Egeria
and user-written analytics routines that are *also not properly
architected yet*. This note states the pieces, the working assumptions and
the requirements to fill in, so the inventory and the paper walkthroughs
that follow have something to check against. Assumptions may change; they
are explicit so the change is visible.

Related: `docs/Architecture.md` (the survey and query layers, the six table
families), `project_egeria_resource_explorer_redesign` (2026-08-06: shared
analytics materialised, not live-called), EA's report-spec redesign,
`DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md`.

## 1. Eight points, in two groups

A user-added analytic step touches eight things. Today RE wires most of
them by hand, per analysis.

**About the step itself**

| # | point | today in RE | the gap |
|---|---|---|---|
| 1 | **Declaration**: id, resource kinds, intent tier, prerequisites, cost, availability, what it produces | `analysis_catalog.yaml`; `executes_at`, `native_process_ref`, `requires_input` | the most complete part; not shared with EA or the Portal |
| 2 | **Execution**: where the code runs, who owns the process | three executors chosen per step: RE-local Python, Prefect, Egeria's engine host (Java) | no plugin protocol; adding a step means editing `surveyors/` |
| 3 | **Results contract**: what comes back | per-analysis shapes in `_store_results`; per-analysis readers in `facts.py` | the gap everything else falls into: absence versus zero is re-decided per reader |
| 4 | **Binding**: which questions a result answers, how a state is derived | `resource_questions.csv` → `analysis_ids` by hand | a new step needs a hand edit to be askable |
| 5 | **Presentation**: headlines, boards, charts, dashboards | /next per stage, Classic, EA report specs, Portal dashboards, each separately | four renderers, no shared data shape |
<dw>Execution also needs to know what kind of code - python only or whatever</dw>
**About what happens to results**

| # | point | today | the gap |
|---|---|---|---|
| 6 | **Storage**: what is kept locally and for how long | registry rows per analysis; `board_summary` materialised; `survey_data` TEXT blobs | an ODS in effect, not by design |
| 7 | **Publication**: how results go back to Egeria as annotations | `EgeriaPublisher`, outbox with proof rows | per-analysis annotation construction; one-way by ruling |
| 8 | **Curation**: what a person may conclude or act on from a result | E3's observation states (proposed, confirmed, overridden, survey-now-disagrees); verdicts | nothing a user step can declare about its own proposals |

The second group is where the honesty rules live: storage decides whether
absence survives, publication decides what Egeria believes, curation
decides what a person may conclude. Keeping them out of the step keeps a
user-written step from deciding any of them.

## 1a. Corrections from the inventory (2026-10-03)

`docs/analytic-extension-points-inventory-2026-10-02.md` (#438) read the
code and found six places where §1 and §2 said what the code does not.
Each is recorded here, dated, and the assumption it touches is amended
below rather than rewritten.

1. **The catalogue carries less than §1 says.** `analysis_catalog.yaml` has
   no `executes_at`, cost, prerequisite or `produces` field. `executes_at`
   is on the Egeria-side `SurveyStep`; `native_process_ref` sits under
   `egeria_registration` on one entry of 66; `requires_input` on four.
   Cost, `produces` and `requires_context` live on the Python `StepInfo`
   (43 repo steps, 7 database steps). So "declaration" is today split
   across three places: the YAML menu, the Python step registry, and the
   Egeria survey definition. A1/A3 assume one; the architecture must say
   which of the three becomes the declaration and how the others derive.
2. **`trellis-context` has no resolver.** It holds `spec.py` and
   `packer.py` only; resolution lives in RE's `context_compile.py`, and
   **sections bind to analysis ids today**, not annotation types (R18 and
   C4 answered: no). A13's "the compiler is the shared resolver" is the
   target, not the state.
3. **Annotation construction is per annotation type**, not per analysis
   (`annotation_props.py`, 10 types), but each of 42 sub-surveyors builds
   its own `Annotation` objects (119 constructions). §1's "per-analysis
   annotation construction" was half right: the mapping is typed, the
   emission is scattered.
4. **"Envelope" already means something else in RE** (`facts.py`'s
   `Envelope`, an answer wrapper). The absence vocabulary this note wants
   exists as `result_status.py` and `Fact.state`, and it is **derived at
   read time, not stored with the result rows**. A2's envelope therefore
   needs a different name on the wire, and A4's ODS needs the state
   persisted, not recomputed.
5. **The E3 observation states live in `/next` JavaScript**
   (`observation-state.js`), not in a Python table. A8's "model for every
   proposal" is a front-end state function today; the ODS and the
   publisher cannot read it.
6. **EA has no analysis engine, annotation engine or results store.** Its
   reports are pyegeria report specs. A9 stands, but "EA's routine runner"
   in the inventory brief did not exist; EA contributes the spec lifecycle
   and the ranker, nothing on the execution side.

Also from the inventory, for the architecture rather than the
assumptions: the only things a user can add today without app code are an
RE question row (unbound), an RE annotation-type metadata row (not
publishable), an Egeria survey definition over existing step keys, a
Portal dashboard or saved query, a pyegeria report spec by JSON drop, and a
pyegeria analytic function by dotted path, which runs unsandboxed in the
Portal's web process (against A6). An RE step is not user-addable.

## 2. Working assumptions

Each is stated so it can be argued with. A changed assumption gets a dated
line here, not a silent edit.

**A1. Python is the language of analytic and survey steps.** A SQL body is
allowed for database steps, because much database analysis is a query and
a SQL step can be reviewed as data. Java only for steps that must run
inside Egeria's engine host as survey action services. Dr.Egeria markdown
for declarations, never for logic. No fourth choice is in view.

**A2. Every step's output is typed annotations** in the Egeria annotation
vocabulary, which the Egeria leads are extending for the needs this work
uncovers. RE's requirement of the extension is small: a required
**envelope** on every annotation that distinguishes *measured*, *not
established* with a reason, and *not applicable*, plus a reference to the
run that produced it. Everything else in a type is domain-specific. RE's
Admin annotation-type registry becomes a cache of Egeria's types, not a
second list.

**A3. Declarations live in Egeria; code lives in versioned packages; results
live in the ODS.** Survey definitions, governance action types and
annotation types are Egeria elements already, and RE discovers native
surveys from there. Steps ship as Python packages exposing their steps
through entry points with a manifest; Java services as connectors. A user
adds a step by installing a package and registering its declaration; RE,
EA and the Portal see it through Egeria, not through each other's files.

**A4. The registry is an operational data store, by design.** Egeria holds
current state and lineage; the ODS holds every run so trending and
cross-resource analysis stay cheap; nothing is computed from Egeria's graph
that the ODS can answer. The ODS schema follows the annotation envelope,
one table family per annotation type, which is what the queued reader
consolidation wants anyway. EA and the Portal read the same Postgres.
*Amended 2026-10-03:* the ODS persists the result state per row
(correction 4); a derived state is a cache, not a record.

**A5. Git holds declarations, code and dashboard definitions, never
results.** Steps as packages, report specs and Dr.Egeria documents as
markdown, dashboards as files: versioned, reviewable, deployable. One
further use: provenance. A result records the commit of the step that
produced it, so a changed step cannot silently reinterpret an old
measurement. <dw>Conceptually agree - however there could be some pragmatic issues - I think we should consider saying that
git is used to extend and modify but is not required for an out of the box execution without customization</dw>

**A6. Steps run under an executor RE chooses, never inline in the web
process.** User code runs in the Prefect worker's environment, which is
the sandbox boundary; Egeria-hosted services run in the engine host.
Availability stays a declared field, not a derivation from cost. <dw>should we also leverage Prefect in the Portal? We do need to allow analysis routines to run in simple python and jupyter</dw>

**A7. Publication is one-way and idempotent** until the
source-of-truth-by-topology rule is decided (owner, 2026-10-01: local in
dev, Egeria possibly in production, RE when disconnected). Reads from Egeria
inform display with a provenance mark; they never overwrite local rows.

**A8. Curation actions are proposed from measured annotations only, never
from judgements, and a person confirms them.** A step may declare which of
its annotations are *proposable* and for which observation field; it may
not set a disposition, a classification or a verdict. The E3 observation
states are the model for every proposal.

*Amended 2026-10-02 (owner):* a curation action may also be proposed from a
**signed judgement** made at Enrichment. The rule above is about what a
*survey* may propose; a person's judgement is already a person's, so an
action that follows from it (sensitivity = PII proposes a confidentiality
classification, a governance zone, a retention rule) is a proposal with
clear provenance, "proposed from your sensitivity judgement · *date*", and
a person confirms it as with any other. A proposal may combine the two
sources, measured PII columns plus a confirmed sensitivity, and names both.
What stays out: a survey inferring the judgement itself, then proposing
from it.

**A10. There are two kinds of analytic function, and they need two names.**
A **step** measures a resource: it needs a credential and an executor, runs
asynchronously under RE's control, and emits annotations into the ODS. A
**routine** computes over data already collected, the ODS or Egeria, for a
presentation: it runs where the presentation runs, synchronously or near
it, and returns a result set or a figure, not annotations. "Analysis" is
not used for either, because RE's catalogue already means steps by it.
Steps may call routines; routines never call steps. A routine declared once
serves EA's report specs, the Portal's dashboard tiles and RE's boards. <dw>Routines should be executable be executable in python and jupyter</dw>

**A11. The execution environment for both is a library, not a service.** One
shared Python runtime (a Trellis shared library) provides the data access,
reading the ODS, reading Egeria through pyegeria, reading annotations by
type, and the envelope types. It imports the same way from a plain Python
script, the Portal's apps and Trellis. Steps add the executor harness
(Prefect, the engine host) on top; routines need only the library. Which
means a routine written against a script today runs unchanged in a Portal
tile tomorrow, and the proof is that one routine is tested from all three.<dw>Where do the libraries live? How are they registered - what metadata is captured?</dw>

**A9. EA's report specs, user analytics routines and Portal dashboard tiles
are the same eight points with a different input.** A report spec declares
what to fetch; a routine is an executor over catalogue metadata rather than
over a resource; a tile is a renderer. One analytic-unit model across the
apps lets a Portal dashboard show RE's annotations and RE's boards show a
routine's output.

## 3. Requirements to flush out

Each needs an owner and a yes/no before architecture. Blank where unknown.

- **R1 Authoring.** How a user writes a step: a Python package with a
  manifest; a single file dropped in a directory; a notebook; a Dr.Egeria
  document naming a package. Which, and which first. <dw>perhaps all></dw>
- **R2 Registration.** How a step becomes known: installed package scanned
  at startup; explicit register command; Egeria element created by the
  package. How a removed step is retired without orphaning results. <dw>Dr.Egeria?</dw>
- **R3 Versioning.** A step's version on every result; what happens to old
  results when a step changes; whether two versions may coexist.<dw>versions can co-exist; old results remain attached to the old version</dw>
- **R4 Inputs.** What a step may read: the resource (with which
  credential tier), prior annotations, the ODS, Egeria, the network. How
  prerequisites are declared and resolved (the existing resolver). <dw>modeled on the egeria process spec design?</dw>
- **R5 The envelope.** The exact required fields of the annotation
  envelope, agreed with the Egeria leads; how "not applicable" is
  distinguished from "not established"; how partial (◐ within credential
  scope) is carried.
- **R6 Binding.** Whether a question binds to annotation types plus a
  predicate, to step ids, or both; who may add a question; how a user step
  gets a question without editing the CSV.
- **R7 Readers.** One generic reader per annotation type, with the state
  derived from the envelope; what a step may add beyond that (a headline
  template, a unit).
- **R8 Storage.** ODS retention; whether raw outputs are kept beside
  annotations; the TEXT→JSONB migration; what trending needs per type. <dw>this could also be user configurable</dw>
- **R9 Publication.** Which annotation types publish by default; what <dw>perhaps there is a default profile for this and users/teams can select and modify their profiles</dw>
  needs a person's confirmation first; how a user step's new annotation
  type reaches Egeria's type registry.
- **R10 Curation.** Which fields a step may propose to; the proposal
  envelope; whether a step may propose a relationship (semantic
  assignment) or only a value.
- **R11 Presentation.** A generic renderer per annotation type for
  /next boards and the Portal tiles; what a step may declare about
  presentation (a chart kind, an ordering) without owning a renderer.
- **R12 Security.** Who may install steps; what a step may not do (write
  to the resource, publish directly, read other tenants' ODS rows); how
  secrets reach a step (never in its declaration).
- **R13 Cross-app.** Which of this is shared code (trellis shared
  libraries), which is RE's, which is the Portal's; how EA's report-spec
  model maps onto points 1–5.
- **R15 Routine declaration.** How a routine is declared and found: the
  report-spec mechanism naming a routine with parameters, or the same
  manifest as steps with a `kind: routine`. What a routine returns (a
  table, a figure spec, both) and how a renderer reads it.
- **R16 One runtime, three hosts.** The shared library's surface, its
  dependency footprint (pyegeria, psycopg, nothing heavier by default), how
  it finds the ODS and Egeria from each host (env, config, the Portal's own
  settings), and how a routine proves it runs from a script, the Portal and
  RE.
- **R14 Testing.** What a step author must supply: a fixture resource, an
  expected annotation set, and the absence case (the step on a resource
  where the thing isn't there), so the envelope is exercised before the
  step is installed.

## 4. What happens next, in order

1. **Inventory**, read-only, same method as the Classic-versus-/next
   parity table: every extension point as it exists today across RE, EA,
   the Portal and Dr.Egeria. Where declarations live, how executors are
   chosen, every result shape, how each presenter finds its data, what
   publishes, what proposes. Output: a table per point, with "not checked"
   where not checked.
2. **The envelope**, agreed with the Egeria leads, as a short spec with
   examples for three existing annotation types and one new one.
3. **Two paper walkthroughs**, before any code: one user-added survey step
   end to end through the eight points (candidate: a HazMat-term detector
   over column names and comments, which also feeds Find), and one
   user-written Portal dashboard tile over its results.
4. Then architecture, as a design note with the designer's drawings for
   the authoring and registration surfaces.

## 5. Open questions

- The first concrete user step and the first concrete tile, named by the
  owner; everything above is checked against them.
- Whether a SQL-bodied database step is a step kind of its own or a
  Python step with a SQL helper.
- Whether the Portal's report-spec mechanism becomes the declaration form
  for routines, or routines get the same manifest as steps. <dw?Report specs say what routines to execute but don't define them - that would be another spec</dw>
- How much of this is Egeria's to standardise (annotation types, step
  declarations as governance action types) and how much stays Trellis's.<dw>are there other standards or best practices for us to consider adopting?</dw>

## 6. Owner review of 2026-10-06, and what it changes

The owner's comments are kept in place above as `<dw>…</dw>`; this section
answers each and names the assumption or requirement it changes. Where a
comment is a decision, it is recorded as one.

| # | Where | Owner's comment, in short | Response | Changes |
|---|---|---|---|---|
| 1 | §1 point 2, Execution | execution also needs to know what kind of code: Python only or whatever | Agreed. The declaration carries a `language` (or runtime) field and the executor is chosen from it, not from the step's name. First kinds: Python package, Python single file, notebook, SQL-bodied (a Python step with a SQL helper until §5 decides otherwise), Java on the engine host. | Point 1 gains `language`; A6 reads "an executor RE chooses *from the declared language*"; R1 lists the kinds. |
| 2 | A5, git | conceptually agree, but git should be for extending and modifying, not required for out-of-the-box execution | Accepted as a rule. Out of the box, steps and routines run from installed packages with no repository present. Git is the path for customising and contributing. Provenance then records what exists: the package name, version and a content hash of the step's code, and the commit only when there is one. | A5 reworded; R3 gains "provenance without a commit". |
| 3 | A6, executors | should the Portal also use Prefect; routines must run in simple Python and Jupyter | Two answers. Routines run anywhere the shared library imports: a plain script, a Jupyter notebook, a Portal tile, Trellis; no executor is needed for a routine. Steps use an executor because they touch resources with credentials and write annotations; the Portal may use Prefect for scheduled routines but is not required to. | A6 applies to steps only; A10/A11 name Jupyter as a host explicitly. |
| 4 | A10, routines | routines should be executable in Python and Jupyter | Yes; that is A11's claim made explicit. The proof becomes: one routine tested from a script, a notebook and a Portal tile. | A11's proof lists three hosts by name. |
| 5 | A11, the library | where do the libraries live, how are they registered, what metadata is captured | The shared library is one package in the Trellis monorepo, published as a wheel so a notebook can `pip install` it. Routines and steps register through a manifest (id, version, language, author, inputs and their tiers, output shape or annotation types, cost, availability, tests) discovered by Python entry points at startup, and optionally declared in Egeria by a Dr.Egeria document (comment 7). | New R19 "Library packaging and discovery"; R2 names the manifest fields. |
| 6 | R1, authoring | perhaps all | All four forms, in this order of delivery: Python package with a manifest first, single file second, notebook third (via a parameterised-notebook runner), with a Dr.Egeria document able to name any of them. | R1 decided. |
| 7 | R2, registration | Dr.Egeria? | Yes. A Dr.Egeria document registers a step or routine as an Egeria element and retires it; retirement sets a status and never deletes, so results keep their producer. Startup scanning of installed packages remains for the no-Egeria case. | R2 decided; ties to A3. |
| 8 | R3, versioning | versions can co-exist; old results remain attached to the old version | Recorded as the decision. Two versions may be registered and runnable at once; every result carries the version that produced it; a reader never reinterprets an old result under a new version. | R3 decided. |
| 9 | R4, inputs | modelled on the Egeria process spec design? | Yes, as the model to study first: prerequisites, guards and action targets as in Egeria's governance action processes, so a step's declaration can later be published as a governance action type without translation. Where the existing resolver already does this in Python, it stays. | R4 gains "study Egeria's governance action process model"; §5 last bullet partly answered. |
| 10 | R8, storage | retention could also be user-configurable | Yes: a default retention per annotation type, overridable by a person with the right role, with the override recorded like any other setting. | R8 gains the override. |
| 11 | R9, publication | a default profile that users or teams select and modify | Adopted. A publication profile names which annotation types publish by default and which need a person's confirmation; one default profile ships; a team may copy and edit it; a resource's profile is a recorded choice. | R9 decided. |
| 12 | §5, report specs | report specs say what routines to execute but do not define them; that is another spec | Agreed, and it settles the bullet: a routine has its own declaration (the manifest of comment 5); a report spec and a dashboard tile reference routines by id. | §5 bullet answered; R19. |
| 13 | §5, standards | are there other standards or best practices to consider adopting | Candidates to evaluate before R5 and R7 are closed, with a recommendation each: OpenLineage facets (already used; adopt for run and dataset facts); Egeria's own survey annotation types (adopt; A1); Great Expectations' expectation and validation result shapes (study for quality annotations); MLflow model cards and the model-metadata fields (study for model resources); Prefect deployment specs (adopt where a step is scheduled); Jupyter papermill parameters (adopt for notebook steps). A short comparison note is the deliverable. | New R20 "Standards adopted and studied". |

### Assumptions and requirements as amended

- **Point 1 (Declaration)** now includes `language`.
- **A5** reads: *Git holds declarations, code and dashboard definitions for
  customising and contributing, never results. Out of the box, steps and
  routines run from installed packages with no repository present.
  Provenance records package, version and a content hash, and the commit
  when there is one.*
- **A6** applies to steps. *Routines run wherever the shared library
  imports: script, notebook, Portal tile, Trellis.*
- **A11**'s proof: one routine tested from a script, a notebook and a
  Portal tile.
- **R1** decided: all four forms; package, single file, notebook, Dr.Egeria
  document, in that order.
- **R2** decided: Dr.Egeria document registers and retires; startup scan for
  the no-Egeria case; manifest fields as in comment 5.
- **R3** decided: versions coexist; results stay with their version.
- **R4** studies Egeria's governance action process model first.
- **R8** gains a per-type default retention with a recorded override.
- **R9** decided: publication profiles, one default, team copies.
- **R19 Library packaging and discovery.** Where the shared library lives,
  how it is versioned and installed, how manifests are discovered.
- **R20 Standards.** The comparison note of comment 13.

Still open from the owner's earlier asks: the first user step, the first
tile and the first context-spec flavour to build, and the context-as-product
note (data scientist and engineer first; the non-technical view is a
separate chat-first Trellis application on the same data).

# Design — the lifecycle of analytic routines and survey steps: where they live, how they are managed (2026-10-30)

> Superseded in part by Q2(b), 2026-10-31: analytics are found through the `pyegeria.analytics` entry point (`trellis.analytics` is only an alias), installing the package registers it, and report specs name functions by registered name, not dotted path or `PYEGERIA_REPORT_SPEC_MODULES`. See `packages/trellis-analytics/README.md`, "How analytics are found".

Owner's words (architect chat, 2026-10-30): "We also need to architect the lifecycle for analytic routines (and survey
steps), where they live and how they are managed."

Builds on DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS (R1 authoring forms; R2 Dr.Egeria registers and retires,
start-up scan for the no-Egeria case; R3 versions coexist and results stay with their version; R8 retention; R9
publication profiles; R19 packaging and discovery — still open), DESIGN-DECLARED-EXTENSIBILITY §1–§7, the eval of
2026-10-30 (Portal coordination), and what is built: `trellis_analytics/{manifest,discovery,step,declare}.py`,
`survey_step_host.py`, `engine_host.py`, the generators, `analysis_catalog.yaml`. Design only; the decisions are §7.

## 1. Two kinds, one lifecycle

| | Routine | Step |
|---|---|---|
| what it is | a function over data RE already holds (ODS/registry view, Egeria) — `curation_coverage`, `context_pack` | a measurement of a resource during a survey, run under a guard, producing annotations |
| runs where | anywhere the library imports: script, notebook, Portal tile, RE | RE's step executor (in-process synthetic step or Prefect worker), or dispatched by Egeria through RE's engine host |
| manifest | `RoutineManifest` (kind routine) | `StepManifest` (kind step: inputs, credential tier, annotation type, request type, guard) |
| results | returned to the caller with an envelope; a tile or a chat answer | stored (`step_annotations`, `step_runs`) with version and envelope; published as annotations |
Both share: a declaration, a version, a source, an entry-point registration, pyegeria's registry, and the states below.

## 2. The states, and what moves a routine or step between them

```
 authored ──declared──▶ packaged ──installed──▶ discovered ──▶ admitted │ refused │ not installed
                                                                   │
                       ┌───────────────────────────────────────────┘
                       ▼
                 catalogued (card, question) ──▶ declared to Egeria (optional) ──▶ enabled for an investigation
                       │                                                                 │
                       ▼                                                                 ▼
                  runnable ──priced──▶ run (versioned) ──▶ results kept ──▶ published (profile) ──▶ retained/expired
                       │
                       ▼
                 changed (new version; old coexists) ──▶ deprecated (status) ──▶ removed (results keep producer)
```

| State | Means | Who / what moves it | Where it is recorded |
|---|---|---|---|
| **authored** | `run()` + manifest written; §0 of the tutorial answered | the developer | their package |
| **declared** | the manifest validates (units, as-of, sentences, kind; soon basis/capped/categories) | `Declaration` validators at import; refusals are sentences | the package; a failing import never reaches RE |
| **packaged** | a distribution with the `trellis.analytics` entry point and a version (`dynamic` from the package) | the developer | PyPI / a wheel / an editable path |
| **installed** | present in the venv RE (or the notebook, or the Portal) runs from | `pip`/`uv` by an admin; **today also the only place a step "exists"** | the venv; nothing in RE's registry yet |
| **discovered** | `discovery.discover()` read the entry point at start-up | RE start-up (and `refresh`) | start-up line "library steps N"; `LOAD_ERRORS` |
| **admitted / refused / not installed** | admitted: no built-in's name or request type, no `connection`, publishable annotation type; refused: with the sentence; not installed: catalogued (fallback entry) but no package | `survey_step_host.attach` | `_LIBRARY`, `_REFUSED`, `_MISSING`; Admin and the card say which |
| **catalogued** | card in the analysis catalogue, question merged into the question catalogue | automatic on attach; the committed fallback via `generate_external_step_entries.py` | runtime catalogue; `analysis_catalog.yaml` GENERATED block |
| **declared to Egeria** | SurveyActionService + GovernanceActionType under RE's engine | generator writes `docs/dr-egeria/engine-host/library/<step>.md`; the owner runs it (a write) | Egeria; the document in git |
| **enabled for an investigation** | the step's purposes intersect the investigation's; perspectives shown | purposes in the manifest; the investigation's purposes (Egeria valid values) | the Questions/Survey tabs |
| **runnable → priced → run** | `runnable_and_reason`; the priced line; the press or the Egeria dispatch | a person (as themself) or Egeria's engine action | `step_runs` (executor, version), `activity_log` |
| **results kept** | annotations with the envelope, stamped with the producer's version | the executor | `step_annotations` (+ the step's own tables where it has them) |
| **published** | per the publication profile (R9) and the owner's two-channel rule | Curate press or unattended engine path | SurveyReport annotations; `published` rows |
| **retained / expired** | per-type default retention, overridable (R8) | a sweep (not built) | the registry |
| **changed** | a new package version; results of the old version stay readable and labelled (R3) | the developer; an admin installs | both versions may be registered; the card shows the running one |
| **deprecated** | Egeria `Content Status` DEPRECATED on the declaration; RE stops offering it to run, keeps showing its results with "retired <date>" | a Dr.Egeria document (R2); Admin mirror | Egeria; RE reads it |
| **removed** | package uninstalled; the fallback card says "not installed"; results keep producer + version | an admin | `_MISSING`; nothing deleted |

## 3. Where they live (three tiers, by who owns the code)

| Tier | Lives in | Examples | Rules |
|---|---|---|---|
| **T0 built-in** | `resource_explorer/` (RE's own package) | schema_inventory, value_profile, statistics_freshness's db twin… | declared like everything else (generated declarations); first claim on names and `re-<name>` request types; change through RE PRs and gates |
| **T1 library** | `packages/trellis-analytics` (shared routines, readers, the wrapper) and its `examples/steps/*` (reference steps) | curation_coverage, context_pack; fs_activity, hub_tables… | ship with trellis; **should be workspace members** (§5); gated by the extensibility gate; the Portal imports the routines' output over HTTP, never the package |
| **T2 third-party** | any package anywhere, installed into the venv | a customer's step | found only by entry point; admitted by the same rules; never in trellis's lock; declared to Egeria by its owner |
| **(Portal)** | `PyegeriaWebHandler/*` functions registered in pyegeria's registry | count_elements, metric_trend, re_analytics tile | coordinated through pyegeria's spec (eval §3/§4); not RE's lifecycle but the same registry |

Notebooks and single files (R1 forms 2 and 3) are T2 by another packaging: a parameterised-notebook runner that wraps
a notebook as a routine with a manifest; not built; ordered after packages.

## 4. How they are managed (the surfaces)

1. **Admin › Analytics** (exists in part; to complete): one row per discovered manifest — kind, tier, package and
   version, state (admitted / refused + sentence / not installed), declared-to-Egeria yes/no (read from Egeria, with
   the Content Status), last run and executor, results kept, "retire" (writes the Dr.Egeria document for the owner to
   run, never writes Egeria itself). The refusal sentences are the same ones the start-up line prints.
2. **CLI** `resource-explorer steps list|check|declare <step>|retire <step>`: `list` = the Admin table in text; `check`
   = import every manifest and run the declaration validators without starting RE (for CI of a step package);
   `declare` and `retire` = write the Dr.Egeria documents.
3. **Dr.Egeria documents** are the register/retire channel to Egeria (R2). RE reads Egeria's status back; it never
   infers deprecation from a missing package.
4. **The catalogue fallback** (`analysis_catalog.yaml` GENERATED block) keeps a package-less environment aware of a
   step: the card says "not installed · provided by <package>" rather than nothing. Generated only; a PR that changes a
   generator's input and one that commits its output are gated together.
5. **Egeria** holds the governed view: the engine's services and action types, their Content Status, and the
   SurveyReports that results publish into. Lineage from a result to its producer is the version + request type on
   the annotation's envelope.

## 5. Packaging and discovery (R19, the open requirement; the one thing broken today)

- **Fact:** `[tool.uv.workspace] members = ["packages/*"]`; the example steps live under
  `packages/trellis-analytics/examples/steps/*` and are NOT members. `uv sync --exact` removes them (2026-10-21
  incident); `uv run --package` re-syncs and uninstalls them (2026-10-15). The gate worktree needs a re-install after
  every sync, by hand.
- **Design:** promote the reference steps to workspace members (`members = ["packages/*", "packages/trellis-analytics/examples/steps/*"]`
  or move them to `packages/steps/*`), so the lock pins them and sync keeps them; RE's `dev` extra depends on them so
  8813's venv always has the eight. Third-party steps stay outside the lock by definition; Admin shows them as T2.
- **Versioning:** every step package has its own version (`__version__`), stamped on every result (built). Two versions
  of one step may be installed only as two distributions with distinct names (`trellis-step-fs-activity` and
  `…-v2`), each with its own entry-point name — the manifest `name` stays, the registry key gains the version; the
  card shows the newest as runnable and lists the older's results under "earlier versions". (Design; not built.)
- **Content hash:** provenance records package, version and a content hash of the manifest+code (A5); the
  statement pins already do this for the vendored TestGen SQL; extend `discovery.origin()` to compute it.
- **Discovery is one group:** `trellis.analytics` today; if pyegeria adopts an entry-point group (eval §4.2), trellis's
  becomes an alias and the Portal stops using env-var lists.

## 6. What this changes in RE (small) and what it does not

Changes: workspace membership of the reference steps; Admin › Analytics completed (Egeria status, retire action,
earlier versions); the `steps` CLI; `discovery.origin()` content hash; deprecation read from Egeria status; the
expiry sweep (R8) as a later slice. Not changed: the manifest, the wrapper, the admission rules, the generators, the
two publish channels, action identity.

## 7. Decisions for the owner

1. **Reference steps as workspace members** (recommended) vs a separate `trellis-steps` repository. Members: one
   lock, one gate, sync keeps them. Separate repo: cleaner "no RE code change" proof, but a second release train.
2. **Who may install a T2 step on the shared instance** — an Admin role in RE, or only the operator (pip). Recommended:
   operator installs; Admin shows and retires; RE never pip-installs from a screen.
3. **Deprecation sweep** — when a step is DEPRECATED in Egeria, do its unpublished results stay visible for ever,
   for the retention period (R8), or until an admin archives them? Recommended: retention period, labelled "retired".
4. **Two versions at once** (R3 says they may coexist) — worth the registry-key change now, or only when a second
   version exists? Recommended: when it exists; record the design here so it is not improvised then.
5. **R1 forms 2 and 3** (single file, notebook) — still wanted, and after dashboards D1 or before?

## 8. What to hold in Egeria (owner's words, 2026-10-30: "we need to decide what we should hold in egeria - and then we will create new objects for it if needed")

Rule applied (owner 2026-10-07, "follow Egeria's lead"): an existing Egeria type before a new one; a new object only
where no existing type carries the fact without bending it. Every "candidate" row below is to be checked against
Egeria's type catalogue with Mandy before any object is created — this table is the proposal, not the inventory.

| Lifecycle fact | Held where today | Egeria home | New object? |
|---|---|---|---|
| a **step exists and Egeria may dispatch it** | Egeria: `SurveyActionService` + `SupportedGovernanceService` (request type) + `GovernanceActionType` `ResourceExplorerSurvey::<request type>` with executor, under engine `ResourceExplorerSurvey` (generated declaration, owner runs it) | **keep** | no |
| a step's **manifest fields** (version, source, inputs, tier, guard, questions, purposes) | `additionalProperties` rows on the GovernanceActionType (ISSUE-150 (c)) | keep as the carrier; it is what Egeria offers for "the type's own facts" | no |
| a step's **declaration: measures** (name, unit, as-of, basis, capped, categories, annotation type) | only in the package; on Egeria only as prose in `additionalProperties` | **candidate: one `GovernanceMetric` per declared measure** (displayName = measure, description = unit + sentence, measurement/target fields for basis), linked to the step's GovernanceActionType. Pay-off: the Portal's Governance Metrics panel (P4) lists RE's declared measures with no Portal change, which is what #603 wanted the panel to show | no new TYPE; new ELEMENTS, generated from the declaration like the engine-host document |
| a step's **absence sentences and confidence form** | package; `additionalProperties` prose | stay in the declaration (they are rendering words, not governance facts); the GovernanceMetric description may quote the measured/not-measured pair | no |
| a **routine** (curation_coverage, context_pack) exists | nowhere in Egeria (routines have no element) | **candidate: an Egeria asset of an analytics kind** — Egeria's Area 2 analytics assets (model / model-run style types) are the lead to check first; else a `GovernanceService`-style definition without an engine. The Portal reaches routines through report specs, which the Portal already keeps as Egeria `Report` elements, so a routine's Egeria home should be what a `Report`'s spec points at | type check needed; possibly the first genuinely new object |
| a **run** (who, when, version, executor, outcome) | RE `step_runs`; for engine-dispatched steps the engine action + `SurveyReport` | keep: `SurveyReport` (+ completion status, the cross-repo item from #603 §8.1) for steps; for routines a run is not an Egeria event unless it publishes | no (the completion-status convention is the open ask to Egeria) |
| **results** | RE `step_annotations` + step tables; published as annotations on the SurveyReport (channel 1) or facts through Curate (channel 2) | keep the two channels as ruled | no |
| **version** of the producer, per result | envelope `additionalProperties` on every annotation (built) | keep; if GovernanceMetric elements exist, each carries the version it was declared from | no |
| a **package / distribution** (name, version, content hash, where installed) | venv only; `discovery.origin()` | **candidate: `SoftwareCapability` / deployed-component element per installed distribution** on the engine host's `SoftwareServer`, with version and hash as properties — gives "which version is where" across RE instances | type check; likely existing types suffice |
| **deprecation / retirement** | not held (R2 design) | `Content Status` DEPRECATED on the GovernanceActionType (steps) and on the routine's element; RE reads it back | no |
| the **questions** a step answers; **purposes** | question catalogue → Egeria glossary terms (94 exist); purposes → Egeria valid-value list (ruled 2026-10-13) | keep; a step-brought question (`question_defs`) becomes a glossary term the same way RE's do | no |
| **publication profile** (which annotation types publish by default) | not built (R9) | candidate: Egeria valid-value set, like purposes | no new type |
| **retention** per annotation type (R8) | not built | RE setting; Egeria `Retention` classification on published elements if ever needed | no |
| the **analytic catalogue** as a browsable whole ("what can be measured here") | RE's analysis catalogue (runtime + YAML); pyegeria registry; Portal's Analytic Functions Browser | derive it from the elements above (engine services + metrics + routine assets); do not add a catalogue object | no |

**Net:** two sets of NEW ELEMENTS, no new types unless the routine check says otherwise: (1) `GovernanceMetric` per
declared measure, generated from declarations alongside the engine-host document and run by the owner; (2) a
deployed-component element per installed step/routine distribution. One TYPE question for Mandy: the Egeria home of
a routine (an analytics asset type vs a governance-service definition). The completion status on `SurveyReport`
stays the cross-repo ask it already is.

Owner decisions added: 6. generate `GovernanceMetric` elements from declared measures (yes/no; if yes, before or
after the Portal declares its own); 7. routine home — ask Mandy which type, or defer routines' Egeria presence until
one publishes; 8. distribution elements — worth it with one RE instance, or only when a second host exists.

## 10. Owner ruling 2026-10-30 and the plain version of this note

Owner's words: "we should support a common library (maybe in git) of steps and analytic functions that can be
enabled for plain report_specs, in the portal, in re and probably ea." This answers decision 1: **the library gets a
repository of its own**, not workspace membership in trellis.

**In one page, for anyone:**
- A step or function is **written** by a developer, with a short description next to the code saying what its numbers
  mean and what "nothing" means. If the description is incomplete, the package refuses to load and says which word is
  missing.
- It is **shipped** as an ordinary Python package from the common library's repository. Installing the package is the
  whole registration: the host finds it on start-up and lists it by name, with a reason if it refused it.
- It is **turned on** for a kind of investigation through the purposes it declares, and **run** by a person or by
  Egeria, at a stated price, with the person's own identity. Every result carries the version that produced it.
- Its results are **kept** in the host, **published** to Egeria only through the two agreed channels, and kept for a
  retention period.
- When it **changes**, a new version ships; old results keep saying which version made them. When it is **retired**,
  Egeria records the retirement as a status; nothing is deleted, and the host stops offering it to run.
- Administrators see all of this on one Admin page and one command line; Egeria holds the governed copy: which steps
  exist, which version is installed where, and (proposed) one governance metric per declared measure, so the Portal's
  Governance Metrics page shows them with no extra work.
- Remaining decisions for the owner: §7 items 2–5 and §8 items 6–8.

## 11. Three assumptions in the current model, and what to do about each (owner, 2026-10-30)

Owner's questions: "are you assuming that all analytic steps/analytics are numeric? are you assuming a common shape of
input? are you assuming a single input (rather than, for instance, a comparison between sources)?" — and the ruling:
"we can put comparison implementation on backlog but we should document the current proposal; we might want to make
the other changes you suggest now since they can be leveraged more broadly".

| Assumption | As built | Decision |
|---|---|---|
| **A. Numeric** | eight result kinds (measure, series, distribution, list, graph, finding, proposal, table), but every declaration must carry `measures` (numbers with unit + as-of), and the readers, publish table and the new basis/capped/categories fields read measures only. RE's own catalogue declares 26 of 42 analyses as list / finding / proposal / table, with nothing kind-specific declared for them | **change now** — declared words per kind (brief part B) |
| **B. One input shape** | a step declares inputs from a fixed list (resource, ods, connection, source_report); results are one annotation per scope string | keep the fixed list for unattended safety; **generalise the subject** (part C) and, later, the roles (§12) |
| **C. One subject** | `HostContext` names one resource type and one slug; a step cannot see two resources, a reference set, or a work list | **change now for sets** (brief part C: a work list or an investigation's members as the subject — the Q3 quality batch across a work list is the host precedent); **backlog for comparison** (§12) |

## 12. Comparison across sources — the proposal, documented for the backlog

What it is: a step whose subject is two or more things in **roles**, e.g. primary = a database table, reference = the
same table in another database, a glossary, a file of expected values, or an earlier snapshot from another tool
(the TestGen channel). RE has comparisons today but none a third party could write: table comparison for drift (two
profiles of one table), the compare agent and history diff (RE's own records over time), the TestGen reader (another
tool's profile of the same resource).

Proposal (not for a builder yet):
- **Manifest:** `subjects: list[SubjectRole]`, each `{role: str, resource_types: [...], tier: none|read_only, required: bool}`; the
  existing single-subject step is the degenerate case `[{role: "primary"}]`, so no manifest changes meaning.
- **Host:** `HostContext.subjects: dict[role, (resource_type, slug)]`; the host opens each role at its tier; a role may
  also be a non-resource reference (a valid-value set, a glossary) handed as plain data under `source_report`'s pattern.
- **Scope of a result:** a pair or tuple in words, "a.customers ⇄ b.customers", with the roles named in the envelope so
  publish knows which element the annotation hangs on (the primary) and which it cites (the reference).
- **Declaration:** a comparison's measures are the same shape (a count with a basis; "12 of 480 rows differ"); the
  absence sentences gain the case "reference not available".
- **Where it runs:** RE host first (a person picks the two subjects on screen); Egeria dispatch later (its process
  model has action targets for exactly this, R4's study item).
- **Pyegeria core:** `subjects` belongs in the shared manifest spec, so decide it with the library-repository split.

## 13. Decisions for the owner (added)
9. Part C's set subject: a work list only, or also "every member of an investigation"? Recommended: work list first.
10. §12 comparison: wait for the library split (recommended) or design the manifest field now so the split carries it.

## 14. Owner rulings on the fifteen decisions (architect chat, 2026-10-30), quoted

| # | Decision | Owner's words | Effect |
|---|---|---|---|
| 1 | library repository | "Yes - I'll create it" | owner creates the repo (recommended odpi/egeria-analytics); trellis-analytics becomes a dependency |
| 2 | first release contents | "Yes - plus what we have from the portal side - need to review that" | declaration model, wrapper, readers, routines, eight steps, tutorial, PLUS the Portal's registered specs/functions after a review of PyegeriaWebHandler (report_specs_handler specs, overview helpers, re_analytics) |
| 3 | Egeria Advisor | "EA - may become where we a user creates and manages surveys, analytic steps, analytics, local dashboards - it has a lot of the infrastructure already" | EA is a candidate AUTHORING AND MANAGEMENT host, not just a consumer: §4's Admin/CLI surfaces may land in EA; design item |
| 4 | who installs | "For a first round that's fine; we also need to plan how we do this with portal; We also need to have a sensible pre-built default seed set." | operator installs for now; two follow-ups: the Portal's install path, and a default seed set shipped with the library (which steps/routines/specs are on out of the box) |
| 5 | deprecation sweep | "Ok as a start - experience will tell if change is needed" | retention period, labelled retired |
| 6 | two versions | "Ok - I think having the default be the latest version and if you want an earlier one require it be specified is fine." | latest is the default; an earlier version is named explicitly (a `version` parameter on the run / report spec) |
| 7 | single-file + notebook forms | "Yes" | after dashboards D1; notebooks first |
| 8 | GovernanceMetric per measure | "Ok - do we need to add any attributes to Egeria's GovernanceMetric element? I assume we would use the ImplementedBy relationship to also indicate how it is implemented?" | yes; answered in §15 |
| 9 | routine's Egeria home | "Seems like we would use AnalyticalActionProcess with an implementedBy a GoverananceActionService (which runs in an Engine Host - which RE appears to be)? For the portal, it might be implemented by an EmbeddedProcess? Want to discuss?" | discussion in §15 |
| 10 | distribution elements | "OK" | when a second host exists |
| 11 | set subject | "OK" | work list first |
| 12 | comparison | "OK" | wait for the library split |
| 13 | pyegeria core items | "ok - what do you mean by state-aware execution?" | log all three; explained in §15 |
| 14 | Portal order | "after" | RE's generated GovernanceMetric elements first |
| 15 | resizable-tables batch | "ok" | after the quality fix round |

## 15. Egeria types for 8 and 9, read from the open-metadata-types archive (egeria-v6/egeria)

**GovernanceMetric** is an entity under GovernanceControl → GovernanceDefinition, so it already carries the definition
properties (identifier, title/display name, summary, description, scope, domain, importance, implications, outcomes,
results) plus two of its own: `measurement` (how it is measured) and `target` (the desired value). Relationships:
`GovernanceResults` (GovernanceMetric → Asset, with `query` and `queryType`: where the measurements are) and the
`GovernanceMeasurements` / `GovernanceExpectations` classifications (actual and expected values on an element).
**ImplementedBy** is Referenceable → Referenceable, end1 `derivedFrom` ("abstract representation"), end2
`implementedBy` ("resulting refined element"), with design-step/role/transformation/description properties.
**No attribute additions needed for a first pass:** a declared measure maps as displayName = measure name,
description = unit + the measured/not-measured sentences, `measurement` = "RE analysis <id>, field <value_field>, as of
<as_of field>; basis <denominator_label>", `target` = empty (RE declares none); GovernanceResults → the resource's
Asset with query = the RE route that answers it; ImplementedBy → the step's GovernanceActionType (steps) or the
routine's element (9). Unit, basis and capped travel in `additionalProperties` until experience says a typed
attribute is worth asking Egeria for.
**9, the routine's home — the types that exist:** GovernanceActionProcess, GovernanceActionType, GovernanceActionService,
SurveyActionService, GovernanceEngine, EmbeddedProcess, TransientEmbeddedProcess, DeployedAnalyticsModel,
AnalyticsModelRun, AnalyticsEngine. "AnalyticalActionProcess" does not exist; the owner's shape reads as
**GovernanceActionProcess → ImplementedBy → GovernanceActionService** (runs in an engine host; RE's engine
`ResourceExplorerSurvey` is one) for steps — which is nearly what the generated declaration already does, minus the
process and the ImplementedBy link. For routines, two candidates to discuss: (a) the same shape with the Portal's
executor as a second `ImplementedBy` end (an `EmbeddedProcess` for an in-process run, as the owner suggests);
(b) `DeployedAnalyticsModel` + `AnalyticsModelRun` for a routine and its runs, with `AnalyticsEngine` for the host.
Architect's lean: (a) — one shape for steps and routines, with ImplementedBy carrying "how" per host, and
`AnalyticsModelRun` reserved for model-like routines (scoring, inference) if any appear.

## 16. Rulings on 9 and 13 (architect chat, 2026-10-30), and a correction

Correction: `AnalyticalActionProcess` EXISTS — a subtype of `GovernanceActionProcess` in the current type set
(OpenMetadataType enum: ANALYTICAL_, CATALOGUING_, DELETING_, EXPLORING_, PROVISIONING_, SUBSCRIBING_,
SURVEYING_ACTION_PROCESS). §15's "does not exist" was read from the 1.x–3.x archive files only; it is withdrawn.

Owner's words: "AnalyticalActionProcess is a subtype of GovernanceActionProcess. On 9, I think it is important to
indicate how it is implemented - so lets start with EmbeddedProcess - we may change this. One reason to do this
because I want to transition report_specs themselves into a set of Egeria elements and the analytics are also used in
report_specs." And on 13: "ok".

Decided shape (first pass, may change):
- **A routine** = one `AnalyticalActionProcess` element, `ImplementedBy` → an `EmbeddedProcess` for the in-process
  executor (pyegeria's `exec_report_spec`, the Portal tile, a notebook). Manifest fields ride in `additionalProperties`;
  retirement = Content Status.
- **A step** = the existing `SurveyActionService` + `GovernanceActionType` under RE's engine, and, to make the two
  kinds read alike, a `SurveyingActionProcess` (or `AnalyticalActionProcess` where the step derives rather than
  surveys) `ImplementedBy` → that `GovernanceActionService`. The generator gains the process element and the link.
- **A declared measure** = `GovernanceMetric`, `ImplementedBy` → the process element above (§15 mapping).
- **Report specs** (owner's direction): to become Egeria elements themselves, each naming the analytics it runs; the
  `AnalyticalActionProcess` elements are what those report-spec elements will point at. This is a design item of its
  own (it touches the dashboards design: a Dashboard Sheet's Reports already are Egeria elements; the spec behind a
  Report is the missing element) — queue it behind the library split.
- **13 state-aware execution**: accepted; the pyegeria entry reads "the executor reads the declared result state
  before rendering; not measured → the declared sentence and no values".

Generators to extend when built: `generate_engine_host_declaration.py` (process element + ImplementedBy), a new
`generate_governance_metrics.py` (one GovernanceMetric per declared measure), and a routine declaration generator
(AnalyticalActionProcess + EmbeddedProcess) — all generated only; the owner runs them.

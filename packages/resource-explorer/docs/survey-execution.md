# Survey execution — dispatch, engine hosts, and orchestration

**Status:** consolidated design. Current as of 2026-09-02.
**Scope:** how a survey *runs* — who decides, who claims the work, and what
happens when the orchestrator is unavailable. For what a survey *is*, see
`survey-model.md`.

> **This document consolidates six.** Part I is the engine-host design (live
> hazards, the granularity question, the annotation-type defect, the target
> model). Part II merges the unified execution model's decisions, the
> orchestration-tool evaluation, the four execution permutations, and Egeria
> registration. Part III is the settled register.

---

## 0. Summary

RE models the same concept twice. `ANALYSIS_KINDS[k].step_keys` is "a named bundle of steps",
which is definitionally what a Survey Definition is, implemented in a second place with a
second authoring path. 25 of 27 repo analyses are single-step, so that second implementation
is 93% a pass-through.

The resolution is not to pick one. `analysis_id` is doing two jobs — bundling, and naming the
results a step produces — and only the first is duplicated. The second has a first-class home
in Egeria that RE currently fills with redundant data.

Egeria already models everything needed: guards, parameter passing, branching, named
annotation types, and multi-host work distribution with claim-based arbitration. **No extension
to Egeria's type model is justified by anything found here.** The work is removing assumptions
from RE, not adding to Egeria.

Three hazards shipped on 2026-08-26 that make the current state worse than it looks. They come
first because they are live.

---

## 1. Live hazards

All three are in code committed on 2026-08-26. None has fired yet, and each fires the first
time someone does the obvious next thing.

### 1.1 The generator destroys hand-authored definition detail

`scripts/generate_repo_survey_definition.py` ends in `output_path.write_text(markdown)` — a
full overwrite of every Dr.Egeria document it owns.

The generated documents carry exactly three additional properties per step (`executes_at`,
`supported_technology_type`, `re_analysis_step`) and the literal guard `Any` on every edge.
`repo_survey_types.csv` cannot express guards, request parameters, or branching, so any of
those must be hand-authored into the `.md`.

The first hand-authored guard is destroyed by the next generator run. Worse, the resync repair
added the same day (`egeria_resync._do_reauthor_survey_definitions`) *executes* those documents,
so the loss propagates: the guard is removed from the file, and then un-authored from Egeria by
RE's own repair.

**The CSV is a specification of what surveys are needed. The `.md` is the definition.** The
pipeline currently treats the definition as disposable output of the specification, which is
backwards, and which only survives today because nobody has authored a guard yet.

**FIXED 2026-08-26.** The generator now writes a file only when it is absent, byte-identical,
or provably untouched since it was last generated — the last established by a `.generated.json`
sidecar of content hashes. A file that differs from generation *and* from its recorded hash has
been hand-authored, and is reported with the first differing line rather than overwritten;
`--force` discards it deliberately.

The sidecar is what keeps `--force` rare. Without it, every legitimate CSV change would need
`--force` too, people would pass it by reflex, and the hazard would be back.

An earlier draft of this section proposed a second fix — "have the repair refuse to execute a
document that has diverged from the CSV" — and it was wrong twice over. The repair never
replaces documents; it executes them, which for a hand-authored guard is exactly what should
happen. And CSV-divergence is the wrong criterion: divergence is expected once the `.md` carries
detail the CSV cannot hold.

The real interlock is §1.2's hazard, and it shipped alongside: the repair now refuses to run
while any document carries a guard other than `Any`, because the reconciler it must run would
delete the branch. A document it cannot read counts as guarded. That refusal can be removed once
§1.2 is fixed, and not before.

### 1.2 The link reconciler forbids branching

`survey_definition_reconciler.py` identifies a duplicate edge by `(prev, next)` and does not
consider `guard` at all. `compute_expected_edges(survey_group, step_keys)` builds the expected
set as a **linear chain** from the step list.

Both are correct today, because every authored guard is `Any` and every definition is linear.
Neither survives the model in §4:

* `A → B guard=passed` and `A → B guard=failed` are legitimately distinct edges. The reconciler
  deletes one as a duplicate.
* Any genuine branch is absent from the linear expected set, so it is classified stale and
  removed.

This is the same hazard as §1.1 one layer down, and it is reached by the same action: authoring
a real guard. The reconciler is the mandatory post-heal step of the repair, so it runs on every
recovery.

**FIXED 2026-08-26.** `diff_links` is keyed on `(previous, next, guard)`, and
`reconcile_step_links` builds its expected set from the authored document rather than from a
linear chain over the step list. A copy left by a non-idempotent Link command is identical in all
three values, so matching on all three removes exactly what it should and nothing more.

The interlock added earlier the same day — refuse to repair while any document carries a real
guard — is gone with it, because refusing would no longer be honest.

Both sides were needed. Keyed on the guard but still diffing against a linear chain, every branch
edge would have been stale instead of duplicate: deleted either way. There is one document parser
for this (`surveyors/survey_definition_docs.py`), shared with the §1.3 scan, because two parsers
of one format is how they drift apart.

Verified against live Egeria before and after: all eight definitions reconcile to a no-op, with
kept counts equal to `steps − 1`. The change rewrites nothing on real data.

**What is NOT fixed: RE still cannot RUN a branching definition.**
`SurveyDefinitionReader._parse_graph` walks a single chain and raises
`UnsupportedSurveyDefinitionError` on a second outgoing edge. That is a loud, safe failure rather
than silent destruction, so it is not a hazard — but it is not the right end state either, and
an earlier draft of this section gave the wrong reason for leaving it. It said guard evaluation
would mean "a workflow engine inside RE, which is Egeria's job under §4". That framing left RE
with nothing to do when RE is the coordinator. The answer is §4.6: RE delegates to Prefect. RE
should never be the engine, in either case.

Note the irony worth recording: `NextGovernanceActionProcessStep` is MULTI_LINK, which is why
Dr.Egeria's link command duplicates rather than upserts — the problem that took definitions out
of service earlier the same day. The multi-link property is not a quirk. **It is the branching
mechanism.** RE's reconciler currently exists to suppress it.

### 1.3 The drift scan conflates two different checks

`egeria_resync._scan_definition_drift` compares Egeria's authored definitions against the CSV
and reports Egeria as "behind". That is one useful check, but it silently assumes the CSV is
authoritative and that every definition should have a CSV row.

There are two distinct questions, and only one of them is this:

| check | compares | means | tolerates |
|---|---|---|---|
| **recovery** | docs ↔ Egeria | Egeria has lost or drifted from the authored definitions | nothing |
| **specification coverage** | CSV ↔ docs | we said we needed a survey and never authored one | extra definitions |

The second must tolerate definitions with no CSV row at all — Egeria-native surveys, and
anything authored directly. The old scan would have reported those as drift, and did not only
because none exist yet.

**FIXED 2026-08-26.** Split into `definition_drift` (recovery: documents ↔ Egeria, drives the
repair, tolerates nothing) and `specification_gap` (coverage: CSV ↔ documents, tolerates extra
definitions, no repair button — closing a gap edits the source tree, and this panel repairs the
catalog, not the repository).

Two things fell out of doing it properly:

* **Order is now compared, not just membership.** Run order is load-bearing —
  `repo_foss_scorecard` reads what `repo_cve_scan` writes, and ran before it until earlier the
  same day. Verified first that document order matches live Egeria exactly across all eight
  definitions, so ordered comparison does not generate false drift.
* **Descriptions are compared too.** Comparing only step keys is what let Egeria sit on a
  `repo_manifest_parse` description the documents had already moved past — invisible to a scan
  whose entire job is to notice that. The split scan found it in three definitions immediately;
  they have since been re-authored and Egeria now matches.

Coverage remains membership-only, deliberately: the CSV's full-survey row is the sentinel `*`,
which has no order to compare, and its rows are not stored sorted. Order is authoritative in the
documents.

---

## 2. The granularity question

### 2.1 Evidence

Measured 2026-08-26 against `ANALYSIS_KINDS` / `STEP_REGISTRY` / `analysis_catalog.yaml`:

| fact | value |
|---|---|
| repo analyses | 27 (29 catalog entries; `egeria_publish` and `repo_profile_refresh` are actions, not analyses) |
| registered steps | 34 |
| analyses that are exactly one step | **25 of 27** |
| analyses that are real bundles | 2 — `language_file_classification` (3), `architecture_recovery` (2) |
| steps with no `analysis_id` at all | 4 — `repo_file_inventory`, `repo_git_statistics`, `repo_homepage`, `repo_file_size` |
| analyses carrying a results/render payload | 27 of 27 |
| distinct `annotation_types` across 29 analyses | **7** |
| analyses sharing `ClassificationAnnotation` | **14** |

Two readings follow directly.

The **bundling** role of `analysis_id` is duplicated. A bundle of ordered steps is what a Survey
Definition is, and the only two real bundles are exactly that shape. The four steps with no
`analysis_id` are the prerequisite refresh steps — expressible in a survey type, not as an
analysis — which shows the survey type is the *more* expressive of the two, not the coarser.

The **results** role is not duplicated, and `annotation_types` is not it either: 7 values across
29 analyses, one of which covers 14. Those are Python base classes describing the *shape* of a
result. The per-analysis result identity in RE today is the finding **kind** —
`chaoss_metrics`, `supply_chain`, `foss_scorecard` — a 1:1-with-step untyped string passed to
`upsert_finding`, with no registry behind it, which every results reader already keys on.

### 2.2 Three concepts, named

| concept | Egeria | RE today | status |
|---|---|---|---|
| workflow definition | `GovernanceActionProcess` | Survey Definition | ✓ |
| named microflow | `GovernanceActionProcessStep`, `analysisStep` | step key | ✓ |
| **result definition** | `AnnotationProperties.annotation_type` (free string) | finding `kind` | **not published** |
| result *shape* | annotation entity subtype | `ANNOTATION_TYPES_REGISTRY` (7) | ✓ |

`analysis_id` sits across rows 1 and 3 and belongs in neither. Its bundling half should collapse
into the survey type — including single-step survey types, which are a legitimate degenerate
case and not a workaround. Its results half should become a declared annotation type.

---

## 3. The annotation-type defect

`annotation_props.py` builds every annotation body as:

```python
"class":          "ClassificationAnnotationProperties",   # the shape
"annotationType": "ClassificationAnnotation",             # the shape again
"analysisStep":   ann.analysis_step,
```

`annotationType` is filled with the entity subtype name, duplicating `class`. Egeria's
`AnnotationProperties.annotation_type` is a free string intended to name *which* result this is.
The names RE actually has — `chaoss_metrics`, `supply_chain`, `foss_scorecard`, `cve_scan` —
never reach the catalog.

**This has already generated a workaround.** Commit `e915b1c` (2026-08-26) added the
`project_published_analyses` table because publish attribution keyed on annotation types could
not tell which analysis a publish covered — 14 analyses share `ClassificationAnnotation`, so the
question is unanswerable from that field. A local side-table was built rather than the wasted
slot being noticed. If `annotationType` carried the finding kind, that attribution would come
from the catalog for free, and the table would not exist.

**FIXED 2026-08-26.** `annotationType` now carries the result name, resolved from `analysis_step`
through a map derived from `STEP_REGISTRY` and `ANALYSIS_KINDS` — not a hand-maintained second
list, and not computed from string shape, since `CiQualityCheck` → `ci_quality` and
`ApiStructureAnalysis` → `api_structure` follow no snake_case rule and a rule that looked right
for most would quietly mislabel the rest. All 34 steps resolve; a test asserts that exhaustively,
because an unmapped step falls back silently and would reintroduce the defect one step at a time.

Steps with no analysis are named by their own step key — the four prerequisite refresh steps
belong to a survey type rather than an analysis, so that is the most specific true name they
have. Annotations may override with `annotation_type_name` where one step emits several
distinguishable results. Database and filesystem surveyors are not in the repo registry and keep
the old subtype fallback: wrong-as-before beats newly-fabricated.

Verified live — a published annotation now reads back from Egeria as
`annotation_type: "cii_badge"`, `analysis_step: "CiiBadge"`.

**It does not retire `project_published_analyses`, and an earlier draft of this section claimed
it would.** The two carry different facts: annotations record what a run *produced*, while
`steps_run` records what a run *executed*. A step that runs and finds nothing worth annotating
appears in the second and not the first, and collapsing them would make "we looked and found
nothing" indistinguishable from "we never looked" — the failure this codebase exists to remove.
Attribution can now be answered from the catalog for annotations that exist; the table still
answers a question annotations cannot.

### 3.1 The one open modelling question

For steps with `executes_at: resource-explorer` there is no Egeria SurveyActionService provider
to *declare* which annotation types a step produces — RE's steps are not Egeria services, so
nothing declares them. Two options need no extension:

* `additionalProperties` on the process step (already carries `executes_at`, `re_analysis_step`)
* a `ValidValueSet` for the vocabulary

**The test for whether an extension is justified: do you need to query or navigate it?**
"Which steps produce `chaoss_metrics`?", or linking an annotation type to a Perspective the way
`Survey → Question → Perspective` already works — a string map answers neither. If that is the
intent, a real element and relationship is warranted. If it is only ever read once you already
hold the step, `additionalProperties` is sufficient and free.

Recommendation: do not extend until one concrete query motivates it. The symmetry argument
("what would this survey tell me?" mirrors the Question graph) is suggestive but is not yet a
use.

---

## 4. Target model: RE as an engine host

### 4.1 The mechanism exists

Confirmed in pyegeria on 2026-08-26. Nothing below requires a Java engine host or a change to
Egeria.

**Registration** — `AssetMaker.link_supported_governance_service()` writes
`SupportedGovernanceServiceProperties` with `requestType`, `serviceRequestType` and
`requestParameters`. MULTI_LINK: one engine, many request types. This is where `re_analysis_step`
becomes a real request type instead of an `additionalProperties` string.

**Finding work** — `AutomatedCuration.get_active_engine_actions()`, `find_engine_actions()`,
`get_engine_actions_by_name()`, and `GET /governance-engines/{guid}/engine-actions/active-claimed`
for one engine.

**Lifecycle** — `claim_engine_action()`, `update_engine_action_status()`,
`cancel_engine_action()`, over
`REQUESTED → APPROVED → WAITING → ACTIVATING → IN_PROGRESS → COMPLETED | FAILED | CANCELLED | ABANDONED`
(`pyegeria/core/_globals.py:ACTIVITY_STATUS`).

**Sequencing** — already modelled and already unused by RE:

| capability | Egeria | RE today |
|---|---|---|
| guards | `NextGovernanceActionProcessStepProperties.guard`, `mandatory_guard` | writes literal `Any` |
| branching / parallelism | multiple guarded next-steps (MULTI_LINK) | reconciler deletes them (§1.2) |
| parameter passing | `GovernanceActionExecutorProperties.request_parameters`, `request_parameter_map`, `action_target_map` | writes none |
| first-step parameters | `GovernanceActionProcessFlowProperties.request_parameters` | writes none |

Four capabilities, all present, none used.

### 4.2 Kafka is an optimization, not the mechanism

pyegeria has **no Kafka client** — no consumer, no producer. `egeria_kafka_endpoint` is a config
value (default `localhost:9192`) and `create_kafka_server_element_from_template()` catalogs a
Kafka server as an asset; neither consumes Egeria's own out topic. Consuming it means a direct
Kafka dependency in RE, outside pyegeria.

That matters less than it appears. Polling `active-claimed` has identical semantics to consuming
the topic. **Correctness rests on `claim`, not on delivery** — every host may see an action, one
claim wins. So the polling implementation can be built first and the topic added later purely to
cut latency and poll load, with no redesign.

### 4.3 Arbitration — CONFIRMED 2026-08-26

The model rests on the server refusing a second claim. Tested against the live platform by
POSTing `/engine-actions/{guid}/claim` for an action already `IN_PROGRESS` under the running
`EgeriaWatchdog` engine host:

```
OMAG-GENERIC-HANDLERS-403-003  Engine Host OMAG Server with a userId of erinoverview is not
allowed claim the engine action ... because it is already claimed
systemAction: The system cannot claim an engine action because another Engine Host OMAG
Server has got there first.
```

**Server-enforced, first-claim-wins.** The refused claim left the watchdog's action untouched
(still `IN_PROGRESS`, same owner), which is also the property that makes polling safe: every host
may see an action, and only one can take it. So Kafka remains an optimization (§4.2) — correctness
never depended on delivery.

**Correction 2026-08-27 — there is no per-engine claimable listing to poll.** §4.1 listed
`GET /governance-engines/{guid}/active-engine-actions` as the discovery primitive, following
`survey-execution.md (§7)`. Verified in Egeria's Java source: that route's handler is named
`getActiveClaimedEngineActions` and its Javadoc says "claimed by this caller's userId ... used
when the caller restarts" — the same restart-recovery operation as `.../active-claimed`, under a
URL that reads otherwise. It also 404s on the View Service. Enumerating every per-engine route in
Egeria yields four, none of which lists claimable work.

Discovery therefore means whole-server `get_active_engine_actions()` filtered client-side by
engine GUID and unclaimed status — a wider fetch, and safe for the same reason polling was:
`claim` refuses the second claimant. A per-engine endpoint would be a **server-side** addition.

**The client gap is closed (2026-08-27).** `claim_engine_action`,
`update_engine_action_status` and `get_active_claimed_engine_actions` were missing from the
installed pyegeria 6.0.18.4 — §4.1 had read the `egeria-python` working tree and reported them as
available when they were only on `main`. They ship in **6.1.5**, and RE is upgraded to it: 2613
unit tests, 20 live smoke tests and a live alignment scan all pass.

`claim_engine_action` now works through the client, not just as a raw POST, and refuses with a
typed `PyegeriaUnauthorizedException` carrying `OMAG-GENERIC-HANDLERS-403-003`. That matters for
the design: an engine-host loop needs to catch "another host got there first" as an ordinary,
expected outcome and keep polling, distinct from a real failure. A string-matched error would
have made that fragile.

**Engine-host participation is no longer gated on anything external.** Server capability
confirmed, client methods available, arbitration typed. What remains is RE-side work: register
its steps as request types via `link_supported_governance_service`, and run the
find → claim → execute → report loop.

### 4.4 What it changes

`executes_at` collapses. Today `{resource-explorer, prefect, egeria}` is RE's own configuration
describing who runs a step. Under this model it is catalog data — which governance engine holds
a `SupportedGovernanceService` for that request type — and RE stops being a special case
alongside Egeria's native surveys.

It also answers §2 from a third direction: **the step is the unit of coordination and the survey
type is the process that sequences it.** Guards, branching and parameter passing stop being
things RE must express, because Egeria's process engine walks the graph and RE executes leaves.

And it raises the stakes on §3: RE's annotations would be consumed by things that are not RE, so
`annotationType` carrying the real finding kind stops being tidiness and becomes the interface.

### 4.5 Prefect is the executor whenever RE coordinates

**RE should not be a workflow engine in either direction.** Two coordinators, neither of them RE:

* Egeria coordinates — RE is an engine host, claims engine actions, executes leaves (§4.1).
* RE coordinates — RE hands the workflow to **Prefect** and lets Prefect sequence it.

Today it does neither. `survey_definition_executor.execute()` walks `survey_def.steps` in its own
`while i < n` loop, deciding step order itself and dispatching individual steps to Prefect via
`prefect_adapter.run_prefect_step` (batching consecutive local ones). So **RE is the sequencer and
Prefect is a task runner** — the inversion of what it should be. `prefect/flows.py` already
defines `@flow re_survey_flow` and `@task run_surveyor_step_task`, so the machinery exists at the
wrong granularity rather than not existing.

The thing forcing this is an API detail worth naming: `SurveyDefinition.steps` is a **flat list**,
produced by `_parse_graph` walking a single chain — which is also why branching raises. The graph
is already there beside it, in `SurveyDefinition.links`, carrying `guard` and `mandatory_guard`
per edge. RE flattens a graph it has already parsed, then sequences the flattening by hand.

So making branching runnable is not "build a guard evaluator in RE". It is: stop flattening, and
translate the step graph into a Prefect flow whose edges are the guards. That work is independent
of the engine-host decision — it is what RE needs whenever RE is the one coordinating, which is
every offline and local run, and the case §4.6 says must survive indefinitely.

**BUILT 2026-08-26, behind `prefect.enabled`.**

* `surveyors/survey_execution_plan.py` — pure: a definition in, a DAG out. Dependencies come
  from `.links`, not list order. Guards are *carried, not evaluated*; deciding one here would
  rebuild the engine in the file whose purpose is to stop RE from being one. A cycle raises
  rather than planning part of a survey.
* `prefect/flows.py::re_survey_definition_flow` — takes the whole plan and submits each step as
  a task with its upstreams as dependencies, so **Prefect resolves the order**, not RE. A step
  whose guard was not emitted reports `skipped`, never `ok`: a branch not taken and a branch that
  ran are different facts. A failed step skips its dependents with a stated reason and leaves
  independent branches running.
* `survey_definition_executor.execute()` delegates the whole definition when Prefect is enabled,
  and everything after sequencing — publishing, the activity entry, the assembled result — is
  shared, so the two paths differ only in who ordered the steps.

The local loop stays as the fallback and that is not temporary: a Prefect-less or offline run
must keep working, the same argument that made Automate local-first. An unreachable Prefect
degrades to it with a logged reason rather than failing a survey the loop could have run.

Verified: every live definition plans to exactly its current order (so nothing that runs today
changes), a real `RepoCoarseScout` run sequenced all three steps through Prefect end to end, and
the same definition with Prefect off still runs locally. Branching itself is covered by
in-process flow tests — Prefect 3 runs flows without a standing server, so the ordering asserted
is Prefect's own.

**The reader followed, 2026-08-26.** `_parse_graph` walks the whole reachable graph in
topological order instead of following one edge and raising on a second. Refusing was honest
while nothing could run a branch, but it left a branching definition unreadable as well as
unrunnable — not displayable, not diffable against its document, not repairable.

Cycles still raise, and the distinction is the point: a branch is a shape that can be ordered, a
cycle is one that cannot be, and any order for it would be a fiction.

Steps the process declares but no path reaches are now reported on
`SurveyDefinition.unreachable_step_guids` and logged, instead of being dropped because the walk
never arrived at them — such a step was in the definition, absent from every run, and
indistinguishable from one never authored.

Verified: all eight live definitions read back to exactly their authored order, and a branching
definition now parses, plans, and runs end to end with the untaken side reported `skipped`.

### 4.6 What it costs

**Inversion of control.** `SurveyOrchestrator.run(slug, steps=…)` owns the loop today. For
Egeria-coordinated runs it would not. RE must keep the local path indefinitely — the argument
that made Automate local-first (`docs/automate-notification-manager-pyegeria-spec.md`) applies
unchanged whenever Egeria is unreachable. This is two execution paths for the same steps, for a
long time, and that is the real price.

**Recovery still runs on the local copy.** If Egeria becomes the runtime authority, a wipe means
rebuilding from RE's own copy — which must therefore be the *authored Dr.Egeria documents*, not
a projection of Egeria's graph. A projection preserves only what the reader models, and the
reader models neither request parameters nor branching, so round-tripping through it would
flatten exactly the detail §1.1 is about. This is why the documents are the durable artifact and
why the generator must not own them.

---

## 5. Sequencing

Ordered by dependency, not by value.

1. ~~**§1.1 generator seed-only.**~~ **Done 2026-08-26**, with the §1.2 interlock alongside it.
   Hand-authoring a guard is now safe from the generator, and refused by the repair rather than
   silently flattened.
2. **§3 publish the real `annotationType`.** One string, already held. Independently useful,
   and it retires the `project_published_analyses` workaround.
3. ~~**§1.3 split the drift scan**~~ **Done 2026-08-26**, with order and description
   comparison added — the latter caught real stale content on its first run.
4. ~~**§1.2 reconciler keyed on `(prev, next, guard)`**~~ **Done 2026-08-26**, with the expected
   set built from the authored document. Authoring a guard is now safe end to end; running one
   is still refused, deliberately.
5. ~~**Confirm §4.3** against a live platform.~~ **Done 2026-08-26** — arbitration is
   server-enforced. Engine-host work is now gated on a pyegeria release carrying
   `claim_engine_action`, not on an unknown.
6. ~~**Hand sequencing to Prefect (§4.5).**~~ **Done 2026-08-26**, behind `prefect.enabled`,
   reader included. A branching Survey Definition can now be authored, parsed, planned, run and
   repaired; RE sequences none of it.
7. **Collapse `analysis_id`'s bundling role into survey types.** Migration with real blast
   radius: `resource_schedules` has live rows keyed by `analysis_id` and 27 render payloads to
   repoint. Own design pass.
8. **Engine-host participation**, polling first.

Steps 1–4 are corrections to shipped code and were worth doing whether or not §4 is ever built.
All four are done. Step 5 gates 8; step 6 does not depend on the engine-host
decision at all.

---

## 6. Open questions

* **Where is the source of truth?** This note assumes CSV → `.md` (definition) → Egeria
  (runtime authority) → local docs (recovery). An alternative is Egeria-as-truth with the local
  copy purely a cache — but §4.5 shows the cache cannot be a projection, so the two converge on
  the documents being durable regardless. Worth stating explicitly rather than leaving implied.
* **Who authors?** `repo_survey_types.csv` and its generator live in RE, which sits awkwardly
  with "EA is the authoring environment for Dr.Egeria documents, RE is not." Making survey types
  the sole granularity puts ~30 definitions on that path and forces the question. Unresolved;
  `survey-execution.md (§10)` D4 deferred it and it is still deferred.
* **Do single-step survey types need Egeria to exist to be schedulable?** Under §4 yes, which is
  a new hard dependency for something RE does locally today.
* **Is `analysisStep` the step key or the display name?** RE writes `ann.analysis_step`, which is
  the surveyor's `STEP` constant (`ChaossMetrics`), not the registry key (`repo_chaoss_metrics`).
  Harmless today; ambiguous the moment anything joins on it.

---

# Part II — Execution permutations, orchestration, and registration

*Merged 2026-09-02 from five notes.*

## 7. The four execution permutations

*(from `re-as-engine-host-plan.md` — status: ON HOLD, design complete, case 4 built)*

Who *initiates*, who *orchestrates* (owns process state), and where *steps
execute* are three independent choices. They yield four permutations, and the
value of the table is that it collapses them to two real ones.

| # | Initiates | Orchestrates | Steps execute | Net new work |
|---|---|---|---|---|
| **1** | RE | Egeria (native) | Egeria (native, all) | **None** — `initiate_gov_action_process()` already exists; RE needs a route and a read-back |
| **2** | RE | Egeria (native) | Mixed — some native, some RE | RE becomes a claiming engine: poll/claim/complete plus engine and service registration |
| **3** | RE | RE | RE | **Today's architecture, unchanged** |
| **4** | RE | RE | Mixed — some RE, some Egeria | Thin step-runner over the existing `initiate_engine_action`, plus a completion poll |

**Case 1 is real but has no current target.** It needs *every* step to have a
working Java connector already. None of RE's own analyses do, and porting them to
Java would cost strictly more than case 2 for the same steps while delivering the
identical native orchestration. Case 1 applies only where Egeria already ships
full native coverage — Postgres and filesystem surveys — and there RE has no
execution work at all, just trigger and read back.

**Case 2 is the real capability to build.** It is not "case 1 minus some steps".
Every RE-specific survey needs case 2's investment, or stays case 3, because RE's
bespoke logic — GitHub API calls, tree-sitter extraction, license classification —
has no Java equivalent and building one would be duplicated effort with no benefit
over letting RE claim the step itself.

**Case 4 is nearly free and is built.** **Case 3 stays the permanent default**,
not a stepping stone to be phased out.

## 8. The unified execution model

*(from `unified-survey-execution-model-plan.md` — planned, not built except D7a's first slice)*

Its load-bearing decisions, in the numbering other documents cite:

- **D1** — one survey concept: a named, ordered bundle of analytic steps.
- **D2** — two separable kinds of "publish", with a real precondition between them.
- **D3** — for pure-RE surveys, publish-results defaults to **on**, reversing the
  earlier default.
- **D4** — sniff-test-ability is *derived* from a survey's execution tier, not
  declared separately.
- **D5** — file profiling is an under-recognised survey, not a peer category.
- **D6** — dependency and sequencing between steps. The refinement that matters:
  **model shared *resources*, not step-to-step data dependencies** (D6.1). A
  `ResourceProvider` registry plus `requires_resources` on `StepInfo` (D6.2–6.3)
  lets the orchestrator resolve each resource **once, before the run** (D6.4),
  instead of threading outputs between steps. That is what makes a microflow
  self-contained and independently runnable.
- **D8 (A2A) deferred** 2026-08-15 — not rejected.

## 9. Choosing an orchestrator

*(from `distributed-survey-orchestration.md`, `distributed-survey-best-practices.md`)*

Prefect was selected after comparing flow tools, and the integration is
deliberately narrow: it orchestrates **locally-run survey steps**, giving them
real flow-run state, per-task logs, and cancellation — none of which the plain
thread-based path has. It gives no additional visibility into `executes_at: egeria`
steps, which Egeria coordinates itself.

**The fallback is not a degraded mode, it is a requirement.** With no Prefect
server reachable, `run_prefect_step`'s own logic (`surveyors/prefect_adapter.py`)
correctly falls back to running the step locally in-process, costing only a
connection attempt per step. But that fallback is not the whole story: found
2026-09-04, `PREFECT_ENABLED=true` with no server reachable let Prefect's *own*
client start an ephemeral in-process subprocess server (nothing in RE ever shut
it down) — 13 orphaned `prefect.server.api.server:create_app` processes,
days old, were found this way. `PrefectConfig.enabled` now defaults to `False`
(see its docstring in `config.py`), and `surveyors/prefect_adapter.py` /
`resource_explorer/__init__.py` additionally force
`PREFECT_SERVER_EPHEMERAL_ENABLED=false` in-process as a second, independent
guard. Enable Prefect only where a compose service actually provides it
(egeria-workspaces' `optional-associated-runtimes/prefect`), with both
`PREFECT_ENABLED=true` and `PREFECT_API_URL` set explicitly.

The comparative research behind the choice — how DataHub, OpenMetadata and Egeria
differ in orchestration model, plus enterprise ingestion and human-in-the-loop
case studies — lives in the archive rather than here; it informed the decision and
is not itself design. The conclusions that survived: orchestration state belongs
to *one* owner per run, and human curation is a stage in the pipeline rather than
an interrupt to it.

## 10. Registering an analysis step with Egeria

*(from `analysis-step-egeria-registration-plan.md` — investigation complete, not built)*

`re_analysis_step` currently travels as an `additionalProperties` string. The
target is a real **request type**, registered via
`AssetMaker.link_supported_governance_service()`, which writes
`SupportedGovernanceServiceProperties` with `requestType`, `serviceRequestType`
and `requestParameters`. That relationship is MULTI_LINK — one engine, many
request types — which is exactly the shape needed.

The mechanism was confirmed present in pyegeria on 2026-08-26. **Nothing here
requires a Java engine host or a change to Egeria.**

---

# Part III — Settled — do not reopen without re-measuring

| Question | Settled | On what basis |
|---|---|---|
| Should RE's steps be ported to Java connectors? | **No** | Case 1 costs strictly more than case 2 for the same steps, with identical benefit |
| Is local orchestration (case 3) a stepping stone? | **No** — permanent default | It must keep working indefinitely; Prefect is an addition, not a replacement |
| Model step-to-step data dependencies? | **No** — model shared resources | D6.1; resources resolve once before the run, which is what keeps a microflow self-contained |
| Use Dr.Egeria as the runtime invocation path? | **No** — A2A | Markdown through MCP is the wrong shape for a machine-to-machine governance call; Dr.Egeria keeps *authoring* |
| Does Prefect give visibility into Egeria-side steps? | **No** | It orchestrates locally-run steps only |
| Is `PREFECT_ENABLED=true` with no server unsafe? | **Partly — revised 2026-09-04** | `run_prefect_step`'s own fallback is safe, but with no ephemeral-start guard Prefect's client would leak a subprocess server; now guarded and off by default. *Changed 2026-09-19: on by default, with the guard kept; see deploying-trellis-with-quickstart.md §5.1.* |
| Does engine-host registration need Egeria changes? | **No** | Confirmed in pyegeria 2026-08-26 — the mechanism already exists |
| Should `analysis_id` carry both bundling and results meaning? | **No** | It sits across two rows of the granularity table and belongs in neither |

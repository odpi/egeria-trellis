# Curation lenses — Purpose-driven curation of recovered architecture

**Status: design, unmeasured (2026-09-08).** Nothing here is built. §8 defines the spike that
must run before the interface is designed in detail; §8's numbers decide whether §4's model is
right or merely plausible. Companion to `architecture-recovery.md` (which this document does not
restate) and `curate-evidence-based-decisions-plan.md`.

> **Superseded 2026-10-08 (one point):** "promotion out of the draft zone" (§ table, line 50) describes the 2026-09 default; the draft zone was retired as a default, see `resource_explorer/egeria_identity.py`.

**Decision (project owner, 2026-09-08):** curation should start from what the user is trying to
achieve. The Purpose sets the coarseness and scope of the components presented; components are
made of sub-components, so a Purpose chooses a level rather than a different set of things.
Three worked examples were given and are the first three lenses in §5: integrating the repository
with external infrastructure, using what the repository offers, and maintaining it.

## 1. The problem

The Architecture Verdicts tab presents one verdict per candidate component (`accepted`,
`rejected`, `retyped`) and one per proposed blueprint (`accepted`, `rejected`), keyed by scope,
materialised to Egeria on accept. That shape is fine for a few dozen decisions and fails at the
scale recovery actually produces: kafka yields 649 components, egeria-python 87 candidates from
one extractor alone, and the recovery design records the "999 unreadable candidates" outcome that
led to hierarchical clustering. Three properties of the problem defeat a flat queue:

1. **Overlap.** Several extractors (file tree, manifests, imports, compose, documentation) each
   propose a partition of the same code. Candidates from different extractors cover the same
   scope and are not duplicates of each other; they are different claims about it.
2. **Perspective.** `architecture-recovery.md` §4.1 establishes four architecture perspectives
   (physical, deployment specification, logical, dev/devops), enforced below the model. A
   deployment blueprint and a logical blueprint over the same repository are both correct and
   share components. A single list makes every verdict ambiguous about which question it answers.
3. **Unit of decision.** Curators decide about groups, boundaries and exceptions, then about
   stragglers. A per-component verdict makes the straggler case the only case.

Confidence, cohesion and provenance exist in the data (`Cluster.signal`, `coupling.py`'s
import cohesion, `cochange.py`'s change coupling, each finding's `surveyed_at` and step) but reach
the curator only as an undifferentiated list.

## 2. What already exists and is kept

Everything below is an input to this design, not something it replaces:

| Asset | Where | Role here |
|---|---|---|
| Four architecture perspectives, enforced below the model | `architecture-recovery.md` §4.1, §4.1b | the axes a lens selects among |
| Nested clusters: `Cluster(children=...)`, roll-up levels, `oversized` reported not truncated | `surveyors/arch_recovery/clustering.py` | the hierarchy a lens cuts |
| Cluster provenance: `signal` (which declared boundary produced it), `carrier` (collection vs composition) | same | shown per decision |
| Structural cohesion (import cohesion, sharply bimodal) | `arch_recovery/coupling.py` | one of three cohesion families |
| Evolutionary cohesion (change coupling from history) | `arch_recovery/cochange.py` | second family |
| Interface surface and API structure | `arch_recovery/interfaces.py`, `api_structure` analysis | the Learn lens's primary signal |
| Blueprints are Collections and nest; a cluster is a Collection, not a parent component | `architecture-recovery.md` §3.3a, §14 | the Egeria shape a cut materialises to |
| Report first, curate second; verdict tables; materialise on accept; promotion out of the draft zone | §17, `web/routes/curate.py` | the write path, unchanged |
| Investigations carry Purposes (`investigations.purposes_json`); Purpose ranks and never excludes | `investigation-framing-design.md` | the lens is selected from the Investigation's Purpose |
| The question catalog's Purpose vocabulary: Select, Explore, Assess, Deploy, Certify, Maintain, Learn, Share, Attest | `configdata/question_catalog.yaml` | lens names reuse it; no new vocabulary |
| Reflexion models (Murphy, Notkin, Sullivan): extracted model and hypothesised model kept separate, joined by a computed correspondence | cited in §4.1 | the per-cluster summary in §6 |

## 3. Related work this leans on

Kept short; the recovery design already carries the recovery literature.

- **No single right clustering.** Garcia, Ivkovic and Medvidovic's comparative analysis of recovery
  techniques (ASE 2013) and the USC ground-truth work show low agreement between techniques and
  with experts, and slow, disagreeing experts. Recovery output is a proposal to be edited.
- **MoJo** (Tzerpos and Holt): distance between two clusterings in move-and-join operations, which
  is the cost a curator pays. The spike uses it to compare cuts to accepted structure.
- **Cohesion families.** Structural (modularity, fan-in and fan-out, Bunch's MQ), evolutionary
  (Zimmermann et al.'s change coupling; Tornhill's temporal coupling and hotspots), semantic (Kuhn,
  Ducasse and Gîrba's semantic clustering over identifiers). They disagree informatively.
- **Constrained and interactive clustering.** Wagstaff's must-link and cannot-link constraints;
  every curator action becomes a constraint and the proposal recomputes under it.
- **Triage at scale.** Dedupe.io's active-learning entity resolution: ask only where the model is
  uncertain, sort by information gain, accept the confident by default with an audit trail.
  Prodigy and Label Studio: one decision per screen, keyboard-driven, bulk accept.
- **Views.** ISO 42010 (concerns → viewpoints → views), Kruchten's 4+1, SEI Views and Beyond, and
  the C4 model's levels (context, container, component, code). A Purpose is a concern; a lens is
  a viewpoint; a blueprint is a view. The C4 levels are the same tree at different cuts.
- **Interfaces that scale.** The Dependency Structure Matrix (Lattix, Structure101) stays legible
  at thousands of elements and exposes layers and cycles by reordering; CodeScene for
  evolutionary views; Backstage and Sourcegraph for "detected, please confirm" bulk onboarding.

## 4. The model

### 4.1 One hierarchy per perspective; a lens is a cut

Recovery already produces, per repository and per architecture perspective, a hierarchy of
candidate components and nested clusters. This design adds nothing to that hierarchy. It adds a
**lens**: a declaration that selects perspectives, chooses the **depth at which to cut** each
hierarchy, weights the cohesion signals, names the analyses that supply evidence, and states stop
rules. The same shape as the containment tree in `docs/context-compilation-architecture.md`: one
parse, and a consumer chooses the depth.

```
Investigation.purposes  ->  lens  ->  { perspectives, cut depth, signal weights, evidence analyses, stop rules }
                                          |
        candidate hierarchy (per perspective) --cut--> the decision set the curator sees
```

**A lens decides what is shown and at what grain. It never decides what exists.** If a lens
removed candidates from the model rather than from the view, the next lens would start from a
smaller world. This is the same invariant Perspective carries in dispatch ("ranks, never
excludes") and it is enforced the same way: lenses read `Cluster` trees; they do not write them.

### 4.2 The cut

A cut is a set of nodes such that every leaf is under exactly one node in the set. The lens
chooses it by depth with two adjustments:

- **Stop early on a cohesive node.** A node whose members cohere above the lens's bar is shown
  closed even if the depth would open it; opening it is a curator action, not the default.
- **Open a node that is not cohesive.** A node below the bar at the cut depth is shown open one
  level, because accepting it as a unit would be accepting a boundary the evidence does not
  support.

Which cohesion signal decides "cohesive" is the lens's business (§5). `Cluster.oversized` nodes
are always shown open, as the clustering design already requires.

### 4.3 Verdicts: what is Purpose-independent and what is not

| Decision | Depends on lens? | Why |
|---|---|---|
| **Component verdict** (real, worth cataloguing; accepted / rejected / retyped) | **No** | "This is a real thing" is true or false regardless of why you asked. One verdict per component, reused by every lens. |
| **Cluster verdict** (accept this grouping as a blueprint or as a composition) | **Yes** | A Deploy blueprint holds ten runnable units; a Maintain blueprint holds sixty functional components nested under them. Both reference the same accepted components at different depths, which nested Collections express directly. |
| **Membership** (this component belongs in that blueprint) | **Yes** | Membership is a claim within a view. |
| **Boundary edits** (move, merge, split, rename) | **Yes**, but the constraint is global | The edit is made under a lens; the must-link / cannot-link constraint it produces applies to every subsequent recompute, in every lens, because it is a statement about the code, not about the view. |

**Verdict inheritance.** A cluster verdict at the cut applies to every component beneath it
unless a component already carries its own verdict, which wins. Opening a node and deciding
inside it narrows the inherited verdict; it never widens one. The verdict history the routes
already keep (`component_verdict_history`) records the lens and the cut node each verdict came
through, so an inherited verdict is distinguishable from a direct one.

### 4.4 Overlap as an object

When two extractors propose components over the same scope, the overlap is presented once, with
its resolutions: **same component** (one accepted, the other becomes evidence for it), **nested**
(one composes the other), or **distinct per perspective** (both stand, in different hierarchies,
bridged by `ImplementedBy`). This is one decision where the flat tab produced three verdicts that
could silently contradict each other. The overlap object records which resolution was chosen and
by whom, and the chosen resolution is itself a constraint for recompute.

## 5. The first three lenses

Authored, not coded: lenses live in a spreadsheet-generated YAML beside `question_catalog.yaml`,
guarded by a validator, per the house pattern (`context-compilation-design.md` §19). The three
below are the project owner's worked examples with the existing vocabulary filled in.

| Lens (Purpose) | Perspectives | Cut | Cohesion that decides | Evidence analyses | Stop rules |
|---|---|---|---|---|---|
| **Deploy** — integrate the repository with our infrastructure | deployment specification first; physical for the assets it points at | coarse: runnable units and external touchpoints; C4 container level | manifests and compose structure; outbound dependencies; import cohesion only to confirm a runnable unit is one thing | `dependency_analysis`, `cve_scan`, `secret_scan`, `security_features`, `security_scan`, deployment detectors, `sub_resource_survey` | do not open a runnable unit unless it has more than one external touchpoint |
| **Learn / Select** — use what the repository offers | logical, with the interface surface as the primary boundary | medium: public surface and integration techniques; inner workings closed; C4 container plus interfaces | interface surface and API structure; documentation coverage; exported symbols; semantic cohesion of what is exported | `interface_surface`, `api_structure`, `architecture_doc_lens`, `documentation_coverage`, `license_classification`, `repo_conventions` | open a node only if it exposes an interface |
| **Maintain** — keep it working | logical, physical, dev/devops | fine: functional components and their tests; C4 component and code | structural (import cohesion) and evolutionary (change coupling) together; hotspots; test-to-code mapping | `architecture_recovery`, `code_symbol_extraction`, `ci_quality`, `repo_conventions`, `contribution_provenance`, `documentation_coverage` | open every node whose change coupling crosses its boundary; never close a hotspot |

Two things the table makes explicit. **The cohesion families are weighted per lens**, and
disagreement between them is surfaced only where the lens says it matters: change coupling that
crosses a proposed boundary tops the Maintain queue and is irrelevant to Deploy. And **ports and
wires arrive at the Deploy and Learn cuts on their own**, because the edges that leave a coarse
cluster are exactly the integration points those Purposes care about; the wire work that was
deferred (`curate.py`'s note of 2026-09-03) has a natural home here rather than a separate tab.

Purposes not listed (Assess, Certify, Explore, Share, Attest) either compose these (Certify is
Deploy plus a standard as target; Assess is Learn plus Deploy at the coarse cut) or have no
architecture-specific cut and fall back to the coarsest one. That is a claim to measure, not a
rule.

## 6. What the curator sees

Not a UI specification; the shape the spike must justify.

1. **A queue of nodes at the cut, ordered by need for a human.** Bottom: cohesive nodes whose
   signals agree, marked "accept as a unit" with one action and an audit trail. Top: contested
   nodes: signals disagree, overlaps with another node, `oversized`, or low confidence. Sorting is
   by uncertainty times impact (fan-in, size, being a dependency of accepted components).
   Confidence routes; it never hides.
2. **A reflexion summary per node:** members, the edges that stay inside, the edges that leave and
   which node they go to, which extractor and signal proposed it, and the cohesion values under
   the active lens. For Deploy and Learn the leaving edges are the candidate ports.
3. **Actions:** accept, reject, open, move, merge, split, rename, resolve overlap. Each becomes a
   constraint; the proposal recomputes; nothing already decided is re-asked.
4. **A Dependency Structure Matrix view** for the cut, for the cases a list cannot show: cycles,
   layers, and the wire structure between accepted units. This is the seed of the ports-and-wires
   surface.
5. **The straggler queue**, which is the current per-component form, demoted to what remains
   after the cut is settled.
6. **Chat as explainer and bulk operator, over the same objects.** "Why is `connect/` grouped with
   `runtime/`?" is answered from the cohesion evidence the clustering used; "move everything under
   `tools/` into its own blueprint" is a constraint applied through the same mechanism. The
   manifest-as-return-path idea from the context design applies unchanged: chat and table act on
   one model.

## 7. What this does not change

- The recovery pipeline, its detectors, its persistence and withdrawal rules (§16 of the recovery
  design) are untouched. Lenses read.
- The verdict tables and the materialise-on-accept path are reused; the additions are the lens
  and cut node on each verdict row, the overlap object, and the constraint store.
- Egeria's shape: `SolutionComponent`, nested `SolutionBlueprint` Collections, `ImplementedBy`,
  ports and wires when built. A lens is an RE-side concept; what reaches Egeria is a blueprint.
- The user Perspective (Security, Admin, …) is not involved. The recovery design's warning about
  the two meanings of "perspective" stands; this document uses the word only in the architecture
  sense.

## 8. The spike: measure before designing the interface

**Question.** Does cutting the existing cluster hierarchies at the three lenses' depths reduce the
curator's decision count to something a person can work through, and do the cohesion signals
agree with the decisions curators have already made by hand?

**Inputs, all already stored:** the `architecture_recovery` results and cluster proposals for
`kafka`, `egeria_python_git` and `docling`; import cohesion from `coupling.py`; change coupling
from `cochange.py`; identifiers and docstrings from `code_symbol_extraction` for a semantic
family; the verdicts already recorded in `architecture_component_verdicts` for those repositories.

**Method.**

1. For each repository and each perspective present (logical, deployment), load the cluster tree.
2. Compute per node: size, depth, import cohesion, change-coupling cohesion (fraction of co-change
   pairs that stay inside the node), semantic cohesion (a simple TF-IDF cosine over member
   identifiers and docstrings, or LSI if cheap), fan-in and fan-out across the boundary,
   `oversized`, `signal`, `carrier`.
3. Apply each of the three lenses' cut rules from §5 with the §4.2 adjustments. Report, per
   repository and lens: the number of nodes at the cut (the decision count), their size
   distribution, how many are "accept as a unit" by the lens's cohesion bar, how many are
   contested, and how many components are stragglers below any node.
4. Agreement between families: for every node, do structural, evolutionary and semantic cohesion
   agree above or below the lens's bar? Report the agreement matrix and list the nodes where they
   disagree, with their sizes; those are the expected human questions.
5. Agreement with humans: where verdicts exist, compute MoJo distance between the cut and the
   accepted structure, and check whether accepted nodes are the ones where the families agree.
   State the sample size honestly; if fewer than twenty verdicts exist per repository, report the
   comparison as anecdotal.
6. Cost: wall-clock for steps 1 to 4 per repository, since a lens must be re-cut interactively.

**Deliverable.** `docs/experiments/curation-lenses-spike.md` with the tables above, the disagreeing
nodes named, and one recommendation per lens: cut depth as proposed, deeper, or shallower.
Script under `scripts/curation_lenses_spike.py`, read-only over the registry, no writes to Egeria.

**What would change the design.**

- If the Maintain cut on kafka still yields several hundred decisions, the hierarchy needs another
  roll-up level before any interface is worth building; §4.2's stop rules alone are not enough.
- If the cohesion families agree almost everywhere, one score suffices and §6's "contested" queue
  is small; if they disagree often, the queue is the product and the DSM view earns its place
  early.
- If accepted nodes are not the ones where signals agree, curators are trusting something the
  signals do not capture, and the spike should say what (documentation? naming? deployment
  grouping?) before the routing rule in §6 is built on cohesion.
- If import cohesion stays as bimodal as `clustering.py` measured (94% exactly zero), it is a
  classifier, not a ranking, and the evolutionary and semantic families must carry the ordering.

**Not in the spike:** any UI, any write to Egeria, any change to the clustering algorithm. The
spike reads trees and verdicts and reports numbers.

## 9. Sequencing after the spike

1. Author the three lenses in the catalog spreadsheet; generate and validate.
2. Add lens and cut node to the verdict rows; add the constraint store and the overlap object.
   Additive schema, no migration of existing verdicts.
3. Re-cut on demand: an endpoint that returns the decision set for (resource, lens), read-only.
4. The node queue and the reflexion summary in the Curate tab, with accept-as-a-unit and open.
5. Constraints feeding recompute; move, merge, split.
6. The DSM view, then ports and wires from leaving edges.
7. Chat over the same objects.

Each step is measurable against the spike's baseline: decisions per lens, and the share of them
made in bulk.

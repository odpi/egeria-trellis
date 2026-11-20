# Context compilation — a shared spec model for RE and EA

> **Status (2026-11-19, 6.3 Tech Preview):** the original design; **partly implemented**. `trellis-artifact-tree` and `trellis-context` (`ContextSpec`, the packer, the manifest) exist; what was built is described in [context-compilation-architecture.md](context-compilation-architecture.md), which wins where the two differ. The sharing with Egeria Advisor is not built.

**Status: design. Nothing built, and — unlike
`packages/resource-explorer/docs/investigation-framing-design.md`, which measured its dispatch
chain against the real catalog before asserting it — nothing here is measured.** Everything
below is asserted. §3's weighting claim and §5's portability claim are both testable and both
untested; §9 lists what to measure before building. This doc also **proposes revising two
verdicts** in `docs/re-ea-consolidation-audit.md` (§7) — proposals, not findings.

## Why this doc exists

"Context compiler" is the current industry framing for: a declarative spec of what a task's
context should contain → typed resolvers → budget-aware packing with a compression ladder →
content-addressed cache → provenance out the other side. It is mostly a fad, because it is
usually applied on top of a vector store with no schema, where the "compiler" is a heuristic
packer wearing a build-system metaphor.

It applies here for a reason that is not generic: **RE has already built the front half and
called it question dispatch.**

```
Purpose (why) + Perspective (whose concerns) → Questions → analysis_ids → Surveys & Analyses
```

That is a spec language, an intermediate representation (the question catalog), and a resolver
registry (the analysis catalog). What is missing is the back half — budget solve, compression
ladder, provenance envelope, manifest, cache key — and RE assembles that last mile by hand in
the Chat panel today.

EA, separately, has the cache-key discipline RE lacks, a serialization format and lifecycle for
specs, and a working ranking engine. Neither app has the back half.

**So the recommendation is not "adopt context compilers." It is: finish the compiler RE already
has, using the parts EA already built.** RE is the newer thinking; EA's concepts have not all
been pulled across yet.

## 1. The four focusing axes

| Axis | Scope | Compiler role | Identity-bearing? |
|---|---|---|---|
| **Investigation** | the body of work | the **compilation unit**; membership defines the frontier | Yes |
| **Purpose** | the engagement | selects the spec; section priority; the one axis that may **gate** | Yes |
| **Perspective** | the person | budget weights, compression level, ordering, emphasis | **No** |
| **Intent** | right now, one resource | the section *shape* | Yes |

The identity column is EA's test from `REPORT_SPEC_BUILDER_DESIGN.md` §"Three parameter
categories" — *change it, get different data?* — applied to RE's axes. It is a cache-key
partition, and RE has no equivalent statement anywhere today.

Two consequences that are easy to get wrong:

**The cache is keyed on Investigation, not on resource.** Membership is N:M and a resource
belongs to many investigations simultaneously (confirmed 2026-08-24, framing design §1). The
same repo therefore compiles to *different* contexts depending on which body of work is asking.
A per-resource context cache — the obvious implementation — is wrong at the grain, in exactly
the way `entity_egeria_project_context`'s one-row-per-resource key was wrong for membership.

**Perspective's non-identity is an invariant to protect, not an observation.** The moment
Perspective gates anything, it becomes identity-bearing, and a cache keyed without it starts
silently serving contexts built for someone else.

## 2. What the compiler adds that neither app has

- **Budget solve** — allocate a token budget across sections, degrade rather than truncate.
- **Compression ladder** — each section declares `full → summary → identifiers-only`.
- **Provenance envelope** — every packed span carries `{guid, version, fetched_at, source_kind}`.
- **Manifest** — what was included, compressed, dropped, and why. Emitted alongside the prompt.
- **Cache key** — `(spec version, resolved GUIDs+versions, compression level)`, partitioned by
  the identity column in §1.

The manifest is not a debugging nicety; it is **the return path for user input**. Purpose,
Perspective and Intent are already user-set UI state (declared at Investigation creation, the
multi-select chip row, the exclusive intent nav). What is missing is showing the user what their
selections actually did to the context. With a manifest, the chips become a live control on
context rather than only on display — which is what focusing results *with user input* requires.
The current Chat panel, scoped to the selected resource and nothing else, cannot express this.

## 3. Perspective is a weight, not a filter

Framing design §3 measured Perspective against the real catalog and found it cannot drive
dispatch: the twelve perspectives' analysis sets are strictly nested, not one reaches anything
unique to it, `Privacy` reaches nothing at all, and 17 of 41 questions carry 4+ perspectives.
Perspective varies the *size* of the result, never its *content*.

**Those properties are disqualifying for a filter and close to ideal for a budget allocator:**

- "Varies size, not content" is a defect in a dispatch key and a specification for a weight
  function.
- **Nesting is a safety property.** Because nothing is reachable *only* via one perspective,
  running out of budget can never drop the one section that perspective alone needed. It
  degrades down the ladder instead. That guarantee does not hold for any discriminating axis.

This is the job Perspective should have, and it is consistent with framing design §3's
"Perspective ranks within that — presentation, ordering, emphasis."

**Untested claim:** that these weights actually improve answers. Nesting makes them *safe*; it
does not make them *useful*. See §9.

`Privacy` reaching zero analyses becomes a feature under this model: the manifest reports an
empty evidence section instead of the Chat panel quietly answering from RAG prose with no
signal that nothing analysis-backed was behind it. Coverage gaps surface rather than smoothing
over.

## 4. Purpose gates — and the gate must be typed

Purpose is the discriminating axis (overlap 0.22 vs Perspective's 0.37) and it primarily
*ranks*. But framing design §4 gates RFA emission on Purpose, with `Select` emitting none at
all, and the design doc explicitly warns that implementing that gate as a ranking produces a
flooded RFA drawer and **fails silently** — surfacing as noise nobody traces back to the rule.

In the compiler, sections carry `mode: rank | gate` as a schema field. The distinction becomes
typed and testable instead of a convention that has already been misread once in its own design
document. This is a genuine improvement over the current model, not a re-encoding of it.

## 5. What EA contributes

| EA asset | Role in the compiler |
|---|---|
| "Part of spec identity?" partition | the cache-key discipline (§1) |
| Dr.Egeria markdown catalog entry | spec serialization, already user-editable |
| draft → preview → save lifecycle | spec authoring, already built |
| `collection_router.py` scoring engine | **the packer's weight function** |
| `feedback_collector.py` | **the tuning signal for those weights** |
| `graph_query_depth` | the budget dial |
| `_ModelOverrideClient` / `get_planning_llm()` | cheap model for the ladder, strong model for the answer |

Two of these are the substantive pulls:

**EA's collection router is already a ranker.** 400 lines of real scoring — explicit mention,
domain-term overlap, intent-keyword boosting, policy boosts, feedback-adjustment multipliers, an
informational-query guard, fallback expansion. RE's equivalent is a 57-line intent→content-type
lookup. RE's Perspective needs precisely a weight function, and one has been built once already.
The consolidation audit kept the two routers separate because the *collection models* genuinely
diverge — that stands; the scoring engine underneath is not the collection model.

**EA's feedback collector already keys on perspective.** Star rating, category, **perspective**,
routing agent, sentiment, feeding `analytics.py` / `sentiment_analysis.py`. You cannot tune
budget weights without a quality signal dimensioned along the axis being tuned, and EA has been
collecting one on the right axis for 652 lines' worth of implementation. RE's equivalent is a
21-line thumbs-up/down CLI prompt.

`graph_query_depth` is noted in EA's design as "straddling shape and performance." That is not a
wart awaiting resolution — depth is *the* budget dial, the one parameter that is simultaneously
content and cost, which is exactly what a packer trades off.

## 6. Scope is not fixed — what that costs

Purposes and intents are expected to extend into data quality, conformance, certification,
timeliness, provenance, comparison and historical analysis. Some devolve to direct Egeria
queries, some need new gathering, some need new analysis. Sorted by what each costs the
compiler:

**Additive — new Purposes, new questions, new resolvers, zero compiler change.** Data quality,
conformance, certifications. The compiler is deliberately indifferent to whether a resolver is a
direct Egeria query, a fetch, or a multi-step analysis; all the packer needs is the cost tier,
which CLAUDE.md rule 17 already makes you tag (Discovery as the cheap tier gating the expensive
ones *is* a budget policy, stated in resource terms). That indifference is most of why this is
worth building.

**Nearly free, but only with the envelope.** Timeliness reads `fetched_at`. Provenance questions
are answered *from the manifest*, not by a resolver at all. Both are cheap once the envelope
exists and awkward to bolt on after — which moves the envelope up the build order.

**Not additive — these two change the compiler's shape and should be designed in now:**

1. **Comparison is arity.** The compile unit becomes a set. The failure mode is specific: if the
   packer exhausts its budget mid-pack and subject A gets full evidence while subject B gets
   summarized, the model will systematically favour A, **and nothing in the output reveals why.**
   Comparison specs need a **symmetric packing constraint** — equal per-subject budget, all
   subjects degrade to the same ladder rung together, manifest asserts symmetry. This is a
   correctness property, not a nicety, and it changes the packer's loop order, so retrofitting
   is real work.

2. **Historical is time — and Egeria supplies most of it.** *(Revised 2026-08-27: an earlier
   draft of this section proposed a local retention archive and told the reader to go measure
   how much history Egeria keeps. Egeria versions **and** timestamps elements, supports as-of
   time queries, and imposes no limit on history. The retention question was largely the wrong
   question.)* For Egeria-sourced sections, historical and timeliness questions devolve to an
   as-of query — a **resolver parameter, not a cache policy**. See §10.

   What Egeria does *not* cover is RE-local resolver output that was never published: survey
   results, AST/dependency analytics, RAG-derived material. Those are the only sections needing
   local retention — which is a direct argument for publishing analysis results through the
   existing outbox/publisher, since publishing buys unlimited history for free rather than
   requiring a second archive to be designed and maintained.

   **Comparison and historical compose.** "How did A and B differ six months ago" is a
   symmetric pack at `as_of=T`. The symmetry constraint above must hold across subjects *and*
   across time points; a spec comparing two timestamps of the same resource is the same problem
   with the subjects being `(resource, T1)` and `(resource, T2)`.

## 7. Proposed revisions to `re-ea-consolidation-audit.md`

The audit examined implementation duplication. This framing sits mostly *downstream* of the line
it drew, but it moves two entries.

**Item 8 (LLM client) — `(c)` "RE has no multi-model need today" becomes a real need.** A
compression ladder creates one: the summarize-down-a-tier step wants a cheap, high-volume model
while the answer step wants the strong one. That is exactly EA's `_ModelOverrideClient` /
`get_planning_llm()` split. The framing manufactures the need the audit correctly reported did
not yet exist. Reclassify as "port when the ladder is built," not "port when needed."

**"Prompt templates — pure string content, no logic to extract" stays true and stops being the
whole story.** A compiler moves the boundary: templates remain app-specific (still correct),
prompt *assembly* becomes shared. A new extraction candidate the audit could not see, because it
audited what exists rather than what is proposed.

**Intent classification/routing stays `(c)`, and that is what makes this mergeable.** RE's flat
regex classifier and EA's tiered-plus-LLM-fallback system really do track different routing
surfaces. The compiler sits downstream of whichever classifier each app runs, so both stay
divergent and still feed one spec model. No part of this proposal requires relitigating that
verdict.

## 8. The shared package boundary

**Moves into a shared package** (same tier as `trellis-microflow`, per the audit's
extraction pattern):

- `ContextSpec` — schema, serialization, versioning
- Resolver registry — typed loaders keyed by cost tier
- Packer — budget solve, compression ladder, symmetric-packing constraint
- Provenance envelope + manifest
- Context cache — the audit's item 1 `QueryCache` (RE's TTL/Redis/invalidation base) with the
  identity partition from §1

**Stays per-app:** query classifiers, question and analysis catalogs, collection models, prompt
template content, agent rosters, auth.

This is a narrower and more actionable answer to the standing "how do RE and EA stay in sync on
shared code" question than the general one, because the interface is small and the audit already
established the tiering precedent.

## 9. Open questions — measure before building

- **EA's compile unit — proposed resolved, see §17.** EA has no Investigation equivalent, and
  `docs/design/SESSION_AND_INTERACTION_STATE.md` records that EA has *no backend-owned session
  concept at all today*, with `draft_id` generation unscoped by user or session. The proposal is
  that EA adopt RE's Investigation rather than grow a new concept — an unbound local row
  classified `PersonalProject`. What remains open is the migration, not the model.
- **Does Perspective weighting improve answers?** §3 shows nesting makes weights safe. Whether
  they help is unmeasured. EA's feedback data, dimensioned by perspective, is the instrument —
  check first whether it has enough volume per perspective to say anything.
- **Is EA's collection-router scoring engine actually portable**, or is it fused to EA's fixed
  catalog the way its collection model is? The audit found the models diverge; it did not
  examine whether the scorer separates from them.
- **How much version history does Egeria retain** for the properties historical questions will
  ask about? Determines the retention policy in §6.
- **Master-detail parameter inheritance** is already flagged unresolved in EA's own design
  (whether detail specs inherit `content_filters` / `shape_defaults` or carry their own
  three-category model). It interacts directly with nested Investigations via
  `ProjectHierarchy`, and the two should be resolved together rather than twice.
- **Is the replayability guarantee (`same spec + same as_of + same materialized state → same
  context`) actually true, or only true of some resolvers? (added 2026-08-30, from a literature
  check.)** "On the Reproducibility Limitations of RAG Systems" (arXiv:2509.18869) names retrieval-
  side nondeterminism as a real, distinct problem from sampling randomness — good evidence this
  guarantee targets something real. RAGdeterm (ScienceDirect, 2026) gets determinism the same way
  this design does: grounding retrieval in an explicit structured representation instead of
  similarity search. But "structured" does not imply deterministic — an unordered query, a
  paginated cursor, or a resolver that calls an LLM mid-resolution are all "structured" and still
  non-replayable (§9's own item above and the survey-side telemetry entry in `Backlog.md` both
  flag LLM-based steps specifically). **The guarantee is a property of the resolver, not the
  compiler as a whole**, and should be checkable per-resolver — a `deterministic: bool` tag in the
  resolver registry, mirroring the cost-tier tags CLAUDE.md rule 17 already requires — rather than
  an assumption that holds because the underlying store is Egeria and not a vector index. Neither
  source states the guarantee in this doc's exact terms; I could not find this replayability
  contract formulated anywhere in the public literature. See `Backlog.md`.
- **How many sections should compete for one budget? Measured 2026-09-09, and answered for
  now.** The compiled-versus-RAG experiment (`packages/resource-explorer/docs/experiments/
  compiled-vs-rag.md`, two runs, rubric v2) found compiled answers *cited* evidence (36% vs 1%) and
  asserted results for missing analyses less often, but made **more** unsupported claims
  (0.82 vs 0.58 per answer). The loss audit traced that to breadth: every analysis any catalog
  question reaches became a section, so 24–27 sections shared 6,000 characters, the budget pinned
  at its ceiling in all 156 compiles, and 78% of packed sections sat at SUMMARY — where the
  structural rung's counts ("5 key(s)") were being narrated as values. A sweep over the three
  experiment repos, six questions each, at budget 6,000:

  | section cap | sections | FULL share | top-3 ranked at FULL | used, median |
  |---|---:|---:|---:|---:|
  | none | 26.0 | 22% | 43 / 54 | 5,985 |
  | 12 | 11.9 | 88% | 52 / 54 | 5,610 |
  | 8 | 8.0 | 90% | 53 / 54 | 4,368 |

  Two changes followed, both in `context_compile.py`: a cap of 12 evidence sections applied
  *after* ranking, with the rest reported in the manifest as `deferred` (ranking still orders and
  never excludes at the derivation level — §3's rule survives; what changed is what competes for
  the budget); and the structural SUMMARY rung now shows scalars as values and names lists and
  mappings without counting them, headed "structure only". A third, found on the way: the ranking
  weight divided relevance by catalog position, so a full-text match at position 30 lost to a
  one-word match at position 0; it is additive now, and "repository" (13 of 52 catalog questions)
  joined the relevance stopwords. A fourth change, a one-shape refusal template owned by the
  compiler's instructions section, was tried in run 3 and reverted: it over-triggered, turning 107
  of 156 compiled answers into pure-template refusals and inventing gap states for analyses that
  had actually been packed. Run 4 of the experiment measures the three changes that stayed against
  the run-2 baseline; its numbers go in the experiment doc, not here. *(Outcome, 2026-09-10: the run-2 "more unsupported claims" finding
  was an artefact of a judge that never saw the packed text — an audit found 89% of the flagged
  claims in the evidence. Re-judged with the text, compiled beats RAG on every content metric in
  both runs, and the three packing changes are neutral on answer quality; they stay for cost and
  manifest honesty. Details and caveats in the experiment doc.)*

---

*Sections 10–16 added 2026-08-27, in response to a review pass on explainability, observability,
feedback, testing, ingestion, agent structure, and model selection.*

## 10. As-of time — the parameter that makes everything else work

Egeria versions and timestamps elements, supports as-of time queries, and keeps unlimited
history. That single fact upgrades three things this design had hedged on:

**`as_of` becomes a first-class ContextSpec parameter, and it is identity-bearing.** Different
`as_of` → different content → different cache entry. It sits alongside `content_filters` in EA's
partition.

**Reproducibility stops being a lie.** An earlier framing of this design conceded that "same spec
→ same context" cannot hold against live-changing metadata, and that determinism claims needed a
pinned snapshot RE did not have. Egeria *is* the snapshot mechanism. Pin `as_of` and the same
spec recompiles byte-identically, indefinitely.

**Which makes the manifest replayable, not merely readable.** This is the hinge for §11: an
explanation you can only read is an assertion; an explanation you can re-execute is an audit.
`(spec version, as_of, resolved GUIDs)` is a complete, permanently re-runnable description of a
compile.

Two things to get right:

- **Resolvers must accept and propagate `as_of`.** A resolver that ignores it silently mixes
  current data into a historical compile — the worst failure mode here, because the output looks
  coherent. Resolvers should declare `temporal: as_of | current_only`, and a compile at a past
  `as_of` containing a `current_only` section must be marked as mixed in the manifest, not
  quietly packed.
- **`fetched_at` and `as_of` are different fields.** The envelope needs both: when the fact was
  true (Egeria's timestamp) and when we read it (staleness, per RE's stale-but-labeled policy).
  Collapsing them loses the ability to distinguish "old fact" from "stale read."

## 11. Explainability

The manifest and envelope in §2 are necessary and not sufficient. They record *what was packed*.
They do not record *why those sections were candidates*, which is the part a user actually asks
about. Two distinct artifacts:

| Artifact | Answers | Source |
|---|---|---|
| **Derivation** | why these sections exist at all | Investigation → Purpose → Question → `analysis_ids` |
| **Manifest** | what survived the budget, at which rung | the packer |
| **Envelope** | where each span came from, as of when | resolvers |

The derivation trace is the explanation with real content — *"this section is here because your
Investigation's Purpose is `Certify`, which ranked Q17, which dispatches `security_scan`"* — and
it is available today only as an implicit consequence of the dispatch chain. Making it an emitted
artifact is close to free and is the piece most likely to be skipped.

Three requirements that follow:

- **Citation contract.** Without one, the envelope is write-only. Answers must cite
  `(guid, version, as_of)`, and an uncited claim is a detectable defect (§14).
- **Negative explainability.** "Why did I *not* see X" is answerable here in a way it usually
  isn't: since Perspective never excludes and Purpose ranks, the honest answer is nearly always
  "ranked below the budget line at rung N," which the manifest can state precisely.
- **Replay.** Per §10, an explanation is auditable rather than merely plausible.

## 12. Observability

The compile is a span tree that maps onto OTel with no impedance mismatch: `spec resolve →
per-resolver spans (attributed with cost tier) → pack → answer`. RE's `phoenix_client.py` is
already hardened for BeeAI (with the measured 7.89s dead-collector precheck) and EA has none —
consolidation audit item 4 already recommends EA adopt it, and the compiler raises the payoff,
because under this design the compile is the interesting span, not the LLM call.

The natural division of labour between the two systems each app half-has:

- **Phoenix/OTel — runtime traces.** RE's implementation is the base; EA adopts (audit item 4).
- **MLflow — spec experiments.** EA's 742-line implementation is the base; RE's is a 34-line
  stub (audit item 5). Evaluating spec variants — does adding this section improve answers? —
  is experiment tracking, not tracing, and it is where the tuning loop in §13 reports.

Metrics that do not exist today in either app and that this design needs:

- budget utilization and **ladder-rung distribution per section**
- drop rate by section (a section always dropped is a spec defect; a section never dropped is a
  budget defect)
- cache hit rate **partitioned by identity** (§1) — a low rate here usually means the key is
  wrong, not that the cache is cold
- resolver latency by cost tier — which **empirically validates CLAUDE.md rule 17's tiering**.
  Rule 17 currently asserts Discovery-tier cheapness per analysis, with one measured exception
  (`architecture_recovery` at ~5s). Compiler telemetry turns that per-analysis argument into a
  continuously measured property.

## 13. Feedback

EA's `feedback_collector.py` already carries the right dimension (perspective). The gap is
attachment point: **feedback today attaches to the answer, and weight tuning needs it attached to
the compile.** A rating must carry the manifest id, or a poor answer cannot be distinguished
between "wrong evidence was selected," "right evidence was compressed to rung 3," and "the model
had everything and reasoned badly." Those need different fixes, and without the manifest link the
signal cannot separate them. This is cheap to add now and expensive to retrofit across accumulated
feedback data.

Three loops, deliberately separate:

1. **Answer quality → weight tuning.** Perspective-dimensioned ratings + manifest → §3's weights.
2. **"This section was useless / I needed X" → spec editing.** Routes into EA's existing
   draft → preview → save lifecycle. The spec is already a user-editable Dr.Egeria markdown
   document, so this loop needs a route, not a mechanism.
3. **RFA.** Already exists; human decisions on findings, gated by Purpose (§4). Unchanged.

**Guard against double-counting.** EA's collection router already applies feedback-adjustment
multipliers. If both the router and the packer learn from the same signal, the same feedback is
applied twice with compounding effect and no trace of it in either component. Pick one consumer
per signal, or dimension them explicitly.

This is a concrete piece of the "redesign AI-response feedback into a real closed loop" workstream
already flagged as the genuinely hard one of RE's three feedback systems.

## 14. Testing and guardrails

Packing is otherwise untestable by inspection, which is most of the argument for making it an
explicit component. Properties worth asserting:

- **Determinism** — same `(spec version, as_of, GUIDs)` → byte-identical context. Only possible
  because of §10, and the highest-value test here.
- **Monotonicity** — increasing the budget never *removes* content. Catches a large class of
  packer bugs cheaply.
- **Nesting invariant** — adding a Perspective never removes a section. Guaranteed today by the
  framing-design measurement; asserting it means a future question-catalog retag that breaks
  nesting fails a test instead of silently changing dispatch semantics. This is the guardrail
  protecting §1's identity invariant, and the same pattern as `tests/test_check_registry.py`.
- **Symmetry** — comparison specs pack all subjects at equal budget and the same rung (§6).
- **Gate coverage** — every `mode: gate` section has an explicit test, §4's RFA rule first.
- **No dangling references** — extend the existing question→analysis id check through
  spec → section → resolver.
- **Budget ceiling is a hard failure, not a truncation.** Silent truncation at the window
  boundary defeats every other guarantee in this document.

Runtime guardrails:

- **Refuse rather than degrade below a floor.** The `Privacy`-reaches-zero-analyses case should
  report an empty evidence section, not backfill with RAG prose.
- **Mixed-temporality compiles must be labelled** (§10), never silently packed.
- **Packed resolver content is untrusted input.** RE ingests arbitrary GitHub repositories —
  README text, commit messages, code comments, DB object comments. Spans must be delimited and
  marked as data, never as instructions. This is a live exposure given RE's discovery flow
  imports repos found by keyword search, not a theoretical one.

## 15. Ingestion — the ladder has to be built upstream

This is the largest downstream implication, and it touches a verdict in
`docs/ingestion-pipeline-audit.md`.

**Rungs cannot be generated at compile time.** Summarizing on the hot path is too slow and too
expensive at the volume a packer needs. The compression ladder therefore has to be *precomputed
at ingest*: ingestion emits multi-resolution artifacts, not just chunks and embeddings. The
ingestion audit already raises "stage ingestion instead of fetch-chunk-embed-in-one-pass" as an
open item; this design gives that item a concrete requirement to satisfy.

**Chunk size and rung boundaries are different axes.** *(Revised 2026-08-27: an earlier draft
framed this as two competing strategies — "structure for rungs, windows for retrieval." That was
too binary.)* Chunk size is a **tuned parameter**, driven by artifact category (code vs. doc) and
by a rough content profile, because retrieval quality measurably improves when chunk size matches
content style. Rung boundaries are a **containment** question — a structural unit can contain
several retrieval chunks. Neither derives from the other.

The synthesis both need: **parse once, emit a containment tree.** Retrieval chunks are its leaves,
sized by profile. Ladder rungs are cuts across it at different depths. One parse generates both.

This resolves `docs/ingestion-pipeline-audit.md` item 3 more cleanly than the earlier draft did.
The audit found RE's fixed-size windowing and EA's semantic-unit extraction to be a real, confirmed
divergence — "windowed-chunk RAG vs. semantic-unit RAG" — and ruled `(c)`, leave alone. That
verdict compared two *outputs*, and it stands as a comparison of outputs. The shareable thing is
the **tree that produces them**, which neither app currently emits as a first-class artifact.

**Where profiling actually earns its keep.** For code it is largely moot: tree-sitter already
gives authoritative boundaries, so the tree *is* the profile. Profiling matters where structure is
weak — prose docs, wikis, generated output, plain text. Two refinements:

- **Profile per document, not per repository.** A README, an ADR, and generated API docs share a
  repo and nothing else stylistically.
- **The signals are things RE's survey layer largely computes already** — heading density and
  depth, code-to-prose ratio, tokens per semantic unit, list/table density, boilerplate ratio. The
  chunk-tuning profile is a derived view of survey output, not a new tool to acquire.

**And the better instrument is the feedback loop, not a better profiler.** Rough profiling sets the
prior. Retrieval quality per section, rung distribution (§12), and manifest-linked ratings (§13)
tune the posterior empirically. This is an argument for treating chunk-size heuristics as
provisional-and-measurable rather than for investing in more sophisticated up-front profiling.

Two further ingestion-spec consequences:

- **Envelope fields must be captured at ingest.** `source guid`, Egeria `version`, Egeria
  timestamp, and `fetched_at` (§10) — retrofitting provenance onto already-embedded material is
  painful and often impossible.
- **The shared Egeria-family corpus becomes temporal, not a rolling snapshot.** A compile at
  `as_of=T` needs material as it stood at T. This is a larger change to the materialization model
  than it first sounds, and it should be decided before the corpus grows further.

## 16. Agent structure and model selection

### Agent structure

The compiler's main structural effect is **taking context assembly away from the agents.** Today
both apps' specialist agents each gather their own context; under this design they split into
three roles with different implementations:

| Role | What it is | Implementation |
|---|---|---|
| **Resolvers** | produce evidence; no conversation | BeeAI agents *or* plain functions, uniform contract |
| **Packer** | budget solve, ladder, envelope | **deterministic code — explicitly not an agent** |
| **Answerer** | consumes packed context, cites it | BeeAI `RequirementAgent` |

The packer being ordinary code is the important line. Every guarantee in §14 — determinism,
monotonicity, symmetry, the budget ceiling — is unavailable if an LLM decides what to include.

Consequences:

- **Consolidation audit item 2 (shared BeeAI agent base) rises in value**, since resolvers want
  one uniform contract across both apps.
- **The per-app classifiers stay divergent (§7), but their output changes**: a classifier now
  selects a *spec*, not an agent. That is a small change at each call site and is the actual
  integration point between the existing routing layer and this design.
- **EA's 10+ specialist agents will partly collapse into resolvers.** Do not do this as a big
  bang; convert one agent, measure, continue.

### Model selection and tuning

Three roles rather than one, which is the need consolidation audit item 8 recorded as not yet
existing:

- **Summarizer** — builds ladder rungs. High volume, and per §15 it runs at *ingest*, not at
  compile. Precomputation inverts the usual economics: because latency no longer matters, this
  can use a *larger* model than a hot-path summarizer could, once, per artifact.
- **Answerer** — strong model, sees only packed context.
- **Judge** — optional, for the §13 evaluation loop.

EA's `_ModelOverrideClient` / `get_planning_llm()` is the mechanism; RE has multi-provider but not
multi-model.

**The target model is identity-bearing.** Its context window is an input to the budget solve, so a
different model produces different packing and therefore different content. It belongs on the
`content_filters` side of EA's partition, not in `performance_hints` — an easy and consequential
thing to misfile, since "which model" reads like operational tuning.

**Tuning** runs through MLflow (EA's mature implementation, per §12) with labels supplied by the
manifest-linked feedback of §13. Longer term, a compiler also generates precisely the training
data for the text→spec fine-tunes already tracked as an option — `(question, spec, outcome)`
triples fall out of the loop as a byproduct. Noted as a downstream benefit, not a plan.

## 17. EA's compile unit — adopt Investigation, don't invent a concept

§9 flagged this as the largest hole. The proposal is that **EA adopts RE's Investigation rather
than growing a parallel session concept**, on the grounds that the framing design already built
the general case:

- An Investigation is **one local row with a nullable `egeria_project_guid`** in all three
  creation paths, which makes promotion a fill-in rather than a migration.
- `PersonalProject` is **already one of the four Egeria `Project` classifications** the framing
  design enumerates (`PersonalProject` / `Task` / `StudyProject` / `Campaign`). A private EA
  working session is a `PersonalProject`-classified local Investigation, promoted to a bound
  Egeria Project on the day it needs to be shared or audited — which is exactly the lifecycle a
  session concept would otherwise have to reinvent.

This is the better answer than a new concept for four reasons: the compile unit is then **shared
across both apps**, which is what §8's shared package needs to key its cache on; the promotion
path is designed and its structural decision already argued; per-user partitioning gets a natural
owner, addressing an item confirmed needed in three separate places (EA plan drafts, Portal
dashboard-sheet JSON, RE dr.egeria artifacts); and it avoids adding a fourth meaning to the word
"project," which the framing design §7 identifies as already carrying three.

**Investigation sits above EA's existing two work objects, and replaces neither.** The LGCI Plan
is imperative and single-run; a Report Spec is declarative and repeatable. An Investigation is the
container both run inside — the same relationship RE's Investigation has to its surveys.

**Migration is not a constraint** (confirmed 2026-08-27: there are no users yet). What would
otherwise be the hard part of this — rehoming unscoped `draft_id`s, backfilling scope onto existing
work — costs compute and schema churn, not coordination. What remains open is placement:

- **Where the table lives.** RE owns the Investigation schema today. The ingestion audit
  established a third centralization pattern — **cross-schema read** over the shared Postgres
  instance (`advisor/re_code_symbol_reader.py` reading RE's tables directly, no shared package) —
  which is the cheapest option here, but needs a deliberate decision about *write* ownership, since
  EA would be creating Investigations rather than only reading them.
- **Master-detail parameter inheritance** (unresolved in EA's own report-spec design) and
  Investigation nesting via `ProjectHierarchy` are the same question about inherited scope, and
  should be resolved once rather than twice.

## 18. Sequencing — what to protect

With no users, schema churn and re-ingestion are cheap. **The expensive asset is curated
intellectual work, not data**: the 41 questions and their Purpose/Perspective tagging, the 21
analyses and their cost-tier assignments, the framing design's model. Data regenerates; tagging
does not. Sequence to protect the vocabulary, not the schema.

This also restates §14. The guardrails there were argued partly from silent-failure-in-production,
which does not apply yet. They remain worth building for a different reason: what they protect is a
sole contributor's debugging time against silent drift, which is the scarcer resource here.

1. **Instruments first** — envelope fields captured at ingest (§10), manifest-linked feedback
   (§13). Both purely additive. Everything in this document is asserted and unmeasured, and these
   are what make it measurable; building them first is the same discipline
   `investigation-framing-design.md` applied to its own dispatch chain.
2. **The containment tree (§15) second.** The only item with a real clock on it — not because of
   users, but because re-ingestion cost scales with a corpus that keeps growing.
3. **`ContextSpec` + packer third, wrapping the existing dispatch chain.** The question catalog,
   analysis catalog, Purpose/Perspective tagging and rule 17's tiers all become *inputs* to the
   packer. None of the curated work is invalidated, which is the point.
4. **EA adopts Investigation and the shared package fourth**, once the packer is proven in RE.

Deliberately deferred: collapsing specialist agents into resolvers (§16 — one at a time, measured),
the model-role split (§16 — needs the ladder to exist first), and any fine-tuning.

**The one thing to stop investing in now:** context-assembly logic inside specialist agents. If the
packer takes over assembly, per-agent gathering code is the work most likely to be discarded. Stop
adding to it rather than continuing to refine it.

---

*Sections 19–22 added 2026-08-27: catalog authoring, non-RAG resolvers, unknown artifact types,
and dynamic feedback.*

## 19. Catalogs are authored artifacts — keep the authoring surface

`question_catalog.yaml` says so in its own header: it is **generated by
`scripts/csv_to_question_catalog_yaml.py` from `docs/dr-egeria/resource_questions.csv` — the
source of truth — and must not be hand-edited.** The curated matrix (question × stage ×
perspectives × purposes × `answering.kind` × `analysis_ids`) is authored in a spreadsheet, because
a sparse tagging matrix is what spreadsheets are genuinely good at, and compiled into the YAML the
readers consume.

This is the **third instance of a house pattern**, alongside Dr.Egeria's compact JSON →
`refresh_specs` → templates/help, and EA's report specs authored as Dr.Egeria markdown. Three
independent arrivals at *human-authored source of record → generated machine artifact → validator*
make it the pattern, not a coincidence.

Three consequences:

**EA needs its own instance, not a share of RE's.** Different questions, different context, a
somewhat different column structure — but the same shape. This is authoring method reuse, which is
the kind that transfers cleanly precisely because the content does not.

**The invariant worth carrying across is the Egeria join.** RE's catalog notes that `question`
matches the Egeria `GlossaryTerm` display name exactly, "case+punctuation-sensitive, since it's
also the join key back to Egeria's own Question elements." That anchoring is what keeps the
curated matrix from being a private local file: it makes the question corpus Egeria-native,
queryable, and eligible for the shared Egeria-family RAG corpus. EA's matrix should anchor the same
way.

**`ContextSpec` sections get the same treatment.** Authored in the spreadsheet alongside the
questions they serve, generated into the spec catalog, never hand-edited, and guarded by a
validator in the manner of `validate_compact_specs` and `tests/test_check_registry.py`. §18's
"protect the curated vocabulary" is only enforceable if the generated artifacts are unambiguously
build output.

## 20. Resolvers are mostly not RAG

§15's length gives a misleading impression of proportion. **This is a multi-agent system in which
retrieval is one source among several** — agents run surveys, execute analytics, query Egeria
directly, read the registry, or do work that fits none of those categories. §6's claim that the
compiler is indifferent to resolver kind is the load-bearing one; ingestion is simply the source
with the most machinery behind it.

Taking that seriously has one large architectural consequence:

**The packer must never trigger a survey.** Resolver latency spans milliseconds (an Egeria query),
seconds (a Discovery-tier analysis), and minutes (a survey run). No packer can synchronously await
the third class. So the resolver contract carries an availability dimension:

| `availability` | Behaviour at compile time |
|---|---|
| `materialized` | read the stored result; packable now |
| `schedulable` | **not** run inline — emit a manifest gap and optionally queue the job |

A compile that needs an analysis which has not run reports *"this section is empty because
`security_scan` has not run for this resource; queued"* and proceeds. It does not block, and it
does not silently omit. This preserves the existing scheduler/outbox architecture rather than
competing with it, and it is consistent with the standing decision that shared analytics are
**materialized, not live-called**.

**Implementation finding (2026-08-27), reversed (2026-08-30).** Two corrections from building this,
one of which held only until a counterexample this section didn't have:

- ~~**`availability` is derived, not tagged.**~~ **REVERSED, the project owner's ruling, 2026-08-30.** The
  argument above was right while `run_time` and "may a compiler run this inline" always agreed —
  `architecture_recovery` is the case where they came apart. Its *compute* is 5.9s, so
  `run_time: fast` is honest; but a compile running it inline also pays *acquisition* (14.4s warm,
  ~30s cold — `docs/Backlog.md` "costs 110s to fetch and 5.9s to run"), inside a packer this
  section requires to **never trigger a survey**. `run_time` was carrying two loads — how
  expensive the compute is, and whether a compiler may run this inline — and only the second
  carries a hard safety requirement, so it is now its own field: `AnalysisCatalogEntry.availability`
  is **declared**, defaulting to `queued` (unset means queued deliberately — guessing cheap is
  still the dangerous direction, that half of the original reasoning stands). 20 entries declare
  `inline` (fetch-free *and* fast, read off `STEP_REGISTRY.requires_resources`, not by eye); 9
  declare `queued` (4 acquire a zipball/clone, 5 are minutes-scale or actions).
  `architecture_recovery` is pinned `run_time: fast` **and** `availability: queued` — a
  combination that was structurally impossible under the derived rule, which is the whole point of
  no longer deriving it. `_derive_availability()` survives only as a fallback for
  `_egeria_merge_entries`'s hand-built live-Egeria dicts, which have no catalog row to declare
  from. Reopening the "two columns drift" risk this section originally argued against is answered
  the same way §19 answers it elsewhere: guarded by tests (nothing `inline` acquires a resource or
  runs slow, something *is* `inline`, `architecture_recovery` is pinned so the divergence can't be
  quietly re-derived away) rather than hoped.
- **`temporal` does not belong on the analysis catalog at all.** The compiler reads *stored,
  timestamped* analysis results, never a live run — so every analysis is as-of-able over its own
  results, and tagging 34 entries `as_of` would be a column with one value. `current_only` is a
  property of resolvers that query a live source at compile time (a direct API fetch, an unstamped
  registry read), so the declaration belongs on **resolver kind**, not on analyses. An earlier draft
  of this section put it on both.

**And this is what rescues determinism.** §14 asks for byte-identical recompiles, which is
impossible if an agent runs inside the compile — agents are nondeterministic. The resolution is
that the guarantee is over *materialized inputs*: `same spec + same as_of + same materialized state
→ same context`. Agent output must therefore be **written down, versioned, and provenance-stamped
before it is packable.** The determinism test is precisely the mechanism that keeps agent
nondeterminism out of the context path, rather than a property in tension with the multi-agent
design.

## 21. Unknown artifact types are the normal case

EA brings far more content diversity and far less structure than RE — PDFs, office documents, and
formats not yet encountered. **The set of artifact kinds is open and will never be fully known up
front**, which is a requirement rather than a problem.

Three things make it evolvable:

**An adapter registry, following the `ResourceTypeAdapter` precedent.** RE already absorbed
metadata repositories as "just another resource type" through that plugin pattern rather than
building a parallel surface; artifact ingestion takes the same shape, keyed by artifact type.

**The containment tree (§15) is the format-independence boundary.** PDF, docx, markdown, wiki
export, plain text — each adapter's job is to emit a tree. Everything downstream (rungs, chunking,
budget, packing) is written against the tree and never against a format. This is a second, stronger
reason for the tree than the one §15 gives, and it means new formats cost an adapter rather than a
pipeline change.

**Absence of an adapter must degrade, never block.** An unrecognized artifact falls back to flat
text: one rung, profile-tuned windows, no structure. It is ingested and usable immediately; a
later adapter improves fidelity without a migration. Adapters are an optimization over a working
default, not a gate in front of one.

**Extraction fidelity belongs in the envelope** — *"this PDF was extracted via the generic text
path; structural fidelity low."* It then reaches the manifest and the answer's citations, so a
low-confidence extraction is visible rather than indistinguishable from a clean structural parse.

## 22. Dynamic feedback — make the manifest editable

§13 covers feedback *after* the answer, which trains weights offline. Dynamic feedback is the
user adjusting the compile **in the moment** — drop this section, go deeper on that one, not that
repository, as of last quarter — and seeing the result change.

The mechanism already exists in pieces: the manifest is a complete description of the compile, so
**making it editable turns it into a spec override, and a recompile follows.** This is the concrete
form of the §2 claim that the manifest is the return path for user input.

**Three grains of recompile, with wildly different costs that feel identical to the user:**

| Grain | What changed | Cost |
|---|---|---|
| **Re-pack** | budget weights, rungs, section on/off | milliseconds — nothing is re-fetched |
| **Re-resolve** | `as_of`, content filters, membership | moderate — resolvers re-run over materialized state |
| **Re-gather** | an analysis that has not run | asynchronous (§20) — queued, not awaited |

The UI must distinguish these, because a user cannot tell from the outside that "show me more
depth" is instant while "include the security scan" is a scheduled job. Re-pack being nearly free
is a direct payoff of the packer/resolver split — adjusting a weight is a repack, not a re-gather.

**Reuse EA's lifecycle rather than inventing one.** Draft → preview → save already exists for
report specs; dynamic feedback on a compile is the same lifecycle applied to a context instead of a
report.

**Persistence is a real choice and should be explicit:** apply once, remember for this
Investigation, or remember for me. The third writes back to the user's Perspective profile and ties
directly to the per-user partitioning item.

**An accepted manifest edit is a better training label than a star rating**, and it is free. A
rating says the answer was poor; an edit says *what was wrong with the context* — this section
needed more budget, that one was irrelevant. Those are gradients rather than scalars, and they
arrive already attached to the manifest that §13 needs them attached to.

## 23. What this actually looks like in RE and EA

*(Added 2026-08-27 — the document described the machinery without ever saying what a user does with
it. Proposals, unmeasured like the rest.)*

### RE

**Today:** the Chat panel is RAG-backed, scoped to whichever resource is selected, and independent
of the active intent. It assembles its own context, and nothing about the Investigation, the
Purpose, or the Perspective chips reaches it.

**Under this design:** the panel becomes Investigation-scoped and axis-aware, and the assembly moves
out of it. Three flows, in the order they are worth building:

**1. The adoption gate (`Purpose = Certify` or `Assess`).** A user with an active Investigation,
Perspective chips set to Security, in the Assessment intent, selects a repo and asks whether it is
ready to adopt. The compiler picks the spec Purpose ranks first, packs the materialized evidence
(`security_scan`, `license_classification`, the supply-chain scorecard checks), and cites each span
by `(guid, version, as_of)`. The manifest shows what ranked in, what fell below the budget line, and
that one analysis has not run and is queued (§20). This is the smallest flow that exercises the
entire path — spec, resolvers, budget, envelope, manifest, gap — which is why it goes first.

**2. Impact (`Purpose = Maintain`).** "What breaks if this changes." The dependency closure is
bounded by **Investigation membership first, depth second** — the membership join is the frontier,
not an arbitrary depth-N walk. This is the flow where hand-assembled context is most obviously
fragile today.

**3. Comparison (`Purpose = Select`).** "Which of these three should we adopt." Exercises the
symmetric packing constraint (§6), and is the first flow that can silently produce a *biased* answer
rather than a poor one, so it should not be first.

**Where it plugs in:** `question_catalog_reader` already resolves Purpose and Perspective to
questions and `analysis_ids` — that becomes spec selection rather than checklist display. Analyses
are already materialized in the registry. The visible new surface is a manifest pane in the Chat
panel.

### EA

**Today:** report specs compile to human-readable reports, and a separate tiered classifier routes
Q&A to 10+ specialist agents, each gathering its own context.

**Under this design**, two uses, which are the same machinery pointed differently:

**1. Narrate a report from its own evidence.** A report spec already declares what to fetch and
show. The compiler packs *that resolved data* plus its provenance, so the narration cites the rows
it is describing and cannot drift from them. The spec is unchanged; a second consumer is added
beside the renderer.

**2. Q&A with real sections rather than retrieved chunks.** A question compiles to a spec whose
sections are the relevant Egeria elements (at `as_of`), the report specs that touch them, the shared
corpus, and RE's code symbols — the last via the cross-schema read already established in
`advisor/re_code_symbol_reader.py`, not a new integration.

**3. AI-Ready Data Products** (still open in the Egeria Overview dashboard work). This design offers
a testable definition rather than a vague tile: **a data product is AI-ready when it ships a
compilable context spec** — declared sections, resolvers that answer, provenance, and a known
budget. That converts "is this AI-ready?" from a judgement into a check, and it is the same
machinery inverted: producing context for other consumers rather than consuming it.

### Shared

The manifest pane is one component in the shared package, not two implementations — it renders
derivation, what packed at which rung, and what was gapped or queued. It is also where §22's
editable-manifest feedback lives, so both apps get dynamic feedback from the same surface.

## 24. Implementation task list

*(Added 2026-08-27.)* The first *work* and the first *impact* are different things, and impact can
come well before the compiler exists. Phases 0–2 produce no compiler and are worth having anyway —
that is deliberate, not scaffolding.

### Phase 0 — derivable from today's catalogs, no new architecture

Needs no spec, no packer, no tree. These are §18's measurement instruments in their cheapest form.

1. **Emit the derivation trace.** `question_catalog_reader.py` already resolves
   Purpose + Perspective → questions → `analysis_ids`; return that chain instead of discarding it.
   This is §11's derivation artifact, and it exists implicitly today.
2. **Standing coverage report** over `question_catalog.yaml` + `analysis_catalog.yaml`:
   Purpose × analysis and Perspective × analysis reachability. The Privacy-reaches-zero finding was
   computed by hand once; make it a script that runs.
3. **Assert the nesting invariant as a test**, in the manner of `tests/test_check_registry.py`, so a
   future retag that breaks nesting fails a test rather than silently changing dispatch semantics.

**This is the first point of impact.** (1) is user-visible, (2) shows where the catalog is thin,
(3) protects the curated vocabulary. None of it commits to building a compiler.

### Phase 1 — authoring, not code

*(Status, 2026-08-30: item 4 is half-built, half-superseded, and reversed once already — see
below. Items 5 and 6 are unbuilt: no `fetched_at`/`as_of`/source-GUID envelope is captured at
ingest for an Egeria-sourced artifact, and `feedback` carries no `manifest_id`.)*

4. ~~**Two columns on the analysis catalog**: `availability: materialized | schedulable` and
   `temporal: as_of | current_only`.~~ **Partially built, on a different history than either
   earlier note here predicted.** The vocabulary moved twice: this item's own
   `materialized | schedulable` was never built; a 2026-08-27 finding then said `availability`
   should be *derived* from `run_time` rather than tagged at all (superseding this item); and
   the project owner's 2026-08-30 ruling reversed that (§20) once `architecture_recovery` showed `run_time` and
   "safe to run inline" coming apart. **Current state:** `availability: inline | queued` **is now
   a declared field** on `AnalysisCatalogEntry`, defaulting to `queued`, with 20 entries declaring
   `inline` and 9 declaring `queued` — see §20 for why the derived version stopped being safe. So
   this half of the item is built, just not the way it was proposed, and not the way the
   2026-08-27 note said it never would be.
   * `temporal` **still does not belong on the analysis catalog** — the compiler reads stored,
     timestamped results, so every analysis is as-of-able over its own output and the column would
     have one value. `current_only` is a property of **resolver kind**, and no resolver-kind
     registry exists yet — this half remains open, in a different place than this list said.

   *Left struck through rather than deleted because the reasoning is the useful part: a reader
   following the task list would rebuild exactly what §20 argued against, which is how this was
   found.*
5. **Envelope fields captured at ingest** — `source guid`, Egeria version, Egeria timestamp,
   `fetched_at` (§10). Additive schema; needs re-ingestion, which is cheap now.
6. **Manifest id on feedback records** (§13). One column. Without it the signal cannot distinguish
   wrong-evidence-selected from over-compressed from model-reasoned-badly.
   *(Status, 2026-09-08: **built.** `CompiledContext.compile_id` is a content hash of the packer's
   inputs; every compile is persisted to `context_compiles` with `hits` counting repeats (the §9
   replayability check, measured rather than asserted); `conversation_history.compile_id`,
   `resource_feedback.compile_id` and the metrics store's `query_log.compile_id` carry it; the chat
   stream and `POST /api/context/compile` return it. Verified live: two identical compiles → one row,
   `hits = 2`; a chat turn → both turns linked to the compile and attributed to the signed-in user —
   which also found and fixed the streaming route's thread dropping the caller's identity.)*

### Phase 2 — the containment tree

7. Parse once, emit a containment tree (§15); retrieval chunks are its leaves, ladder rungs are cuts
   across it. **The only item with a clock on it** — re-ingestion cost scales with a growing corpus.

### Phase 3 — the compiler, and RE's first real flow

8. `ContextSpec` schema — authored in the CSV, generated, validated (§19).
9. **Packer as deterministic code** (§16) — budget solve, ladder, symmetry constraint, hard ceiling.
10. Wire the **adoption gate** (§23) into RE's Chat panel with a manifest pane.

**This is the first point at which the compiler impacts RE.** Everything before it is invisible.

*(Status, 2026-08-30: 8 and 9 are built and green — `trellis_context.spec`, a 338-line packer, and
`context_compile.py` with 40 passing tests. **10 is not started**, and it shows: the only caller of
`compile_context` anywhere in the repo is `tests/test_context_compile.py`. On this list's own terms
the compiler currently has no impact on RE.)*

*(Superseded 2026-08-31: **10 is done and the "no caller" claim above is false.** `compile_context`
has two real callers — `agents/conversation_agent.py`'s `_compiled_evidence()`, which injects packed
evidence and judged gaps into the prompt, and `web/routes/compile_context.py` behind
`POST /api/context/compile`, which the Chat panel's Evidence button calls. The compiler impacts RE.*

*The stale line is left standing rather than rewritten because of what it is: a statement that was
true when written, about a mechanism, that kept reading as a claim about the world after the world
moved. That is the failure this document argues the compiler exists to prevent — "measured and found
nothing" versus "never ran" versus "ran and could not tell" — and it went uncorrected here for a day
while the thing it described was being built. A status note with no expiry is the same defect in
prose. Date status notes, or make something fail when they go stale.)*

### Phase 4 — EA

Gated on a decision rather than a task: **where the Investigation table lives and who writes it**
(§17). EA cannot adopt the compile unit until that is settled, so the decision is worth making early
even though the work is late.

### Caveat

The payoff scales with how much RE's Chat panel is actually used. If it is peripheral to how the
work happens, phase 3 is a lot of machinery for a low-traffic surface — worth checking real usage
before committing to it.

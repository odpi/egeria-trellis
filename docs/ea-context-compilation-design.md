# Extending context compilation to Egeria Advisor

> **Status (2026-11-19, 6.3 Tech Preview):** design agreed 2026-09-06; **not built**. Egeria Advisor does not import `trellis-context` in 6.3.

**Written:** 2026-09-06. **Status:** design agreed, nothing built. The six questions this
document opened were ruled on the same day — see §9. Two of them were answered by rejecting
the options as posed rather than choosing among them.

The project owner asked that RE's context compilation be extended to Egeria Advisor "as
appropriate." This document is mostly about the *as appropriate* — where the boundary falls and
why — because a scheme that compiles everything is easier to write and worse to own.

Prior art, all of which this builds on rather than restates:

- [`context-compilation-design.md`](context-compilation-design.md) — the original design, which
  already reasons about EA in §5, §9, §17, §19, §20 and §21.
- [`context-compilation-architecture.md`](context-compilation-architecture.md) — what actually
  shipped, and which of the design's claims were falsified.
- [`trellis-architecture.md`](trellis-architecture.md) — the two apps, six libraries, one Postgres.

Where those documents and the code disagree, this one follows the code and says so in §8.

---

## 1. What "compiled context" is, concretely

Read from `packages/trellis-context/trellis_context/{spec,packer}.py` and
`packages/resource-explorer/resource_explorer/context_compile.py`.

A compiled context is **a bounded, ordered bundle of text plus a manifest describing how it was
bounded.** Three objects:

| Object | Where | What it is |
|---|---|---|
| `ContextSpec` | `spec.py` | The *compile target*: named, versioned, a tuple of `Section`s. Each section carries `role`, `weight`, `required`, `mode` (rank\|gate), `floor` (coarsest allowed `Rung`), `group` (symmetric packing). |
| `Candidate` | `packer.py` | What a *resolver* produced for one section, offered at several `Rung`s (FULL → SUMMARY → IDENTIFIERS), plus `provenance` and an optional `Pointer` to a live view. |
| `PackedContext` | `packer.py` | Sections that fit, plus a `Manifest` of `packed` / `dropped` / `gaps` / `notes` and a hard budget accounting. |

Four properties are the reason it is ordinary code and not a prompt: determinism, monotonicity,
symmetry across grouped sections, and a **hard ceiling that raises `BudgetError` rather than
truncating**.

**The packer never calls a resolver.** `pack()` takes an already-resolved `dict[str, Candidate]`.
Everything app-specific — what a section contains, where it is read from, what it costs — lives
outside the shared package. That is the seam EA plugs into.

**Storage model: there is none.** `compile_context()` (`context_compile.py:337`) reads stored
analysis results and packs them fresh on every call. No table, no column, no cache entry holds a
compiled context anywhere in this repo today. The *inputs* are persisted (findings, analysis
results, artifact trees in the `artifact_tree` schema); the *output* is ephemeral. This matters
for §5 and §6: "extending context compilation to EA" does not currently mean extending a cache,
because there isn't one.

**Invalidation, therefore, is also not a thing that exists.** Freshness comes from the inputs
being read at request time. What *does* exist is `trellis_querycache`, whose `CacheEntry` already
carries a `scope` and whose `key()` (`cache.py:74`) folds scope into the key, with
`invalidate_scope()` to drop a namespace. §5 leans on this.

**How RE uses it (the real call sites).** One bridge module, two consumers: the conversation
agent's prompt, and `POST /api/context/compile` behind the Evidence button beside Send
(`context-compilation-usage.md`). Everything else in RE is computed live.

**`availability` vs `run_time`.** RE's CLAUDE.md rule 17 records the ruling: `run_time` says how
expensive the *compute* is; `availability` says whether a compiler may run the analysis *inline*.
They came apart on `architecture_recovery` — 5.9s of compute behind ~14–30s of acquisition — so
`availability` became its own declared field on `AnalysisCatalogEntry`
(`analysis_catalog_reader.py:104`), defaulting to `queued`, pinned by
`test_a_fast_analysis_may_legitimately_be_queued`.

**But `availability` currently has no reader.** Grepped repo-wide: outside
`analysis_catalog_reader.py` and `tests/test_catalog_invariants.py`, nothing consults the field —
not `context_compile.py`, not `run_queue.py`, not `scheduler.py`, not any route. RE's CLAUDE.md
rule 17 and `context-compilation-design.md` §20 both describe it as the switch a compiler uses to
decide inline-vs-queued; no such switch exists, because `compile_context()` never dispatches
anything at all and every missing analysis becomes a gap regardless of its tag. The safety property
is achieved **structurally** (the compiler has no inline branch), and the field documents an
intention the code has not yet needed.

That matters for EA in a specific way. RE could get away with a structural guarantee because it has
exactly one dispatch policy: read stored results, never run. **EA cannot** — §3's plan-authoring
compile genuinely wants some resolvers to run at compile time (an `EgeriaContext` zone lookup is
milliseconds) and absolutely must not run others. EA is therefore the app that will make
`availability` load-bearing rather than declarative, and it should be read there from the first
compile rather than re-derived. §7 step 3 is where that happens.

---

## 2. What EA already has — read this before building anything

The most useful finding in this exercise is how much of EA is already doing pieces of this under
other names. **EA has zero references to `trellis_context`** (verified by grep across
`packages/egeria-advisor/`), but it is not starting from zero.

| EA asset | What it really is | Verdict |
|---|---|---|
| `rag_retrieval.py::build_context()` (line 214) | **EA's existing packer.** Takes score-ordered chunks, formats each, stops when `rag_context_budget_tokens` or `max_context_length` is exceeded. | Single-section, no rungs, no manifest, **silently truncating** (a `logger.warning` and a `break`) and returning the string `"No relevant code found."` on empty. Replace, don't extend. |
| `query_cache.py` → `trellis_querycache` | LRU over **retrieval results**, process-global, no TTL, keyed on query text + params with no user dimension. Lives in `rag_retrieval.py:62`, not in `rag_system.py`. | Correct as-is today (see §4). Do not add compiled contexts to it without a scope. |
| `command_keyword_index.py` | A derived index (~126 commands, 4 confidence tiers) built lazily from the action catalog plus the template filesystem, memoised with `@lru_cache`. | **This is already compilation** — of a lookup index, not of a context. A resolver *input*. Leave it alone. |
| `report_spec_parser.py` | Markdown (Dr.Egeria RSD) → `FormatSet`/`Column`/`ActionParameter`. Parse, not pack — but `register_report_spec()` (line 212) mutates pyegeria's **process-global** `base_report_formats.report_specs`, found by walking `sys.modules`, and `report_spec_docs.list_inbox()` re-registers every visible spec on every listing. | Resolver input, untouched — but see §4: it is already a last-write-wins global shared across users. |
| `db_consolidated.py` | Self-provisioning DDL for ~11 tables in EA's `public` schema, including `advisor_sessions`. | Where a compiled-context table would go, if one is ever wanted. See §6. |
| `cache/` directory | `doc_sections.json` (21 MB), `metadata.json`, `cli_commands.json` — file timestamps **May and July 2026**. | An *ingestion-pipeline* artifact, not a query-time cache. Not part of this design; flagged in §8 because its name invites the wrong assumption. |
| `collection_router.py`, `feedback_collector.py` | Scoring engine and perspective-keyed feedback. | Already identified in `context-compilation-design.md` §5 as the packer's weight function and its tuning signal. Unchanged by this document. |

Whether `build_context()` is replaced by the shared packer or left in place for plain RAG answers
was the single largest scoping call in this document. Settled 2026-09-06: it stays out
permanently, and `build_context()` is fixed rather than deleted — see §9.

---

## 3. What is worth compiling in EA — and what is not

The sorting criterion is the one RE already paid for: **a section is worth compiling when several
heterogeneous sources have to be fitted into one bounded prompt, and the reader needs to know what
was left out.** Where there is one source, or no budget pressure, or no reader who acts on the
omission, compiling adds a manifest nobody reads and a spec nobody edits.

Applied to EA's actual surfaces (routes enumerated from `advisor/web/app.py`, 74 handlers):

### Compile these three

**1. Plan authoring — `GovernancePlanAgent` / `PlanElicitor` (LGCI).**
The strongest case, and it is not close. Decomposing an intent into commands currently draws on
the action catalog (55 actions), the keyword index (~126 commands), the template filesystem, the
validator's rules, `EgeriaContext`'s live lookups (actor profiles, governance zones, project
existence), and the draft's own history. That is six heterogeneous sources competing for one
prompt, assembled ad hoc across `_decompose_intent` and `_entities_to_commands`. It is exactly
the "context-assembly logic inside specialist agents" that `context-compilation-design.md` §18
names as *the one thing to stop investing in now*. It also has real gaps a user must see: an
`EgeriaContext` lookup that could not reach Egeria is not the same as a zone that does not exist,
and today both render as absence.

**2. Report-spec building — `ReportSpecElicitor` / `ReportSpecAgent`.**
Second-strongest, for a different reason: the report spec **is itself a declarative spec with a
three-category parameter model** (`content_filters` / `shape_defaults` / `performance_hints`, EA
design rule D). It already has the identity-vs-tuning partition that `ContextSpec.identity()`
implements, and the design doc §5 credits EA's "part of spec identity?" test as the source of that
discipline. The compile target here is the context handed to the elicitor while it helps a user
build a spec — target type, available columns, the validated client methods (rule C), what a
similar existing spec looks like. Multiple sources, bounded prompt, and a user who needs to know
when a column list is partial.

**3. `CodeIntelAgent` ("Inspect") — structural codebase facts.**
Reads `code_symbols` / `code_relationships` by live SQL, and separately EA already reads RE's own
tables cross-schema (`advisor/re_code_symbol_reader.py`). Two stores, one answer, and a query like
"what implements this interface" can legitimately return a very large result set that must be
bounded. This is where `Rung` earns its keep: IDENTIFIERS (names only) → SUMMARY (signatures) →
FULL (bodies) is a natural, free ladder over symbol data, exactly as `_findings_to_rungs` is free
over RE's findings.

### Do not compile these four

**4. Plain RAG Q&A (`/api/query` → `DocAgent`, `ExamplesAgent`).**
One source (the 9 collections), already ranked by score, already budgeted. The packer's
guarantees buy nothing here: there are no sections to order, no groups to keep symmetric, and no
gap a manifest could name that "min_score 0.30 returned nothing" does not already say. Compiling
this would mean building a spec with one section — ceremony, not structure. *(But see §6: the
`"No relevant code found."` string is a bug independent of this decision.)*

**5. Report *execution* — `ReportPipeline` / `run_report` via MCP.**
The output is a table produced by pyegeria against live Egeria. It is not model context; nothing
is fitted into a budget. Bounding it is a pagination problem, not a compilation one.

**6. Dr.Egeria action execution — `DrEgeriaActionAgent`.**
Imperative and single-run. Its inputs are a validated command list, not evidence. It writes to
Egeria; a compiled context has no role in a write path, and inserting one would put a lossy,
budget-truncated artifact upstream of a mutation.

**7. The reports catalog, drafts list, plans list, auth and admin routes.**
CRUD over the filesystem and Postgres. No LLM, no budget, no evidence.

**Net: three of EA's seven surface families.** That ratio is the point. Everything the compiler
does costs a spec that must be authored, versioned and kept honest (`context-compilation-design.md`
§19: authored source → generated artifact → validator). Three specs is a maintainable set; seven
is a second catalog to keep in sync with the first.

---

## 4. The multi-user question

This is where the framing that prompted this document needs correcting before it can be answered.

**Grounded facts.** EA acquired identity on 2026-09-04. Egeria is the identity provider; there is
no local user table. The app JWT carries the user's Egeria bearer token, never a password
(`advisor/auth.py`, `docs/trellis-auth-extraction.md` §6). Identity reaches deep call frames
through a `contextvars.ContextVar` set once per request by `_user_context_middleware`
(`advisor/request_context.py`). And then per-user state splits two ways:

- **Sessions are Postgres-backed** — `advisor_sessions` in EA's `public` schema
  (`db_consolidated.py:155`), `user_id` + `session_id` + `state JSONB` + `expires_at` pinned to the
  owning JWT's expiry. `query_metrics` also gained a `user_id` column.
- **Drafts and plans are *not* Postgres-backed.** `DraftManager` writes JSON to
  `~/egeria-plans/users/{user_id}/drafts/` (`advisor/governance_draft.py:71–120`), namespaced by
  *directory*, with a shared unnamespaced root as the anonymous fallback. Ownership is enforced by
  which manager instance a request gets, not by a `WHERE user_id = ...`.

So EA is multi-user in identity and in session, and multi-*tenant-by-filesystem* in its work
objects. Any design that assumes a single Postgres row-level ownership check will be wrong at the
draft layer.

**And visibility is three-tier, not two.** This is the part most likely to be missed, and it is
already implemented:

1. **Shared namespace** — the un-namespaced `~/egeria-plans/drafts/` root, visible to everyone
   including anonymous (`governance_draft.py:362`: `if owner is None: return True`).
2. **Per-user namespace** — `users/{user_id}/`, resolved through `resolve_draft()` /
   `list_visible_drafts()` (`governance_draft.py:421–459`) and the document equivalents
   (`governance_docs.py:355–364`, `load(..., enforce_ownership=True)`).
3. **Curator override** — `_CURATOR_ROLES = {"admin", "curator"}` (`governance_draft.py:92`,
   mirrored in `governance_docs.py:111`) sees *every* user's namespace.

Ownership failures return **404, not 403**, deliberately, so ids cannot be enumerated
(`session_state.get_for_owner()`, `advisor/session_state.py:96–110`). Any compiled-context surface
must preserve that: a gap for a resource the caller may not see reads "not compiled," never "you
are not allowed to see this."

### The answer: hybrid, split on the *source*, not on the surface

**Compiled context in EA is per-user when any of its sections resolve through the requesting
user's Egeria token, and shared otherwise.** The determining question is not "which page is
this?" but "did producing this candidate require the caller's identity?"

- **Shared (safe to cache globally):** the 9 vector collections, the action catalog, the command
  keyword index, the Dr.Egeria template filesystem, `code_symbols`/`code_relationships`. These are
  properties of the corpus and the installation. Every user with an account sees the same thing.
  This is why EA's existing user-blind `QueryCache` is correct *today* — it caches retrieval over
  exactly this material.
- **Per-user (must be scoped):** anything through `EgeriaContext`, `apply_token`, or
  `require_egeria_user` — actor profiles, governance zones, project/glossary existence, live
  element lookups. Also the user's own drafts, plans and session state.

**The correctness hazard, stated plainly.** Egeria authorises reads per user. A compiled context
whose Egeria-sourced sections are cached under a key that omits the user id will serve user B a
section assembled from elements user A was permitted to see and B is not. It will not error, it
will not look wrong, and it will read as a confident answer — the exact failure shape this repo's
absence-as-answer convention exists to prevent, arriving through the cache rather than through a
null. Getting this wrong is a data-disclosure bug, not a performance bug, and it is *latent today*
only because nothing user-scoped is cached yet.

**A curator's compile is not a shared compile.** The tier-3 override means the same spec, over the
same inputs, legitimately produces *more* content for a curator than for anyone else. A scope key
of `user:{user_id}` handles this correctly by accident; a scope key of "is this identity-scoped
at all" does not. The scope must carry the identity, not merely the fact of one — otherwise the
first curator to compile poisons the shared entry for every ordinary user. This is the single
easiest way to get §4 wrong while believing it has been handled.

**There is already one cross-user global in this area, and it is not ours to fix here.**
`register_report_spec()` mutates a process-global pyegeria registry, last-write-wins, and
`list_inbox()` re-registers every *visible* spec on every listing — so which specs are registered
depends on who listed most recently. A report-spec compile (§3 item 2) reads that registry. This
design does not create the problem and does not solve it, but a compiled context keyed as "shared"
while reading a registry whose contents depend on the last caller would *cache* the problem, which
is worse than having it. Report-spec compiles are therefore per-user regardless of source, until
that registry is scoped. Ruled 2026-09-06: ship the per-user compile, log the registry as a
pyegeria issue rather than fixing it first — see §9.

**Why not per-user for everything.** Because the shared material is the expensive material.
Per-user keying of corpus-derived candidates multiplies the store by the user count while every
entry holds identical bytes, and it destroys the hit rate that makes caching worth having at all.

**Why not shared for everything.** The paragraph above.

**The mechanism already exists and needs no new code.** `trellis_querycache.QueryCache.key()`
folds `scope` into the key, and `invalidate_scope()` drops a namespace wholesale. A compiled
context caches under `scope = f"user:{user_id}"` when it contains any per-user section and under a
corpus-version scope otherwise. `advisor/request_context.py::current_user_id()` supplies the id
without threading an argument through the call graph.

**The rule that keeps it honest:** a `Candidate` must declare whether its resolver used the
caller's identity, and a `PackedContext` containing any such candidate is per-user. Derived, not
declared per-surface — because *which* surface is user-scoped will change, and the derivation
cannot drift the way a hand-maintained list can. This is the same lesson as `availability`, one
turn earlier: make the safety-bearing property its own field rather than inferring it from a
correlate.

---

## 5. Reuse and divergence from `trellis_context`

`trellis_context` has RE as a live consumer. Anything below that changes it is flagged.

### Usable unchanged — most of it

`ContextSpec`, `Section`, `Candidate`, `Pointer`, `Manifest`, `pack()`. Nothing in them is
RE-shaped: `pack()` takes a spec and a dict of candidates and knows nothing about resources,
surveys, findings or Egeria. The `Rung` ladder comes from `trellis_artifact_tree.model`, which is
likewise generic. EA can import the package as it stands.

**`ContextSpec.identity()` is usable unchanged and is the right cache key** — it already excludes
`metadata` as annotation and includes `target_model`, which matters more in EA than in RE because
EA genuinely runs three models (`llama3.1:8b`, `qwen2.5-coder:32b`, `codellama:13b`) and a
different window packs differently.

### Needs a new abstraction — in EA, not in the shared package

**A resolver registry.** RE's `context_compile.py` hardcodes its resolvers against RE's analysis
catalog. EA needs its own bridge module (`advisor/context_compile.py`) resolving EA's sources.
This is per-app by design and should stay so — `trellis-architecture.md` records that the two apps
are "independently deployable services" and that runtime coupling is out of scope.

**An identity dimension on the candidate.** §4's rule needs somewhere to live. This is the one
change to the shared package this design actually proposes.

### The one change to `trellis_context` — and it has a live consumer

Add an optional field to `Candidate`:

```python
identity_scoped: bool = False   # resolver used the caller's identity
```

and surface the derived aggregate on `Manifest`. Purely additive, defaults to today's behaviour,
and RE's existing candidates keep working untouched — but it is still a change to a package RE
depends on, so it lands with RE's tests green and is worth its own small commit rather than riding
in with EA work. **Do not** put user ids or tokens in the spec, the candidate or the manifest; the
flag says *that* identity was used, never *whose*, so a manifest stays safe to log.

### Should anything move from RE into the shared package?

Two candidates, and the honest answer differs.

**Yes: the gap-judging vocabulary.** `context_compile.py::_judge_gap` maps a section with no
content onto `facts.FactLayer`'s `measured` / `nothing_found` / `not_established` / `never_run` /
`partial` plus `can_run`. That vocabulary is not RE-specific — it is the workspace-wide
absence-is-a-first-class-result principle (`trellis-architecture.md`) given a type. EA needs the
identical distinction the moment it compiles anything (§6), and the alternative is EA inventing a
parallel vocabulary, which is precisely the mistake `context-compilation-architecture.md` §4 says
was avoided last time by borrowing rather than inventing. Move the *vocabulary* (an enum and the
manifest's gap shape); leave the RE-specific *judging* behind, since it reads RE's tables.

**No: the rung-building helpers.** `_findings_to_rungs` and `_results_to_rungs` look generic and
are not — they encode RE's findings shape and the hard-won lesson (`architecture.md` §5) that most
analyses store through their own readers rather than the generic findings table. Sharing them
would export an RE data model wearing a generic name.

---

## 6. Invalidation, staleness, and the three states a reader must be able to tell apart

The convention here is not a preference. `trellis-architecture.md` states it workspace-wide:
*both apps must distinguish measured zero from never looked*, because a confident wrong answer is
worse than an error — nothing looks broken.

**Three states, and every EA surface that compiles must render them distinctly:**

| State | Means | Must render as |
|---|---|---|
| **compiled-and-current** | Packed from inputs at or after their last change. | The content, with its `as_of`. |
| **compiled-and-stale** | Packed from inputs known to have changed since. | The content, *labelled stale*, with what changed and when. Never silently. |
| **never-compiled** | No spec has been run for this, or a resolver produced nothing. | An explicit "not compiled" / "has not run", carrying `can_run`. |

The failure this prevents is specific and is already live in EA: `build_context()` returns the
string `"No relevant code found."` for an empty result set. That single string covers *retrieval
found nothing above `min_score`*, *the collection is empty*, *the collection was never ingested*,
and *the vector store was unreachable*. Four different situations, one confident sentence, and the
model downstream cannot tell them apart either. Fixing that is worth doing **whether or not** any
of §3's three surfaces is ever compiled, and it is the cheapest possible demonstration of the
principle — which is why it is step 0 in §7.

**How invalidation actually works here.** Since nothing persists a compiled context today (§1),
the default position is the one RE already has and it is a good one: **compile fresh, cache
nothing.** Staleness then cannot exist, because there is no stored artifact to go stale. Adopt
that first.

If and when measurement shows a compile is too slow to do per request, the mechanism is:

- **Key** on `ContextSpec.identity()` + the resolved input versions + the scope from §4.
- **Scope-invalidate** rather than TTL-expire. `invalidate_scope()` on ingest of a collection, on
  a catalog change, on a template-filesystem change, on a draft write. EA's `ingest_log` and
  `index_metadata` tables already record when a collection was last ingested, so the corpus-version
  scope has a source.
- **Never let a TTL be the only freshness story.** EA's existing `QueryCache` runs with
  `ttl_seconds=None` deliberately (it caches over a corpus that changes only on re-ingest); a
  compiled context over live Egeria has no such excuse.
- **Store the manifest with the context, always.** A cached context without its manifest cannot
  answer "is this stale", "what was dropped", or "what was never there" — the three questions a
  reader has. `context-compilation-usage.md` makes the same point about the API response.

**Decision (project owner, 2026-08-30, carried forward):** `availability` is declared, not derived
— unset means `queued`. EA inherits this. An EA resolver that reaches live Egeria, clones a repo,
or runs an agent is `queued`; only reads of already-materialized state are `inline`. Guessing
cheap remains the dangerous direction, and EA has more live-source resolvers than RE does, so the
default matters more here.

---

## 7. Sequencing

Each step is chosen to *prove something* rather than to deliver a slice. The risk concentrates in
step 3 and everything before it exists to de-risk that.

**Step 0 — make absence visible in EA's existing RAG path. No compiler involved.**
Replace `"No relevant code found."` with a structured result distinguishing the four cases in §6,
and surface it in the UI. *Proves:* the vocabulary from §5 transfers to EA at all, on the surface
with the most traffic, with no new dependency. *Risk:* almost none. *Value even if the rest is
never built:* high — this is a live confident-wrong-answer today.

**Step 1 — the identity flag, in `trellis_context`.**
Add `Candidate.identity_scoped` and the derived aggregate on `Manifest`, with RE's suite green.
*Proves:* the shared package can take an additive change without disturbing its live consumer.
*Risk:* low, but it is the only step that touches RE's dependency, so it goes early and alone
rather than buried in EA work.

**Step 2 — compile one EA surface, read-only, no cache: `CodeIntelAgent`.**
Chosen over the plan agent deliberately. Its sources are two SQL tables (one of them RE's, already
read cross-schema), it is entirely shared-scope so §4's hazard cannot bite yet, and its rung ladder
(names → signatures → bodies) is free. *Proves:* an EA bridge module, an EA `ContextSpec`, and the
manifest rendering, on the surface where the least can go wrong.

**Step 3 — compile plan authoring. This is where the risk is.**
Six sources, live Egeria among them, per-user drafts, and an existing assembly path that works.
This is also where `availability` stops being declarative (§1): EA's resolvers must each declare
`inline` or `queued`, and the bridge must read the field rather than infer it from cost — the
`EgeriaContext` lookups are the first resolvers in either app where an inline branch genuinely
exists, so the field acquires its first reader here.
*Proves:* §4's per-user rule and §6's staleness rendering under real conditions. *Why the risk
concentrates here:* it is the first compile whose candidates are identity-scoped, the first that
must not regress a shipped feature, and the one where a wrong scope key is a disclosure bug rather
than a stale render. Do it after step 2 has established the plumbing, not as the first compile.

**Step 4 — report-spec building.** Straightforward once step 3's per-user handling exists.

**Step 5 — caching, only if measured to be needed.** Not before. §6's default is compile-fresh,
and the cache is the component that introduces staleness as a concept.

**Deliberately not sequenced:** replacing `build_context()` for plain RAG (§3 says don't), and
collapsing specialist agents into resolvers — `context-compilation-design.md` §18 already defers
that, one at a time and measured.

---

## 8. Non-goals

- **Not compiling every EA surface.** §3 names four that should stay as they are, with reasons.
- **Not replacing EA's RAG retrieval, its collection router, or its scoring.** The packer sits
  downstream of ranking, not inside it.
- **Not unifying EA's and RE's vocabularies.** `trellis-architecture.md` records that aligning
  intent/perspective vocabularies is "a real design question, not a refactor," and one attempt has
  already been reverted. Nothing here depends on it.
- **Not coupling the two apps at runtime.** Same source, same Postgres, separate processes. The
  one cross-app read that exists (`re_code_symbol_reader.py`) already does so read-only over the
  shared instance and is unchanged.
- **Not migrating EA's drafts into Postgres.** The filesystem namespacing works and is orthogonal.
  This design reads drafts; it does not rehome them.
- **Not adopting RE's Investigation, and not introducing a compile container at all.** Resolved
  2026-09-06 (§9): the draft/plan/spec is the unit. This was listed as a non-goal while the
  question was open; the ruling makes it a decision rather than an omission.
- **Not touching EA's `cache/` directory.** Despite the name, it is ingestion-pipeline output.

### Corrections to existing documents

Recorded here rather than by editing those files, since several sessions are live in this checkout.

1. **`context-compilation-design.md` §9 and §17 are now stale on the same fact.** Both rest on "EA
   has *no backend-owned session concept at all today*, with `draft_id` generation unscoped by user
   or session." Since 2026-09-04 EA has an Egeria-backed identity, a `contextvars` request
   identity, a Postgres `advisor_sessions` table with `user_id`/`expires_at`, and per-user draft
   namespacing. §17's argument — that EA should adopt RE's Investigation *rather than grow a
   parallel session concept* — was overtaken by events: EA grew one. Whether Investigation is still
   the right compile unit is settled in §9 (it is the draft/plan/spec, not a container),
   but the premise behind §17's argument no longer holds either way.
2. **EA's `CLAUDE.md` query-flow diagram places `QueryCache` inside `RAGSystem`**, checked before
   the query processor. In the code the cache is constructed and consulted in
   `advisor/rag_retrieval.py` (lines 62 and ~108) at the *retrieval* layer.
   `grep get_query_cache advisor/` finds no reference in `rag_system.py` at all. The distinction
   matters for this design: the cache holds retrieval results over the shared corpus, which is
   what makes its missing user dimension currently safe (§4).
3. **EA's `CLAUDE.md` "Current state (July 2026)"** predates login, sessions, and per-user drafts
   by two months.
4. **The framing that prompted this document** described EA as having "Postgres-backed per-user
   sessions/drafts/plans." Sessions yes; drafts and plans no — they are per-user *directories*
   under `~/egeria-plans/users/{user_id}/`. §4 depends on the difference.
5. **RE's CLAUDE.md rule 17 and `context-compilation-design.md` §20 overstate `availability`.**
   Both describe it as governing whether a compiler runs an analysis inline. Nothing reads the
   field (§1). The ruling that separated it from `run_time` was right and the field should stay;
   the docs describe a mechanism that has not been wired up yet. `context_compile.py`'s own module
   docstring (line 12) makes the same overstatement.
6. **Two EA gaps noticed in passing, neither caused by this design and neither fixed here.**
   `GET /api/sessions` and `GET /api/sessions/{id}` (`web/app.py:1894–1909`) perform no ownership
   check, unlike every plan and draft route. And `session_logger.py` writes transcripts to an
   un-namespaced `~/egeria-plans/sessions/` with the user field taken from the `$USER` environment
   variable rather than the JWT. Both are worth their own tickets.

---

## 9. Decisions

Ruled on 2026-09-06. These were the open questions; the reasoning that led to each is kept so a
later reader can see what the ruling rested on, and re-open it if the ground moves.

**Decision (project owner, 2026-09-06):** Plain RAG Q&A stays outside the compiler
**permanently** — one source, one section, and nothing a manifest could say that "min_score 0.30
returned nothing" does not. Revisit only on a named trigger: **a RAG answer that mixes a second
source.** Separately, and against how this question was originally framed, `build_context()` is
**not** deleted. It is the right packer for the one-source case; fixing it (§7 step 0) and keeping
it is the outcome, and deletion would only follow if the compiler subsumed plain RAG, which it
does not.

**Decision (project owner, 2026-09-06):** EA has **no compile unit** — the existing
draft/plan/spec *is* the unit, and no container is introduced. All three options as posed were
rejected, on grounds that are facts rather than preferences: EA has zero references to RE's
Investigation, so there is no write path to build on, and adding one is the cross-app runtime
coupling §8 lists as a non-goal; `advisor_sessions.session_id` expires with the JWT that made it
(1–8 h) while a plan draft is a durable file under `~/egeria-plans/users/{user_id}/`, so the
lifetimes do not match. The work object is already durable, per-user and ownership-checked. This
makes §17's unresolved sub-question about EA writing RE's Investigation table **moot rather than
answered**: EA never touches it.

**Decision (project owner, 2026-09-06):** Compiled context is **not persisted**, and the rule is
sharper than a retention policy: **cache the shared sections, never persist the identity-scoped
ones.** That falls straight out of §4's split — the corpus-derived material is both the expensive
part and the safely-cacheable part, while a context assembled under one user's Egeria
authorization is a disclosure surface whose caching buys nothing, since the identity-scoped
sections are the cheap ones. With that rule there is no retention question to answer.

**Decision (project owner, 2026-09-06):** An EA `ContextSpec` pins a **model slot**, not a model
and not a caller-supplied value — again rejecting both options as posed. EA already resolves
models per slot (`query`, `conversation`, `code`, `maintenance`, `planning`) through
`resolve_llm_tier_config()`, which resolves `num_ctx` and the RAG budget for the active tier at
the same time. Pinning a model would break the tier system, since `dev` / `demo-gpu` / `demo-cpu`
resolve different models per slot; a caller-supplied model makes the cache key unstable. So: the
spec pins the slot, the tier resolves the model and the budget at compile time, and
`ContextSpec.identity()` folds in the **resolved** model — one spec serves the whole roster while
the key stays correct, because a different tier genuinely packs differently.

**Decision (project owner, 2026-09-06):** EA's `ContextSpec`s are authored as **Dr.Egeria
markdown, alongside the report specs**, not as a spreadsheet alongside RE's question matrix. A
`ContextSpec` is the same kind of object as a report spec — declarative, carrying the
identity-vs-tuning partition — rather than the large enumerable dataset that makes a spreadsheet
right for RE's questions. Consistency within EA wins over consistency across the two apps here,
and markdown leaves open the spec living in Egeria as a governance artifact, which a spreadsheet
would close.

**Decision (project owner, 2026-09-06):** pyegeria's process-global report-spec registry is
**not fixed first**. The per-user report-spec compile ships as designed; the registry is logged as
a pyegeria issue and revisited when a fix lands, per this workspace's standing rule that pyegeria
gaps are recorded in `PYEGERIA_ISSUES.md` rather than fixed in place. Blocking an EA feature on an
upstream fix in a dependency is the wrong direction, and per-user scoping is correct on its own
merits rather than only as a workaround.

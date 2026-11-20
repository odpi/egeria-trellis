# Context compilation in Trellis — what was built

> **Status (2026-11-19, 6.3 Tech Preview):** describes what shipped as of 2026-08-29 and is still the best account of the context-compilation packages (`trellis-artifact-tree`, `trellis-context`, RE's bridge `context_compile.py`; all present in 6.3). Not extended to Egeria Advisor: EA does not import `trellis-context` (see [ea-context-compilation-design.md](ea-context-compilation-design.md)). The ten-library map is in [trellis-architecture.md](trellis-architecture.md).

**Status: describes shipped code as of 2026-08-29.** The design that preceded it
is `context-compilation-design.md`; where the two disagree, this one is what
exists. Several of the design's claims were falsified during construction and
are marked below, because the corrections are the useful part.

## The shape

Four packages and one bridge, in dependency order:

```
trellis-artifact-tree     containment trees over ingested artifacts,
                          and a profiler that measures them
        │
        ├── trellis-context        ContextSpec + a deterministic packer
        │           │
        │           └── resource_explorer.context_compile
        │                   resolves RE's stored analysis results into
        │                   candidates, packs them, judges what is missing
        │                            │
        │                            ├── the conversation agent's prompt
        │                            └── POST /api/context/compile → Evidence pane
        │
        └── trellis-vectorstore     (pre-existing; chunks and embeddings)
```

Nothing above depends on an LLM client, a store, or credentials. The tree package
and the context package are both pure over their inputs; only the RE bridge
touches a database.

## 1. The containment tree

**One parse, two consumers.** Chunk size is a tuned parameter driven by content
profile; rung boundaries are a containment question. Neither derives from the
other, and code written against "chunks" alone cannot express a compression
ladder. So an adapter parses once and emits a tree: retrieval chunks are its
leaves, compression rungs are cuts across it at different depths.

**The tree is the format-independence boundary.** Markdown, HTML, source code and
PDF all arrive as the same shape, so everything downstream is written against the
tree and never against a format. A new format costs an adapter, not a pipeline
change.

Five adapters ship. Markdown and HTML need only the standard library; code
(tree-sitter) and PDF (Docling) sit behind `[code]` and `[pdf]` extras; a generic
text fallback catches everything else. **Absence of an adapter degrades, never
blocks** — an unrecognised artifact is ingested flat and improved later by
registering an adapter, with no migration.

Adapters live in the shared package rather than in the apps. An earlier rule put
them "in the app that already depends on the library" and was vacuous: both apps
already depend on both, so it named both and would have duplicated the parser.

### What the tree carries

`Provenance` per artifact — `source_kind`, `source_id`, `fetched_at`,
`source_version`, `source_timestamp`, `extraction_fidelity`. **`fetched_at` and
`source_timestamp` are separate on purpose**: when the fact was true and when we
read it are different, and collapsing them loses the ability to tell an old fact
from a stale read.

`extraction_fidelity` is `structural` when an adapter understood the format and
`inferred` when it was reconstructed — a PDF states layout, not structure, so
Docling's hierarchy is inferred and a reader deserves to know.

### Diagnostics

A tree can parse cleanly and say nothing useful, and from outside that is
indistinguishable from a short document. Two signals, deliberately separate:

- **near-empty** — almost no text; a rasterised page or scan; OCR would help
- **structureless** — text but no containment; not a failure, but a packer
  cannot cut it by depth

The threshold is measured, not chosen: across 18 real PDFs the lowest content was
1,196 characters, so near-empty sits an order of magnitude below at 200. It fires
only for fidelities where OCR could help — across all 15,983 artifacts, 20% are
near-empty and almost all are short markdown stubs, so reporting them all would
bury the case that matters.

## 2. The profiler

Measures what chunk size a slice of corpus actually wants, replacing two
hand-maintained tunings that cannot re-derive themselves.

**Pick the unit first, then size to it.** The rule went through three versions and
each revision came from measurement:

| version | rule | falsified by |
|---|---|---|
| v1 | p75 of unit (section) size | 340/164/356 against EA's 768/1024/1536 — wrong magnitude *and* ordering |
| v2 | p75 of document size | fits egeria-docs within 20%, fails on code: pyegeria's p75 document is 32,467 tokens, a module |
| v3 | units-per-document decides which unit is coherent, then size to that | current |

This also explains a disagreement neither app documents: **RE's constants track
p75 unit size** (`markdown_docs`=384 vs a measured 340), **EA's track p75 document
size** (768 vs 905). Neither is wrong — they chunk different things because they
answer different retrieval questions. A profiler with one rule for both would
silently break whichever app it did not resemble.

Overlap is derived as a ratio, not a separate decision: EA's three collections are
all 19.5%, so it was never independently tuned there either. `min_score` and
`top_k` are deliberately *not* derived — they are properties of measured retrieval
quality, not of computed content shape, so the profiler sets a prior and the
feedback loop should tune the posterior.

## 3. The packer

Turns a `ContextSpec` and resolved candidates into a bounded context plus a
manifest. **Ordinary code, and that is the design** — determinism, monotonicity,
symmetric packing and the hard ceiling are all unavailable the moment a model
decides what to include.

| guarantee | meaning |
|---|---|
| determinism | same spec + same inputs → byte-identical output |
| monotonicity | more budget never *removes* content |
| symmetry | grouped sections pack at equal budget and the same rung |
| hard ceiling | the budget is never exceeded; it fails rather than truncating |

The ceiling is load-bearing for the rest: silent truncation at the window boundary
defeats every promise above it.

**Breadth before fidelity.** Every section is admitted at its cheapest acceptable
rung before any is upgraded. A proportional per-section allocation was tried first
and is *not monotone* — with a larger budget a heavy section upgrades to FULL,
eats the headroom a lighter one occupied, and the lighter one disappears. So
weight governs *upgrade priority* rather than a fixed share, which is also the
better reading of "Perspective sets budget weights": a weight says what to spend
spare capacity on, not what to exclude.

Upgrades step one rung per round rather than jumping to the richest, so a
weight-1 section can overtake a weight-3 one mid-climb. That is deliberate:
**Perspective weighting is unmeasured** — the design establishes only that its
strictly-nested sets make it *safe*, not that it helps — and a policy that cannot
starve a section is the right default under that uncertainty.

Two distinctions are typed fields rather than conventions, because both had failed
silently before: `mode` (rank vs gate) and `floor` (the coarsest rung before a
section is dropped instead — less is not always better than nothing).

## 4. The RE bridge

`context_compile.compile_context` closes the loop the question catalog opened.
`get_questions()` already resolved Purpose + Perspective to questions and to the
`analysis_ids` answering them, and returned that chain as a **derivation** — those
ids become the spec's sections, RE's stored analysis results become the
candidates, and the packer decides what fits.

**Nothing is run.** Only stored results are read, so a compile never blocks on a
survey. Candidates come from findings where they exist and from each analysis's
own results reader otherwise — the same readers the UI renders from.

**Gaps are judged, not listed.** A section with nothing to pack is asked about:
`facts.FactLayer` already carries `measured` / `nothing_found` / `not_established`
/ `never_run` / `partial`, and `can_run` — the steps that would change the answer.
So the prompt distinguishes *ran and found nothing* from *has not run*, which are
the same number and opposite answers. That vocabulary was borrowed rather than
invented; a parallel one is how four retired RE perspectives ended up beside
Egeria's twelve.

## 5. Two claims that were false

Both were caught by running, not reading, and both are the same shape — **a true
statement about the mechanism read as a claim about the world.**

The resolver read only the generic findings table while most analyses store
through their own readers, so seven analyses were reported as gaps while holding
data. The manifest's own reason string — "no candidate, resolver produced nothing
yet" — was accurate throughout; calling that set "gaps" is what turned it into a
claim about the catalogue.

The prompt then asserted those analyses "have NOT run". Two of three run cleanly
and emit annotations explaining the empty result, and re-running cannot change
them because `project_dependencies` is written only by `IngestionPipeline` and by
no survey step.

## Where the design was wrong

- **§3's chunk rule**, twice. See the profiler table above.
- **§15's chunking model.** It framed structure-for-rungs and windows-for-retrieval
  as competing strategies. They are different axes: chunk size is tuned by content
  profile, rung boundaries are containment, and one parse produces both.
- **§19's "EA needs its own question corpus".** All 41 RE questions are an exact
  subset of Egeria's 84 Question-classified terms. There is one corpus already.
- **§5's storage argument, applied too early.** The profiler's computation needs
  no storage, so it lives in the tree package; the shared-package question was
  deferred until something needs to persist a profile.

# The shared corpus profiler

> **Status (2026-11-19, 6.3 Tech Preview):** **partly built.** The profiler exists (`trellis_artifact_tree.profile`, run by `packages/resource-explorer/scripts/profile_corpus.py`), but nothing re-derives EA's or RE's chunk sizes from it at ingest; the tunings in "What it replaces" are still hand-set. The experiment in §7 is not recorded here.

**Status: design, unmeasured (2026-08-28).** Nothing here is built. §7 names the one
experiment that would falsify it, and that experiment should run before the code does.

## What it replaces

Two hand-maintained tunings, neither of which can re-derive itself:

- **Egeria Advisor** splits `egeria-docs` into path-bounded collections with per-collection RAG
  parameters: `egeria_concepts` (`site/docs/concepts`) at chunk 768 / overlap 150 / min_score 0.45
  / top_k 5; `egeria_types` (`site/docs/types`) at 1024 / 200 / 0.42 / 6; `egeria_general`
  (`site/docs` minus those) at 1536 / 300 / 0.38 / 8. Short definitions get small chunks and a high
  threshold; tutorials get large chunks and a low one. This is correct, and it was derived by a
  person reading the corpus. Nothing recomputes it when the corpus moves.
- **Resource Explorer** splits by *format* — `markdown_docs`, `python_code`, `pdfs` — with one
  fixed chunk size per format applied uniformly to every repo. All of `egeria-docs` lands in one
  `markdown_docs` collection at one chunk size, concepts and tutorials alike.

These are orthogonal axes and both are needed. Format decides **how to parse**; content character
decides **how to chunk and retrieve**. RE has the first, EA has the second by hand, neither has
both, and `docs/ingestion-pipeline-audit.md` item 3 ruled the collection *models* a confirmed
divergence — correctly, and that verdict left this uncovered.

## 1. The unit is a slice, not a repo and not a format

A **slice** is a `(repo, path boundary, format)` triple. `egeria-docs @ site/docs/concepts @
markdown` is a slice; `egeria-docs @ / @ markdown` is a coarser one over the same files. Slices
nest, and a profiler's first job is deciding where to cut.

This is the thing neither app can currently express. RE's collections are `(repo, format)` —
no path axis. EA's are `(repo, path, format)` but hand-declared, so only the boundaries someone
already thought of exist.

## 2. Profiles come from the containment tree, not a new pass

`trellis-artifact-tree` already computes almost every signal a profile needs, as a byproduct of
parsing (`docs/context-compilation-design.md` §15):

| signal | where it already exists |
|---|---|
| tokens per containment unit | `Node.rungs[FULL]` length, per node |
| unit-size distribution | across a slice's nodes |
| structural depth | tree depth |
| heading density | sections per document |
| units per document | children per root |
| code-to-prose ratio | node `kind` mix |
| extraction fidelity | `Provenance.extraction_fidelity` |

So profiling is a **query over materialised trees**, not a second walk over the corpus. That
matters for three reasons: it is cheap enough to re-run whenever ingestion runs; it cannot
disagree with what was actually ingested; and it inherits the tree's format independence, so a
PDF slice and a markdown slice profile the same way.

It also fixes an assumption `context-compilation-design.md` §15 made and did not check. That
section says chunk size is "a tuned parameter driven by artifact category and content profile."
The profile it assumed existed does not — outside EA's four hand-authored collections.

## 3. Recipes are derived, and mostly from one number

A **recipe** is the ingestion and retrieval parameters for a slice. Reading EA's three
collections as data rather than as choices:

| collection | chunk | overlap | overlap ÷ chunk | min_score | top_k |
|---|---|---|---|---|---|
| concepts | 768 | 150 | 0.195 | 0.45 | 5 |
| types | 1024 | 200 | 0.195 | 0.42 | 6 |
| general | 1536 | 300 | 0.195 | 0.38 | 8 |

**Overlap is not independently tuned — it is 19.5% of chunk size in all three.** So the profiler
does not derive four parameters. It derives *one*, and the rest follow:

- `chunk_size` ← **pick the unit first, then take a high percentile of that unit's size.**
  Revised twice, both times by measurement (§7). v1 said unit size; v2 said whole-document size;
  neither generalises, and the reason is that *which* unit is coherent varies by slice:

  | slice | units/doc | p75 unit | p75 doc |
  |---|---|---|---|
  | egeria-docs `concepts` | 1 | 340 | 905 |
  | egeria-python `pyegeria` (code) | 23 | 495 | 32,467 |
  | egeria-python `commands` (code) | 3 | 480 | 2,568 |
  | egeria-python `sample-data` (md) | 20 | 29 | 1,728 |

  **Units per document is the discriminator, and it is already in the tree.** Where it is ~1 the
  document *is* the unit (a concept page is one section), so size to the document. Where it is
  many, the unit is sub-document — a function, a template — and document size is meaningless:
  `pyegeria`'s 32,467-token p75 is not a chunk size, it is a module.

  This also explains why RE and EA disagree by 2× on the same corpus without either being wrong.
  **RE's constants track p75 unit size** (`markdown_docs`=384 vs measured 340; `python_code`=512 vs
  495 and 480). **EA's track p75 document size** (768 vs 905). They chunk different things because
  they answer different retrieval questions, and a profiler that picks one rule for both would
  silently break whichever app it did not resemble.

- `overlap` ← a fixed fraction of `chunk_size` (~20%), until something shows otherwise.
- `min_score`, `top_k` ← inversely related to chunk size in EA's data: short precise units want a
  high threshold and few results, long discursive ones the reverse.

**Retrieval parameters get a prior here, not an answer.** `min_score` and `top_k` are properties
of retrieval quality, which is measured, not of content shape, which is computed. The profiler
sets the prior; the feedback loop tunes the posterior (`context-compilation-design.md` §13, §22).
Presenting a derived `min_score` as authoritative would be the same overreach as EA's hand-picked
one, with more arithmetic.

## 4. Boundary discovery

Given a repo, which path boundaries deserve their own slice?

**Divergence, not clustering.** Walk directories; at each, compare the directory's profile against
its parent's. Where they differ materially, propose a boundary. `site/docs/concepts` separates
from `site/docs` because its unit-size distribution is visibly different — which is exactly why a
person split it by hand.

Chosen over similarity clustering because it is explainable: a proposal comes with the statistic
that produced it, and a person can disagree with a number. A clustering result cannot be argued
with, and this output is meant to be reviewed.

**Propose, never enable.** Same rule as `collection_drift.py`: a new slice changes what gets
embedded and costs real ingestion time, so the decision stays with a person.

## 5. Ownership

Same answer as the containment tree, for the same reason, and there is now a precedent to follow:
**a shared package both apps call**, its own Postgres schema, owned by neither.

Both apps ingest, so both would profile — a cross-schema *read* cannot serve a write path
(`docs/re-ea-consolidation-audit.md`'s third centralization pattern is read-only).

**Concurrency is the new part.** The tree is written per artifact by whoever parsed it. A profile
is an aggregate over many artifacts, so two apps profiling the same corpus at once can race.
Make profiles **content-addressed** — keyed on the slice plus the set of tree versions that fed
it — so concurrent runs converge on the same row instead of overwriting each other, and a stale
profile is detectable rather than merely old.

## 6. What "initiated by them if needed" means

Neither app should profile on a schedule of its own. The trigger is **ingestion completing for a
slice**: trees changed, so the profile that summarises them is stale. The app that ingested asks
the profiler to recompute; the profiler decides whether anything actually changed (content
addressing makes that cheap) and returns either a new profile or the existing one.

`collection_drift.py`, added 2026-08-27, is a degenerate profiler — it answers "does this repo
contain files of type X". Under this design it becomes one query over a profile rather than its
own module, and its precision problem (extension matching reporting 4,583 false positives) is the
same problem in miniature: eligibility is a content question, and it was being answered with
filenames.

## 7. The experiment that should run first

**Profile `egeria-docs` and see whether the derived chunk sizes land near EA's hand-picked
768 / 1024 / 1536.**

**Run 2026-08-28, against trees already in `artifact_tree` from RE's own ingest — no new code, no
re-parse.** Results:

| slice | docs | median sections/doc | p75 section | p75 whole-doc | EA chunk |
|---|---|---|---|---|---|
| concepts | 179 | 1 | 340 | 931 | 768 |
| types | 168 | 4 | 164 | 844 | 1024 |
| general | 562 | 3 | 356 | 1742 | 1536 |

Section size fails badly — wrong magnitude and wrong ordering (it makes `types` the smallest,
where EA gives it the middle value). Whole-document p75 lands within ~20% on all three. §3 has
been corrected accordingly.

Two things this bought that no amount of design would have:

- The hand-picked numbers were **not arbitrary** — they encode a real property of the corpus, and
  one a computation can recover.
- The signal a careful reader was using was **document size, not unit size** — the exact "missing
  signal" case this section was written to catch, caught on the first run.

**Second run, `egeria_python_git` — a corpus with no hand-tuned baseline to reverse-engineer.**
This is where whole-document size failed: it gives 32,467 tokens for `pyegeria`, which is a module,
not a chunk. The units-per-document discriminator above came out of that failure and is the version
now in §3. Notably `sample-data` (Dr.Egeria templates) has a p75 *unit* of **29 tokens** across 20
units per document — neither RE's 384 nor EA's 768 fits it, and it is the clearest case in either
corpus for a slice needing its own derived number rather than a shared constant.

The original framing of the three possible outcomes, kept because the reasoning still applies to
the next slice profiled:

- **They land close** — the derivation captures what a careful reader was doing, and the
  hand-tuning can be replaced with a computation.
- **They diverge, and the derived values are better under measurement** — EA's numbers were a
  reasonable guess and the profiler earns its place.
- **They diverge, and the derived values are worse** — the profile is missing a signal the reader
  was using. That is the most informative outcome and the reason to run this before building the
  rest.

Do this before the boundary discovery in §4, which is the largest piece of work here and rests
entirely on §3 being right.

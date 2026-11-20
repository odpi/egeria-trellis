# Ingestion pipeline audit: what's shareable between RE and EA

> **Status (2026-11-19, 6.3 Tech Preview):** a completed audit (August 2026), kept as the record of why RE and EA ingestion stayed separate. Its verdicts are not maintained.

## Why this doc exists

Resource Explorer (RE) and Egeria Advisor (EA) each run their own repo → parse → chunk →
embed → pgvector pipeline, built independently before the Trellis merge. It's tempting to
assume duplication and go straight to extracting a shared package — but the workspace already
has one clear counter-example: `trellis-vectorstore`'s extraction found that "every
constructor parameter traces to a confirmed, deliberate behavioral difference... not a guess"
(`docs/trellis-vectorstore-extraction.md`). This doc does the same audit for ingestion, one
module-pair at a time, before any code moves. Each pair gets one of three verdicts:

- **(a) identical/near-identical** — extract as-is
- **(b) diverged for a confirmed reason** — extract with parameters, like `PgVectorStore`
- **(c) genuinely different problems** — leave alone

## Precedent already in place: code/symbol intelligence

Before auditing what's still open, note what's already been resolved — this is the strongest
evidence for what a good outcome looks like here.

**Java (and other non-Python-language) structural symbol extraction has already moved from EA
to RE**, via what the code calls the "AST-ownership-transfer plan" (referenced across ~15
files; no standalone design doc under that name was found, only in-code docstrings — worth
writing one retroactively, see Follow-ups). Verified in code:

- `packages/egeria-advisor/advisor/data_prep/java_symbol_extractor.py` is marked `DEPRECATED`
  in its own docstring: "no longer called from anywhere in the ingestion pipeline... Resource
  Explorer now owns Java symbol extraction... a genuine upgrade: fixes a real bug this module
  had, where `node.child_by_field_name("modifiers")` silently returned `None`..." Kept in place
  only as a rollback safety net (decision D8), not wired into `CodeIngester` anymore.
- `packages/resource-explorer/resource_explorer/ingestion/java_symbol_extractor.py` is the
  live replacement — tree-sitter based, structurally ported from EA's old module but fixing
  the confirmed bugs, emitting RE's own `CodeSymbol` shape.
- EA doesn't re-implement a reader against a shared package — it queries RE's data directly:
  `advisor/re_code_symbol_reader.py` is "a thin reader over Resource Explorer's
  `resource_explorer.project_code_symbols` / `project_code_relationships` tables," matching
  `CodeSymbolStore`'s existing method shapes so `analytics.py`'s call sites don't change.
  `advisor/re_code_scope.py` holds the shared collection-name→project-slug scope resolution
  both readers reuse.

**This is a third centralization pattern**, distinct from `trellis-vectorstore` (shared
package) and `trellis-microflow` (shared primitive): **cross-schema read**, viable specifically
because both apps already sit on the same Postgres instance (`egeria_advisor` database, RE's
tables under the `resource_explorer` schema). No new package, no duplicated extraction logic —
EA just reads what RE already wrote. This is the pattern to reach for first when one app's data
is a strict superset/upstream of what the other needs, rather than defaulting to a shared
package.

## What's still open: module-by-module

### 1. Python structural/symbol extraction — candidate for the same pattern

| | RE | EA |
|---|---|---|
| Module | `ingestion/code_symbol_extractor.py` (Python via stdlib `ast`, per `docs/question-answering-and-context.md (§10)`) | `data_prep/code_parser.py` — `CodeParser`/`CodeElement` (357 lines; docstring, signature, decorators, params, return type, complexity, `is_async`, `is_private`, `bases`) |
| Storage | `project_code_symbols` (SQLite/Postgres registry) | `code_symbols` table via `CodeSymbolStore` |
| Status | Live, Phase 1–2 complete | **Still EA's own** — actively imported by `data_prep/pipeline.py`, not touched by the ownership-transfer plan |

**Verdict: (c) for now, but a strong candidate to extend the existing pattern rather than a
candidate for a new shared package.** The ownership-transfer plan visibly stopped at Java (and
presumably the other non-Python languages RE already owned) — Python wasn't moved, likely
because EA's Python extraction target (pyegeria) isn't one of RE's ingested projects yet, so
there was no RE-side data to point EA at. That's a scoping fact to confirm with whoever ran
that plan, not something to guess further on here — flagged as a Follow-up, not decided in
this pass.

### 2. Documentation parsing — genuinely different jobs, not duplicates

| | RE `ingestion/doc_parser.py` | EA `data_prep/doc_parser.py` |
|---|---|---|
| Size | 79 lines | 410 lines |
| Produces | `DocChunk` — fixed-size token windows (384/48 overlap) off heading boundaries, for embedding | `DocumentSection` — structural metadata per section: title, heading level, code blocks, links, images, parent/subsection tree |
| Purpose | Feed the vector store | Feed both the vector store *and* structural queries about doc content |

**Verdict: (c) — leave alone.** These aren't the same module written twice; RE's is a chunker,
EA's is closer to a structural extractor (the same "chunk vs. symbol" split as item 1, just for
docs instead of code). A real merge here would mean RE growing EA's structural-section
extraction, not deduplicating existing code — out of scope for an ingestion-pipeline audit.

### 3. Code chunking-for-embedding strategy — a real, confirmed divergence

| | RE | EA |
|---|---|---|
| Chunk unit | Fixed-size token windows with overlap, split on function/class boundaries where possible (`code_parser.py`, `CodeChunk`) | One embedding per semantic unit — one vector per `CodeElement`/`DocumentSection`, no windowing (`ingest.py`'s `DataIngester.ingest_code_elements`/`ingest_documentation`) |

**Verdict: (c) — leave alone, deliberately.** This is a real design difference in retrieval
strategy (windowed-chunk RAG vs. semantic-unit RAG), not accidental duplication. Neither app's
`CLAUDE.md` documents *why* each chose its strategy — worth a one-line note in each if anyone
revisits this, but not a reason to unify them now.

### 4. Embeddings device-select wrapper — already resolved, don't reopen

RE's `embeddings.py` (single function) and EA's `embeddings.py` (`EmbeddingGenerator` class)
duplicate the same MPS/CUDA/CPU auto-select logic — but `trellis_vectorstore/embeddings.py`
already exists as an `EmbeddingProvider` Protocol seam over both, with an explicit docstring
saying the implementations stay separate on purpose: RE lazy-imports module-level functions to
keep `torch`/`sentence-transformers` off its import path until needed; EA holds a stateful
object. **Verdict: (b), already done.** Nothing to extract further here.

### 5. Repo acquisition and orchestration — different problems, stay divergent

RE: GitHub zipball API, one call per project, no persistent clone, incremental re-index off
commit diffs, registry-integrated (`IngestionPipeline`, `ProjectRegistry`). EA: `git clone` into
`data/repos/`, persistent checkout, manually re-synced (`scripts/update_repos.sh`), phase1/phase2
JSON-cache pipeline (`data_prep/pipeline.py`), script-driven with no registry.

**Verdict: (c) — leave alone.** RE ingests an open-ended, user-registered set of arbitrary
repos/DBs/filesystems; EA ingests a small, fixed, hand-picked set of ~5 Egeria-ecosystem repos.
Fixed-catalog + persistent-clone is a reasonable fit for EA's shape; dynamic-catalog +
zipball-no-clone is a reasonable fit for RE's. Forcing one orchestrator on both is the
highest-risk, lowest-confidence move available here and shouldn't be attempted before EA work
resumes enough to know what EA's pipeline actually needs to become.

*Narrower correction (measured, not this pass's original claim):* within RE's own orchestrator,
sibling repos that share a host are genuinely re-doing the same work — see item 8. That's real
ingestion-pipeline duplication and belongs to this item's scope even though the RE-vs-EA
orchestrator split above stays as-is.

### 6. Coverage measurement itself has a trap — a real, concurrently-measured finding

A parallel session auditing `repo_arch_lens`'s website-ingestion trigger (`sub_surveyors/
arch_lens.py`, adding a `RequestForAction` for `repo_website_ingestion` when a doc site can't
be read) measured `website_ingestion` across all 60 registered repos while this audit was being
written, and it bears directly on how any future ingestion-coverage audit should be run:

- **`website_ingestion` writes metrics, not findings** — `findings: 0 of 60` vs. `metrics: 6 of
  60`. A findings-shaped coverage query says "never ran" for a step that *has* run six times.
  Any audit of ingestion coverage (this one included, if extended) must read the metrics table,
  not just `query_findings`.
- **`declined` is a correct outcome, not a gap.** Of the 6 runs: 2 ingested, 4 declined
  (`self_published`: the repo builds the site itself, so its source is already ingested in a
  better form — see `ingestion/site_discovery.py`'s design note on this exact case;
  `code_host`: the "site" is just a GitHub URL, nothing to ingest). Scoring declines as failures
  would be measuring the system being right.
- **Collection naming has drifted, and metrics vs. the vector store can disagree.**
  `list_collections()` shows both older-shaped names (`openlineage_web_docs`,
  `egeria_docs_web_docs`) and newer per-domain names (`web_docs_sqlglot_com`,
  `web_docs_openlineage_io`) — and for `openlineage` specifically, metrics say
  `ingested: False, reason: self_published` while `web_docs_openlineage_io` exists in the store.
  Not chased down yet (may be a stale run under the old naming), but it means **neither source
  alone tells you what's actually ingested** for a given project. `ingestion_status(registry,
  slug)` in `arch_lens.py` returns a four-state read (ingested/declined+reason/pending/error)
  but currently only consults the metrics table — if the store/metrics disagreement turns out
  to be systematic rather than a one-off stale run, that function is reading half the truth and
  should consult the vector store too before being treated as a complete answer.

This doesn't change the module-by-module verdicts above (items 1–5 are about duplicated
*extraction/chunking* code, not this trigger step), but it's a real, measured caution for
whoever next asks "how much of X is actually ingested?" against this pipeline.

### 7. Catalog identity — a since-fixed bug that changes what "ingested against what" means

A separate, since-fixed bug is worth recording here because it changes the baseline any
ingestion-coverage audit measures against: `_find_or_create_asset` matched on `qualifiedName`
with `starts_with=True` and took `existing[0]` — so `docling` matched against `docling_eval`'s
qualified name and adopted its Egeria asset GUID. Two distinct repos shared one catalog entry;
one repo's survey/ingestion results were attaching to the other's asset. This was live as of
this morning and has since been corrected across all 22 affected repos (each now holds its own
correct GUID). Not an ingestion-pipeline duplication issue (items 1–5 don't touch it), but any
historical ingestion data pulled from before the fix should be treated as measured against a
catalog that was, for some repos, genuinely wrong — not just incomplete.

### 8. Within-run duplicate ingestion across sibling repos — FIXED

Originally measured as: `egeria-project.org` ingested **three separate times in one run** — once
each for `egeria_git`, `egeria_python_git`, and `egeria_workspaces_git` — 6018 chunks, 187 pages,
~175 seconds, every time. RE's `web_docs` collections are host-keyed by design specifically so
sibling repos that share a doc site land in one destination collection — but nothing memoized
the *work*, only the destination.

**Status: fixed** (`sub_surveyors/website_ingestion.py`, same day). Numbers below are historical,
kept for the before/after:

```
before   egeria_git 79s · egeria_python_git 46s · egeria_workspaces_git 49s
after    0.4s · 0.2s · 0.2s
```

**The shape of the fix corrects this doc's original framing, and is worth recording as the
better answer.** This audit's recommendation called it "run-scoped memoization." That's not
what was built, and the person who fixed it explained why: `SurveyOrchestrator.run()` is
**per project** — there is no run spanning sibling repos to memoize within. The fix is instead
keyed on the **collection's own state** ("has this collection been ingested recently, with
content in it") — which also correctly dedupes siblings surveyed days apart, by the scheduler,
or one at a time from the UI, none of which a within-run cache would ever catch. **"Ingest-once
semantics on a shared collection" is the accurate name for this, and it's a strictly larger fix
than what this doc originally proposed.** Filed here as a correction to the audit's own
recommendation, not just a status update — the general lesson (worth carrying into any future
audit written before seeing the actual fix) is that "memoize the repeated work" defaults to
whatever scope the audit happened to be looking at (a run, here), when the right scope is
usually a property of the data (a collection's staleness), not the caller.

Two conditions the fix deliberately does **not** treat as "already done," both worth an audit
knowing are handled rather than assuming:
- **A run that stored nothing** — `milvus` had recorded a *completed* ingest with zero chunks
  after 400 failed fetches; skipping re-ingestion because a completed record exists would make
  that failure permanent.
- **Staleness** — a 24-hour constant, so a daily scheduled survey re-ingests once a day rather
  than once per sibling repo per day.

And the skip path still registers the collection on the repo being skipped — the query router
searches a repo's own collection list, so skipping the fetch without that registration would
leave a repo unable to search a site it points at, which would make the "saving" cost the thing
the ingest was for.

**Also measured, not yet chased down:** a bug where `detail["ingested"]` was hardcoded `True`
regardless of outcome — `milvus` recorded `ingested: True` after 400/400 sitemap-page fetches
failed and 685 seconds were spent, while the adjacent `StepOutcome` (`unverified`, from
`known_positive=bool(fetched)`) was correct the whole time. Since fixed (`ingested` now reads
`bool(chunks_added)`), by the same concurrent session — noted here because it means any
ingestion-coverage numbers pulled from before the fix over-report, the same caveat as item 7 but
for a different field. Current corpus snapshot after a 38-repo bulk run: 25 ingested, 20
`web_docs` collections; refusals split `self_published` (13) / `non_doc_host` (2) /
`unrelated_host` (1) / `code_host` (1) — the last two are new guards, one of which caught a real
bug (a homepage resolving to someone else's documentation site and pulling 3096 chunks of it
into the wrong collection, before the guard existed).

**Open question, deliberately not answered here:** ingestion currently fetches, chunks, and
embeds in one pass with no staging/decision point — the dead-site case above (8 unchecked
assumptions, one bad one costing 685s before anything noticed) is the concrete argument for
profiling/staging *before* deciding whether and how to ingest. Whether that's worth building is
a product/design call this audit doesn't have the standing to make on its own — recorded as a
Follow-up below rather than a verdict.

## Recommendation

1. **Extend the existing cross-schema-read pattern to Python symbol extraction** (item 1)
   once EA's Python ingestion targets (pyegeria etc.) are also registered as RE projects — ask
   whoever ran the AST-ownership-transfer plan whether that was the actual blocker, rather than
   re-deriving it. This is the only item in this audit with a clear, proven playbook to reuse.
2. **Don't touch** items 2, 3, or 5 (RE-vs-EA orchestrator split) — each is a confirmed,
   reasonable divergence, not duplication.
3. **Nothing further on embeddings** (item 4) — already resolved via the `trellis-vectorstore`
   Protocol seam.
4. ~~Fix item 8's within-run duplication~~ — **done.** Confirms this audit's own method: the
   item flagged lowest-risk/highest-confidence was the one acted on first, and fixed same-day.
   The shape that shipped (collection-state-keyed, not run-scoped) corrected this doc's original
   proposal — see item 8.

## Follow-ups (not part of this pass)

- Write a real design doc for the "AST-ownership-transfer plan" (currently only exists as
  in-code docstrings scattered across ~15 files in both packages) — the next person to touch
  this area shouldn't have to `grep` for it the way this audit did.
- ~~`advisor/ingest_to_milvus.py` is still named for the pre-pgvector-migration vector store~~
  — **done**, renamed to `advisor/ingest.py` (2026-08-25), alongside removing
  `packages/egeria-advisor/airflow/dags/incremental_update_dag.py`, an orphaned DAG with live
  `pymilvus`/`airflow` imports — neither package is a dependency anywhere in the workspace, and
  nothing else referenced the file. Two operational docs (`TROUBLESHOOTING.md`,
  `VENV_SETUP_GUIDE.md`) had import-check snippets that still said `import pymilvus`, which
  would have failed for a new developer following them — corrected to `pgvector`. Historical
  design docs mentioning Milvus (dozens, under `docs/design/`) and
  `resource-explorer/scripts/migrate_vectors_milvus_to_pg.py` (the completed migration script
  itself) were deliberately left alone, same rationale as RE's own rule 9: historical record,
  not live code.
- Confirm with the user whether EA's fixed 5-repo catalog should eventually register those
  repos as RE projects (making item 1's extension possible) — that's a product decision, not
  an engineering one, and shouldn't be assumed here.
- **Stage ingestion instead of fetch-chunk-embed-in-one-pass** (raised alongside item 8):
  website ingestion currently makes eight unchecked assumptions between "found a homepage" and
  "chunks are embedded" — the homepage is the docs site, a sitemap finds the pages, a plain
  client can fetch them, tag-stripping yields text, one chunk size fits everything, every page
  is worth embedding, the site is one version, the content isn't already held. The dead-site
  case in item 8 (all 400 fetches failing, 685 seconds spent before anything noticed) is the
  concrete argument for profiling/staging *before* committing to a full ingest — deciding
  whether and how, not just whether. Whether that's worth the added complexity is a design call
  this audit deliberately leaves open rather than assumes.
- **Out of scope for this doc, flagged for tracking elsewhere:** a concurrent session hit the
  same bug shape three times in one day — a curated field allowlist silently dropping anything
  added upstream without saying so (`_note`'s prop filter dropping `ingested_by`,
  `arch_recovery/persist.py`'s `operationCount`, and a `detail` field before that). Each is
  individually defensible, but three in one day across unrelated code suggests a repeated
  pattern rather than three accidents. It's a general codebase-hygiene finding, not an RE-vs-EA
  ingestion-pipeline-duplication one, so it doesn't get its own item in *this* doc — but it's
  real and worth a tracking issue or backlog entry of its own. **Filed:** `docs/Backlog.md`,
  "Corpus, signals & testing" — "A curated field allowlist silently drops anything added
  upstream — three instances in one day."

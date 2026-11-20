# Using the context-compilation tooling

> **Status (2026-11-19, 6.3 Tech Preview):** current for the shipped flags (`ARTIFACT_TREE_ENABLED` is still off by default, checked against `config.py`); other flag names in this guide were not re-checked for 6.3.

Practical guide to what shipped. Architecture and rationale are in
`context-compilation-architecture.md`; this is what to type.

Everything here is **off by default** and additive. Nothing changes behaviour
until a flag is set.

## Turning containment trees on

```bash
ARTIFACT_TREE_ENABLED=true uv run resource-explorer refresh <slug> --no-stats
```

Off means byte-for-byte the previous behaviour: no connection opened, no table
created, no work done. On, trees are built **in addition to** the chunks that
walk already produces — never instead of them.

Trees land in their own Postgres schema, `artifact_tree`, created on first use.
It belongs to neither app: both RE and EA produce trees, so a read-only
cross-schema pattern could not serve it.

Four ingestion paths build trees: markdown, code (Python/Java/JavaScript), HTML
(`web_docs`), and PDF. Go has no grammar, so `go_code` reports `unsupported`
naming what is missing rather than failing per file.

### PDFs

OCR is **off by default and that is a workaround, not a preference**. Docling's
default engine is rapidocr, which needs omegaconf; omegaconf cannot resolve above
2.0.6 in this workspace because `soda-core` pins `antlr4~=4.11`, and 2.0.6 rejects
the `PosixPath` rapidocr passes. With the default engine *every* conversion fails.

Tables, headings and lists do **not** need OCR — `do_table_structure` is a
separate Docling stage and works without it. OCR is only for text that exists as
pixels: a scanned page, or labels inside a rasterised diagram.

To enable it, pick an engine that is not rapidocr:

```bash
pip install 'trellis-artifact-tree[ocr]'      # easyocr, portable
pip install 'trellis-artifact-tree[ocr-mac]'  # ocrmac, macOS, much lighter
PDF_OCR_ENABLED=true PDF_OCR_ENGINE=easyocr uv run resource-explorer refresh <slug>
```

Neither depends on omegaconf, so this does not require the `soda-core` migration.

## Compiling a context

```bash
curl -X POST http://localhost:8810/api/context/compile \
  -H 'Content-Type: application/json' \
  -d '{"resource_slug":"egeria_git","question":"is this ready to adopt?",
       "perspectives":["Security"],"budget":4000}'
```

Returns `{text, manifest, derivation}` together. A caller with only the text
cannot say why a section is there, what was dropped, or what is missing — the
three things people actually ask.

- **`manifest.packed`** — sections included, at which rung, and their size
- **`manifest.dropped`** — cut for budget, with the reason
- **`manifest.deferred`** — ranked below the section cap and never offered to
  the packer, with rank and weight. Since 2026-09-09 a compile packs at most
  `MAX_EVIDENCE_SECTIONS` (12) evidence sections, chosen after ranking;
  `compile_context(max_sections=...)` overrides it and 0 disables it. Measured
  on the experiment repos at budget 6000: uncapped, 26 sections shared the
  budget and 22% reached FULL; at 12, 88% did and the budget still filled to
  5610 of 6000, so the budget rather than the cap decides the last sections in
- **`manifest.coverage`** — `{kind, question, note}` for the best-matching catalog question;
  `kind` is the catalog's answering kind (`analysis`, `mixed`, `partial`, `direct`, `human`,
  `chart`, `gap`) or `none` when nothing matches
- **`manifest.lists`** — `{section key: {list field: {total, shown: {FULL, SUMMARY}}}}` for every
  packed reader-derived section: the "N of M shown" the text carries, as data, so a UI can say M
  without recounting; pick N by that section's packed rung
- **`manifest.gaps`** — sections the derivation says should exist, **judged**:
  `nothing_found` (ran, a real zero), `never_run`, `not_established`, each with
  `last_run_at` and `can_run` where known
- **`derivation`** — which question, which Purpose matched, which analysis
- **`compile_id`** (also `manifest.compile_id`) — a content hash of everything
  the packer saw: spec, version, budget, target model, and every candidate's
  rungs. Same materialised state → same id, so a repeated id is a compile that
  replayed. Every compile is persisted in the registry's `context_compiles`
  table (manifest and derivation as JSON, user, session, `hits`), and both
  conversation turns and feedback carry the id, so a rating can be traced to
  the exact context the model was given. Send it back on
  `POST /api/query/feedback` as `compile_id`; the chat stream's `done` event
  carries it at top level.

Budget is in **characters**, not tokens. The caller owns the conversion, because
only it knows which model the context is for.

Conventions the packed text relies on, from the two audits of the compiled-versus-RAG
experiment (`packages/resource-explorer/docs/experiments/audits/`, 2026-09-08 and -09):

- **A reader-derived section leads with its headline** — the same one-line verdict the
  analysis card shows, e.g. `headline: none in 0 of 61 declared dependenc(ies) (warn)` for a
  `cve_scan` that could query nothing. The raw fields below it once read as "no vulnerabilities";
  the headline functions already own the absence-versus-zero distinction, so the compiler uses
  them rather than re-deriving it.
- **FULL is flat bullets, not fenced JSON.** `- key: value`, one level of nesting as
  `- detail.forks: 4`, lists of scalars inline, lists of records as indented blocks, empties
  said out loud (`(empty list)`). **A partial list carries its total on its opening line**
  (`- components: 32 item(s), 10 of 32 shown here`, `- files (20 of 25 shown): …`), never a
  trailing `… and N more`: the designer's rule is that prose never enumerates — a list-shaped
  fact is its total plus what is shown, and the pane holds the items (2026-09-12, "The blank
  rail, and what a list answer should be" §2). The matching prose sentence exists as
  `INSTRUCTION_VARIANTS["list_rule"]`, not in production, until an A/B shows it beats the
  evidence rung it would displace.
- **The middle rung is `abridged`, not "structure only".** Headline, scalars with values, then
  the first three entries of each list or mapping with a `(first 3 of 62)` marker. A section
  whose abridged form is not materially smaller than FULL has no middle rung at all.
- **A `Coverage:` line leads the text when the catalog itself says no analysis answers the
  question** — kinds `direct`, `human`, `chart`, `gap`, or no matching catalog question — and
  `manifest.coverage` carries the kind, the matched question and the catalog's note. Without it,
  a question nothing covers arrived with an empty gap list and the model answered from the
  nearest-ranked sections.
- **The matched question's catalog caveat rides in the instructions.** The question catalog's
  Rationale/Source column says what an answer may and may not claim ("DECLARED dependencies
  only, so a zero is 'none found in what we can see'"; secret_scan "never claims 'no secrets'").
  Since 2026-09-11 the best-matching question's caveat sits **inside the section it qualifies**,
  under the headline (`caveat: …`, clipped at 400 characters and marked; 160 at the SUMMARY rung)
  — beside the value, not in a preamble. It leads the instructions instead only when the matched
  question maps to no packed section. Verbatim in `manifest.coverage.caveat`,
  `manifest.coverage.caveat_placed_in` names where it went, and it is part of the compile id.
  Only the matched question's caveat: one about another question would be noise.
- **The refusal wording is deliberately loose** (*"say so and name what is missing — do not
  infer from absence"*). A one-shape template was tried in run 3 and reverted: it turned two
  thirds of answers into refusals, including questions whose answering analysis was packed.

## In the UI

**Chat answers now use compiled evidence.** The agent no longer just names the
available collections; the analyses that answer the question are packed into the
prompt with the missing ones named. Nothing is run to fill a gap, so a question
never blocks on a survey.

**The `Evidence` button beside Send** shows the manifest, gaps first. It is
deliberately not rendered under the assistant's reply, and says so: that reply
comes from RAG, and a manifest beneath an answer it did not produce is a
provenance claim.

## Measuring what a corpus wants

```bash
uv run python scripts/profile_corpus.py egeria_python_git --min-docs 20
```

Reads trees already in Postgres — no re-parse, no fetch — and reports per
top-level path boundary:

```
boundary       docs  u/doc  p75 unit  p75 doc  chunk  basis
sample-data     960   21.0        28     1728     28  unit
pyegeria         82   19.5       495    31260    495  unit
```

`basis` is the decision: around one unit per document means the document *is* the
unit; many means the unit is sub-document. **It reports only** — changing a chunk
size means re-embedding a corpus.

Requires `ARTIFACT_TREE_ENABLED` to have been on for an ingest of that resource.

## Collection drift

`refresh` now reports collection types a resource is eligible for but not
ingesting — a query over the file inventory, no scan:

```
Eligible but not enabled — pdfs: 15 matching file(s) (needs 1)
```

Collections are proposed once, at onboarding, and never re-evaluated, so a repo
that later gains PDFs keeps ignoring them. To act on it:

```bash
uv run resource-explorer repair enable-collection egeria_docs pdfs
```

It refuses on zero chunks rather than enabling an empty collection.

## Repair operations

```bash
uv run resource-explorer repair rename <old> <new>
uv run resource-explorer repair set-github-url <slug> <url>
uv run resource-explorer repair enable-collection <slug> <type>
uv run resource-explorer repair list-memberships <slug>
uv run resource-explorer repair repoint-membership <slug> <from-inv> <to-inv>
uv run resource-explorer repair drop-membership <slug> <investigation>
```

Also in Admin → 🔧 Repair. `rename` moves every table carrying the slug *and* the
pgvector collection names, which embed it; `set-github-url` refuses unless
confirmed, because existing collections hold content from the old URL.

## Catalog coverage

```bash
uv run python scripts/question_catalog_coverage.py          # table
uv run python scripts/question_catalog_coverage.py --json
```

Recomputes Purpose × analysis and Perspective × analysis reachability. Purpose
measures 0.22 mean pairwise overlap and Perspective 0.37 — the gap that makes
Purpose the primary dispatch axis. `Privacy` reaches zero analyses.

## Inventory export — and an overlap to resolve

```bash
uv run python scripts/export_inventory.py --no-timestamp -o inventory.json
```

Exits non-zero if the export fails verification, so a backup that captured
nothing cannot look successful.

**This overlaps existing functionality and should be consolidated.**
`resource-explorer export-resources <path.csv>` already exports repos, databases,
filesystems, groups and dispositions — with a matching `import-resources`, which
the JSON export has no equivalent of. I wrote the JSON export without checking,
and duplicated that half.

What the JSON export adds and the CSV genuinely lacks: **investigations and their
membership** (zero coverage in `batch_io.py`), servers, aliases, and a counted
manifest that `verify()` checks — an export that captured nothing otherwise reads
exactly like a correct export of an empty estate.

The right resolution is to add investigations to the CSV path rather than keep
two exports. Until then, use `export-resources` for anything you intend to import
back, and the JSON export when you need investigation membership captured.

# Brief: rebuild a repository's chat index from scratch

Status: **design PASS from the architect's read-back of 463c555a (2026-11-10), with C1–C4 folded in below (§11).
All eight questions decided (§8).** No code yet.
Asked for by the project owner on 2026-11-10 ("refresh the download and ingestion of a repo").

## 1. The gap

RE can make a repository's **first** index (`first_index.py`, 2026-10-20: asked first, run-queue kind `first_index`) and
can **refresh** an existing one (`IncrementalIndexer.refresh`, the `rag_ingestion` step, `resource-explorer refresh`,
`POST /api/projects/{slug}/refresh`). A refresh re-embeds only the collection types whose files changed since the last
commit it saw. Nothing rebuilds an index that already exists:

- an index built before a chunking, parsing or embedding change keeps its old chunks until each file happens to change;
- an index that came out wrong (a failed download, a crawl that returned an error page) stays wrong;
- the only way out today is the CLI wizard's "Re-index it?" (it **drops** the collections first, so chat has nothing
  for the whole rebuild) or remove + add (which also drops the registry row and everything keyed to it).

What laz holds today (the coordinator's read-only check of `resource_explorer` on the shared Postgres, 2026-11-10):

- own collections far too small for their source: `egeria_workspaces_git_java_code` 14 chunks,
  `egeria_workspaces_git_release_notes` 28; `egeria_git_java_code` 6,775, worth checking against its file count;
- site collections that look like failed or blocked crawls: `egeria_docs_web_docs` 14, five `web_docs` sites at 1–3
  chunks, `quay_io` 3;
- nothing records which chunking or embedding version built a collection;
- `chunk_feedback` has 25 rows (closed-loop step 0), all on `egeria_git_markdown_docs` (20) and
  `egeria_python_git_markdown_docs` (5), both plausible rebuild targets;
- the shared Postgres is under heavy load today, and 8813 serves chat from these tables.

## 2. What a rebuild is

A rebuild of repository R makes **new collections for R's own collection types, from a fresh download, alongside the
live ones; checks them; then swaps them in**, keeping the old ones until the swap is accepted. Chat keeps answering
from the old index throughout.

It is the first index's work (the same `RepoAnalyzer` plan and `IngestionPipeline` ingestion), run for a repository
that already has an index, with three differences: it builds aside, it swaps, and it carries the chunk votes over.

**In scope:** R's own collections (`{slug}_{type}`, `index_state.own_collections`).
**Not in scope:** the shared site collections (`web_docs_*`, `index_state.shared_collections`), which belong to
`website_ingestion` and are shared by several repositories (Q5); databases and file systems, which have no chat index of
this kind.

## 3. Build aside, then swap

1. **Claim.** A run-queue row, kind `reindex`, with the requester recorded (as `first_index`). Refused while R has any
   `first_index` or `reindex` run queued or running, or while any other `reindex` is running anywhere: one rebuild at a
   time (the coordinator's rule; the run queue makes it visible to `running_work.py`, so a restart can wait for it).
2. **Build** each collection type into `{slug}_{type}__rebuild`. The pipeline gains one parameter, the name a collection
   is written under, and the store's chunk ids are hashed under the **final** name (`{slug}_{type}`), not the aside one
   (§5). The pipeline's other writes (file inventory, dependencies, data profiles, CI, supply chain, conventions)
   **are** run, as on a first index (Q6), and an undo does not undo them.
3. **Check.** Per collection: old count, new count, and the files each came from. Refuse the swap when a collection
   that had chunks now has none, unless the plan no longer proposes it. The collection types come from the analyzer's
   plan **today** (Q1), so the check also lists what changed: types added (`collection_drift.py`'s eligible-but-missing
   ones) and types the plan no longer proposes. A dropped type leaves the live index at the swap and stays in
   `__previous` with the rest, so undo brings it back.
4. **Report or swap** (Q2): either stop here and show old and new counts for a person to accept (the coordinator's "dry
   run"), or swap at once when the check passes.
5. **Swap, in one transaction** (C3): every one of R's collections, live → `{name}__previous` and `__rebuild` → live,
   together with `projects.collections`, under a bounded `lock_timeout` and retried on a lock timeout. Not
   `MultiCollectionStore.rename_collection` as it stands: it commits each RENAME alone, so between two of them chat
   would read "relation does not exist". The swap also **renames each table's HNSW index** with it (C4, below). Then the
   baseline commit SHA, the `rag_ingestion` metric, and R's query cache, as `run_first_index` does.
6. **Keep the previous index for 7 days** (Q4), then a scheduled job drops it. **Undo**, within those 7 days, renames
   it back in one transaction, as the swap does. A type the re-plan **added** has no `__previous`, so undo also drops
   (or sets aside as `__rebuild`) that added live collection, restoring `projects.collections` in the same transaction
   (the architect's nit on cb9974fe). A failed build drops the `__rebuild` tables and leaves the live index untouched.

Space: up to twice R's index for the duration, so a rebuild is **refused when the database's free disk is below twice
R's current index** (the architect's addition). Load: a full download and embed of R. Both are reported in the ask (§6).

**Index names (C4).** Each collection's HNSW index is `{table}_embedding_idx`, created `IF NOT EXISTS`, and
`ALTER TABLE … RENAME` does not rename indexes. Left alone, after one swap the live table's index would still be named
`…__rebuild_embedding_idx`; the next rebuild's `CREATE INDEX IF NOT EXISTS` on a new `__rebuild` table would find that
name taken and silently create nothing, and chat would scan sequentially. So the swap renames the indexes with their
tables, and before swapping it checks that each `__rebuild` table owns its own vector index. Any table or index name
over 63 bytes is refused before the build, since Postgres truncates it silently.

## 4. While it runs

- Chat reads the live collections: nothing changes for it until the swap.
- A refresh of R (the scheduler's `rag_ingestion`, the CLI, the route) is refused while R's rebuild is queued or
  running, with a sentence saying why. A refresh writing into the live tables mid-build would be lost at the swap.
- The pipeline's `projects.status = INDEXING` write is skipped on a rebuild, since the live index still serves.

## 5. Chunk votes survive a rebuild

`chunk_feedback` is keyed by `chunk_ref = "<collection>:<id>"`, and an id is
`sha256("<collection>:<position>:<text>")[:32]`. So:

- **Same text at the same position, built under the final name:** the same id. The vote stays, with nothing to do.
  This is why ids are hashed under the final name in §3 step 2.
- **But "position" is the insert batch's index, not the chunk's place in its file** (C1). A first index numbers a whole
  collection from 0; a refresh numbers only its changed-file subset from 0 (`ingestion/incremental.py`). So one added
  chunk shifts every later id, and a repository that has been refreshed has ids no full rebuild reproduces. **The remap
  below is therefore the main path, not the exception.** Q8 proposes ids that do not depend on batch position.
- **Same text at a different position** (a chunk before it was added or removed): a new id. The rebuild **remaps** it.
  Before the swap, for every voted ref in an old collection, read its text from the old table and look for a row in the
  new table with identical text. On one match, the vote's counts move to the new ref. Ties are narrowed by the same
  `file_path` first (the architect's read-back); what is still tied is reported as **"ambiguous (N identical)"**, apart
  from "text gone", and kept orphaned rather than guessed.
- **Text gone or changed:** the vote is **kept, and said to be orphaned.** It is not dropped and not guessed onto
  another chunk. The result says "N votes carried over, M orphaned", and the Admin feedback view lists orphaned refs as
  such. They no longer affect retrieval, since no chunk has their ref.

The remap runs inside the swap step, so it reads the `__previous` rows before anything is discarded. It records every
old → new ref it moved in the run's result, so **undo** moves those votes back with the index.

## 6. Asked first

The same rule as the first index: **nothing is rebuilt without a yes that names it.** The ask shows, from data RE
already holds:

- **measured:** the current chunk count per collection, the file count from the latest inventory, and when R was last
  indexed;
- **estimated:** time, only from timed rebuilds or first indexes recorded here; otherwise "unknown until the first
  rebuild". No time is guessed (the coordinator has no measured number on laz);
- the temporary space (about the current index again), and that chat keeps answering from the old index until the swap.

## 7. Where it is offered

**Admins only** (Q3): every entry point below refuses anyone else, as Admin → Feedback does.

**As built (R2, #1041), a deviation:** `/next` has no Analysis card grid (analyses are question rows), so the door
is in the **repository header**, beside the first-index cue, for an indexed repository, not on the "Refresh &
Re-ingest" card. The architect accepted it at the read-back of 9fb4980b.

- **Analysis**, on the "Refresh & Re-ingest" card: "Rebuild from scratch…", which opens the ask.
- **CLI:** `resource-explorer refresh <slug> --rebuild`, which asks on the terminal unless `--yes` is given.
- **API:** `POST /api/reindex/queue {slug}`, `GET /api/reindex/progress`, `POST /api/reindex/{run_id}/accept` (for the
  held first rebuild, Q7), `POST /api/reindex/{run_id}/cancel` (the same, refused), `POST /api/reindex/{slug}/undo`,
  `POST /api/reindex/{slug}/discard-previous`.
- **A held rebuild, said plainly (added 2026-11-10, before the first timed run).** Its run ends once built, so the run
  queue records it as succeeded; the activity row says "built and held for an admin to accept; nothing has been
  swapped yet", never "done". Judge it by `phase == "awaiting_accept"`. While held, the repository counts as rebuilding
  (refresh, first index and a new rebuild wait), so a held run can be **cancelled**: the phase becomes `cancelled`
  first, then its `__rebuild` tables are dropped (any left over are named on the run row as `left_aside`, and the next
  build drops them first). The live index was never changed, so there is nothing to undo. A cancel does not end Q7:
  the next rebuild is held again, since only a rebuild that reaches `done` does. An accept whose continuation finds the
  original cancelled fails on its first step and swaps nothing.
  Accept and cancel are each read-then-write, so they take one claim per held run (`reindex-decision:{run_id}`) around
  their check and write; whichever comes second is refused ("right now", then "already accepted"/"already
  cancelled"). The continuation also re-reads the original right before its swap (the architect's C-1 on 6a57117e).
- **No batch.** One repository at a time, deliberately. A batch over the suspicious list is a sequence of single asks.

**Operating rule on laz, not code:** a rebuild rewrites shared tables other sessions read, so it is requested after a
peer round, and not during a heavy Egeria period or a gate (the coordinator's rules). The code enforces one at a time;
the rest is the coordinator's routine.

**What the first timed rebuild found (laz, egeria_docs, 2026-11-10).** It failed safely: the check refused because
`egeria_docs_pdfs had 36 chunk(s) and now has none`, the live index was untouched, and the build took 27 min 39 s.
The cause was not the rebuild: Docling 2.118 compiles its layout model with `torch.compile` by default, every batch
failed on Apple MPS (no float64), and Docling returned empty documents without raising, which `_ingest_pdfs` then
skipped quietly. Turning compilation off (`disable_torch_compile`) was not enough: gate b354 ran a real PDF on laz
with compile off and the layout model still failed, because transformers' RT-DETRv2 hard-codes a float64 tensor.
Fixed by running Docling on the CPU wherever it would pick MPS (`keep_off_mps`, in trellis-artifact-tree and RE's
`build_pdf_converter`); compilation stays off. transformers 5.22.2 fixed the float64 upstream, and the lock now
carries it with Docling 2.150, so MPS is used again; the CPU fallback only applies to an older transformers, or when
`TRELLIS_DOCLING_ALLOW_MPS=0`. Every PDF that yields no text is now recorded
(`pipeline.pdf_failures`): a rebuild refuses and names the files, and a refresh says so in its output. Progress now
carries `built_seconds` on failure too, and the run row's `heartbeat_at`.

**The PDF rule refuses only a regression (Architect read-back of #1055, 2026-11-11).** Refusing on any PDF that
yields no text meant one image-only or corrupt file would block every rebuild of its repository. The check now
compares each such file with its chunks in the live index (`count_by_metadata(name, "file_path")`): a file that had
live chunks and now has none refuses the swap (`failed_files`); a file that had none either is a note in the built
step (`no_text_before`, "gave no text, as before"). The collection-to-zero refusal stays. A file left with no chunks
because data preparation dropped every one as a copy of another file's is a note too (`duplicates`, naming the
file it copied): the three egeria_docs PDFs with no chunks on laz were exactly that. To compare per file, PDF chunks
now store their repository-relative `file_path`, as every other collection does; they used to store the run's
temporary download path, so live rows written before this are matched by path suffix. That also lets a refresh's
per-file replace find a changed PDF at last: until the repository's next rebuild, its old absolute-path rows stay
beside the new ones.

## 8. Questions for the project owner

**Decision (project owner, 2026-11-10):** Q2, Q3 and Q5 as recommended: swap at once when the check passes, keeping the
previous index for undo; admins only; the shared site collections get a separate re-crawl, later.

**Decision (project owner, 2026-11-10):** Q1, not as recommended: a rebuild **re-plans** its collection types from the
repository today (the analyzer's proposal), rather than keeping the set it has. §3 step 3 says what that changes.

**Decision (project owner, 2026-11-10):** Q7 and Q8 as recommended: the first rebuild is a timed measurement on one
mid-size repository, held for an admin to accept before it swaps (later rebuilds swap at once); and chunk ids are
minted per file, in their own PR before R1.

**Decision (project owner, 2026-11-10):** Q4, not as recommended: the previous index is kept for a **fixed 7 days**,
then dropped. So undo is available for 7 days after a swap. The drop is a scheduled job (the scheduler that runs
`rag_ingestion`), it is recorded in the activity log, and the ask and the progress view say when the undo window ends.

**Decision (project owner, 2026-11-10):** Q6, not as recommended: a rebuild **re-runs the pipeline's other writes**
(file inventory, line census, dependencies, data profiles, dataset descriptors, CI, supply chain, conventions), as a
first index does. They run during the build, before the swap, and write the live tables directly: they are not
built aside and are not undone by an undo, which restores only the chat index. The ask says so.

1. **Which collections are rebuilt:** the ones R has now (recommended: a rebuild should not silently change what is
   indexed), or the ones the analyzer proposes today, which may add types (`collection_drift.py` already reports
   eligible but missing types)?
2. **Swap at once or held:** swap as soon as the check passes, keeping the previous index for undo (recommended), or
   hold every rebuild for a person to accept after seeing old and new counts (the coordinator's "dry run")?
3. **Who may rebuild:** any signed-in person, as for a first index, or admins only (recommended: it rewrites a shared
   index for everyone)?
4. **How long the previous index is kept:** until someone discards it (recommended for now, since the space is visible),
   or a fixed time, such as 7 days?
5. **The shared site collections** (`web_docs_*`), which hold most of the suspicious counts: a separate "re-crawl" action
   on `website_ingestion` (recommended, a later brief), or part of this one?
6. **The pipeline's other writes** (file inventory, dependencies, profiles): skipped on a rebuild (recommended: they
   are survey results with their own steps), or re-run alongside?
7. **A first measurement:** may one mid-size repository be rebuilt, timed, once the code exists, to give the ask its
   first real estimate (the coordinator's suggestion), with that first rebuild held for acceptance (the architect's)?
8. **Chunk ids per file** (C2, the architect's): mint an id as `sha256(collection:file_path:index_in_file:text)`
   instead of by batch position. Ids would then survive a refresh and a rebuild wherever a file's chunks are unchanged,
   and it fixes a bug that exists today: two refresh runs can give different files' chunks the same id (identical text
   at the same batch index), and `ON CONFLICT` overwrites one with the other. It changes every id once, so it needs the
   same vote remap, run once, when it lands. Recommended: yes, as its own PR before R1.

## 9. Phasing

Each its own PR, with tests and a read-back:

- **R0** (Q8, before R1): chunk ids minted per file, `sha256(collection:file_path:index_in_file:text)`, with the
  one-time vote remap to the new ids. It also fixes the refresh id collision (§8 Q8).
- **R1.** The pipeline's write-name parameter and final-name ids; the rebuild run (build aside, check, swap, previous
  kept, undo); refresh refused during a rebuild; the vote remap. CLI `refresh --rebuild`. No UI.
- **R2.** The ask, the Analysis card action, progress, accept (the held first rebuild, Q7), undo and discard in the UI.
- **R3** (if Q5 says so): the site re-crawl.

No new table: the run row and the activity log carry the state, as for the first index. The `__rebuild` and
`__previous` collections are pgvector tables like any other, and they are named so a person can see what they are.

## 10. Tests

- A rebuild of a repository that has been refreshed: its ids differ from a full rebuild's, and the remap carries its
  votes (C1).
- The swap is one transaction: a reader between the two renames never sees a missing relation (C3).
- After a swap the live table owns `{table}_embedding_idx`, and a second rebuild's `__rebuild` table gets its own vector
  index (C4). A name over 63 bytes is refused before the build.
- Tied identical text is narrowed by file path, and what remains is reported ambiguous, apart from text gone.
- A rebuild is refused when free disk is below twice the index.

- A rebuild with unchanged content keeps every id, so every vote stays where it was.
- A chunk that moved keeps its vote through the remap; one whose text changed is reported orphaned and kept.
- Chat reads the old collections until the swap, and the new ones after it.
- A failed build leaves the live index and drops the `__rebuild` tables; a failed swap rolls back.
- A refresh during a rebuild is refused with its sentence; a second rebuild is refused while one runs.
- Undo restores the previous index and its votes; discard drops it; after 7 days the scheduled job drops it.
- Undo after a re-plan that ADDED a type removes the added live collection and restores `projects.collections`.
- Shared `web_docs_*` collections are neither rebuilt nor dropped.

## 11. The architect's read-back of 463c555a

Design PASS, merge after C1–C4, all folded in above: C1 (position is the batch index, so the remap is the main path,
§5), C2 (per-file ids, put to the owner as Q8), C3 (one-transaction swap, §3 step 5), C4 (index names, §3). Also taken:
narrow remap ties by file path and report "ambiguous" separately; check free disk before a rebuild. `pg.py` caches no
table names or statements, and the query-cache invalidation already exists.

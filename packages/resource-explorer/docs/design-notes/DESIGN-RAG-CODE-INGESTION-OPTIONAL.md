# DESIGN — RAG ingestion of code: optional per resource, counted before it runs, ruled when it runs (2026-10-07)

Design session note, short, at the owner's word. Trigger: the ingestion
step produced **51,729 chunks** from egeria_git's code. Owner: refine the
ingestion rules, or at least make code ingestion optional. No code.

## What happens today, read on main c5304bb5

- Full ingestion runs **at registration** (`ingestion/pipeline.py`,
  `IngestionPipeline.run`), over every content type the repository's
  profile lists: four code types (Python, JavaScript, Java, Go) and five
  document types (Markdown, web docs, API references, examples, PDFs).
  Vendored paths are skipped (`ingestion/vendored.py`); nothing else is.
- The survey step `rag_ingestion` ("Refresh & Re-ingest", Analysis stage,
  `recommended: false`, run time minutes) wraps the **incremental** indexer:
  a no-op when the commit is unchanged, otherwise it re-embeds changed files.
  Its docstring is explicit that a survey step must never be the thing that
  embeds a repository from scratch; registration is.
- There is no per-resource switch, no per-type switch, no size or count
  cap, and nothing tells a person how many chunks a registration will
  produce before it produces them. The number appears afterwards in the
  step's annotation.

## Rules

**R1. Code ingestion is a choice per resource, visible before it runs, off
where the resource's purpose does not need it.** Documents stay on by
default: a repository's README, docs and API references are what Chat
answers from first and are small. Code is on by default only when the
investigation's purpose is code understanding (Integrate, architecture
recovery) or a person turns it on.

**R2. The count comes first.** Before any embedding, RE walks the tree
(the file inventory the survey already keeps) and shows "would ingest N
files · about M chunks · ~T minutes" per content type, from the chunker's
own size rule, so the person decides with the number in front of them.
The estimate is labelled an estimate; the actual appears after, next to it.

**R3. When code is on, rules decide what, and the rules are the same ones
the sub-resource survey uses to call a folder worthy**: generated and
vendored excluded, tests and fixtures excluded by default (a switch), file
size cap, file-count sanity per folder, and a **chunk budget per resource**
with the honest sentence when it is hit: "stopped at the budget of B chunks
· N files not ingested · raise the budget or narrow the folders". Folders
are chosen with the same Include | Leave out selector as Curate's tree, so
"ingest only `src/` and `docs/`" is a choice a person makes once and RE
keeps as the resource's record.

**R4. Status words derive from what was done.** The step's annotation and
the Analysis row say "ingested N chunks from M files · code off · docs on ·
budget B" and never a bare chunk count; the Chat rail says which content
types its answers can draw on for this resource.

## The switch alone, as an early slice

Yes, it should be its own slice, before the demo if the owner wants the
count down, because it is small and the rest is not:

- **What it takes.** A per-resource setting `ingest_code` (default: on for
  repositories already registered, so nothing changes under them; off for
  new registrations unless the investigation's purpose is Integrate or a
  person ticks it) stored on the resource record, read by
  `IngestionPipeline.run` to drop the four code dispatchers, and by the
  incremental indexer to skip code files. A control on the resource's
  Enrichment or Automate pane: "Ingest code for Chat and Understanding"
  with the current count beside it and "Re-ingest" to apply a change.
  Turning code **off** for an already-ingested repository deletes its code
  chunks by metadata (the per-file replace path already exists) and the row
  says "code chunks removed · N · docs kept". Proof: the annotation's counts
  after, by content type.
- **What it costs.** One column, one control, one read in two places, a
  test per path; a morning. It does not deliver R2's count-before or R3's
  rules, which are a second slice after the demo.
- **What it does not do.** It does not shrink an existing index until a
  person turns code off and re-ingests, so egeria_git keeps its 51,729
  chunks until the owner presses that control, which is itself the gate.

## Order

1. The switch slice (above), gated on egeria_git: turn code off, read the
   count fall and the words, turn it on, read it rise.
2. Count-before and the Include | Leave out folder choice (R2, part of R3),
   sharing the tree and selector the Curate nesting brief designs.
3. The rules and the budget (rest of R3), with the sub-resource survey's
   worthiness rules reused rather than a second list.

## Questions

**Owner:** the default for new registrations (my proposal: docs on, code
off unless the purpose is Integrate); the budget's default size.

**Designer:** where the switch and the count sit (Enrichment's doc-sources
section, which already has "Save and probe", or Automate); the sentence
when the budget is hit.

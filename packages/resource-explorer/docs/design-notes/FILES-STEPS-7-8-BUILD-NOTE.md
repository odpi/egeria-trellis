# Files build steps 7 and 8, and RE's own file-system survey report (build note, 2026-10-28)

Branch `claude/re-files-steps-7-8`, cut from `origin/main` at `b7a7f528` (which has step 6,
`FILES-STEP-6-DERIVED-STEPS-BUILD-NOTE.md`). Brief: `BRIEF-FILES-QUESTIONS-AND-ANALYSES-TO-RETIRE-CLASSIC.md`. Nothing
here was run against Egeria or the shared Postgres (:5442). Every suite ran with `PGVECTOR_PORT=1`, and the new tests
use scratch SQLite files and fakes (a fake vector store, a fake Egeria boundary, a fake data-class loader).

**Decision (project owner, 2026-10-28):** "start Files steps 7–8, including publishing the file-system survey report"
(relayed by the Monday Coordinator). The earlier ruling the publish rests on: a file system's chosen measures publish
through Resource Explorer's own file-system survey report, not the root-folder publish.

## 1. The mapping

| Step | Brief | Built here |
|---|---|---|
| 7 | §3 item 4: `fs_layout_signals` (F10), and the section-chunked docs ingestion of the narrative files found in step 1 (F11's long form) | `fs_layout_signals` (Analytic Library step); `narrative_docs` (the folder survey card's press) |
| 8 | §3 item 5: `data_class_match` over file profiles (F7), proposal-only like databases | `file_class_match` (the card's press, reusing the database matcher) |
| (publish) | §3 item 3's "publishing as Egeria's folder-survey annotation types", deferred in step 6 | RE's own file-system survey report carries the derived results, plus the declared-measures hook |

## 2. What was built

**`fs_layout_signals` (F10, Q-0104).** A sixth zero-fetch step under `trellis-analytics/examples/steps/`, with the same
declaration rules as step 6. It reads the NAMES on the newest walk's kept rows, plus a new typed read,
`filesystem_walk_not_walked`, which is the walk's own count of the noise directories it skipped. That read is admitted
to `STEP_ODS_METHODS`. The step recognises four structures:

- a project: a manifest, or a `.git` directory the walk did not enter;
- a dataset layout: two or more dated or `key=value` folders, or a dictionary, codebook, schema or layout file;
- an archive;
- backups: backup suffixes, dumps, or a file under a backup-named folder.

The result is one ResourceProfileAnnotation, counts only. "Not measured" covers both no walk and a walk that kept no
rows, since a walk with no rows is not a run the reader can see. A walk with no signal is `nothing_found` and names
what it looked for. A capped walk and a walk that never recorded its skipped directories each say so; neither is
counted as 0. Q-0104 is now answered by this step.

**`narrative_docs` (F11's long form).**

- Inputs: exactly the files the walk's `folder_narratives` recorded, read whole up to the walk's 512 KB narrative
  limit through `walk_extensions.read_plain_file`.
- Processing: each file is split on its own headings (Markdown `#`, underlined titles, reStructuredText with an
  overline), then cut into `DocParser` word windows.
- Output: the windows replace the collection `fs_<slug>_narratives` whole on each run. Every passage carries its file,
  folder, section heading, modified time and walk run, with `status: proposal` and `stated_by: <file>`.
- Files not read, each counted with its own words: a secret-looking name is never opened; a file that grew past the
  limit since the walk is not read; a file that is no longer a plain file is not read.
- The price is shown before the press. "Not priced", "nothing to read" and "not read yet" are separate states. A run
  becomes stale once a newer walk exists.
- Safeguards: one run per file system at a time (a claim), an unreachable mount refuses, nothing is written into the
  path, and nothing is published.
- Interfaces: `GET/POST /api/filesystems/{slug}/narrative-docs`, and the card section "Narrative files, read in full".

**`file_class_match` (F7, Q-0103 stays PARTIAL).** The database matcher (`column_matching.data_class_match`) runs
unchanged over the newest profile run's columns.

- Values: taken from the head of each file Resource Explorer profiled (1 MB cut at a whole line, or a Parquet file's
  first row group), 200 values per column. Every claim is stated against that sample.
- Files Egeria profiled are never opened (step 5's rule). Their columns are `not_sampled`, and the result says why.
- Data classes: read from Egeria as the signed-in person, read-only. If they cannot be read, every compared column is
  `no_candidates` with the reason, never "no match".
- A pattern no class has is a proposal. Nothing is created or published in Egeria.
- Filename rules are a separate proposal, made on the name only.
- Interfaces: `GET/POST /api/filesystems/{slug}/class-match`, and the card section "Personal or sensitive data". File
  and column names appear on RE's screen only.

**RE's own file-system survey report** (`EgeriaFileSystemSurveyor._publish_step_annotations`). The report's own
annotations come first, so their indices and published names never move. After them it now carries:

- **The derived steps' stored results for this walk** (`walk_derived.report_annotations`). Each goes out as the type
  its measure declares: ResourceMeasure; ResourceProfile, with integer value counts; or RequestForAction. Some results
  are not sent, and each says why in `derived.not_sent`:
  - a result that was not measured;
  - a result derived from another walk;
  - a request for action with nothing to request.
- **`declared_measures`**, when a caller passes one, in `publish_table.annotations_for`'s shape. It is off by default.
  This is the hook ext-publish-table asked for. Lifting the publish table's file-system gate (`NOT_FOR_KIND_WORDS`,
  and `annotations_for` being database-only) stays with that work.

After publishing, the report is read back by name. Each sent row carries its qualifiedName and the GUID the read
found, or "" with the reason. The report GUID is in the proof. On the card, a derived result that a publish proved
(a GUID read back for the same walk) says "in Egeria · report <guid>".

## 3. Open points and their answers

**Decision (project owner, 2026-10-28):** "accept all six, approve the narratives press after merge" (relayed by the
Monday Coordinator). The six rows marked "Owner accepted 2026-10-28" below were the six marked "assumed, owner to
confirm" when this note was written. The narratives press is covered in §4.

| Open point | Answer | Source |
|---|---|---|
| What counts as each layout signal | The fixed lists in `fs_layout_signals/step.py` (manifests, dated-folder regex, `key=value`, the narrative rule's dictionary words minus code files, archive and backup suffixes, backup folder names); a dataset layout needs two or more dated or partition folders | Brief F10's examples. **Owner accepted 2026-10-28** |
| Where the long-form narratives live | Resource Explorer's search index (pgvector), one collection per file system, replaced whole per run; never in Egeria | Brief §2a (quotable "with its source"). **Owner accepted 2026-10-28** |
| Narrative chunk sizes | `DocParser`'s defaults (384 words, 48 overlap), sections first | Existing repo ingestion. **Assumed** |
| Run inline or queued (narratives, class match) | Inline, under a per-file-system claim, like `file_profiles` (step 5) | Step 5's precedent. **Owner accepted 2026-10-28** |
| How values are sampled from files | The head of the file: 1 MB cut at a whole line, or the first Parquet row group; 200 values per column | None recorded. **Owner accepted 2026-10-28** |
| Files Egeria profiled | Not opened; `not_sampled` with why. Reading Egeria's `InspectDataValues` value counts as the sample is a follow-up | Step 5's rule ("measures nothing Egeria measured") |
| Personal-data name words | `PERSONAL_NAME_WORDS` in `file_class_match.py`, a proposal on the name only | None recorded. **Owner accepted 2026-10-28** |
| Proposed classes for files | Kept as proposals in RE; no DRAFT DataClass is created in Egeria for files | Brief §3 item 5 ("same proposal-only rule"); the Egeria write is a follow-up |
| Which derived results publish | `measured` and `nothing_found` for the same walk; a request for action only when measured | Absence is never published as a finding. **Owner accepted 2026-10-28** |
| The report's types vs the engine host's | The report carries ResourceMeasure, ResourceProfile, RequestForAction; the engine host's `PUBLISHABLE_ANNOTATION_TYPES` is unchanged, so Egeria still may not dispatch fs_activity, fs_permissions or fs_layout_signals | Kept apart on purpose: one governs RE's own report, the other Egeria's dispatch |

## 4. DDL and shared writes for the owner

- **No registry DDL.** Results and runs go in the existing `step_annotations` and `step_runs` tables. The new read is
  a SELECT.
- **One runtime shared write, by the narratives press only.** Pressing "Read narrative files" inserts into the shared
  pgvector store (:5442), and on the first press for a file system it creates that collection's table,
  `fs_<slug>_narratives` (`trellis_vectorstore.pg.create_collection`, `CREATE TABLE IF NOT EXISTS`). Each later press
  drops that table and creates it again, because the collection is replaced whole. This uses the same mechanism
  repository ingestion already uses (`MultiCollectionStore`). No test made this write. **Approved by the project
  owner on 2026-10-28, for after this branch merges.** The coordinator runs a peer round first and names the file
  system. The builder does not press it.
- **One runtime Egeria read, by the class-match press.** It reads Egeria's DataClasses and ValidValueSets as the
  signed-in person. It writes nothing to Egeria.
- The report publish writes to Egeria only when a person presses Publish on the walk, which is unchanged. It now
  sends more annotations under the same report.

## 5. Tests

- **trellis-analytics, `tests/test_files_layout_signals.py` (11):** the manifest, the guard, an empty walk, no signal,
  each structure, a lone `.git`, one dated folder, "not recorded", a capped walk, the newest walk, no path in a result.
- **RE, `tests/test_files_narrative_docs.py` (15):** the section split, the price states, the run, a file read in full
  past the 64 KB excerpt, a secret name never opened, a link or an oversized file not read, the collection replaced
  whole, the run words and staleness, nothing written into the path, the claim and an unreachable mount, the routes.
- **RE, `tests/test_files_class_match.py` (12):** the precondition, a match stated against its sample, a proposal, a
  file Egeria profiled never opened, an unread catalogue, the filename rule, a file with no columns, staleness,
  nothing written into the path, the claim and an unreachable mount, the routes.
- **RE, `tests/test_files_survey_report_publish.py` (12):**
  - the derived results come after the report's own, as their declared types;
  - "not measured" and other-walk results are not sent;
  - when nothing is stored, nothing is sent;
  - counts only;
  - qualifiedNames and read-back GUIDs, an annotation the read did not find, and an unreadable report;
  - the declared-measures hook, and its off-by-default state;
  - what the card reads after a publish.
- **Render harness:** `files7-narrative-docs.test.mjs` (6), `files8-class-match.test.mjs` (4).
- **Pinned expectations updated on purpose:**
  - `test_engine_host_library_steps.py`, the read-method set;
  - `test_files_derived_steps_host.py`, the step list;
  - `test_question_catalog_review_rules.py`, Q-0104 is no longer a gap;
  - `files6-derived-steps.test.mjs`, the derived section's words.

## 6. Follow-ups

1. Chat and the Resource Locator do not query `fs_<slug>_narratives` yet. Wiring file-system retrieval with "stated by
   <file>" citations is the reader half of F11's long form.
   **Chat: built (Files follow-up 1, `narrative_context.py`).** A compile scoped to a file system, or to an investigation
   holding one, adds a `narratives` section: the top 5 passages for the question, packed inside the compile's own budget,
   each cited "stated by <path> § <section> — proposal". The manifest carries `narratives` (state, passages with
   `passage_id` = walk_run|path|section), and the journal entry stores those ids. Absence is said three ways: "not read yet"
   (never pressed), "read, and no passage matched" (no hit), "index could not be reached" (a failure, never an absence).
   Rows from an old press are cited as they hold stated_by, with the metadata's `file_path` added.

   **Resource Locator: built (`narrative_context.locate`, `GET /api/filesystems/by-passages?q=`).**
   **Decision (project owner, 2026-10-29):** "rank by best passage, search only pressed file systems."
   A file system is searched only if it has a `narrative_docs` step run; each is ranked by its single best-scoring
   passage, shown with the chat citation ("stated by <path> § <section>") and the proposal label. The file systems
   never pressed are counted and named ("N file systems not read yet, not searched"), so an unread one is never taken
   for a non-match; a pressed one whose index cannot be searched is listed as unavailable (a failure, not an absence),
   the same three wordings as chat. No UI tab yet: the route and function are the reader half; a screen can call them.
2. Q-0105 (F11) stays GAP and Q-0101 (F3) stays GAP in the CSV, although `folder_narratives`, `narrative_docs` and
   `file_profiles` are built. None of them has a catalog entry for the question catalog to name.
3. For files Egeria profiled, read the value counts from Egeria's CSV report, so their columns can be matched too.
4. F9's "sensitive" should join F7: `fs_permissions` still uses the walk's secret-name rule only. It needs a typed read
   of the class-match proposals.
5. Create the DRAFT DataClass for an unmatched-but-patterned file column, as the database step does, once the owner
   rules on writing it.
6. The publish table's file-system gate and its proof rows (ext-publish-table): `annotations_for` for file systems,
   passed as `declared_measures`, and `record_proofs` keyed by the slug and the report GUID in the proof.
7. Engine-host dispatch of the ResourceProfile and RequestForAction steps (`PUBLISHABLE_ANNOTATION_TYPES`).
8. §3a's one `FileSystemSurvey` definition that mixes Egeria's and RE's steps: not started.

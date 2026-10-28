# DESIGN — The Artifacts view: lists of endpoints, classes and other artifacts, with fine-grained documentation links (2026-10-15)

From the design session, at the owner's request. His words, looking at
"Does TestGen have an API?" on dataops_testgen (2026-10-15):

> "I'd like to be able to see the list of rest calls - and more
> generally, a user is going to want to see lists and optionally details
> of APIs, classes, and other artifacts. This is where ingesting this
> information and the documentation is useful - also providing a
> fine-grained link within the documentation is good as well."

Facts below come from the PR/CI session's read-only inventory
(`re-artifacts-inventory-2026-10-15.md`, dataops_testgen, egeria_git,
sqlglot) and the evidence-list fix on `re/answer-evidence-lists`.

## 1. What is stored today, and what is wrong with it

| Store | Holds | Gap |
|---|---|---|
| `project_code_markers` (written by the symbol extraction, read by interface_surface) | endpoints: method, path, line, handler, kind (http_api, grpc, graphql, messaging, cli, config, events); 2,438 rows for testgen | 2,373 of them are in `tests/`: mock patches counted as FastAPI routes; 11 "celery" hits are invoke tasks. The rung is right, the count ("1869 registrations") is wrong. sqlglot has the same (13). |
| `project_code_symbols` | classes, functions, methods, modules with file, start and end line, signature, docstring, complexity; 9,713 (testgen), 44,499 (egeria_git), 5,895 (sqlglot) | no commit SHA per survey; only `projects.last_commit_sha`, which is current, not the surveyed one |
| marker → symbol join on (slug, file, qualified_name) | 2,438 of 2,438 join | no reader uses it |
| `api_structure` | two symbol counts | not endpoints; its count is stale (5,937 against 9,713) because it ran two seconds before symbol extraction in the same run |
| docs chunks (website ingestion) | 16,233 chunks over 205 pages for testgen, all homepage pages; each a 384-character window with url, path, source, index | no heading path, no anchor, no line range; no chunk references a symbol or path; `docs_url` empty because Scouting has not re-run since #662; `artifact_tree` empty |
| `context_compiles.manifest->'lists'` | the extents shown to the model per answer; stored | the compiled text is not stored, only its size; for the TestGen answer the model saw summary lines only, never an endpoint row; citations are section keys |

Three fixes PR/CI is proposing to the owner separately, which this
design assumes: exclude test paths and mock patches from route markers;
order api_structure after symbol extraction; re-scout testgen so
`docs_url` fills and ingestion takes the docs site.

## 2. The view

One **Artifacts** tab per repository (and later per package or module
once child-as-resource exists), on the Understanding stage's rail beside
At a glance, Contents and Over time, because it answers "what is in it"
for a reader; Curate's Structure tab links to it for verdicts.

Lists, each with a count that says what it counted and what it left out:

| List | Source | Row | Detail (on open) |
|---|---|---|---|
| REST endpoints | markers kind http_api, role core | method, path, handler, file:line | parameters, auth marker, the handler's signature and docstring (via the join), callers, doc link |
| gRPC, GraphQL, messaging | markers by kind | service or type, operation, file:line | the same |
| CLI commands | markers kind cli, entry points | command, module, file:line | options parsed from the parser call where present, docstring, doc link |
| Configuration keys | markers kind config | key, default, file:line | where read, docstring, doc link |
| Events | markers kind events | topic or event name, producer or consumer, file:line | payload type where present |
| Classes and interfaces | symbols kind class or interface, public by default | name, module, file:line, line count | signature, docstring, methods (symbols with this parent), subclasses, callers, doc link |
| Modules and packages | symbols kind module | path, symbol counts | the symbols under it |

Every row carries a **source link**: a permalink to the repository at the
surveyed commit with `#L<start>-L<end>`, never a branch link, so the
link still shows what the survey saw after the code moves. That needs
the commit stored per survey (§3).

Every count is labelled with its filter: "54 routes · under testgen/ ·
2,373 in tests/ left out · survey 10-14". The role filter (core, test,
generated, vendored) is the same classifier the ingestion-rules design
will use; it is defined once.

## 3. Store versus derive

**Store (new columns, additive, their own DDL record):**
- `survey_commit_sha` on the symbol and marker rows, or on the survey
  run they belong to, with the rows carrying the run id. Without it no
  permalink is honest.
- `file_role` on symbol and marker rows (core, test, generated, vendored,
  docs), set at extraction by the shared classifier, so the lists filter
  without re-reading the tree.
- Docs chunks gain `heading_path` (the page's heading chain at that
  chunk), `anchor` (the nearest heading id) and `page_title`. This is an
  ingestion change: chunk documentation pages by section, not by a
  384-character window, so a chunk is "the section 'Running the API' on
  page /docs/api", citable by url#anchor. Code stays window-chunked.
- `doc_links` (derived, stored, replaceable): one row per artifact → doc
  section with the evidence (the exact token matched: the path
  `/api/v1/tests`, the class name, the command) and a confidence; a cache
  row, never a proof row, rebuilt on each docs ingestion.

**Derive at read time:** callers, subclasses, methods of a class and the
symbol a marker belongs to, from the existing join. Counts per list.

**Never store twice:** the row text the model saw (§4) is stored by id
reference to these rows, not copied, unless the row cannot be addressed
after a re-survey, in which case the text is copied once onto the answer.

## 4. How an answer cites these lists

- The compiled manifest's extents are already stored per answer. Add:
  the ids of the rows each extent was built from, and the compiled text
  itself (it is 532 characters for the TestGen answer, not a size
  problem). An answer is then provably composed from rows a person can
  open.
- "Open what this answer saw" opens those rows at that survey. The live
  list is a second, labelled control: "current list · may differ from
  what the answer saw".
- An answer that saw only summary lines says so: "answered from the
  interface summary, not the endpoint rows · open the current list".
  Today's TestGen answer is exactly this case and must read as such.
- A question about artifacts ("does it have an API", "which endpoints
  change customer data", "is there a CLI") gets the matching list's rows
  into its extents, not the summary, once the lists exist.

## 5. Fine-grained documentation links

- With section chunks, a doc link is `url#anchor` plus the heading path,
  shown on the artifact row as "documented: API reference › Tests ›
  Running the API" and on the doc page's side as "describes: POST
  /api/v1/tests (testgen/api/tests.py:41)".
- Matching, in order of confidence: an exact path or command string in
  the section (high); a class or function name in a code span (high); a
  name in prose (medium, shown as "mentions"); nothing (no link, and the
  row says "no documentation found for this endpoint", which is itself a
  finding worth a count: "38 of 54 routes have no documentation").
- The link is a cache row with its evidence; a person can confirm or
  reject it (a signed event), and a confirmed link may later publish to
  Egeria as an external reference on the asset.

## 6. Honesty rules specific to this view

- A list's count names the role filter and the survey date.
- A row whose file no longer exists at the current commit says "moved or
  removed since survey 10-14" and keeps its permalink.
- A marker without a joined symbol (none today, but the join is not
  guaranteed) shows without detail and says why.
- "No documentation found" is a measured absence; "documentation not
  ingested" (docs_url empty, no ingestion run) is a different sentence.
- Nothing in this view is published to Egeria by itself.

## 7. Slices, in order

1. Fixes assumed above (PR/CI's three), plus `survey_commit_sha` and
   `file_role` at extraction with their DDL record.
2. The Artifacts tab with the endpoint and class lists, permalinks and
   labelled counts; evidence rows for answers (§4) in the same slice,
   since the lists are what answers should cite.
3. Section-chunked documentation ingestion with heading path and anchor;
   `doc_links` derived with evidence; "no documentation" counts.
4. CLI, config and events lists; confirm or reject on a doc link.

Gate, by use on dataops_testgen: the owner opens Artifacts, sees 54
routes under testgen/ with the tests left out and named, opens one, lands
on the handler at the surveyed commit, and from the same row opens the
documentation section that describes it; asks "Does TestGen have an API?"
again and the answer's "open what this answer saw" opens endpoint rows.

## 8. Open questions for the owner

- [ ] Is the Artifacts tab on Understanding (a reader's view) right, or
      does it belong beside Curate's Structure tab where verdicts live?
- [ ] Should a confirmed doc link publish to Egeria as an external
      reference on the asset, or stay in RE?
- [ ] Are private symbols listed on request, or never?

# Egeria Advisor — Backlog

Consolidated work list for the `egeria-advisor` repo.  
Status: `open` · `in-progress` · `done` · `deferred`

---

## Performance measurement

### PM-1 — the embedding and RAG benchmarks exist and have never been reported

**Status:** `open` · consolidated here 2026-09-03 from the cluster-4 documentation
review and from `REMAINING_TODOS.md` (dated 2026-03-07, since archived out-of-repo
— the name is given for git-history search, not as a live link).

Two separate benchmark gaps, both with the same shape — the harness exists, the
number does not:

- **ONNX vs PyTorch embeddings.** `scripts/benchmark_onnx.py` exists, both
  exported models are on disk (`models/all-MiniLM-L6-v2.onnx` and
  `.optimized.onnx`), and the migration plan set explicit targets of 2x+ on CPU
  and 3x+ on GPU. **No result is recorded anywhere**, and `backend: pytorch`
  remains the active setting. So the ONNX path is either an unrealised speedup or
  a measured disappointment, and nothing on disk distinguishes those. Running the
  existing script answers it.
- **Async vs sync RAG under concurrent load.** Carried from the 2026-03-07 TODO
  list, where it was already marked low priority. Still unmeasured.

Cheap to close and worth closing, because the first one gates a decision that is
currently being deferred by default: whether to switch the embedding backend or
delete the unused path.

See `docs/design/RUNTIME_AND_HARDWARE.md` §4a.

---

## Test debt

### TD-1 — `test_report_spec_planner.py`: 6 failures, never passed in this repo

**Status:** `done` — fixed `2026-09-04` (`114209e`, "repair stale report-spec planner test
mocks"), 5 days after being found here; this entry was never updated to say so until noticed
2026-09-07 while touching this same test file for TC-5 slice 6 (BACKLOG.md, `packages/egeria-advisor`).
All 7 tests pass now, confirmed live.

Original finding, found 2026-08-30 during a documentation review: six of the seven tests in
`tests/unit/test_report_spec_planner.py` failed, not a regression — the file hadn't changed since
`67352a6` ("Import Egeria Advisor as a workspace member package", 2026-08-06) and came over broken
from the original repo. Four distinct causes: a `TypeError` from calling the route handler
directly instead of through a `Request`/`TestClient`, an `Error`-vs-`Completed` mismatch, an empty
params dict where a populated performance-hints block was expected, and dynamic schema discovery
returning `[]`. `114209e` addressed these; see that commit for what changed.

---

## Deployment portability

### DP-1 — checked-in `configdata/mcp_servers.json` hardcodes one developer's Mac paths; breaks `dr-egeria` MCP on every other machine

**Status:** `open` · **Priority:** high · found 2026-09-06 while configuring EA against a local
Quickstart deployment on a Linux box ("hedwig").

Both MCP server entries in `advisor/configdata/mcp_servers.json` — a file checked into the repo,
not a per-machine `.env` — hardcode absolute Mac paths:

```
"command": "/Users/dwolfson/localGit/egeria-python/.venv/bin/python"
```

`pyegeria`'s entry survives on other machines by accident: `advisor/mcp_config.py`'s
`resolve_pyegeria_mcp_command()` has special-case logic that resolves it to the current
interpreter when `pyegeria` is importable there, silently overriding the JSON. `dr-egeria` has no
equivalent fallback and fails outright:

```
Failed to connect to MCP server dr-egeria: [Errno 2] No such file or directory: '/Users/dwolfson/localGit/egeria-python/.venv/bin/python'
```

Confirmed live on a Linux checkout where the equivalent real paths exist under
`/home/dwolfson/localGit/egeria-v6/egeria-python/.venv/bin/python` and
`.../egeria-v6/egeria-workspaces/compose-configs/egeria-quickstart/PyegeriaWebHandler/mcp_server.py`
— the JSON's `-fs`-suffixed workspace name (`egeria-workspaces-fs`) doesn't even match this
machine's checkout name (`egeria-workspaces`), so this isn't just an OS path-separator
difference; it's a different checkout layout entirely.

**Effect:** Dr.Egeria command/template execution over MCP is unavailable on any machine other
than the one whose Mac paths happen to be baked in. Core RAG Q&A and report execution are
unaffected — both go through the `pyegeria` MCP connection, which self-heals via the fallback
above.

**Fix shape:** give `dr-egeria` the same machine-relative resolution `pyegeria` already has in
`mcp_config.py`, rather than hand-editing the checked-in JSON per machine — editing it to one
box's paths would just break it on whatever deployment it's currently correct for.

**Update 2026-09-06, from a second session working a live containerised EA deployment (not
touched here — relayed cross-session):**

- **Both hardcoded fields matter, not just `command`.** `args` also points at
  egeria-workspaces' `PyegeriaWebHandler/mcp_server.py`, and the `env` block carries
  `EGERIA_ROOT_PATH` under `/Users/dwolfson/` plus `EGERIA_VIEW_SERVER_URL=https://localhost:9443`
  — wrong inside a container regardless of any path fix, since `localhost` there means the
  container itself, not the host running Egeria.
- **A config-only fix cannot work in the demo/container profile at all.** Verified inside
  `trellis-ea-web`: `pyegeria` is importable (`True`), `dr_egeria_md` is **not** (`False`), `mcp`
  is `2.1.1`, and `mcp_server.py` isn't in the image. So the real fix needs either shipping the
  script plus `dr_egeria_md` into the image, or making the entry skip cleanly when its command is
  unresolvable — right now EA emits a hard error at every container start regardless.
- **No env override exists for the config file's location** — `egeria_context.py`, `auth.py`,
  `perspective_manager.py`, and `mcp_agent.py` each independently build
  `Path(__file__).parent/"configdata"/"mcp_servers.json"`, so there's no way to point a
  deployment at an alternate file without also touching all four call sites.
- Confirms the earlier read on impact: EA still reports "MCP agent pre-warmed on startup" and
  the `pyegeria`-server report tools keep working; only Dr.Egeria command execution from EA chat
  is unavailable.
- **Offer standing from that session:** a running containerised EA is available there to test
  any candidate fix, including rebuilding the image from a branch.

**Update 2026-09-07 — both fixes verified in a container on pop-os, not yet merged to main:**

- `ea/dr-egeria-mcp-resolution` (`4251f58`) — the dr-egeria MCP defect itself. In-container:
  `dr-egeria` is now disabled with a logged reason instead of failing; zero
  "Failed to connect"/"Failed to initialize"/`Errno 2` occurrences at startup, down from two
  every time. `pyegeria` still resolves and connects, MCP agent still pre-warms, EA healthy,
  Portal advisor tile green. (That box has the non-`-fs` sibling checkout this entry's "different
  checkout layout" note called out — `_workspaces_checkout()` resolves to
  `/home/dwolfson/localGit/v6.1/egeria-workspaces` there.)
- `fix/ea-image-carries-repo-config` (`501ce84`) — a second, quieter half of the same problem,
  found while testing the first: the 2026-09-06 `config/` → repo-root move (landed on `main` as
  part of the reorg pulled into this checkout) left the EA Docker image not copying the new
  location, so a containerised EA had been silently running with 44% of its Dr.Egeria command
  index missing — `CommandKeywordIndex` 145 (catalog only, `template_root: None`) instead of the
  full 333 (`catalog: 145, template: 188`) — smaller, not broken, nothing logged. Fixed by copying
  `config/` into the image.
- **Two corrections to the update above, from the same session, worth recording as a methodology
  note as much as a factual one — both came from inferring an outcome from a missing file instead
  of checking the running system:**
  - "Two blockers" (`mcp_server.py` absent + `dr_egeria_md` unimportable) were actually **one**:
    `dr_egeria_md.py` is a local sibling of `mcp_server.py` inside `PyegeriaWebHandler/`, not a
    PyPI package, so the missing workspaces checkout removes both together. A containerised
    `dr-egeria` is therefore achievable (mount a checkout, set `EGERIA_WORKSPACES`), not
    structurally impossible as first stated.
  - "Report specs missing entirely from the image" was wrong — `run_find_report_specs()` returns
    325 in the container from pyegeria's own generated specs; `PYEGERIA_USER_REPORT_SPECS_DIR`
    only augments those. The real gap was just trellis's three annotated files.
- Neither branch has landed on `main` as of this update (not present in this checkout's `git log
  --all`) — status stays `open` until merged; downgrade to `done` once `4251f58`/`501ce84` (or
  their PRs) land.

Related, found the same session: `resource_explorer/auth.py`'s `RE_JWT_SECRET`/
`TRELLIS_JWT_SECRET` lookup reads raw `os.environ` with no `load_dotenv()` anywhere in that
package, so a value that only exists in RE's `.env` is silently invisible to it (must be a real
exported env var) — the same *documented-but-not-actually-.env-backed* shape as this item, in the
sibling app. Not filed as its own entry here since it lives in `resource-explorer`, not
`egeria-advisor`; see `packages/resource-explorer/docs/Backlog.md` if it isn't already tracked
there.

---

## Intent Button Redesign

See also `egeria-workspaces-fs/BACKLOG.md` IB-1 through IB-7 for the full item list.

**Goal:** Replace the current seven buttons with a cleaner four-mode model.

| Mode | Button | `intent_override` | Current state |
|---|---|---|---|
| Learn | **Explain** | `explanation` | Works; needs broader doc corpus (IX-1 to IX-5) |
| Find | **Show me** | `code_help` | Works for code; needs Dr.Egeria templates + report specs (IB-3) |
| Find | **Inspect** | `code_intel` | pyegeria only; needs all-repo expansion (IB-4) |
| Author | **Create** | `create` | Not yet implemented (IB-1) |
| Execute | **Act** | `command` | Single Dr.Egeria command only; needs report + ad-hoc (IB-2, IB-7) |
| Execute | ~~**Plan**~~ | removed | Merged into Create |
| Execute | ~~**Report**~~ | relabelled | Becomes "Run Report" label only (IB-5) |
| Diagnose | **Troubleshoot** | `debugging` | Works but untested in practice |

### IB-1 — Add `Create` intent + `CreateRouter`
**Status:** done  
**Scope:** `advisor/rag_system.py`, new `advisor/agents/create_router.py`, `advisor/web/static/index.html`

CreateRouter logic:
- Contains plan/governance/project/zone/steward/policy → PlanElicitor
- Contains report/show/list/glossar/asset/collection/project type names → ReportSpecElicitor
- Ambiguous → disambiguation response: two buttons "Governance Plan" / "Report Spec"

Remove `Plan` button from `index.html`. Map `plan` intent_override to CreateRouter (backward compatible for any saved links/scripts).

### IB-2 — Expand `Act` to cover report execution + Dr.Egeria commands
**Status:** done  
**Scope:** `advisor/rag_system.py` (Act routing block), possibly new `ActRouter`

Verb-based split inside the `command` intent block:
- SHOW / LIST / GET / FIND / DISPLAY → ReportPipeline.process()
- CREATE / UPDATE / ASSIGN / LINK / REMOVE / DELETE → DrEgeriaActionAgent

Post-run follow-up actions are **conditional on whether a spec was matched**:

| Scenario | Follow-up actions |
|---|---|
| Matched + ran an existing spec | **[Modify spec ▸]** (full spec canvas — columns + all 3 param categories) · **[Run again]** |
| Ad-hoc exec (no matching spec) | **[Save as Report Spec]** · **[Run again with filter]** |

"Modify spec" opens the full Report Spec canvas pre-populated from the matched spec.  
**Depends on RS-1** (parameter panels in canvas) — without those, "Modify spec" only exposes columns, not content_filters / shape_defaults / performance_hints.

### IB-3 — Expand `Show me` to surface Dr.Egeria templates and report specs
**Status:** open  
**Scope:** `advisor/agents/examples_agent.py`, `advisor/rag_system.py`

ExamplesAgent currently: code examples and API method listings.  
Add:
- Dr.Egeria template search (via DrEgeriaTemplateAgent or direct filesystem lookup)
- Report spec catalog search (query `list_inbox()` + match by topic/title)
- Composite response: code example + related template + related report spec

### IB-4 — Expand `Inspect` to cover all repos
**Status:** open
**Priority:** low
**Scope:** `advisor/agents/` (code_intel agent or equivalent), vector indexing

Currently pyegeria + egeria_java only. Should cover:
- egeria-workspaces FastAPI handlers, compose files (low priority)
- egeria-advisor source (the advisor itself) (low priority)

Needs multi-repo search path; may require dedicated collections for workspaces and advisor code.

### IB-5 — Rename `Report` button → `Run Report` in UI (label only)
**Status:** done  
**Scope:** `advisor/web/static/index.html` line ~259

One-line change; do alongside IB-1 so the button set updates atomically.

### IB-6 — "Fork / Customize" per report spec in sidebar
**Status:** open  
**Scope:** `advisor/web/static/index.html` (report list rendering), `advisor/web/app.py`

Each report in the sidebar gets a small "⑂ Customize" link. Clicking it opens the Report Spec Builder pre-populated from that spec's markdown — allows users to extend an existing spec without starting from scratch.

### IB-7 — Conditional post-run follow-up actions in Act
**Status:** open  
**Scope:** `advisor/agents/report_spec_agent.py` or new ActRouter, `advisor/rag_system.py`

No pre-run disambiguation modal. After a successful run, response includes conditional nav buttons:

**Matched existing spec:**
- **[Modify spec ▸]** — opens full Report Spec canvas (columns + content_filters + shape_defaults + performance_hints) pre-populated from the matched spec. **Requires RS-1.**
- **[Run again]** — re-runs the same spec, prompts for search_string override

**Ad-hoc exec (no spec match):**
- **[Save as Report Spec]** — promotes the ad-hoc run to a catalog entry via ReportSpecElicitor.start()
- **[Run again with filter]** — re-prompts for search_string override

The `act_result` response dict should include `matched_spec_id` (populated when a spec was found) so the UI knows which set of buttons to render.

---

### IB-8 — Contextual "learn more" follow-up options after an Inspect answer
**Status:** open  
**Scope:** `advisor/agents/code_intel_agent.py`, `advisor/web/static/index.html`

After a `CodeIntelAgent` answer, offer buttons for related follow-up questions instead of
making the user retype a new query. E.g. after "what is class X":
- **[Class hierarchy ▸]** — re-queries `get_class_hierarchy(X)`
- **[Methods on X ▸]** — new query type: list methods defined on a given class (no existing
  tool for this — `get_class_for_method` goes method→class, not class→methods; would need a
  new `get_methods_for_class(class_name)` tool)
- **[Who inherits from X ▸]** — re-queries `get_class_hierarchy(X)` descendants specifically

Same conditional-buttons pattern as IB-7 (Act). The response dict would need a
`follow_up_options` list (label + re-query text or intent_override) so the UI can render
buttons generically, similar to how `renderIntentClarification()` already renders
`candidates`/`candidate_intents` for the "Show me" disambiguation clarify.

---

### IB-9 — Supplement the Egeria type-name registry with a live type listing
**Status:** open
**Priority:** low
**Scope:** `advisor/egeria_type_registry.py`

`advisor/egeria_type_registry.py` (added to fix Act's element-type extraction dropping the
second word of multi-word type names — "external references" → "External" instead of
"ExternalReference") is currently built from the 75 distinct `target_type` values in
`config/report_specs/report_specs_annotated.json` (repo root) — a static, bundled list that's correct but
only covers types that have a Dr.Egeria create template. It works without a live Egeria
connection (the exact scenario that surfaced this bug — Egeria was unreachable at the time),
but real Egeria has hundreds of open metadata types, most without a Dr.Egeria template.

Consider adding an optional refresh path — a script (or lazy background call) that pulls the
full type list from a live Egeria server via pyegeria (e.g. `EgeriaTech`'s type-listing
methods) and merges it into the cached registry, falling back to the static list when Egeria
isn't reachable. Also worth widening `resolve_type_name()`'s use beyond Act's
`_extract_type_and_filter()` — e.g. `ReportSpecElicitor`, the Report Spec Builder's own
element-type field, and anywhere else free-text type mentions get parsed — since it's a
general-purpose "resolve fuzzy type phrase → canonical Egeria type name" utility, not
Act-specific. The `_ALIASES` dict (currently just `"data product(s)"` → `DigitalProduct`, for
the common industry-vs-Egeria terminology mismatch) should grow as more mismatches surface.

---

## Report Spec Builder

| # | Item | Status | Notes |
|---|------|--------|-------|
| RS-1 | Canvas parameter panels — Content Filters + Shape Defaults + Performance Hints sections | done | Three collapsible `<details>` sections added above column cards. Debounced PATCH on field change. |
| RS-2 | Preview mode (zero-cost stateless run, no result snapshot written) | open | Call exec but discard result; show inline in chat. |
| RS-3 | Meta-level navigation / discovery for ambiguous types ("databases") | open | RAG over `egeria_types` + `egeria_concepts`; present as structured choices. See design doc. |
| RS-4 | "Fork / Customize" entry point from sidebar (see IB-6) | open | Pre-populate elicitor from existing spec. |
| RS-5 | Master-detail parameter inheritance model | deferred | Unresolved: do detail specs inherit content_filters / shape_defaults from master? |
| RS-6 | Parameter profiles ("deep traversal", "quick lookup") | deferred | Named reusable parameter sets. |
| RS-7 | Report spec import/export — feature parity with Plans | done | Added `ReportSpecDocumentManager.import_document()` (validates via `parse_report_spec_markdown`, raises `ValueError` on malformed content — `report_spec_docs.py`), `GET /api/reports/docs/{doc_id}/export` + `POST /api/reports/specs/import` (`app.py`, mirroring the Plans endpoints), and sidebar UI: "⇧ Import" button + modal in the Custom Specs header, "⤓" export button on inbox/outbox rows (`index.html`). |
| RS-8 | Survey Report + Annotation as report-spec target types | open | Prerequisite for the curator-review use case below. The catalog's 150 specs contain **no** survey- or annotation-shaped target type; `Solution Blueprint`/`Solution Component`/`Solution Role` exist but are auto-generated Dr.Egeria create-template attribute sets, not query reports. Same root cause as **IB-9** — the registry is built from the 75 `target_type` values that have a create template, and annotations are not authored that way. IB-9's live type listing fixes both. |
| RS-9 | Pass-through column format for embedded Mermaid | open | Column `format` supports `False \| True \| "bulleted-list"`; a Mermaid source blob must reach the output unmangled. Needed because RE carries the proposal diagram *in the annotation* — pyegeria's own `MERMAID` output cannot draw a proposal, since the proposed components are not yet Egeria elements. |
| RS-10 | Expose `MERMAID` / `REPORT-GRAPH` output formats in the builder | open | Already supported by pyegeria (`view/format_set_executor.py:535,834`) but not offered in the builder UI. Meaningful for graph-shaped reports in a way they are not for tabular catalog reports. |
| RS-11 | Curator-review spec: annotations of one SurveyReport | open | The payoff of RS-8..RS-10, and the crude first tier of curation tooling for RE's *report, then curate* model (`resource-explorer/docs/architecture-recovery-report-then-curate.md`). Run against a single report GUID carried by the RFA. `DataDiscovery.get_annotations_for_element` already defaults `report_spec="Annotations"`, and its parameters map one-to-one onto the three categories (`search_string`→content_filters, `graph_query_depth`/`output_format`→shape_defaults, `start_from`/`page_size`→performance_hints). |

---

## Vector Index Expansion

See `egeria-workspaces-fs/BACKLOG.md` IX-1 through IX-5 for the full item list.

---

## Plan Composition — Basic/Advanced Template Fidelity

| # | Item | Status | Notes |
|---|------|--------|-------|
| PC-1 | `_load_template()` in `governance_plan_agent.py` always loaded the **basic**-tier Dr.Egeria template, regardless of `spec["mode"]` | done (2026-08-29, `17269a9`) | Composition half fixed: `_load_template()` now takes `mode`, loads `root / mode` with a `mode → basic → root` fallback, and the two live call sites that matter (`_rebuild_command_sequence`'s pair of `_load_template(action, mode)` calls) thread the plan's own tier through. Resolved the "decision on merge behavior" this entry had left open by measuring, across all 325 template pairs, that advanced is a strict superset of basic with an identical required-set — so there is no cross-tier merge to perform; advanced mode simply stops discarding fields the user set. Elicitation half (`_build_pending_questions` in `plan_elicitor.py`) is **deliberately left basic-tier** — a documented product decision, not a bug: loading advanced templates there would take a five-command plan from ~42 optional questions to ~137 (measured 2026-08-28, mean 27.4 vs. 8.4 optional questions/command). One remaining unfixed call site, `_load_template(action)` with no `mode` arg in `_handle_legacy_generate` (line ~220) — confirmed dead code, zero callers anywhere in the codebase, not a live gap. |

| B2a | After trellis' pyegeria floor includes `md_processing.templates` (expected 6.2.32): remove the fallbacks | open (blocked on the floor bump) | B2 (BRIEF EA-CATCHUP-AND-DASHBOARDS-2026-11-10, trellis `6959ccd3`) points all three template loaders at the installed pyegeria via `advisor/dre_templates.py`. After the bump, delete `TemplateIndex._legacy_base_paths`, `agents.tools._legacy_templates_root` and `CommandKeywordIndex._find_legacy_template_root`, plus `note_fallback`. Retire `config/dr-egeria-templates/`. Drop the dr-egeria-command-sync skill's EA sync step. The admin page's "Dr.Egeria templates" badge goes red whenever a fallback is in use. |
| B2b | The RAG index still embeds Dr.Egeria templates from the egeria-python clone | open | `collection_config.py` (the collection with `source_paths=["sample-data/templates/basic", "sample-data/templates/advanced"]`) indexes the templates from `ADVISOR_DATA_PATH/repos/egeria-python`, the 2026-08-17 clone. So retrieval can still surface command text the composer no longer uses (e.g. no Views & Reports family). Point the indexer at `md_processing.templates` (`templates_root(level)`) the same way B2 did for the composer, then stop relying on the clone for templates. Out of B2's scope; filed from the Architect's read-back, 2026-11-10. |

---

## Session & Interaction State

Full design: `docs/design/SESSION_AND_INTERACTION_STATE.md`.

Confirmed via code review (Jul 2026) that a user finishing one flow (report
spec / plan draft) and switching to another (e.g. running a pre-built report
from the sidebar) can leave the system acting on stale state from the
previous flow. The `fix/report-selection-execution-rework` merge introduced
a unified `_ctx` "authoritative task/phase state" object which is a real
structural improvement, but did not close the gap — see SS-1.

| # | Item | Status | Notes |
|---|------|--------|-------|
| SS-1 | Fix interaction-mode leak — report run doesn't clear active task context | done | `runReport()`'s `confirmRunReport()` and `setIntent()` now call `clearContext()` on mode switch (`index.html`); backend `_process_query` now clears `context.task`/`draft_id` unconditionally when `query_type_override == 'report'` (`rag_system.py:483-486`), covering both the context-task branches and the legacy `draft_id.startswith("draft_report_")` fallback. |
| SS-2 | Tighten bare-word regex false positive in `report_spec_elicitor` context routing | done | `rag_system.py:498` — tightened to `run\s+(?:the\s+)?spec\|run\s+it` (mirrors the `plan_elicitor` block's `run\s+the\s+plan` fix). `run report X` no longer matches; `run it`/`run the spec`/`execute`/`go ahead`/`proceed` still do. |
| SS-3 | Backend session store — session-scoped ephemeral interaction state | open | New `session_id`, minted client-side (UUID in `sessionStorage`, sent as `X-Session-Id` header), backend in-memory `Dict[session_id, SessionState]` (TTL-evicted). Needed because `user_id` scoping alone is insufficient — demo/shared accounts run multiple concurrent sessions under one `user_id`. Note: `session_id`/`user_id` already exist as params threaded through `_process_query` (`rag_system.py:464-465`) but only for metrics/observability — frontend never sends `session_id` today, and no storage manager uses it. |
| SS-4 | Per-user artifact directory namespacing | open — **priority: medium**; design: `docs/design/PER_USER_ARTIFACT_NAMESPACING.md`. Namespace key and migration settled 2026-08-29 (namespace = the Egeria user we connect as; existing artifacts assigned to `peterprofile`). **Blocked on a prerequisite found while designing it: `settings.egeria_user` is CWD-dependent** — `env_file=".env"` is relative, so a process started outside `packages/egeria-advisor/` silently falls back to the `garygeeke` default and artifacts would scatter across two namespaces. | `DraftManager`, `DocumentManager`, `PlanTemplateManager`, `SessionLogger` (all under `~/egeria-plans/`) and `ReportDraftManager`, `ReportSpecDocumentManager` (under `~/egeria-reports/`, added by the Report Spec Builder work — same unscoped pattern replicated) need `user_id`-scoped roots. Currently any client that knows/guesses a `draft_id` can act on another user's draft — no ownership check exists. `GET /api/plans`, `/api/drafts`, `/api/reports` list every user's documents unfiltered, and `/api/plans` has no auth check at all. This is the remaining half of "does egeria-advisor support multi-user on the same machine?" — the *other* half (live Egeria calls using the wrong/shared identity, and a singleton credential-caching bug that could leak one user's credentials into a concurrent user's request) was root-caused and fixed 2026-07-11: `advisor/auth.py` gained `get_egeria_credentials()`/`resolve_egeria_credentials()`, threaded through `rag_system.py` → `report_pipeline.py`/`dr_egeria_agent.py`/`governance_plan_agent.py`/`egeria_context.py`, and the previously-unauthenticated live-Egeria endpoints (`/api/plans/*/execute\|validate\|retry\|rerun`, `/api/reports/docs/*/execute\|retry`, `/api/templates/*/fields`, `/api/egeria/zones`) now hard-require login. That fix does not touch storage layout — SS-4 (this item) is unaffected and still needed before the app is safe for genuinely concurrent multi-user use, since one user can still see/resume/execute another's plans and drafts by ID. |
| SS-5 | Optimistic concurrency check for same-user concurrent draft edits | deferred | Two sessions of the same (demo) user editing the same draft. Spec already has `updated_at` — reject/warn a save if it moved since this session last read it. Not blocking SS-1 through SS-4. |
| SS-6 | Plan Canvas edited a stale draft copy once a document existed — reorder/notes silently didn't reach the actual document | done | `PlanCanvas.open(draftId)` now checks for an existing `doc_id` and redirects into a new document-backed mode (`openDocument()`): fetches/parses the actual plan document via `_parsePlanMarkdown()` (reused from `plan_editor.js`, purified — `_synthesizePlanMarkdown()` split into a pure `_synthesizePlanMarkdownFrom()`), and Save is now explicit (`ArtifactCanvas.autoSync: false` + `flush()`) rather than auto-syncing every edit to the draft. Also added drag-reorder to the full-screen Plan Editor's command cards (it had none) and fixed a `---` duplication bug in `_parsePlanMarkdown` that would compound on every parse→save round-trip. |
| SS-7 | "Execute" faked a chat message ("execute the plan X") instead of calling a direct endpoint — hung indefinitely (silent LLM call) whenever `context.task` happened to be `plan_elicitor` at click-time | done | Three call sites (Plan Canvas execute button, full-screen Plan Editor's `_executePlanDoc()`, sidebar inbox row's ▶ button) all sent literal chat text and relied on context/routing to correctly interpret it as an execute command rather than a plan-modification instruction — if that interception failed (e.g. `_spec_d` didn't resolve in the `plan_elicitor` context branch in `rag_system.py`), it silently fell through to `continue_draft()`, an LLM-based refinement call, with no console output until (if ever) it completed. SS-6's Canvas fix made this reachable for the first time in practice, since the Canvas no longer closes after a plan is generated, so `context.task == 'plan_elicitor'` can now be set at the exact moment "Execute" is clicked. `Validate` already called `POST /api/plans/{doc_id}/validate` directly and never had this problem — added the missing `POST /api/plans/{doc_id}/execute` to match, and switched all three call sites to it, bypassing chat-text routing entirely for this action. |
| SS-8 | `MCPClient._send_request()`'s 30s `invoke_tool()` timeout could never actually fire — a genuine, unrecoverable hang if Dr.Egeria's MCP subprocess didn't respond | done | Confirmed via `py-spy dump` on a live hang: stuck exactly in `_send_request` → `self.process.stdout.readline()`, a plain blocking call on a `subprocess.Popen` stream, called directly inside an `async def` with no thread delegation. Calling blocking I/O straight from a coroutine freezes the event loop thread itself — `asyncio.wait_for()` can only act at an actual await/cancellation point, and there wasn't one while the thread was stuck inside `readline()`, so the configured 30s timeout was silently inert regardless of how long the MCP server took (or never) to respond. Fixed by running the write and each `readline()` via `loop.run_in_executor()`, so `wait_for()`'s cancellation has something to actually interrupt. Independent of whatever caused Dr.Egeria itself to stop responding in this instance — this only makes egeria-advisor fail gracefully (a clear `MCPTimeoutError` after 30s) instead of hanging its own worker thread forever. |
| SS-9 | A crashed Dr.Egeria execution (e.g. Postgres out of shared memory) was reported as full success — all green, output file created, no indication anything failed | done | `_parse_dr_egeria_response()`'s fallback for a non-JSON response unconditionally returned `success=True` ("Plain text — Dr.Egeria ran but didn't return structured output yet"). An unhandled crash on Dr.Egeria's side also comes back as plain text, not the expected `{"success": ...}` envelope, so this silently reported real failures (confirmed live: a Postgres `no space left on device` shared-memory error) as clean successes. Added `_PLAIN_TEXT_FAILURE_RE` — scans the plain-text response for common failure signals (`error`, `exception`, `traceback`, `no space left`, `connection refused`, etc.) before trusting it as success; verified it flags the exact Postgres error text and doesn't false-positive on a clean success message. |
| SS-10 | Resuming a plan from "Active Drafts" after it had already been executed once produced `Plan document <id> not found in inbox` on Execute | done | Root cause: a draft spec's `doc_id` field is set once, when the plan is first generated into an inbox document, and was never updated when that document was later executed and moved to outbox under a new `{orig}_executed_{ts}` filename — `DocumentManager.load()` does an exact filename match with no `_executed_*` fallback, so resuming the old draft handed back a `doc_id` that resolved to nothing anywhere. Fixed end-to-end: added `DraftManager.update_doc_id(draft_id, new_doc_id)` (`governance_draft.py`); `GovernancePlanAgent.execute()` now accepts an optional `draft_id` param and calls it after a successful `move_to_outbox`, keeping the draft's `doc_id` in sync with the live outbox filename; `rag_system.py`'s `plan_elicitor` context branch and `POST /api/plans/{doc_id}/execute` (`app.py`) both thread `draft_id` through; all three frontend execute call sites (Plan Canvas button, full-screen Plan Editor's `_executePlanDoc()`, sidebar inbox row's ▶ button) now send their known `draft_id` in the request body so the sync actually fires regardless of which UI surface triggered execution. |
| SS-11 | "Save as Template" and "Save As" from inside the full-screen Plan Editor appeared to do nothing — the confirmation modal was actually rendering behind the editor, invisible | done | `#save-as-template-modal` and `#builder-title-modal` (reused for "Save As") both used `z-50`, same as `#plan-editor-overlay`. All three are `fixed inset-0` and the editor overlay has an opaque `bg-slate-950` background; since it appears later in the DOM, equal z-index ties resolve in its favor, so it painted over the modal instead of the modal appearing on top of it. Confirmed live — the user found the modal "lurking behind the plan window." Bumped both modals to `z-[60]` so they always render above the editor overlay. `#cmd-picker-modal` (opened from Plan Canvas) wasn't affected — Plan Canvas is a docked side panel, not a full-screen overlay, so no z-index clash there. |
| SS-12 | SS-10's fix regressed — the same `doc_id` staleness bug ("not found in inbox"/"not found") resurfaced through *three more* independent call sites (chat-typed "execute", the Active Drafts sidebar's resume click, and the Plan Canvas's own Execute button never re-pointing itself at the post-execution doc_id) that were added or left un-migrated after SS-10 | done | Root cause of the regression: SS-10 fixed each known call site individually (thread `draft_id` through, call `update_doc_id`) rather than making the invariant structurally impossible to violate — nothing stopped a new caller from reading `spec.get("doc_id")` directly and trusting it, which is exactly what kept happening. Consolidated into two shared primitives on `DraftManager` (`governance_draft.py`): `resolve_live_doc_id(draft_id, spec=None)` — self-heals a stale id by finding the newest outbox file sharing the pre-`_executed_` base name (same heuristic `check_doc_ids()`/`_find_repair_candidate()` now share) — and `sync_document(draft_id, spec, new_content, edited_by=None)`, replacing the recurring "`doc_manager.update(...)` then separately `dm.save(spec)`" pattern that could desync. Every doc_id read that precedes a load/edit/execute now goes through the resolver: `GET /api/drafts/{draft_id}` (self-heals transparently for every frontend consumer — Plan Canvas's `open()`, the sidebar, chat resume — with no frontend changes needed), `PATCH /api/drafts/{draft_id}/commands`, `PlanElicitor.process()`'s desync check, `PlanElicitor.resume()`, `PlanElicitor._handle_refine()`'s 5 document-write sites, `GovernancePlanAgent.save_as_template()`. Also fixed a genuinely-live silent-failure bug found while tracing this: the canvas PATCH endpoint would silently skip the document write (and still return `{"status": "ok"}`) whenever `doc_id` was stale, since `doc_manager.load(doc_id)` returned `None` and the sync block was just skipped with no warning. `GovernancePlanAgent.execute()`'s own doc_id handling (operates on the id it just loaded synchronously moments earlier) was deliberately left alone — not part of the "many independent stale readers" problem. Confirmed report-spec drafts (`ReportDraftManager`/`report_draft.py`) do **not** share this bug class and don't need the same fix — `ReportSpecDocumentManager.move_to_outbox()` never renames/moves the inbox source file (an explicit documented invariant, "Report Spec Builder Design Rules" rule A in CLAUDE.md — only a separate result snapshot goes to outbox), so a report-spec draft's `doc_id` structurally can't go stale the way a governance-plan draft's can. Also added a lighter mitigation for the residual cross-tab/cross-surface case resolution can't fully close (two sessions with the same plan open, one executes elsewhere): Plan Canvas's `save()` and the full-screen Plan Editor's `_savePlanEdits()` now catch a save failure, explain what likely happened, and reopen via the draft (which resolves the current doc_id) instead of leaving a dead editor pointed at a doc_id that no longer exists — deliberately does not auto-retry the write itself, which could silently clobber whatever changed the document. This does not address SS-5 (concurrent edits from two sessions of the same user) — only the "document got renamed/moved" case that this bug class covers. Two new admin actions, `check_draft_doc_ids`/`repair_draft_doc_ids` (`POST /api/admin/maintenance/{action}`, buttons in `/admin`), audit/repair all drafts in one pass using the same primitives, for recovering from any staleness that predates this fix. |

---

## Trellis Consolidation (RE ↔ EA)

Full detail: `docs/re-ea-consolidation-audit.md` (root of the Trellis workspace). Cross-app
items that require changes in both `resource-explorer` and `egeria-advisor` are filed in
`packages/resource-explorer/docs/Backlog.md` under "Platform & orchestration" (TC-1 through
TC-3 below point there); this section holds the items scoped to EA alone.

| # | Item | Status | Notes |
|---|------|--------|-------|
| TC-1 | Adopt the shared `QueryCache` once extracted | open | See `packages/resource-explorer/docs/Backlog.md`, "extract a shared query cache" — EA's `query_cache.py` is named/documented as LRU but isn't (plain `dict`, no `move_to_end()` on access, just FIFO eviction). The extraction fixes this for EA as a side effect; EA's hit/miss/`most_popular` telemetry gets layered onto the shared class rather than lost. |
| TC-2 | Adopt the shared BeeAI agent base/runner once extracted | open | See `packages/resource-explorer/docs/Backlog.md`, "extract the BeeAI agent base/runner" — `advisor/agents/base.py`'s `BaseAdvisorAgent` and RE's `BaseExplorerAgent` hand-roll the same `_build_agent()`/`_run_agent()` BeeAI-in-a-thread pattern, copy-pasted rather than convergent. Check first whether the second, "legacy... not using BeeAI" `BaseAgent` class in this file is still referenced anywhere — it should probably be deleted regardless of this extraction. |
| TC-3 | Adopt RE's dual-backend registry connection-management pattern | open | See `packages/resource-explorer/docs/Backlog.md`, "EA should adopt RE's dual-backend registry connection-management pattern" — `db_consolidated.py`'s `ConsolidatedDBManager` reinvents a narrower, Postgres-only version of RE's `ConnectionWrapper`/SQLAlchemy dual-engine abstraction. Schemas stay separate; only the connection/DDL-provisioning mechanism would move. Not urgent while EA's Postgres-only assumption holds. |
| TC-4 | Add Phoenix/OTel tracing — EA has none today | done (2026-09-07) | Ported RE's `observability/phoenix_client.py` pattern to `advisor/phoenix_client.py` (flat module, matching EA's existing convention — `mlflow_tracking.py` etc. — rather than a new subpackage): reachability-gated init (own `_is_collector_reachable`, same shape as `mlflow_tracking.py`'s existing `_is_tracking_server_reachable`), `BatchSpanProcessor` (never `Simple` — see RE's docstring for the measured 7.89s-per-span cost against a dead collector), `BeeAIInstrumentor`, wired into `RAGSystem.__init__` alongside the existing MLflow init. `PhoenixConfig` already existed in `config.py` (`enabled=False` default, kept — nothing previously read it) but nothing consumed it; also fixed its `collector_endpoint` default (`config.py` and `advisor.yaml` both said `http://localhost:6006`, missing the `/v1/traces` OTLPSpanExporter needs — it uses `endpoint` verbatim, unlike `OTEL_EXPORTER_OTLP_ENDPOINT`'s auto-append). Added `openinference-instrumentation-beeai`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp` as base deps (construction doesn't connect, so safe unconditionally) and an `arize-phoenix` `phoenix` extra for the collector server itself, matching RE's split. Tests: `tests/test_phoenix_client.py` (8 tests, mirrors RE's `test_observability_reachability.py`) — verified live against a real socket for both the unreachable-skip and the reachable-and-instruments paths, plus idempotency. Full suite: 315 passed, 3 pre-existing failures unrelated to this change (auth JWT signature tests, one data-root-resolution test — none in a module this touched). |
| TC-5 | Refactor `web/app.py` toward RE's router-per-domain factory pattern | **done (2026-09-07)** | RE's `web/app.py` is 106 lines, 20 `include_router()` calls to one file per domain in `web/routes/`. EA's `web/app.py` started at 2178 lines, 71 routes across 14 groups, only `admin.py` extracted. Done in 6 slices (full per-slice reasoning in `git log`, each slice its own commit): (1) `shared.py` (cross-cutting state — `_get_rag()`'s singleton, `QueryRequest`/`FeedbackRequest`, `_intent_meta()`, report-catalog helpers) + `auth.py` (6 routes); (2) `feedback.py` (4); (3) `query.py`+`plan_templates.py`+`sessions.py` (2 each) + a deliberately partial `templates.py` (1 of 2); (4) `drafts.py` (5, self-contained); (5) `plans.py` (all 19, self-contained); (6) `reports.py` (23 routes: docs/drafts/specs CRUD, `discover_draft_schema_internal`/`_SCHEMA_CACHE`) + completing `templates.py`'s deferred `Column/fields` route, which imports `discover_draft_schema_internal` from `reports.py` now that it exists — the reason that route waited for last. `app.py`: 2178 → 1814 → 1702 → 1447 → 1300 → 914 → **231 lines** (89% reduction, close to RE's 106). `advisor.web.app` still re-exports `_STATIC`/`_intent_meta` for `advisor.rag_system`'s live external lazy import — verified resolving after every slice. Slice 6 also found and fixed 2 test files hardcoding the pre-refactor module path: `tests/unit/test_report_spec_planner.py` and `tests/unit/test_request_context_namespacing.py` both imported/monkeypatched `discover_draft_schema_internal`/`_SCHEMA_CACHE`/`get_report_draft_schema`/`patch_report_draft_columns` from `advisor.web.app` — a real gap `ruff`/syntax checks can't catch, since a route handler's bare-name call resolves against its *own* defining module's globals: `monkeypatch.setattr("advisor.web.app.X", ...)` after the move would silently mock nothing, since the code moved to `reports.py`. Both updated to `advisor.web.reports`; caught immediately by the regression suite (5 failures) and fixed. Verified every slice the same way: `ruff check --select F` clean; `tests/unit/test_login_required_middleware.py` (13 tests) passing; full suite 315 passed every time (baseline match, 4 pre-existing unrelated failures deselected); live `TestClient` requests against every route in every new router, including full real lifecycles with artifact cleanup confirmed (plans: import→export→save→fork→save-as→save-as-template→delete→restore→purge; reports: builder-draft→columns-patch→schema-discovery→delete). One verification-method false alarm in slice 1 (resolved, not a real bug): naive `app.routes` enumeration makes `include_router()`-added routes look absent on this FastAPI version (lazy `_IncludedRouter` wrapper) — real HTTP requests are the check that holds. |
| FB-1 | `POST /api/feedback` hangs indefinitely (pre-existing, found during TC-5 slice 2) | open | Discovered verifying the feedback router extraction: a live `TestClient` POST to `/api/feedback` with a valid token never returns — reproduced identically against the *unmodified, pre-TC-5* `app.py`, so this is not a router-extraction regression, and TC-5 slice 2 moved this code verbatim without touching its logic. Freezes right after `advisor.feedback_collector:__init__`'s "Initialized feedback collector" log line, before `record_feedback()`'s own code runs any further logging — narrows it to something inside `FeedbackCollector.record_feedback()` (`advisor/feedback_collector.py`) blocking with no timeout, most likely a DB write via `ConsolidatedDBManager`/`db_consolidated.py` (ANALOGY to TC-3's write-up: EA's Postgres-only connection layer has no documented pool-exhaustion or lock-wait timeout). GET `/api/feedback/extended` and PATCH `/api/feedback/extended/{idx}` — the two routes that only touch the JSONL file, not `FeedbackCollector` — both work correctly and returned in the same test run, so the hang is specific to `record_feedback()`'s DB path, not something environmental about running `TestClient` against this app in general. Needs a `py-spy dump`-style trace (SS-8's approach) to pin the exact blocking call before fixing. |
| TC-6 | Check whether `query_classifier.py` is still live | answered (2026-09-07) | Not an RE/EA question — found while comparing routing modules across the two apps. EA carries three parallel-looking classifiers: `query_processor.py` (674 lines, the active priority-tiered pattern system, used by `rag_system.py`), `query_classifier.py` (487 lines, a `QueryType`/`QueryTopic` dataclass-based system), and `llm_intent_classifier.py` (99 lines, the LLM fallback for ambiguous cases). Traced the full reference chain: `query_classifier.py` is imported by `cli_integration.py`, `interactive_response.py`, `pyegeria_agent.py`, `collection_metrics.py`, `assembly_metrics.py`, and `mlflow_tracking.py` — not dead by import count — but every one of those funnels into `advisor/agents/conversation_agent.py`, which is wired up only by `advisor/cli/agent_session.py`, an interactive CLI REPL. Confirmed neither `rag_system.py` (the live web query router) nor anything under `advisor/web/` imports `query_classifier`, `conversation_agent`, `cli_integration`, or `interactive_response` at all — `pyegeria_agent.py` even imports `classify_query as classify_query_type` and never calls it. **So the whole chain is a self-contained CLI-only subsystem, unreachable from the web app** — dead from `rag_system.py`'s perspective, live only if `advisor/cli/agent_session.py`'s REPL is still used. Open question is now narrower: is that CLI REPL itself still wanted? If not, the whole chain (`query_classifier.py`, `cli_integration.py`, `interactive_response.py`, `pyegeria_agent.py`, `conversation_agent.py`, `agent_session.py`) can go together; if the REPL stays, `query_classifier.py` stays with it — either way it should not be removed in isolation. |
| TC-8 | Extract `trellis-auth` — EA's login is the only one; RE has none | **done, found already-complete 2026-09-07** | The extraction itself landed 2026-09-04 (`e734241`, "Extract trellis-querycache and trellis-auth as shared packages") — `packages/trellis-auth/` exists, both `advisor/auth.py` and `resource_explorer/auth.py` import it for the app-neutral JWT/Portal-SSO pieces, and `resolve_egeria_credentials`'s service-account fallback stayed app-specific exactly as designed. This entry was never marked done. Investigating further (2026-09-07, prompted by a follow-up question) found the **entire** 4-part plan in `docs/trellis-auth-extraction.md` had landed, not just the extraction — RE's login UI, RE adopting the login-required policy, and a declared service identity for unattended survey/scheduler runs (`resource_explorer/egeria_identity.py`'s `EgeriaIdentity(is_service_account=True)`) were all done the same day, 2026-09-04, and that doc's own "still to do" lists were stale on all of them. Corrected `docs/trellis-auth-extraction.md` and `packages/resource-explorer/docs/Backlog.md` to match. One genuinely open item remains, filed there rather than here since it's RE-only: 14 of RE's original 26 `os.getenv("EGERIA_USER", …)` call sites still haven't been collapsed onto the authenticated identity. |
| TC-7 | `code_symbol_store.py`'s import from `advisor/ingest.py` (renamed from `ingest_to_milvus.py`) is dead | resolved | Confirmed 2026-08-25: the only `code_symbol_store` import in `ingest.py` is inside `CodeIngester._extract_java_symbols()`, itself already marked "Deprecated, no longer called from `ingest_file()`" (AST-ownership-transfer plan decision D8, kept as a rollback safety net). Not called anywhere live — no further action needed. `ingest_to_milvus.py` itself has been renamed to `ingest.py` (2026-08-25, part of the Milvus-code-removal pass) since its content never actually referenced Milvus, only its filename did. |

---

## Done (recent)

| Item | Date | Notes |
|---|---|---|
| IB-1 — Create intent + CreateRouter | 2026-06-26 | `create_router.py`, `rag_system.py`, `index.html`, `app.py` |
| IB-2 — Act verb split (read→pipeline, write→DrEgeria) + conditional post-run buttons | 2026-06-26 | `rag_system.py`, `index.html` |
| IB-5 — Rename Report → Run Report, Plan → Create | 2026-06-26 | `index.html` |
| RS-1 — Canvas parameter panels (Content Filters / Shape Defaults / Performance Hints) | 2026-06-26 | `report_spec_canvas.js`, `index.html` |
| `validate_report_spec` — fix to check actual client class, not EgeriaTech | 2026-06-26 | `report_spec_parser.py` |
| Lifecycle fix — spec stays in inbox after execute; result snapshots in outbox | 2026-06-26 | `report_spec_docs.py`, `report_spec_agent.py` |
| Three-category parameter model (content_filters, shape_defaults, performance_hints) | 2026-06-26 | `report_draft.py`, `report_spec_elicitor.py`, `report_spec_parser.py`, `app.py` |
| Routing fix — "show X with their Y and Z" → Report Spec Builder, not pipeline | 2026-06-26 | `rag_system.py` |
| Design doc + user guide for report spec builder | 2026-06-26 | `docs/design/REPORT_SPEC_BUILDER_DESIGN.md`, `docs/user-docs/REPORT_SPEC_GUIDE.md` |

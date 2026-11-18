# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Context for new conversations:** See `docs/PROJECT_SUMMARY.md` for a complete
> phase history, capabilities overview, lessons learned, and next steps.
> See `docs/literate-governance-plan.md` for full LGCI design documentation.
> See `docs/design/REPORT_SPEC_BUILDER_DESIGN.md` for report spec builder architecture
> and `docs/user-docs/REPORT_SPEC_GUIDE.md` for usage.

## Project Overview

Egeria Advisor is a RAG (Retrieval-Augmented Generation) system providing intelligent assistance for [Egeria](https://egeria-project.org/) and pyegeria users. It uses local LLMs (Ollama), sentence-transformer embeddings, and a pgvector (PostgreSQL) vector store across 9 specialized collections (~88,900 entities total).

**Current state (July 2026):** Phases 1–12b complete. Core RAG, multi-agent routing, Web UI with Plan Canvas, LGCI plan generation and execution are all operational, including natural-language reorder and relationship editing. Primary active work: LGCI quality improvements and Egeria integration for planning.

**Models in use:**
- `llama3.1:8b` — RAG Q&A (speed)
- `qwen2.5-coder:32b` — LGCI planning: narrative, refinement, complex extraction (quality)
- `codellama:13b` — code generation
- `sentence-transformers/all-MiniLM-L6-v2` — embeddings

## Setup

```bash
# Activate virtual environment
source activate_venv.sh

# Install with dev dependencies
pip install -e ".[dev]"
```

Requires Python 3.12+. External services must be running locally:
- **pgvector** at `localhost:5442` (PostgreSQL with pgvector extension — database: `egeria_advisor`, user: `egeria_advisor`)
- **Ollama** at `localhost:11434` (LLM inference — see model pull commands below)
- **MLflow** at `localhost:5025` (optional, for experiment tracking)
- **Arize Phoenix** (optional, off by default — `observability.phoenix.enabled`) — `pip install -e ".[phoenix]"` then `python -m phoenix.server.main` → `localhost:6006`. Same reachability-guarded init pattern as resource-explorer's (`phoenix_client.py`, TC-4/BACKLOG.md): checks the collector once cheaply and no-ops if nothing's listening, rather than paying an OTLP export timeout per span.

### First-time database initialization

Whether the Postgres role/database already exist depends on the environment:

- **Inside an egeria-workspaces checkout** (the common case): the shared Postgres container
  (`egeria-shared-postgres`, from egeria-workspaces-fs's
  `compose-configs/shared-infra/shared-infra.yaml`) is normally already running, and its init
  script already creates the `egeria_advisor` role/database and runs `CREATE EXTENSION IF NOT
  EXISTS vector` for it. Nothing to do for step 1 below.
- **Standalone/from-scratch** (no egeria-workspaces): nothing pre-exists — create the role and
  database yourself, e.g. `createuser egeria_advisor` / `createdb -O egeria_advisor
  egeria_advisor` against Postgres on `localhost:5442`, matching `pgvector_host`/
  `pgvector_port`/`pgvector_dbname`/`pgvector_user` in `advisor/config.py` (overridable via
  `.env`). Or use resource-explorer's documented `docker run ... pgvector/pgvector:pg17` —
  the two apps share this same database.

1. **Postgres role/database** — see above; not automated by this repo either way (this repo has
   no init script of its own — it either relies on egeria-workspaces-fs's, or you create these
   manually for a standalone Postgres).
2. **Metrics/symbol-store tables** (`query_metrics`, `code_symbols`, `code_relationships`, `indexed_files`, `index_metadata`, `collection_health`, `system_metrics`, `error_log`, `plan_events`, `ingest_log`) self-provision — `ConsolidatedDBManager.connect()` (`advisor/db_consolidated.py`) runs its `CREATE TABLE IF NOT EXISTS` DDL the first time anything touches it (e.g. starting the web app, or any script that constructs `MetricsCollector`/`CodeSymbolStore`). Nothing extra to run for these.
3. **Vector collection tables** — `PgVectorStore.provision_schema()` (`advisor/vector_store_pg.py`) creates `CREATE EXTENSION IF NOT EXISTS vector` plus each collection's table + HNSW index. `advisor/web/app.py`'s startup handler calls it automatically, so starting the web UI is enough to provision empty tables for all 9 collections (`auto_provision_on_insert` is still `False` for inserts themselves — provisioning is startup-only, not per-write). CLI-only usage (no web server) or the `scripts/ingest_*.py` pipeline should still call `PgVectorStore().provision_schema()` explicitly before first use, since it's idempotent (`IF NOT EXISTS`) and safe to call repeatedly. EA's tables are all unqualified in the `public` schema — unlike resource-explorer's `resource_explorer` named schema, there's no separate schema-creation step needed here.

### LLM models (Ollama)

```bash
ollama serve &                    # skip if already running as a background service
ollama pull llama3.1:8b           # RAG Q&A
ollama pull qwen2.5-coder:32b     # LGCI planning (narrative, refinement, extraction)
ollama pull codellama:13b         # code generation
```

## Commands

```bash
# Start the web UI (primary interface)
python -m advisor.web.app          # → http://localhost:8880
# or: uvicorn advisor.web.app:app --reload --port 8880
# or: scripts/run_web.sh            # HTTP always + HTTPS too if ADVISOR_SSL_CERTFILE/KEYFILE are set in .env

# Query (one-shot CLI)
egeria-advisor "What is a glossary term in Egeria?"

# Interactive multi-turn session
egeria-advisor --interactive

# Agent mode (conversational with memory via BeeAI)
egeria-advisor --agent

# Disable MLflow tracking
egeria-advisor --no-track "Show me asset management examples"

# Terminal dashboard (5-second refresh)
python -m advisor.dashboard.terminal_dashboard

# Incremental indexing for a collection
python -m advisor.incremental_indexer --collection pyegeria

# Count vectors per collection
python scripts/count_vectors.py
```

## Testing

```bash
# Quick E2E test suite
python scripts/test_end_to_end.py --quick

# Full test suite
python scripts/test_end_to_end.py --full

# Specific categories
python scripts/test_end_to_end.py --categories environment,config,vector_store

# Pytest with coverage
pytest tests/ -v
pytest --cov=advisor --cov-report=html

# Specialized test scripts
python scripts/test_incremental_indexing.py
python scripts/test_vector_search.py
```

## Architecture

### Query Flow

```
User Query  [+ optional perspective + optional intent_override]
  → Web UI (advisor/web/static/index.html)    ← browser SPA
  → FastAPI (advisor/web/app.py)               ← /api/query
  → RAGSystem (advisor/rag_system.py)          ← main orchestrator
      ├─ QueryCache (advisor/query_cache.py)    ← checked first; large speedup on hits
      ├─ QueryProcessor (advisor/query_processor.py)  ← pattern-match classifier (routing.yaml)
      │    └─ if 'general': LLMIntentClassifier ← zero-temp LLM call → refined intent
      │                      (LIVE_DATA/CODE_HELP/CONCEPT/WRITE_COMMAND/AMBIGUOUS)
      │
      ├─ Format/role-aware routing (before pipeline dispatch, skipped when intent_override set)
      │    ├─ explicit Dr.Egeria phrasing         → DrEgeriaTemplateAgent
      │    ├─ explicit hey_egeria/CLI phrasing     → CLICommandAgent
      │    ├─ explicit Java phrasing               → clarification (Java generation unsupported)
      │    ├─ developer|data_engineer + explicit code/python signal
      │    │    → ExamplesAgent (advisor/agents/examples_agent.py; CodeIntelAgent for structural code questions)
      │    └─ any role + ambiguous "show me"/example signal (no explicit format signal)
      │         → clarification response (Python vs CLI vs Dr.Egeria)
      │
      ├─ quantitative  → Analytics module (direct SQL answer)
      ├─ relationship  → RelationshipQueryHandler
      ├─ report        → ReportPipeline (advisor/report_pipeline.py) via MCP
      │                  ← semantic pre-check (_is_report_query, threshold 0.50)
      │                  ← blocked when _CODE_EXAMPLE_SIGNALS present in query
      ├─ command (+ template/sample/example keyword)
      │    → DrEgeriaTemplateAgent (advisor/agents/dre_template_agent.py)
      │      ← filesystem lookup: {EGERIA_ROOT_PATH}/Templates/Dr-Egeria-Templates/{level}/
      ├─ command (no template keyword)
      │    → DrEgeriaActionAgent (advisor/agents/dr_egeria_agent.py)
      ├─ code_search|example → ExamplesAgent (BeeAI + direct-retrieval fallback)
      │    ├─ method-discovery queries ("what methods", "what api", "list methods", …)
      │    │    → API reference mode: returns structured class/method table
      │    └─ code-example queries → runnable Python example (canonical pattern)
      ├─ explanation|best_practice|comparison|debugging|general
      │    → DocAgent (advisor/agents/doc_agent.py)
      └─ fallback → RAG retrieval + LLM generation
           ├─ CollectionRouter (advisor/collection_router.py)
           ├─ MultiCollectionStore (advisor/multi_collection_store.py)
           │    └─ pgvector (HNSW index, 384-dim sentence-transformer embeddings)
           ├─ LLMClient (advisor/llm_client.py)
           └─ PromptTemplates (advisor/prompt_templates.py)
                └─ perspective addendum injected into system prompt when set
```

MLflow tracking runs in a background daemon thread after `query()` returns — it does not block the CLI response.

### Web UI (`advisor/web/`)

Single-page app served by FastAPI at `http://localhost:8880` (default port).

```bash
# Start the web UI
python -m advisor.web.app
# or
uvicorn advisor.web.app:app --reload
```

**Layout:**
- **Header**: Egeria logo + "Egeria Advisor" title + MCP status dot (green = connected, red = disconnected)
- **Left sidebar** (top): Available Reports grouped by topic — click any report to open the run modal
- **Left sidebar** (bottom): Recent Queries — click to restore a query
- **Chat area**: markdown-rendered Q&A with source citations and 👍/👎 feedback
- **Input area**:
  - **As:** perspective selector — Anyone / Developer / Data Engineer / Data Steward / Governance
  - **Intent:** Auto / Explain / Show me / Report / Act / Troubleshoot

**Report modal**: Opens when a report is clicked from the sidebar. The *Search string* field is passed as `search_string` to the MCP `run_report` tool (leave blank for all records, or enter a filter like `finance`).

**Perspective selector**: Stores the selected role and sends it with every query. The backend injects a role-specific addendum into the system prompt and tags the user message with `[User role: ...]`, so the LLM adjusts the depth, terminology, and focus of its response accordingly.

**API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | index.html |
| POST | `/api/query` | Run query; accepts `query`, `intent_override`, `search_string`, `perspective` |
| GET | `/api/reports` | Report catalog by topic |
| GET | `/api/status` | MCP server connection status |
| POST | `/api/feedback` | Record 👍/👎 |

### Vector Collections (9 active)

| Collection | Content |
|---|---|
| `pyegeria` | Python SDK code & tests |
| `pyegeria_cli` | hey_egeria CLI commands |
| `pyegeria_drE` | Dr. Egeria markdown translator code |
| `egeria_java` | Core Java library (OMAS, OMAG, OMRS) |
| `egeria_concepts` | Core concept definitions |
| `egeria_types` | Type system and schema definitions |
| `egeria_general` | Tutorials, guides, and how-tos |
| `egeria_workspaces` | Jupyter notebooks and deployment examples |
| `egeria_templates` | Dr. Egeria markdown command templates |

`egeria_docs` is disabled — split into `egeria_concepts`, `egeria_types`, and `egeria_general`.

The pgvector table for `pyegeria_drE` is named `pyegeria_dre` (normalized to lowercase). The `_TABLE_NAME_MAP` in `advisor/vector_store_pg.py` handles this mapping.

The `CollectionRouter` selects 1–N collections per query based on classified intent. RAG parameters: chunk_size=512, top_k=10, min_score=0.30.

### Vector Store Backend

`BaseVectorStore` (`advisor/vector_store_base.py`) is the abstract base class. `PgVectorStore`
(`advisor/vector_store_pg.py`) is the sole implementation — uses `ThreadedConnectionPool`.
Milvus was the original backend but has been fully removed (migrated Apr 2026, code/config/
dependency deleted Jul 2026).

`get_vector_store()` in `advisor/vector_store.py` reads `vector_store_backend` from `config/advisor.yaml` and returns a `PgVectorStore` instance.

### Agent Modes (`advisor/agents/`)

| Agent | File | Handles |
|---|---|---|
| `DrEgeriaActionAgent` | `dr_egeria_agent.py` | `command` queries — composes a Dr.Egeria script and runs it in-process via the `md_processing.v2` dispatcher (no MCP — see rule 24). **A chat message never writes** (confirm gate, 2026-11-13): `handle()` runs `validate` only, shows the script and the result, and registers a one-shot pending action; only `confirm(pending_id, digest)` processes it — same sign-in, within `CONFIRM_TTL_S`, with the SHA-256 of the script that was shown. `rag_system`'s `dr_egeria_confirm` context task routes a message that is *only* "yes"/"no" to `confirm`/`cancel`. **Some commands are never composed from chat** (`advisor/chat_command_policy.py`): pyegeria's `chat_allowed: false` on a command or its family's compact file, and until that ships the `CHAT_DENIED_FAMILIES` deny-list (MetadataExpert: type system and raw instances); refused in words before anything is extracted or composed. The same policy applies to LGCI plans: `plan_validator.validate_commands` leaves such a command out with a "Left out of the plan" warning (the keyword index still recognises it, so a request for it is refused, not remapped), `/api/actions` never offers it, and `prepare_execute`/`execute`/`retry` refuse a plan carrying one before dispatch |
| `DrEgeriaTemplateAgent` | `dre_template_agent.py` | `command` + template/sample/example keyword — returns pre-generated Dr.Egeria markdown templates from the filesystem |
| `ExamplesAgent` | `examples_agent.py` | `code_search` / `example` — generates runnable pyegeria code examples *or* structured API-reference listings (method-discovery mode) |
| `DocAgent` | `doc_agent.py` | `explanation` / `best_practice` / `comparison` / `debugging` / `general` — conceptual answers from indexed docs |
| `ConversationAgent` | `conversation_agent.py` | Multi-turn sessions (BeeAI framework) |
| `CLICommandAgent` | `cli_command_agent.py` | hey_egeria CLI command lookup and generation |
| `CodeIntelAgent` | `code_intel_agent.py` | `code_intel` ("Inspect") — structural codebase facts (class info/docstring, inheritance, method→class lookup, hierarchy, stats) via live SQL over the `code_symbols`/`code_relationships` symbol table, not RAG |
| `GovernancePlanAgent` | `governance_plan_agent.py` | `plan` queries — orchestrates full plan lifecycle: decompose → validate → generate → execute → outcome |
| `OutcomeReporter` | `outcome_reporter.py` | Post-execution: selects and runs verification reports, synthesises outcome narrative, appends to plan document |

### LGCI — Plan Document Lifecycle

The **Literate Governance with Context Intelligence (LGCI)** feature allows users to describe a governance task in plain language and receive a complete, executable Plan Document.

**Key files:**
- `advisor/agents/governance_plan_agent.py` — orchestrator: `_decompose_intent` (two-stage + keyword index) → `_entities_to_commands` → validator → `_compose_document` → `execute()`
- `advisor/agents/plan_elicitor.py` — multi-phase conversational Q&A (`confirm_commands` → `elicit_required` → `generate` → `refine` → `template_offer`); includes restart path ("completely wrong") and confidence-gated "Did you mean X?" suggestions
- `advisor/governance_draft.py` — `DraftManager`: persists in-progress sessions to `~/egeria-plans/drafts/`; `builder_mode` flag for Plan Editor drafts; `resolve_live_doc_id()`/`sync_document()` are the required entry points for reading/writing a draft's plan document — see rule 32
- `advisor/governance_docs.py` — `DocumentManager`: inbox/outbox lifecycle for completed plans
- `advisor/plan_templates.py` — `PlanTemplateManager`: save/load reusable `{{placeholder}}` templates
- `advisor/action_catalog.py` — `ActionCatalog`: loads `config/dr_egeria_actions.yaml` (55 actions across 10 families, with ordering, supersedes, narrative templates)
- `advisor/command_keyword_index.py` — `CommandKeywordIndex`: all ~126 Dr.Egeria commands (55 catalogued + ~71 from template filesystem); 4-tier confidence scoring; used by `_infer_type_from_context()` and `/api/actions`
- `advisor/plan_validator.py` — `validate_commands()`: deterministic post-processing rules applied after every decomposition
- `advisor/session_logger.py` — `SessionLogger`: JSONL transcript per session to `~/egeria-plans/sessions/`
- `advisor/egeria_context.py` — `EgeriaContext`: lazy-loaded pyegeria clients; actor profile lookup, governance zone listing, project/glossary existence check
- `advisor/web/static/artifact_canvas.js` — `ArtifactCanvas`: generic split-view canvas base; `addItem()` opens command picker modal
- `advisor/web/static/plan_canvas.js` — `PlanCanvas`: thin adapter over ArtifactCanvas (drag-reorder, add/remove, per-card narrative, field editing, Basic/Advanced toggle)

**Dr.Egeria template library:** ~126 unique commands in 253 files (basic/advanced pairs) across 12 families. The action catalog covers 55 of these. The remaining ~71 are accessible via the Plan Editor (command picker modal uses `GET /api/actions` which scans the template filesystem). **Always check the actual template files for field names before adding catalog entries** — mismatches silently drop values at execution time.

**Design rules:**
13. **Sub-projects use `Create Project` with `Parent ID`** — never emit `Link Project Hierarchy`. The validator converts any `Link Project Hierarchy` commands to `Create Project` with `Parent ID` + `Parent Relationship Type Name = ProjectHierarchy`.
14. **`validate_commands()` runs after every decomposition and after canvas additions** — deduplicate, remove superseded, clear self-referential/orphaned Parent IDs, insert missing containers, role-before-appointment, topological sort. Always call it; never skip. Warnings surface to the user as "Auto-corrected: …".
15. **Two-stage decomposition** — `_decompose_intent` keeps the LLM away from command structure. Stage 1 extracts entities/roles (pattern-based `_extract_entities_patterns` first, LLM `_extract_entities_llm` fallback); Stage 2 `_entities_to_commands` maps entities → commands deterministically. Never ask the LLM to emit Dr.Egeria command JSON directly — local 8B models hallucinate example values and duplicate commands. Add new phrasings to the pattern library and new object types to the action catalog and keyword index.
16. **Planning uses `get_planning_llm()` not `get_ollama_client()`** — returns a client pinned to `llm.models.planning` (`qwen2.5-coder:32b`) for narrative, refinement, answer parsing, and the LLM extraction fallback. RAG Q&A stays on `llama3.1:8b`.
17. **Draft routing in `_process_query`** fires when `draft_id` is set in the request — all messages forwarded to `PlanElicitor.process()` regardless of intent. Navigation commands (`back`, `cancel`, `save and exit`) matched by regex before forwarding.
18. **`plan_clarification` vs `plan` query types** — `plan_clarification` is the active Q&A phase (canvas shows nav buttons); `plan` means a document was saved to inbox; `plan_executed` means execution complete and plan moved to outbox.
19. **The action catalog** (`config/dr_egeria_actions.yaml`) is the authoritative source for ordering priorities, supersedes relationships, container dependencies, and narrative templates. Update it when Dr.Egeria templates change — do not embed these rules in LLM prompts.
20. **Interrogative routing guard** — `_is_interrogative()` in `rag_system.py` fires before any intent override is applied. Queries beginning with "what is", "how does", "explain", "define", "why", "who", "where", etc. are redirected to `explanation` intent regardless of the user's intent selector. Only active when no `draft_id` is set (draft-active queries go through PlanElicitor regardless).
21. **Plan Editor (builder mode)** — `POST /api/drafts/builder` creates a blank draft with `builder_mode: True`. The command picker modal (opened by `addItem()`) fetches `GET /api/actions` which returns all ~126 commands grouped by family from `CommandKeywordIndex.all_commands()`. The `builder_mode` flag is stored in the draft but not yet read by `_process_query` to reroute chat queries — that is the next planned implementation step.

22. **Execute-in-chat intercept** — in `rag_system._process_query`, before forwarding to `PlanElicitor.continue_draft()`, detect execute-intent words ("execute", "run the plan", "go ahead", "do it", "execute it", "run it"). Load the draft spec to get `doc_id`, then call `GovernancePlanAgent.prepare_execute(doc_id, draft_id=...)` directly. This prevents "execute" from being treated as a plan modification instruction which would corrupt/truncate the plan.

22a. **A plan runs only when shown and confirmed with its digest** (the plan confirm gate, 2026-11-13; `advisor/confirm_gate.py`, shared with the chat-action gate). Every route into a real run — chat "execute plan X", the plan_elicitor "execute/go ahead", rule 22's intercept, `POST /api/plans/{id}/execute|retry|rerun`, and `plans execute` in the CLI — first calls `prepare_execute`, which runs nothing, shows the Command Sequence, and registers a pending action holding the SHA-256 of the whole document and the caller's credential identity. `execute()`/`retry()` then run only with `{pending_id, digest}`: the same sign-in, within `PLAN_CONFIRM_TTL_S`, and only if the document re-read at run time is still the shown text (an edit in between is refused in words). Each pending action runs once. In chat the `plan_execute_confirm` context task takes a message that is *only* "yes"/"no"; in the browser every Execute/Re-run button goes through `runPlanConfirmed()` (`plan_editor.js`). `dry_run` writes nothing and is not gated. `_run_plan` is the ungated body: only `execute()`/`retry()` may call it. The gate's cancel is `cancel_execute`; `cancel(draft_id)` is still the draft's. **A re-run (Re-run from outbox, or Retry) that repeats non-idempotent commands needs a separate acknowledgement**: `prepare_execute` lists them (`non_idempotent`, `ack_required`) — pyegeria's `idempotent` flag when its compact spec says (`advisor/compact_specs.py`), else any command whose verb is Link, Attach, Initiate or Import (`UNSAFE_FALLBACK_VERBS`; `idempotent: true` overrides it) — and a plain yes is refused in words without consuming the confirmation; chat "yes, re-run", API `"ack_non_idempotent": true` (exactly true), browser a ticked checkbox in `planConfirmDialog` (which also lists commands as "first N of M" and never cuts the script). A plan recovered from outbox (Recover, Retry) carries a `<!-- previously_executed: <when> · as <executed id> -->` marker in its header (`governance_docs.move_to_inbox`), so executing it from inbox is a re-run too; `update()` and `restore_version()` carry the marker forward, so neither an edit nor a canvas re-compose erases it.

23. **Outbox plan structure** — after execution the outbox plan contains three appended sections in order: (a) `## Outcome` — structured outcome with status, narrative, error tables, Command Results table (GUID + QN + message), filtered Execution Results (Mermaid diagrams and report tables extracted by `_extract_report_sections`), Verification Reports; (b) `## Dr.Egeria Execution Output` — full raw augmented plan markdown in a `<details>` collapsible, always preserved, contains View Report output and Mermaid diagrams. The Plan Editor `<details toggle>` listener re-runs Mermaid when the section is expanded.

24. **`commands_detail` in Dr.Egeria's execution response** — built by `_execute_dr_egeria_markdown()` in `advisor/agents/dr_egeria_agent.py`, which calls the `md_processing.v2` command dispatcher **in-process** (not via MCP — pyegeria's MCP server, `pyegeria.core.mcp_server`, only exposes report tools; Dr.Egeria command execution has no MCP wrapper as of pyegeria 6.0.16.x, see `docs/PROJECT_SUMMARY.md` Phase 25) and reconstructs the same JSON envelope shape (`{success, output, validation_errors, execution_errors, commands_total, commands_succeeded, commands_failed, commands_detail}`) that `governance_plan_agent._parse_dr_egeria_response()` and everything downstream already expect. Each `commands_detail` entry: `{step, command, status, guid, qualified_name, display_name, message}` — `guid`/`qualified_name`/`display_name` come directly from the processor's result dict, not string-parsed. `OutcomeReporter.generate()` uses it directly when available; if `guid` is ever empty, `_GUID_IN_MSG_RE` falls back to parsing it out of the message string `"Executed Verb Object (GUID: …)"`.

25. **`_ACTION_TO_EGERIA_TYPE` completeness** — every `Create X` command that makes a new Egeria element must be in this dict so `_make_cmd()` auto-generates Qualified Name. Currently missing entries are the commonest source of empty QN in Command Results. When adding a new Create command to the action catalog, always add the corresponding entry here. Current gaps known: none for commands in catalog as of Jun 15 2026 — `PersonRole`, `Community`, `ActorProfile`, `UserIdentity` added.

26. **Command list order is authoritative — never re-sort by priority after initial decomposition.** `_command_order_key`/`catalog.ordering_priority` sorting happens exactly once, at initial decomposition (`validate_commands` → `_sort_by_priority`, called from `_decompose_intent`). `_build_confirm_commands_response` and `_merge_answers_into_commands` (`plan_elicitor.py`) must NOT re-sort — `_merge_answers_into_commands` backs both plan generation and the Plan Canvas drag-reorder PATCH (`/api/drafts/{id}/commands`), so resorting there would silently undo any manual or NL-driven reorder on every save. This was a real, previously-shipped bug for cross-family canvas drag-reorder, found while building rule 27.

27. **Natural-language plan editing (reorder + relationships)** — `PlanElicitor` supports deterministic (non-LLM) chat commands to reorder steps ("move step 3 to be the first step", name/ordinal references) and establish relationships (Project Hierarchy: "make Campaign the parent of all other projects"; Project Dependency: "Project 1 depends on Project 2 and 3"), in both `confirm_commands` and `refine` phases. Shared resolver primitives (`_resolve_command_ref`, `_resolve_bulk_command_refs`, `_split_multi_target_refs`) are reusable for other relationship families. Full design and rollout scope: `docs/design/RELATIONSHIP_LINKING_SCOPE.md`.

28. **The document composer always validates against the *basic*-tier Dr.Egeria template, regardless of `spec["mode"]`.** `GovernancePlanAgent._load_template()` hardcodes `root / "basic"` — `spec["mode"]` only controls which Q&A questions the elicitor asks (`_build_pending_questions`), never which template file `_compose_command_block()` checks rendered fields against. **Any field that exists only in the advanced template is silently dropped from every generated plan document, at any mode** (tracked as `BACKLOG.md` PC-1). This means the historical `Create Project` + `Parent ID` sub-project mechanism has likely never actually rendered into a chat-generated plan — the validator sets it correctly in `pre_filled`/`answers`, but the composer discards it before it reaches markdown. The NL hierarchy handler (rule 27) works around this by targeting the newer basic-tier `Sub-Projects` field instead. Before relying on any advanced-only field anywhere in the compose path, verify it's actually in the **basic** template file, not just that it exists in advanced.

29. **`OutcomeReporter._build_command_results()` must not fabricate an "all succeeded" table from an unattributed error.** It builds per-command status by matching `validation_errors`/`execution_errors` to command names, assuming unlisted commands succeeded — correct for genuine per-command Dr.Egeria failures, but a systemic failure (e.g. an MCP call timeout, synthesized by `GovernancePlanAgent.execute()`'s generic exception handler with placeholder `step="?"`/`command="?"`) has no real per-command attribution. When every recorded error is unattributed, the function returns `[]` rather than marking every command "Success", so `_infer_status()` falls back to scanning the raw output text instead of reporting a false "Success" that contradicts the actual failure.

30. **Entity-type extraction (`_extract_entities_patterns`) and `CreateRouter` both fall back to the full `CommandKeywordIndex` (~126 commands), not just the ~25 types hand-registered in `_ENTITY_TO_ACTION`.** The hand-written `_ENTITY_PATTERNS` regexes (and `_PLAN_SIGNALS`/`_SPEC_SIGNALS` in `create_router.py`) only cover the most common phrasings; a generic catch-all pattern (sentinel `etype = "__generic__"`, two capture groups: type phrase + name) catches anything else shaped like "create/add/set up a/an `<type>` called/named/for/to `<name>`" and resolves the type phrase against the keyword index. **The keyword-index search must be scoped to just the captured type phrase, never the whole query** — passing the full sentence lets an unrelated proper noun in the *name* portion (e.g. "...to the Egeria Project web site") win a higher-confidence exact match ("Project") over the correct, weaker partial match on the real type earlier in the sentence. `_infer_type_from_context(scope=...)` takes this explicitly for exactly this reason. A resolved command is carried directly as `obj["action"]`, bypassing `_ENTITY_TO_ACTION`/`catalog.find_by_alias` entirely (which only handles pre-registered types) — `_entities_to_commands` prefers `obj.get("action")` when present. `CommandKeywordIndex.lookup()`'s tier-4 partial-match has a minimum phrase/term length guard (3 chars) — without it, `_normalize()` stripping a leading verb ("create ") can leave a 1-2 char leftover that trivially substring-matches almost any command name.

31. **`PlanElicitor._handle_confirm_commands`'s addition path must deduplicate new commands by `(action, display_name)`, matching `plan_validator.py::_deduplicate()` exactly — never by bare action type.** A plan can legitimately want two instances of the same action with different names (two `Create External Reference` steps for two different sites, two `Create Task` steps, etc.). Filtering on bare action type (with only `Create Project` special-cased to allow multiples) silently discards every other legitimately-repeated action as a false duplicate — `added` ends up empty and the user gets "I wasn't sure how to update the plan from that" for a perfectly clear request. `_deduplicate()` is called right after this filter anyway and already does the correct `(action, display_name)` comparison, so there's no need for `_handle_confirm_commands` to have its own, stricter, wrong version of the same check.

32. **Never read `spec.get("doc_id")` directly to find a draft's live plan document — call `DraftManager.resolve_live_doc_id(draft_id, spec=spec)` instead, and write through `DraftManager.sync_document(draft_id, spec, new_content, edited_by=...)` rather than `doc_manager.update(...)` + `dm.save(spec)` as two separate calls.** Executing a plan always renames its file (`{orig}_executed_{ts}`, moved inbox → outbox) — a draft's `doc_id` pointer goes stale unless something updates it, and this was fixed at individual call sites (SS-10 in BACKLOG.md) only to regress through three *more* independent call sites later (SS-12) that each re-read `spec["doc_id"]` directly and trusted it. `resolve_live_doc_id()` self-heals by finding the newest outbox file sharing the pre-`_executed_` base name; `sync_document()` resolves-then-writes-then-saves as one operation so the two calls can't desync (a real, live bug this surfaced: the canvas PATCH endpoint used to silently skip the document write — while still returning `{"status": "ok"}` — whenever `doc_id` was stale, since `doc_manager.load(doc_id)` returned `None` and nothing downstream noticed). `GET /api/drafts/{draft_id}` resolves before returning, so every frontend consumer (Plan Canvas, the Active Drafts sidebar, chat "resume") gets a self-healed doc_id with no frontend-side staleness handling needed. `GovernancePlanAgent.execute()`'s own doc_id handling is the one exception — it operates on the id it just loaded synchronously moments earlier in the same call, so staleness isn't a risk there. This bug class does **not** apply to report-spec drafts (`ReportDraftManager`) — `ReportSpecDocumentManager.move_to_outbox()` never renames/moves the inbox source (see Report Spec Builder Design Rules rule A below), so their `doc_id` can't go stale the same way.

### Data Pipeline (`advisor/data_prep/`)

`pipeline.py` ingests source repositories (egeria Python, egeria Java, docs, notebooks) using `CodeParser`, `DocParser`, `CLIIndexer`, and `MetadataExtractor`. Run `scripts/clone_repos.py` to fetch source repos before indexing. Use `scripts/ingest_collections.py` to re-index a collection into pgvector.

### Observability

- **PostgreSQL** (`metrics_collector.py` via `ConsolidatedDBManager`, `advisor/db_consolidated.py`) — query latency, collection health, system resources; always active. Not SQLite — it's a separate connection pool into the same `egeria_advisor` Postgres database/instance pgvector uses, holding its own tables (`query_metrics`, `system_metrics`, `collection_health`, `error_log`, etc.) alongside the vector collection tables.
- **MLflow** (`mlflow_tracking.py`) — experiment tracking; non-blocking background thread
- **Arize Phoenix** (`phoenix_client.py`, TC-4/BACKLOG.md) — BeeAI/OTel span tracing; off by default (`observability.phoenix.enabled`), initialized once in `RAGSystem.__init__`
- **FeedbackCollector** — user thumbs up/down tracking from interactive mode
- **Analytics** (`analytics.py`) — aggregated reporting for quantitative queries

## Configuration

Primary config: `config/advisor.yaml`. Environment overrides: `.env` (copy from `.env.example`).

Key config sections: `pgvector`, `vector_store_backend`, `llm`, `embeddings`, `rag`, `observability`, `agents`.

Settings are managed via Pydantic models in `advisor/config.py`.

### Deployment / Egeria Connection Configuration

**Which Egeria instance the app talks to is resolved by `advisor/mcp_config.py::get_pyegeria_platform_config()`, not `advisor.config.settings`.** Priority: `EGERIA_VIEW_SERVER_URL`/`EGERIA_VIEW_SERVER` env vars (`.env`) first, then `config/mcp_servers.json`'s `mcpServers.pyegeria.env` block (which defaults to local Egeria at `https://localhost:9443`/`qs-view-server`). This is the single source every live-Egeria code path uses: `advisor.auth.validate_egeria_credentials` (login), `advisor.report_pipeline.ReportPipeline._read_pyegeria_connection` (report execution), `advisor.egeria_context.EgeriaContext._load_connection` (context enrichment), `advisor.agents.dr_egeria_agent.DrEgeriaActionAgent._get_egeria_conn` (Dr.Egeria actions/templates). To point a deployment at a different Egeria instance, set the two `.env` values — never edit the committed `config/mcp_servers.json` for this. (`advisor.config.settings` has no `egeria_platform_url`/`egeria_view_server` fields anymore — they were dead: declared but never read anywhere. `egeria_user`/`egeria_password` in `.env` are real, but only as the fallback service account `advisor.auth.resolve_egeria_credentials()` uses when a request has no logged-in session (a signed-in request yields `token` set and `password` empty) — most live-Egeria actions require login via `require_egeria_user()` and never hit this.)

**Portal SSO** — `POST /api/auth/portal` (`advisor.auth.exchange_portal_token`) exchanges the Portal's JWT (`{sub, role, display_name, egeria_token, exp}`, shared HS256 secret, `ADVISOR_PORTAL_SECRET` env var or `config/advisor.yaml`'s `auth.portal.shared_secret`) for this Advisor's own session JWT — lets a Portal hand off an already-authenticated user without a second login. Blank/unset `ADVISOR_PORTAL_SECRET` disables it (endpoint returns 503). **Since 2026-09-04 the app JWT carries the user's Egeria bearer token, never the password** (`trellis_auth.create_access_token(user_id, egeria_token, ...)`, `get_egeria_token`, `apply_token(client, token)`); the direct login form exchanges the password for a token once via `login_with_password` and forgets it, and the app session is capped at the Egeria token's own expiry (1 hour on qs-view-server). Every live-Egeria call site builds a client and calls `apply_token`; the one exception is `exec_report_spec`, which pyegeria only accepts with user/password (egeria-python ISSUE-86), so that path runs as the service account for signed-in users until pyegeria takes a token. `docs/trellis-auth-extraction.md` §6 has the full contract and the finding that the old password-shaped Portal payload was never what the Portal sent.

**Cross-origin access** — `advisor/web/app.py`'s CORS middleware always allows `localhost` (any port, for local dev) plus any origins listed in `ADVISOR_EXTRA_CORS_ORIGINS` (`.env`, comma-separated). Only needed when something calls this Advisor's API from a *different* origin (e.g. a Portal frontend); the SPA's own same-origin calls (served from the same host:port as the API) are never subject to CORS regardless of hostname.

**External/forwarded access** — uvicorn defaults to binding `127.0.0.1` (loopback only). An SSH reverse tunnel terminating on this machine's own loopback works fine as-is; a router/NAT port-forward or a reverse proxy forwarding to this machine's real network interface needs `uvicorn advisor.web.app:app --host 0.0.0.0 --port 8880` (or the specific interface IP) to accept those connections at all.

**HTTP + HTTPS together** — `scripts/run_web.sh` always serves plain HTTP (`ADVISOR_HTTP_PORT`, default 8880) and additionally serves HTTPS (`ADVISOR_HTTPS_PORT`, default 8881) when `ADVISOR_SSL_CERTFILE`/`ADVISOR_SSL_KEYFILE` are both set in `.env` to existing files — otherwise HTTPS is silently skipped. uvicorn only serves one scheme per process, so "both" means two `uvicorn` processes sharing the same FastAPI app (`advisor.web.app:app`), started and torn down together by that one script, not one process doing both. `config/certs/` (gitignored entirely, never committed — holds private key material) has the real Let's Encrypt cert for `egeria.pdr-associates.com`: `server.crt` (leaf) + `server-ca.crt` (R13 intermediate) concatenated into `fullchain.pem` (built once — `server.crt` had no trailing newline, so a naive `cat` without inserting one produces a malformed PEM with two certs run together on one line) and `server.key` (private key, verified to match via `openssl x509 -pubkey` vs `openssl rsa -pubout`). Verified live with the real cert: HTTP and HTTPS both return 200 locally, and `curl -v`'s reported cert subject/issuer confirm HTTPS is genuinely serving the real Let's Encrypt cert, not a placeholder. External reachability on the HTTPS port additionally depends on router port-forwarding being configured (same requirement as the existing `:8880`/`:9443` forwards) — not verifiable from this machine alone.

**Don't assume `localhost:9443` is "the" Egeria just because something responds there.** This machine ("hedwig") separately runs its own local `egeria-quickstart` dev stack (Docker container `quickstart-egeria-main`, port-mapped `9443:9443`) — a genuinely different Egeria server from whatever this Advisor is actually meant to talk to, seeded with the same standard sample content pack. A connection attempt to a *different* Egeria hanging/failing is easy to misdiagnose as "must mean I should use localhost instead" — it looked exactly like NAT hairpinning (instant `localhost:9443` response vs. a hard-timeout public hostname) and wasn't; the real cause was that the public hostname's port forward for `:9443` had simply been dropped at the router. **Verify same-server identity with data, not just "it responds"**: fetch a specific element's GUID (`find_elements_by_property_value`) through each candidate connection and compare — GUIDs are per-instance random UUIDs, so a match is near-conclusive and a mismatch is definitive, whereas matching report *output* proves nothing when both servers loaded the same stock sample content pack. Confirm basic reachability first with `curl -k --max-time 15 https://<host>:9443/...` (should be milliseconds once the right port is actually open; a hard timeout means find out why that specific port isn't forwarded before reaching for a different hostname).

**Three separate, independent timeout layers sit between a report request and Egeria's response** — raising one alone does nothing if the others are still short: `ADVISOR_MCP_TOOL_TIMEOUT` (egeria-advisor's own wait for the whole MCP round-trip, `advisor/mcp_agent.py`), `PYEGERIA_MCP_REPORT_TIMEOUT` (pyegeria's `run_report` MCP tool handler's own internal deadline, `pyegeria/core/mcp_server.py` — a *different codebase*), and `PYEGERIA_TIMEOUT_SECONDS` (pyegeria's actual per-HTTP-request timeout to Egeria's REST API, `pyegeria/core/config.py` → `_base_server_client.py`, which overrides an also-hardcoded `httpx.Timeout(30.0)` client default in `_base_platform_client.py`). All three default to ~30s. `config/mcp_servers.json`'s `pyegeria.command`/`args` also matter here: they must launch from a pyegeria installation that actually *has* `PYEGERIA_MCP_REPORT_TIMEOUT`/`PYEGERIA_TIMEOUT_SECONDS` support — an older/different pyegeria checkout elsewhere on disk may have these hardcoded with no env override at all, in which case raising the env var has zero effect no matter how high.

**Keep `pyegeria`'s version floor in `pyproject.toml` aligned with other pyegeria consumers in the same deployment** (e.g. a Portal's own `requirements.txt`), not just whatever's convenient locally — different client versions reading the same Egeria data can still disagree on available features/output formats (e.g. `run_report`'s supported `output_type`s), causing confusing "why does this tool support X but that one doesn't" reports even though the underlying data is identical.

### Report Spec Builder Design Rules

**A. Lifecycle invariant — specs never leave the catalog on execute.** `move_to_outbox` writes a result snapshot `<spec_id>_executed_<ts>.md` to outbox but does NOT unlink the source from inbox. `retry` strips the `_executed_<ts>` suffix and calls `execute()` on `base_doc_id` directly; no file move is needed. `recover` just confirms the spec is in inbox.

**B. Registration key alignment.** `register_report_spec(base_doc_id, spec)` and `exec_report_spec(base_doc_id, …)` both use the same `base_doc_id` (with `_executed_<ts>` stripped). The registration happens in `ReportSpecAgent.execute()` immediately before calling `exec_report_spec`, so the lookup always hits.

**C. Validation checks the named client class.** `validate_report_spec` does `getattr(pyegeria, client_name)` to get the actual class (e.g. `GlossaryManager`) and then `hasattr(client_class, method_name)`. Never check `EgeriaTech` directly — it uses `__getattr__` on instances, so `hasattr(EgeriaTech, method)` on the class always returns False.

**D. Three-category parameter model** — markdown sections `### Content Filters`, `### Shape Defaults`, `### Performance Hints` under `## Create Report Spec`. The parser (`report_spec_parser.py`) merges all three into `ActionParameter.spec_params`. The draft JSON stores them as separate dicts (`content_filters`, `shape_defaults`, `performance_hints`). `_generate_report_spec_md` emits all three. `PATCH /api/reports/drafts/{draft_id}/columns` accepts all three to keep canvas edits in sync.

**E. Custom-column routing guard.** In `_process_query` (before the report pipeline block), `_custom_columns_pattern` detects "show/list X **with their** Y **and** Z" queries. These name specific columns the pre-built reports don't have, so they route to `ReportSpecElicitor` instead of the MCP pipeline. Pattern: `\b(show|list|get|…)\b .{0,60} \b(with their|including|containing)\b .{0,60} \b(and|,)\b`. Bare "show all glossaries" (no column list) does NOT match and goes to the pipeline.

**F. Canvas → markdown sync.** `PATCH /api/reports/drafts/{draft_id}/columns` (in `app.py`) calls `elicitor._generate_report_spec_md(spec)` and `doc_manager.update(doc_id, new_content)` after saving the draft. This ensures canvas edits are reflected in the markdown that `exec_report_spec` reads.

### Report Pipeline Design Rules

1. **Direct dispatch normalises the report name** — `ReportPipeline._resolve_report_name()` strips spaces, hyphens, and underscores and lowercases before comparing, so "IntegrationConnectors" resolves to "Integration Connectors". Call it before invoking the MCP, not after.

2. **Distinguish MCP connection failure from report-not-found** — `_execute_report` calls `_ensure_agent()` before `run_report()`. If `_ensure_agent` raises `ConnectionError` the response says "MCP server not reachable". If the agent connected but `run_report` returned `None` (tool error, unknown report name), the response says "report not found or failed to execute" — never blame the server for a missing report.

3. **`_dict_to_markdown_table` handles three dict shapes**: (a) `{name: {props...}}` — one row per name with flattened columns; (b) `{key: [records...]}` — the list is unwrapped and rendered as a standard data table; (c) `{key: scalar}` — rendered as a Property/Value table. This covers the common pyegeria DICT output format of `{"Report Title": [{...}, ...]}`.

4. **Perspective flows end-to-end**: Web UI → `QueryRequest.perspective` → `RAGSystem.query()` → `_process_query()` → `ReportPipeline.process()` (used by `QuestionSpecIndex.search()` to filter specs) and also into `PromptTemplateManager.get_system_prompt()` which appends a role-specific addendum.

5. **`QuestionSpecIndex.search()` perspective filter** zeros out scores for specs whose `perspectives` list does not contain the selected role (or `"any"`). A `None` perspective disables filtering — all specs are eligible.

5b. **Egeria-held report specs come from the EGERIA tier, not the Phase-1 loader** (brief B3, D3, 2026-11-10). `advisor/egeria_spec_sync.py` runs pyegeria's `sync_report_specs` over EA's consumer folder `ReportSpecFolder::Consumer::EgeriaAdvisor` (`ADVISOR_REPORT_SPEC_FOLDERS`, else `PYEGERIA_REPORT_SPEC_FOLDERS`) at startup in a background thread and from the admin "Sync Report Specs from Egeria" action — never on a query's path, never blocking startup. `load_egeria_report_specs` (QuestionSpec terms in `ReportType::` collections) is retired; Egeria-held specs reach question search through `QuestionSpecIndex._load_egeria_tier_sources()` (annotations `questions`, else purpose/description). `GET /api/reports` carries each spec's `sources` (builtin / egeria / local / user — "user" = registered by EA's own document store) with shadows, and `egeria_held`, whose `message` always says why when nothing is held. The sync reads as the **service account into a shared cache**: public specs only. Zone-restricted specs (B6) must be read per signed-in user and never through this module; see its docstring.

5c. **Dashboard Sheets are read as the signed-in user, never a service user** (brief B5, D4/D6, 2026-11-10). `advisor/egeria_dashboards.py` lists and resolves sheets with pyegeria's `pyegeria.view.dashboard_resolver` (the same data step as the Portal; do not port Portal logic) using the viewer's own token; with no token it refuses ("sign in"). Its cache is keyed by **credential identity** (hash of the presented token + platform + server), never a user_id or a token's `sub`, and caches answers only, never a failed read. Restricted placements ("not visible to you") are never runnable. "No sheets", "could not read" and "not visible" are three states with three messages. Running a placement goes through `ReportPipeline.exec_spec_raw` with the viewer's credentials, which returns pyegeria's raw result and its error (never through the shared `_last_run_error`: tiles run concurrently). Routes: `advisor/web/dashboards.py` (`/api/dashboards…`), read only. **B7a (2026-11-11):** a run returns a TILE (`kpi`, `kpis`, `chart` = pyegeria's Vega-Lite spec, `trend` = metric_trend points keeping pyegeria's `display`/`measurement`, so a lower bound is "≥N" and never a bare number, `markdown`, `empty`, `absent`); tiles run by themselves when a sheet opens and are served from a **result cache keyed on credential identity + spec + format + effective params**, fresh for `RESULT_FRESH_S`, then stale-while-revalidate (one background run) until `RESULT_KEEP_S`, with identical in-flight runs deduped; only answers are cached, never a failed run. ⚙ overrides change only parameters the placement already has (plus a find spec's search string), for that view only. `detail_spec` runs in a drill-down with the drill spec's own defaults. A "missing" placement whose name is a sheet the viewer can list is a nested sheet. The UI is `static/dashboards_view.js` (index.html keeps a mount hook); `static/test/dashboards_view.test.mjs` draws the server's own tiles (`tests/unit/fixtures/dashboards/tiles_for_js.json`, regenerated by `test_dashboard_tiles.py`). **B7b (2026-11-11), authoring:** `advisor/egeria_dashboards_author.py` + `static/dashboards_author.js`, routes `POST /api/dashboards/author/plan` then `/apply`. Every change is previewed (the writer's `dry_run` listing plus any Dr.Egeria document), confirmed (delete: type the sheet's name), then written as the user's token; a plan is one-shot (a retry returns the first result, "already_written") and only the credential that planned it can apply it. Sheets/placements/move/remove/delete use pyegeria's `DashboardSheetEgeriaWriter` directly (not Dr.Egeria `Create Dashboard Sheet`, which also writes pyegeria's local JSON store on EA's host); a placement's Report is Dr.Egeria `Create Report`; a new sheet is filed by Dr.Egeria `Add Member to Collection` into `DashboardFolder::Consumer::EgeriaAdvisor` and says "not filed" when that folder is missing. User text is single-line with no `#`, `___` or `|`, so it cannot add a Dr.Egeria command.

5a. **Act's `_extract_type_and_filter()` resolves element types via `advisor/egeria_type_registry.py`**, not a bare first-content-word heuristic. `resolve_type_name(words)` tries the longest word-prefix (up to 4 words) against a cached registry built from `config/report_specs/report_specs_annotated.json` (repo root)'s 75 `target_type` values, so multi-word type names ("external reference(s)", "data product(s)") resolve to their full canonical Egeria name (`ExternalReference`, `DigitalProduct`) instead of truncating to the first word. Falls back to the bare first word when nothing matches. Real Egeria calls it `DigitalProduct`, not `DataProduct` — common industry terminology doesn't always match Egeria's own type names; mismatches like this go in the registry's `_ALIASES` dict.

### Agent & Routing Design Rules

6. **BeeAI `FunctionTool` objects (produced by `@tool`) have no `.func` attribute** — calling `my_tool.func(...)` raises `AttributeError`. Extract implementation into a `_<name>_raw()` plain function; the `@tool` wrapper delegates to it. Fallback methods import and call the raw function directly. See `_find_dre_template_raw`, `_search_egeria_content_raw`, `_get_egeria_symbol_raw` in `advisor/agents/tools.py`.

7. **Gate ExamplesAgent on `"```python" in response`** (not just `"```"`), so inline-backtick plain-text responses still fall through to the direct-retrieval fallback. Method-discovery (API-reference) mode gates on `"##" in response or "| Method |" in response`.

8. **`_is_report_query` is guarded by `_CODE_EXAMPLE_SIGNALS`** — if the query contains "python", "code example", "pyegeria example", etc., the semantic similarity check is skipped entirely so code-example queries are never mistakenly sent to the MCP report pipeline.

9. **LLM intent classifier (`advisor/llm_intent_classifier.py`) maps `CODE_HELP` → `code_search`** — "python", "example", "sample", "code", "how do I", "write a" all trigger CODE_HELP even when the topic involves creating/updating objects. Only pure imperative commands with no code qualifier map to WRITE_COMMAND.

10. **Dr.Egeria template lookup uses `_templates_root()`** which tries two layouts in order: `{root}/Templates/Dr-Egeria-Templates` (workspace layout) then `{root}/templates` (lower-case fallback). The root comes from `pyegeria.core.config.get_app_config().Environment.pyegeria_root` first, then `EGERIA_ROOT_PATH` / `PYEGERIA_ROOT_PATH` env vars.

11. **Format/role-aware routing in `PerspectiveRoutingEngine.route()`** (called from `_process_query`, before pipeline dispatch). This whole layer — dre/cli/java signal routing, the tech-role code/Python fast path, and the ambiguous "show me" clarify — is gated behind `intent in PerspectiveRoutingEngine._AMBIGUOUS_ELIGIBLE_INTENTS` (`{"code_help", "code_search", "example", "general", ""}`). An explicit `intent_override` for anything else — Act (`command`), Run Report (`report`), Inspect (`code_intel`), Explain (`explanation`), Troubleshoot (`debugging`), Create (`plan`/`create`) — skips this layer entirely and falls straight to default policy routing, so the query text containing "show me" (e.g. Act + "show me the external references") can never hijack an explicit non-"Show me" intent selection. Within the eligible intents: Dr.Egeria phrasing ("dr egeria"/"dr. egeria"/"dr_egeria"/word-boundary `dre`) → `DrEgeriaTemplateAgent`; `hey_egeria`/CLI phrasing → `CLICommandAgent`; Java phrasing → clarification (no Java generation capability exists). Developer/Data Engineer + an *explicit* code/Python signal → `ExamplesAgent` (or `CodeIntelAgent` for structural questions like class hierarchies). Any role with only an ambiguous example/"show me" signal and no explicit format signal → 3-way clarification (Python / CLI / Dr.Egeria) — role-agnostic; Developer/Data Engineer no longer default straight to Python for a plain "show me X".

12. **`routing.yaml` CRITICAL priority patterns** are checked before HIGH/MEDIUM patterns. Python/code example patterns in CRITICAL `example` ensure "give me a python example to create X" is classified before it can match HIGH `command` or `report` patterns. Method-discovery patterns ("what methods", "what api", "list methods", etc.) are CRITICAL `code_search` so they never fall to `general`.

## Code Style

Black + Ruff for formatting/linting, MyPy for type checking. Configured in `pyproject.toml`.

```bash
black advisor/
ruff check advisor/
mypy advisor/
```

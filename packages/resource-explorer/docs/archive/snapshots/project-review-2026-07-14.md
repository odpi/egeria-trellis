# Resource Explorer — Project Review & Backlog Analysis

**Status: DATED SNAPSHOT — 2026-07-14, the oldest document here.** Predates the migration into the Trellis monorepo; its links point at the retired standalone checkout and are deliberately left as written. Historical only.

**Timestamp:** 2026-07-14T08:54:52-05:00

This document provides a comprehensive review of the **Resource Explorer** project, cataloging its core architecture, current implementation status, open backlog items, and upstream Egeria/pyegeria dependencies.

---

## 1. Project Overview & Architecture

**Resource Explorer (RE)** is an Egeria-first tool designed to discover, understand, and catalog information resources (Git repositories, PostgreSQL databases, and filesystems). It uses Egeria as its central catalog of record while leveraging a local SQLite database for caching and offline support.

### The System Layers
1. **Survey Layer:** Introspects resources, writes results to local SQLite, and publishes metadata to Egeria via `EgeriaPublisher` as `SurveyReport`/`Annotation` graphs.
2. **Query Layer:** Employs a multi-agent framework powered by `beeai-framework` to process user queries. Natural-language queries are categorized into four intent paths and routed to specialized agents (e.g., `StatsAgent`, `CompareAgent`, `DocAgent`, `CodeAgent`) or a RAG-backed system (Milvus).

### The Four User Intents
- **Scouting:** Fast, broad inventory checks across resources.
- **Assessment:** Deep, asynchronous annotations and analysis of a single resource.
- **Discovery:** Finding resources using findings from surveys.
- **Enrichment:** Human-in-the-loop metadata profiling, contextualizing, and answering Requests for Action (RFAs).

---

## 2. Current Implementation Status

Based on the latest documentation and code inspections, here is where each major component stands:

| Component | Status | Details |
| :--- | :--- | :--- |
| **Database Support (Phase 4)** | **Complete** | Full FastAPI CRUD routes, database stats/chart endpoints, and query context are in place. The frontend UI (`index.html`) is updated with tabbed entity lists, registration modals, and survey trigger dialogs. |
| **Filesystem Local Survey** | **Complete (w/ Caveats)** | Split into a fast metadata pass and an expensive profiling pass. Tightened directory exclusions (e.g., bare `venv` folders) to prevent silent hangs. Inaccessible files are now collected and published as `RequestForActionAnnotation`s. |
| **Survey Definitions Execution** | **Complete** | Egeria-authored `GovernanceActionProcess` graphs can be fetched, parsed, and executed locally by RE. Skips Egeria-native steps correctly. |
| **Agent / Query Layer** | **Complete** | Router classifies intents before vector store retrieval. Statistical/metadata queries bypass Milvus and read directly from local SQLite. |

---

## 3. Backlog Deep Dive

The active backlog in [Backlog.md](file:///Users/dwolfson/localGit/egeria-v6/resource-explorer/docs/Backlog.md) outlines several strategic enhancements. They are summarized below by area:

### A. Actionable RFAs (Egeria ToDos)
* **Goal:** Convert descriptive `RequestForActionAnnotation` annotations into real Egeria `ToDo` items.
* **Mechanism:** pyegeria's `create_my_todo` / `_async_create_action` allows creating actionable assignments (e.g., setting sponsors, assignees, due dates, and targets).
* **Work Needed:** Determine which RFAs should trigger human `ToDo` items, identify defaults for unattended schedules, and construct a generic promotion path.

### B. Unified Survey Launching & Dashboard
* **Goal:** Replace old "Re-survey" buttons on detail pages with a unified survey-launcher modal driven by Egeria Survey Definitions.
* **Mechanism:** Poll Egeria for native async survey completions, and display them alongside local runs in a single survey results dashboard.

### C. Egeria ↔ RE Bidirectional A2A Collaboration
* **Goal:** Allow Egeria's automation engine to trigger RE's Python-based surveyors.
* **Mechanism:** Extend RE's `agentstack_server.py` to support structured `DataPart` payloads.
* **Work Needed:** Add token-based authentication on the A2A server, define the Egeria connector structure with Mandy, and wire the task-state lifecycle.

### D. Conformance to Egeria Area 6 (Open Survey Framework)
* **Goal:** Align RE's internal survey stages and annotator composition model with Egeria's `AnalysisStep` stages and `SurveyActionPipelineConnector` (e.g., `SequentialSurveyPipeline`).
* **Mechanism:** Model generic analysis steps (like tabular profiling) that apply to databases, filesystems, and datasets alike.

### E. Selective Cataloging & Scheduling
* **Goal:** Create a multi-phase flow: Discover (broad/shallow) → Select/Filter (by extension, size, or temporal change) → Deep Survey & Catalog.
* **Work Needed:** Migrate local scheduling (daemon threads in `scheduler.py`) to Egeria-level scheduling hooks or external services (e.g., Airflow).

---

## 4. Upstream (Egeria/pyegeria) Issues & Workarounds

RE maintains a dedicated tracker for bugs encountered in Egeria and the `pyegeria` Python SDK in [egeria-pyegeria-issues.md](file:///Users/dwolfson/localGit/egeria-v6/resource-explorer/docs/egeria-pyegeria-issues.md). Workarounds have been implemented in RE's codebase where appropriate:

### E1: Postgres Repository Connector 500 error
* **Bug:** Saving folder classifications via template `createMetadataElementFromTemplate` fails in the qs-metadata-store due to a missing `metadata_collection_guid` column value.
* **Workaround in RE:** Fails the publish operation, but caught gracefully so it does not block the local survey completion.

### E2: Stale Single-Colon Separator in pyegeria `AutomatedCuration`
* **Bug:** Stale single-colon (`:`) separators are used instead of double-colons (`::`) in SDK survey-initiation methods.
* **Workaround in RE:** `EgeriaDatabaseSurveyor` bypasses the public SDK method and calls the private `_async_initiate_survey` method with the double-colon qualified name.

### E2b: Typo in `DataDiscovery._async_delete_annotation`
* **Bug:** Typo calls `_async_delete_element_body_request` instead of `_async_delete_element_request`.
* **Workaround in RE:** None yet (RE has not needed annotation deletion yet).

### E3: asyncio Event Loop Threading Issues in Python 3.12+
* **Bug:** pyegeria sync-wrappers use `asyncio.get_event_loop()`, which fails when executed inside background worker threads (like FastAPI `asyncio.to_thread` workers).
* **Workaround in RE:** Background tasks explicitly initialize, set, and tear down a thread-local event loop.

### E4: `AutomatedCuration.get_guid_for_name` returns String Sentinel
* **Bug:** Returns the string `"No elements found"` on a miss instead of `None` or raising.
* **Workaround in RE:** Replaced simple string-length validation checks with a strict UUID format regex match.

### E5: `SurveyReport` Class Properties Inconsistencies
* **Bug:** Asset-maker endpoint requires generic `"class": "AssetProperties"` with `"typeName": "SurveyReport"` instead of `"class": "SurveyReportProperties"`.
* **Workaround in RE:** Sourced properties matching pyegeria's specific sample schema body.

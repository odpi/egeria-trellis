# Cross-resource and conversational intelligence extensions — design and implementation plan

**Status:** In progress (Groups 1–3 implemented and verified; Group 4 chips and A2A `input_required` implemented, lineage deferred).  
**Written:** 2026-10-16  
**Branch:** `re/cross-resource-chat-extensions`  
**Referenced from:** `docs/Architecture.md`, `CLAUDE.md`, `AGENTS.md`

---

## 1. Context and Problem Statement

Resource Explorer originally evolved from Project Explorer with a primary focus on individual Git repositories. While individual asset surveying, single-resource context compilation (`trellis-context`), and specialized agents (`CompareAgent`, `IntegrationAgent`, `ConversationAgent`) were established, significant architectural and operational gaps remained when reasoning across heterogeneous assets:

1. **Siloed Asset Types in Comparison and Integration:**
   `CompareAgent` and `IntegrationAgent` were historically optimized for repository-to-repository comparisons (commit velocity, stars, contributors, doc embeddings). Cross-database comparisons (schema diffing, column types, nullability, primary key constraints) and cross-tier comparisons (application repo ↔ PostgreSQL database) lacked dedicated tools and structured syntheses.

2. **Single-Resource Context Compilation Limitation:**
   The deterministic context compiler (`context_compile.py` + `trellis-context`) strictly compiled survey findings for a single resource slug. Comparative and integration queries either bypassed context compilation entirely or relied on unpredictable, multi-step LLM vector search tool calls that risk token overflow and hallucinations.

3. **Missing Cross-Asset Dependency Linkage:**
   Surveys independently extract ORM models, SQL queries, and migration files in code repositories, and table/column schemas in database surveys. However, no deterministic bridge connected code assets to the database catalog entities they query.

4. **Lack of Chat-Driven Actionability:**
   When conversational agents discovered architectural drift, schema mismatches, or missing documentation, users had no mechanism to convert those conversational findings into persistent governance tasks without manually navigating to other forms.

---

## 2. Design Principles and Architectural Boundaries

To preserve system determinism, auditability, and token efficiency, this work adheres to the core repository constraints:

1. **Zero LLM Calls in Surveyors:**
   All surveys and cross-resource linkages remain strictly deterministic (AST parsing, SQL metadata introspection, regex extraction, schema joins). LLMs are employed exclusively at the query and conversational synthesis layer.
2. **Deterministic Context Compilation Over Open-Ended Tool Loops:**
   Rather than letting agents execute dozens of exploratory tool calls to inspect two assets, the system deterministically compiles structured multi-resource evidence ladders fitting precise token budgets before LLM invocation.
3. **Epistemological Honesty ("Known vs. Never Run"):**
   Comparisons must surface survey coverage discrepancies explicitly using `FactLayer` states (`KNOWN`, `NEVER_RUN`, `FAILED`) to prevent treating an unmeasured property as an absent feature.
4. **Local-First with Graceful Degradation:**
   All cross-resource tools and fallbacks must function fully offline using local PostgreSQL/SQLite registry state, while providing clean hooks into upstream Egeria Open Metadata Highway / OMAG services when configured.

---

## 3. Four-Group Structured Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Group 1: Immediate Actionability & Single-Domain Diffing   │
│ • Chat-to-RFA synthesis tool (create_rfa_from_finding)      │
│ • Database schema comparison tool (compare_database_schemas)│
│ • CompareAgent & ConversationAgent tool integration         │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Group 2: Multi-Resource Context Compilation & Budgeting     │
│ • Multi-slug trellis-context evidence ladder partitioning   │
│ • Epistemological asymmetry matrix (FactLayer coverage diff)│
│ • Proportional token budget allocation (40/40/20 split)     │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Group 3: Cross-Asset Static Linkage & Agent Delegation      │
│ • Static Repo-to-Database table reference extraction        │
│ • ConversationAgent dynamic delegation to specialist agents │
│ • Cross-resource dependency graph resolution                │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Group 4: Upstream Governance Traversal & Frontend UX        │
│ • Egeria OMAG/OMVS lineage queries in IntegrationAgent      │
│ • Interactive multi-resource disambiguation chips in Web UI │
│ • Seamless A2A input_required handling                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Group 1: Immediate Actionability & Single-Domain Diffing (Implemented)

### 4.1 Objectives
- Provide agents with the ability to file structured `RequestForAction` (RFA) entries directly into the persistent `#rfa-drawer`.
- Enable deep, structural database schema comparisons across registered databases.

### 4.2 Implemented Components

1. **`create_rfa_from_finding` Tool (`resource_explorer/agents/tools.py`):**
   - Resolves target resource across repos, databases, and filesystems via `ProjectRegistry`.
   - Persists open RFA entries using `activity_logger.log_rfa()`.
   - Records corresponding activity log entries for full operational auditability.

2. **`compare_database_schemas` Tool (`resource_explorer/agents/tools.py`):**
   - Retrieves latest survey data for two databases from `ProjectRegistry.get_latest_database_survey()`.
   - Computes structural diffs:
     - High-level dimensions (host, port, db_type, total tables/columns).
     - Exclusive vs. common schemas and tables.
     - Per-table column-level diffs (data types, nullability, primary key constraints).
   - Supports optional schema-level filtering (`schema_name_a`, `schema_name_b`).

3. **Agent Integration:**
   - **`CompareAgent` (`resource_explorer/agents/compare_agent.py`):** Enhanced with database detection, schema diffing prompt guidance, and direct fallback execution via `compare_database_schemas_raw`.
   - **`ConversationAgent` (`resource_explorer/agents/conversation_agent.py`):** Registered `compare_database_schemas` and `create_rfa_from_finding` tools in the BeeAI agent toolset.
   - **Prompt Templates (`resource_explorer/prompt_templates.py`):** Updated system instructions to guide agents on when and how to invoke schema diffing. RFA logging guidance was removed from the comparison prompt on 2026-10-19 (see below).

   **Decision (project owner, 2026-10-19):** "Option A, remove it from compare and integration." `create_rfa_from_finding` is carried by `ConversationAgent` only. A traced comparison on 8813 called it unprompted, with an empty summary (the tool refused, so nothing was filed); the comparison prompt had invited the call. `CompareAgent` and `IntegrationAgent` describe an actionable finding in their answer instead; the A2A surface still builds `ConversationAgent` with `allow_writes = False`.

### 4.3 Verification
- Tested via `tests/test_agent_tools.py` covering tool execution, RFA logging, database diff generation, and agent fallback modes against SQLite fixtures.

---

## 5. Group 2: Multi-Resource Context Compilation & Budgeting (Implemented)

### 5.1 Objectives
Extend `context_compile.py` and `trellis-context` integration to compile structured comparative context for two or more resource slugs within a single token budget.

### 5.2 Key Decisions

1. **Proportional Token Allocation:**
   - When compiling context for resources $A$ and $B$, partition the available model context budget (e.g. 32k for dev, 2k for demo tiers):
     - Resource A Facts: 40%
     - Resource B Facts: 40%
     - Cross-Resource Vector/Shared Overlap: 20%
2. **Epistemological Asymmetry Matrix:**
   - Compare `FactLayer` states for both assets across standard survey dimensions (e.g., security scan, license classification, dependencies, schema profiling).
   - Generate an explicit coverage comparison block:
     ```markdown
     | Dimension | Resource A (repo-alpha) | Resource B (repo-beta) |
     |---|---|---|
     | Security Scan | KNOWN (Score: 88/100) | NEVER_RUN |
     | License | KNOWN (Apache-2.0) | KNOWN (MIT) |
     | CI Quality | KNOWN (GitHub Actions) | FAILED (Timeout) |
     ```
3. **Independent Ladder Degradation:**
   - If total facts exceed budget, degrade each resource independently using `trellis-context` rungs (`FULL` → `SUMMARY` → `STRUCTURE_ONLY`) to ensure both resources retain equal representation.

### 5.3 Planned Deliverables
- `resource_explorer/context_compile.py`: Implement `compile_multi_resource_context(slugs, query, perspective, budget)`.
- Unit tests validating token bounds, ladder degradation, and manifest generation across multiple resources.

---

## 6. Group 3: Cross-Asset Static Linkage & Agent Delegation (Implemented)

### 6.1 Objectives
Bridge application code repositories to cataloged databases and enable `ConversationAgent` to dynamically delegate complex comparative/integration sub-goals.

### 6.2 Key Decisions

1. **Code-to-Database Static Matching:**
   - Extract table references from repo surveys:
     - SQL literals and migration scripts (e.g., Flyway, Liquibase, Alembic).
     - ORM entity definitions (e.g., JPA `@Table`, SQLAlchemy `__tablename__`, Django models).
   - Match extracted table names against database schemas stored in `ProjectRegistry`.
   - Store discovered linkages as cross-resource references without requiring external lineage tools.
2. **Sub-Agent Delegation in Multi-Turn Chat:**
   - Equip `ConversationAgent` with delegation tools (`delegate_to_compare_agent`, `delegate_to_integration_agent`).
   - When a user in a repository chat asks a side-by-side or pipeline question, `ConversationAgent` delegates execution and incorporates the specialized agent's structured response (including Plotly comparison figures or symbol tables).

### 6.3 Planned Deliverables
- `resource_explorer/surveyors/cross_asset_matcher.py`: Deterministic table-to-repo reference matcher.
- Delegation tool implementations in `resource_explorer/agents/conversation_agent.py`.
- Tests verifying cross-asset discovery and intent delegation handoffs.

### 6.4 Matcher scoring and tuning notes

Implemented in `resource_explorer/surveyors/cross_asset_matcher.py`; tests are the
`test_cross_asset_matcher*` functions in `tests/test_agent_tools.py`.

**What it matches.** For each repo/database pair it adds up evidence, keeps pairs scoring at least
0.25, and labels them `low` (< 0.4), `medium` (0.4 – 0.6) or `high` (>= 0.6).

| Signal | Score | Notes |
|---|---|---|
| Database driver in the repo's dependencies, for that database's type | +0.30 | Not enough alone for `medium`. |
| Model class name matches a table name | +0.15 each, capped at 0.60 total | Generic or widely shared table names count +0.05 (below). |
| Class-name match with no driver and no ORM dependency | table score capped at 0.30 | Keeps a bare name coincidence at `low`. |
| Confidence rules | n/a | HIGH needs >= 2 distinctive tables; no matched table means `low`. See tuning status. |
| ORM or migration framework in dependencies | +0.10 | Only when a driver or a table also matched. |
| Database name (>= 4 characters) appears in the repo slug | +0.20 | Slug only; symbols are not searched. |

**How names are compared.**
- Only symbols of kind `class` are used (ORM models and entities). Functions, methods and constants
  never count, and there is no cap on how many classes are read.
- Both sides are lowercased with separators removed, so table `order_items` meets class `OrderItem`.
- A table is compared as itself and as a singular (`ies` -> `y`, `sses`/`xes`/`ches`/`shes` drop `es`,
  a trailing `s` is dropped). Names shorter than 4 characters after this are ignored.
- "Generic" table names (`user`, `role`, `group`, `session`, `event`, `task`, `item`, `log`, `file`,
  `type`, `status`, `state`, `config`, `setting`, `data`, `name`, `order`, `account`, `message`,
  `token`, `job`) score 0.05 instead of 0.15, because a class with that name proves little.

**Tuning status.** The weights, the 0.25 threshold and the 0.30 cap are judgment calls. A first run on a
real registry (73 repos, 20 databases, 2026-10-18) found them too generous: 359 of 1,460 possible pairs
scored, 20 of them HIGH, and only about 5 of those 20 were right. The false HIGH links came from generic
table names (`users`, `config`, `orders`, ...) plus a matching driver, and from driver, ORM and database-name
evidence with no matching table at all. The matcher now also applies:
- **Distinctive tables.** A matched table counts fully only if its singular is not in the generic list and its
  name appears in fewer than 3 surveyed databases (`_COMMON_TABLE_DB_COUNT`); otherwise it scores 0.05. The
  count is taken over every surveyed database even when the query is scoped to one.
- **HIGH needs two distinctive tables** (`_HIGH_MIN_DISTINCTIVE`); with fewer, a HIGH-band score is reported
  as `medium`.
- **No matched table, no better than `low`**, however much driver, ORM and name evidence there is.
- **Bounded output.** `query_cross_asset_links` lists only `high` links by default, best first, at most 15,
  and always reports how many medium and low links exist (`min_confidence`, `limit`). Unfiltered, the real
  run produced 144,000 characters.

The generic-name list was also found to be compared against plural table names while holding singular
words, so `users` never matched `user`; it now compares the singular form.

**Known false positives, left in place for now (2026-10-18 re-check, laz, `edd1d8d2`).** The default view dropped
from 359 pairs / 20 HIGH to 7 HIGH; four were right, one was related but not the owner (openlineage with the
Marquez database), and two were wrong, both for the `openmetadata` repo:
- `openmetadata` with `localhost_docker_egeria_advisor` (0.9; `database_schemas`, `databases`, `projects`,
  `query_metrics` and two more).
- `openmetadata` with `egeria_optional_prefect_db` (0.6; `agent`, `event_resources`).

OpenMetadata is a very large Java repo whose model classes cover many ordinary names, and its own catalog tables
share names with other products' tables. **Decision (project owner, 2026-10-18):** "leave the two rows for now but
document for future evaluation." Two candidate rules, neither applied, each with a risk to check against a real
registry first:
1. *Require a table exclusive to one database* for HIGH (`table_db_count == 1`). It would drop the Prefect row
   but may keep the Advisor row if `query_metrics` is exclusive to it.
2. *Penalise a repo that reaches HIGH on several databases.* It would hit both rows, but a repo can legitimately use
   several databases.
Evaluate on the next real-data run. The pairs judged right on 2026-10-18 are the regression set: marquez with
`laz_local_marquez`; egeria_trellis and egeria_advisor_git with `localhost_docker_egeria_advisor`; egeria_git with
`localhost_docker_egeria`.

These changes are tested on synthetic fixtures built from the failures above (`test_cross_asset_matcher_real_data_lessons`);
they have not been re-run on the real registry. Before relying on the confidence labels:
1. Re-run `query_cross_asset_links` over the real registry and review the HIGH and MEDIUM pairs by hand.
   Note false positives and false negatives (the first run found no confident misses).
2. Adjust the module-level constants (`_TABLE_WEIGHT`, `_GENERIC_TABLE_WEIGHT`, `_GENERIC_TABLE_NAMES`,
   `_COMMON_TABLE_DB_COUNT`, `_HIGH_MIN_DISTINCTIVE`, `_MIN_NAME_LEN`) rather than the scoring code, and update
   the expectations in the matcher tests.
3. A change that moves a `high` to `medium` for a pair a person has confirmed is a regression; add that
   pair as a test fixture.

**Known limits.**
- Name-based only. Tables referenced through raw SQL strings, migration files or `@Table` /
  `__tablename__` annotations are not read, so a repo that talks to a database without a same-named
  model class scores on drivers alone.
- Pluralization is English-only and rule-based; irregular plurals (`person` / `people`) are missed.
- It finds that a repo and a database are related, not which direction data flows or which columns are
  used. Impact analysis at column level would need a different source (parsed queries).
- Database surveys must have been run; a database with no `schema_info` has no tables and can only
  match on its driver or name.

---

## 7. Group 4: Upstream Governance Traversal & Frontend UX (Partly implemented; lineage deferred)

### 7.1 Objectives
Integrate upstream Egeria Open Metadata Highway lineage and governance classifications, and provide interactive multi-resource disambiguation in the Web UI.

### 7.2 Key Decisions

1. **Egeria OMAG Lineage Traversal:**
   - Add lineage query capabilities to `IntegrationAgent` targeting Egeria's `DataEngineOMAS` and `LineageOMVS` endpoints via `pyegeria`.
   - Trace end-to-end data flows (e.g., FileSystem Ingestion → Event Topic → DB Store → Service API).
   - Provide strict local fallback when Egeria is unreachable or lineage is unindexed.
2. **Interactive UI Disambiguation Chips:**
   - When `QueryProcessor` detects a comparative or integration query with ambiguous or missing targets, return candidate resource chips in the Web UI chat drawer (mirroring the A2A `input_required` protocol).
   - Allow users to click resource chips to populate the comparison targets directly.

**Decision (project owner, 2026-10-18):** "Skip lineage for now. If the data is all in Egeria we can already use the lineage tool within the portal. Gathering lineage from a Marquez deployment that is not integrated with Egeria is a different initiative; we may come back to it later."

**Consequences of that decision.**
- `query_egeria_lineage` is no longer offered to `ConversationAgent` or `IntegrationAgent`, and the integration fallback no longer asks for it. The stub was removed outright (it read `EGERIA_PLATFORM_URL` and built a bare `Egeria()` client, which the client-factory guard forbids); a real lineage tool must come from `egeria_clients.egeria_client(identity, ...)`.
- Reason: it never queried lineage. After the platform URL and asset GUIDs check out it returned a fixed "no edges indexed" sentence, which an agent would relay as a finding. It should not come back to an agent until it makes a real `pyegeria` call and distinguishes "could not query" from "queried, found none".
- `IntegrationAgent`'s prompt now says lineage is unavailable and must not be inferred.

**Disambiguation chips (implemented).** Shown for comparison and integration questions that name fewer than two resources. Resources the question already names are left out; the resource open in the UI is listed first; the rest rank by words shared with the question (a named resource's own words are ignored), then by slug; at most 8 are shown. The open resource is ranked, not counted as named, because the agents infer their targets from the question text alone. Chip text is set with `textContent`, not `innerHTML`. The ranking lives in `resource_explorer/resource_chips.py`, shared with the A2A agents.

**A2A `input_required` (implemented).** When the compare or integration agent is asked about fewer than two resources, `agentstack_server._choice_status` replies `input_required` with the same ranked candidates, repos and databases alike (it used to list repo slugs only). The text names them for text-only clients; a second, `DataPart` message part carries `{"kind": "resource_choices", "choices": [{"slug", "name", "type"}]}` for clients that can render choices. The part is omitted when nothing is registered. Existing clients that read only text parts are unaffected; the text itself changed wording ("Candidates: …" replaces "Available: …"). Covered by `test_a2a_input_required_offers_ranked_repos_and_databases`; no A2A client has been tried against it.

### 7.3 Planned Deliverables
- `resource_explorer/agents/integration_agent.py`: Add Egeria lineage traversal tool with safe fallback.
- Web UI frontend updates in `index.html` / `query.py` handling `input_required` interactive chip payloads.
- Contract tests for Egeria lineage parsing with mocked network fixtures.

---

## 8. Summary of Milestones & Verification Matrix

| Group | Component | Deliverable | Status | Verification |
|---|---|---|---|---|
| **Group 1** | Actionable RFA Tool | `create_rfa_from_finding` | **Done** | `tests/test_agent_tools.py` |
| **Group 1** | Database Schema Diff | `compare_database_schemas` | **Done** | `tests/test_agent_tools.py` |
| **Group 2** | Multi-Resource Context | `compile_multi_resource_context` | **Done** | `test_multi_resource_context_compilation`, `test_compare_and_integration_agents_use_multi_resource_evidence`, `test_multi_resource_budget_partitioning` |
| **Group 2** | Epistemological Matrix | Cross-slug `FactLayer` diff | **Done** | `test_multi_resource_context_compilation` |
| **Group 3** | Code-to-DB Matcher | Static table-to-repo matcher | **Done** | `test_cross_asset_matcher*` (synthetic fixtures; not validated on real surveys, see 6.4) |
| **Group 3** | Dynamic Sub-Agent Delegation | `ConversationAgent` delegation tools | **Done** | `test_dynamic_delegation_and_lineage_tools` |
| **Group 4** | Egeria Lineage Traversal | `IntegrationAgent` lineage tools | **Deferred** (2026-10-18) | n/a until lineage is revisited |
| **Group 4** | Web UI Interactive Chips | Disambiguation chip rendering | **Done** (no browser test) | `test_disambiguation_chips_rank_and_cap` covers the payload; the rendering is untested |

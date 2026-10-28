# Cross-Resource & Conversational Extensions — Verification & Test Protocols

**Status:** Ready for manual and automated verification  
**Date:** 2026-10-16  
**Branch:** `re/cross-resource-chat-extensions`  
**Referenced Documentation:** `docs/cross-resource-chat-extensions-plan.md`, `CLAUDE.md`, `AGENTS.md`

---

### Executive Overview & Purpose

This document provides complete, step-by-step test protocols to verify all four groups of cross-resource reasoning, context compilation, database diffing, RFA generation, cross-asset static linking, and upstream Egeria lineage features.

All features are implemented locally with graceful offline degradation, deterministic SQLite/PostgreSQL fixtures, and contract-tested interfaces.

---

### 1. Automated Test Suites Execution

Execute the full automated test battery before starting the UI or testing live services.

#### 1.1 Run Agent Tools and Cross-Resource Unit Tests
```bash
uv run pytest tests/test_agent_tools.py -v
```
**Expected Outcome:**
- 12/12 tests passing.
- Validates database schema diffing, `create_rfa_from_finding`, cross-asset dependency matcher, multi-resource context compilation (`compile_multi_resource_context`), epistemological asymmetry matrix generation, dynamic sub-agent delegation (`delegate_to_compare_agent`, `delegate_to_integration_agent`), and Egeria lineage query fallbacks.

#### 1.2 Run Multi-Agent & Routing Suites
```bash
uv run pytest tests/test_agents.py tests/test_catalog_invariants.py -v
```
**Expected Outcome:**
- All agent intent classifications (`comparison`, `integration`, `survey_meta`, etc.) pass cleanly.

---

### 2. Manual UI Verification Protocols

Start the Resource Explorer local web server:
```bash
uv run resource-explorer web --port 8810
```
Open browser at: `http://localhost:8810`

---

### Protocol 1: Database Schema Diffing & Comparison (Group 1)

1. Open the persistent **💬 Chat** panel (top right navigation).
2. Ensure at least two databases are cataloged (e.g. `db_alpha` and `db_beta`).
3. Enter query:
   ```text
   Compare the schemas between db_alpha and db_beta
   ```
4. **Verification Points:**
   - Agent executes `compare_database_schemas`.
   - Response renders high-level database dimensions (host, port, DB type).
   - Response includes a breakdown of common tables vs. tables exclusive to each database.
   - Column-level diffs highlight data type mismatches, nullability differences, and primary key discrepancies.

---

### Protocol 2: Chat-to-RFA Creation into `#rfa-drawer` (Group 1)

1. In the open **💬 Chat** drawer, enter:
   ```text
   Create an RFA for db_alpha to add missing primary keys identified during schema comparison
   ```
2. **Verification Points:**
   - Chat response confirms: `Successfully created RequestForAction (RFA) #<id> for database 'db_alpha' with status 'open'`.
   - Open the **📝 RFAs** drawer (button next to Chat in header).
   - Verify the newly created RFA appears under `db_alpha` with category `Agent Finding` and status `open`.
   - Check **📋 Activity** log to verify the `rfa` activity entry was written.

---

### Protocol 3: Multi-Resource Context Compilation & Epistemological Matrix (Group 2)

1. In **💬 Chat**, enter a comparative question covering survey dimensions:
   ```text
   Compare security scan and dependencies between repo_alpha and repo_beta
   ```
2. **Verification Points:**
   - The compiled context header renders the **Epistemological Coverage & Asymmetry Matrix**:
     ```markdown
     | Dimension | repo_alpha (repo) | repo_beta (repo) |
     |---|---|---|
     | Security Scan | KNOWN (Score: ...) | NEVER_RUN |
     | Dependencies | KNOWN (12 direct) | KNOWN (8 direct) |
     ```
   - Prompt partitions evidence budgets proportionally without hallucinating missing survey dimensions.

---

### Protocol 4: Cross-Asset Code-to-Database Linkage (Group 3)

1. In **💬 Chat**, enter:
   ```text
   What databases or tables are connected to repo_alpha?
   ```
2. **Verification Points:**
   - Agent executes `query_cross_asset_links`.
   - Returns matched ORM entities, SQL table references, and database driver dependencies.
   - Shows confidence score (`HIGH` / `MEDIUM` / `LOW`) and exact matched table names.

---

### Protocol 5: Interactive Disambiguation & Candidate Chips (Group 4)

1. In **💬 Chat**, enter an ambiguous comparison query without naming two resources:
   ```text
   Compare projects
   ```
2. **Verification Points:**
   - Response prompts the user to select resources and renders interactive candidate chips:
     `💡 Select candidate resources to include in comparison / integration:`
     `[ 📦 repo_alpha ] [ 📦 repo_beta ] [ 🗄️ db_staging ]`
   - Clicking any chip appends its slug directly into the chat input field.

---

### Protocol 6: Upstream Egeria Lineage Traversal (Group 4)

1. In **💬 Chat**, enter:
   ```text
   Show data lineage and pipeline connections between ingestion_service and analytics_db
   ```
2. **Verification Points:**
   - Agent executes `query_egeria_lineage`.
   - In offline mode: reports that local survey metadata is used with zero unhandled exceptions.
   - In live Egeria mode: queries Open Metadata Highway asset GUIDs and reports process lineage edges.

---

### Summary Checklist for Return

- [x] Group 1: Database schema diffing (`compare_database_schemas`) & Chat-to-RFA (`create_rfa_from_finding`).
- [x] Group 2: Multi-resource context compilation (`compile_multi_resource_context`) & Epistemological Matrix.
- [x] Group 3: Static code-to-database table matcher (`cross_asset_matcher.py`) & Dynamic delegation.
- [x] Group 4: Upstream Egeria lineage tool (`query_egeria_lineage`) & UI Disambiguation Chips.
- [x] Test protocols saved at `docs/cross-resource-test-protocols.md`.
- [x] Ready for reboot and morning git synchronization.

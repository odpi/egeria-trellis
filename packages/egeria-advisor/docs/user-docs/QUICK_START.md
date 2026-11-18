# Egeria Advisor — Quick Start

**Last Updated:** 2026-11-18

Get the web UI running and ask your first question in under five minutes.

---

## Prerequisites

Ensure these services are running before you start:

| Service | Default location | Required for |
|---|---|---|
| PostgreSQL + pgvector | `localhost:5442` | All queries (vector store) |
| Ollama | `localhost:11434` | LLM generation |
| Egeria / pyegeria MCP server | `localhost:9443` | Report and action queries only |
| MLflow | `localhost:5025` | Optional — experiment tracking |

**Models used:** `llama3.1:8b` for fast Q&A/RAG, and `qwen2.5-coder:32b` for the
Literate Governance planner (narrative generation and refinement). Pull both:

```bash
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:32b
```

```bash
# Check Ollama
curl http://localhost:11434/api/tags

# Check pgvector
psql -h localhost -p 5442 -U egeria_advisor -d egeria_advisor -c "SELECT COUNT(*) FROM pyegeria;"

# Check Egeria (if using reports or actions)
curl -k https://localhost:9443/open-metadata/platform-services/users/garygeeke/server-platform/origin
```

---

## Configure

Do the shared setup in the [top-level README](../../../../README.md) first: clone, `uv sync` at the repository root,
and copy each package's `.env.example` to `.env`. Then, in `packages/egeria-advisor/.env`:

| Setting | What it is |
|---|---|
| `EA_DAEMON_USER_ID` / `EA_DAEMON_USER_PASSWORD` | EA's **own** Egeria service account, never a person. EA uses it for its background report-spec sync and for anonymous reads. On the Egeria QuickStart it is `egeriaadvisornpa`, with its password from the QuickStart's user directory. Unset: those paths refuse in words. (Before TR-34 this was `EGERIA_USER` / `EGERIA_PASSWORD`, still read with a startup warning until after 6.3.) |
| `PGVECTOR_PASSWORD` | The `egeria_advisor` Postgres role's password. Required, with no default. |
| `EGERIA_VIEW_SERVER_URL` / `EGERIA_VIEW_SERVER` | Only if your Egeria isn't the local default (`https://localhost:9443`, `view-server`). |

Don't set `EGERIA_USER` / `EGERIA_USER_PASSWORD` for the EA server: those are pyegeria's names for running its tools
as yourself in a shell, and EA warns at startup if it sees them.

## Start the Web UI

From the repository root:

```bash
make up                                   # preflight, ensure secrets, then start RE and EA together
make up-ea                                # the same, EA only
# or start EA directly, one process, no auto-reload:
uv run egeria-advisor-web --port 8880
# or, loading packages/egeria-advisor/.env first:
packages/egeria-advisor/scripts/run_web.sh
```

Open **http://localhost:8880** in your browser.
**Sign in with your own Egeria user id and password.** Every answer EA runs against Egeria runs as you, so you see
what your zones let you see.

> **EA's MCP tools don't run for a signed-in user yet.** The pyegeria and Dr.Egeria MCP tools sign in as EA's
> service account, not as you, so EA doesn't use them for your questions. Where it would have, it says *"That tool
> can't run as you yet, so I didn't use it."*, and a report-spec search covers EA's local copy only, with a note
> saying so. Report specs, reports and Dr.Egeria templates still work. Per-user MCP access comes later (TR-43).


### Accessing the Web UI from another machine

By default, Uvicorn binds to `127.0.0.1`, which only accepts connections from the same machine. 
If you want to open the web UI from another computer on the same network, bind the server to all network interfaces:

For example, to allow access from any device on the local network:

```bash
uvicorn advisor.web.app:app --reload --host 0.0.0.0 --port 8880
```
Then, from the remote machine, open a browser and navigate to `http://<host-ip>:8880`, replacing `<host-ip>` with the IP address of the machine running the server.

If the page still does not load, check:

- the host machine's firewall allows inbound TCP traffic on the selected port
- you are using the host machine's LAN IP address, not `localhost`
- both machines are on the same network/VLAN
- if running in Docker, a VM, or a container, the port is published/mapped to the host



## UI Layout

```
┌──────────────────────────────────────────────────────────────┐
│  [Logo]  Egeria Advisor                           ●          │  ← Header (● = MCP status)
├───────────────────┬──────────────────────────────────────────┤
│ Reports|Plans|Recent│                                         │  ← tabbed sidebar
│  ▶ Glossary       │  [chat messages appear here]              │
│  ▶ Governance     │                                          │
│  ▶ Projects       │  ─────────────────────────────────────  │
│  ▶ ...            │  As:  Anyone  Developer  Data Engineer   │
│                    │        Steward  Governance               │
│                    │  Intent: Auto Explain Show me Inspect    │
│                    │    Run Report  Act  Create  Troubleshoot │
│                   │  [Enter your question...]       [Send]   │
└───────────────────┴──────────────────────────────────────────┘
```

**Left sidebar:** tabbed — **Reports** (grouped by topic, click to open the Run modal),
**Plans** (drafts/inbox/outbox plus browsable saved Plan Templates), **Recent** (query history).  
**As:** row: select your role (affects routing and response framing).  
**Intent:** row: override automatic query classification.

---

## Your First Five Queries

Try these in order to see each capability:

### 1. Conceptual explanation (Anyone, Auto)
```
What is a governance zone?
```
*→ Explanation from indexed Egeria documentation*

### 2. Live report (Anyone, Report — or click from sidebar)
```
List available glossaries
```
*→ Live data table from your Egeria instance*

### 3. Python API reference (set role to Developer, Auto)
```
What methods are available for governance definitions?
```
*→ Structured table: GovernanceOfficer class, method names, signatures*

### 4. Runnable code example (Developer, Auto or Show me)
```
Give me a python example to create a governance zone
```
*→ Complete Python script using GovernanceOfficer.create_governance_definition with GovernanceZoneProperties body*

### 5. Dr.Egeria template (set role to Data Steward, Act or Show me)
```
Show me a Dr.Egeria template for creating a glossary
```
*→ Markdown template to paste into an Egeria Workspaces Jupyter cell*

### 6. Governance plan — single topic (set Intent to Plan)
```
Set up a glossary for the finance domain with terms and categories
```
*→ Proposed command list → Plan Canvas → Plan Document → Execute*

### 7. Governance plan — multiple items of the same type (Intent: Plan)
```
Create a plan to define solution components for UK Sales Forecast database,
EU Sales Forecast database, US Sales Forecast Database and WorldWide Sales
Forecast Database. Put all of these components in the same blueprint.
```
*→ 1 × Create Solution Blueprint (auto-named from common suffix) + 1 per component,
each with `In Solution Blueprints` pre-filled with the blueprint's qualified name*

---

## Role and Intent Quick Reference

**Role selector (As:)**

| Role | When to use |
|---|---|
| **Anyone** | General questions, live data, conceptual explanations |
| **Developer** | Python code examples, API discovery, integration work |
| **Data Engineer** | Pipeline, connector, ingestion queries — same code routing as Developer |
| **Data Steward** | Dr.Egeria templates, glossary management, data quality — ambiguous "show me" queries ask whether you want Python or a template |
| **Governance** | Policy, compliance, governance zone management — same clarification behaviour as Data Steward |

**Intent selector**

| Button | Use when |
|---|---|
| **Auto** | Default — role + query signals determine the route |
| **Explain** | You want a concept explained, not code or data |
| **Show me** | Force Python code / API reference (even without Developer role) |
| **Inspect** | Ask about the codebase's own structure — classes, inheritance, method locations, complexity — answered from a SQL symbol table, not RAG |
| **Run Report** | Force live data from your Egeria instance |
| **Act** | Force Dr.Egeria command template or execution |
| **Create** | Build a new governance plan or report spec |
| **Troubleshoot** | You're diagnosing an error |

---

## Common Query Patterns

See **[Prompt Patterns Guide](PROMPT_PATTERNS_GUIDE.md)** for a comprehensive set of examples by role and intent. A brief summary:

| I want… | Role | Intent | Example query |
|---|---|---|---|
| Concept explanation | Anyone | Explain | "What is a governance zone?" |
| Live Egeria data | Anyone | Report | "List all governance zones" |
| Python code example | Developer | Auto | "Python example to create a glossary term" |
| API method list | Developer | Show me | "What methods does GovernanceOfficer have?" |
| Dr.Egeria template | Data Steward | Act | "Dr.Egeria template for creating a project" |
| Execute an action | Data Steward | Act | "Create a governance zone called Finance" |
| Debug an error | Developer | Troubleshoot | "Why am I getting 403 on create_governance_definition?" |

---

## Running Reports from the Sidebar

1. Click any report name in the left panel
2. Optionally enter a **Search string** to filter (e.g., `finance`)
3. Click **Run**

The result is rendered as a markdown table in the chat. The sidebar always forces `Report` intent regardless of which intent button is currently selected.

---

## CLI Alternative

```bash
# One-shot query
egeria-advisor "What is a glossary term in Egeria?"

# Interactive multi-turn session
egeria-advisor --interactive

# Agent mode (BeeAI conversational memory)
egeria-advisor --agent
```

---

## If Something Isn't Working

| Symptom | Fix |
|---|---|
| Getting a report instead of code | Add "python" to your query, or use Show me intent |
| Getting code instead of a report | Use Report intent, or click the report in the sidebar |
| Getting a clarification (Python vs Dr.Egeria?) | Set intent explicitly: Show me for code, Act for template |
| Response mentions methods that don't exist | Include the class name: "using GovernanceOfficer" |
| MCP dot is red | Egeria server not reachable — report and action queries won't work |
| "No relevant content found" | Check that collections are indexed: `python scripts/count_vectors.py` |
| Answers reference content that seems missing, wrong, or inconsistent between collections | The downloaded repos, vector store, and config may have drifted apart — see [Repo Update Guide](REPO_UPDATE_GUIDE.md#full-reset) for `scripts/full_reset.sh`, which re-clones every source repo and re-ingests all collections from one consistent snapshot |
| Plan creates wrong command type (e.g. Create Project instead of Create Solution Component) | The server may be running old code — restart it (see below) |
| All plan steps show the same name | Start a **fresh** plan — don't resume an old draft from before a code update |

### Restarting the server after a code update

Python loads modules once at startup; code changes don't take effect until you restart.
If you pull new changes or the assistant makes code edits, always restart:

```bash
# Stop the running server (Ctrl-C), then:
uv run --package egeria-advisor python -m advisor.web.app
```

Existing drafts stored in `~/egeria-plans/drafts/` use commands extracted under the old
code. After a restart, **start a new plan** rather than resuming a draft that was created
before the update.

See **[Query Routing Guide](QUERY_ROUTING_GUIDE.md)** for detailed routing behaviour and troubleshooting.

---

## Planning Multi-Step Governance Tasks

For tasks that involve multiple related objects — a glossary *with* terms, categories, *and* steward roles — describe the full task in plain language and the advisor builds a complete **Plan Document**:

```
"Set up a glossary for the Finance domain with standard terms,
 categories, and data steward assignments"
```

The advisor will:
1. **Confirm the steps** it extracted before asking for any detail
2. Open a live **Plan Canvas** beside the chat — reorder, edit, add, or remove steps
   conversationally or by editing cards directly
3. **Generate** a structured markdown plan with pre-filled Dr.Egeria commands
4. **Execute** it against Egeria when you click **Execute**
5. **Report** the outcome and run verification reports automatically

Plans are saved to `~/egeria-plans/` and accessible from the **Plans** sidebar between
sessions; in-progress sessions appear as **Drafts** you can resume any time.

See **[Literate Governance Guide](LITERATE_GOVERNANCE_GUIDE.md)** for the full workflow, CLI tools, and troubleshooting.

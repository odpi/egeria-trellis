# Egeria Advisor

Experimental AI-powered advisor for the Egeria Python library using local LLMs and RAG.
The goal is to provide a useful advisor for Egeria and pyegeria users. You should be able to ask questions about the
concepts and code, ask for examples, find definitions, and ask the advisor to take actions on your behalf using Dr. Egeria
markdown commands or pyegeria directly.

This is also a testbed for experiments with AI, RAG, Agents, LLMs, and more. There are experimental features and ideas
that are not yet fully cooked, integrated, or tested.

This is a work in progress. There are known limitations and bugs, and the system is not production-ready.

The accuracy of the results is still only fair at best. Hallucinations and errors do occur, and results are tracked and
feedback collected to drive ongoing improvements.

Feedback and comments are welcome. Please share your thoughts and suggestions to help improve the system.

## Overview

Egeria Advisor is a RAG (Retrieval-Augmented Generation) system that helps users and maintainers work with the Egeria
Python library by providing:

### Core Capabilities

- **Multi-Collection Search**: 9 specialized repository collections (~88,900 entities) with intelligent routing
- **Action Execution**: Dr. Egeria integration to compose and execute pyegeria commands from natural-language requests
- **Report Generation**: MCP-based report pipeline for structured Egeria data queries; reports browsable in the sidebar, results rendered as markdown tables
- **Perspective-Aware Responses**: Select a user role (Developer / Data Engineer / Data Steward / Governance Officer) and responses are tailored in depth, terminology, and focus
- **Conversational Agent**: Multi-turn conversations with context and memory (BeeAI)
- **Code Analysis**: Deep understanding of Python/Java code, APIs, and patterns
- **Performance Optimization**: Query cache speedup, parallel collection search, universal GPU support
- **Enhanced Tracking**: MLflow integration for metrics, resource monitoring, and accuracy
- **Incremental Updates**: 10-100x faster updates with file change tracking
- **Real-time Monitoring**: Terminal dashboard with metrics collection

### Key Features

✅ **Multi-Collection Architecture**: 9 specialized collections with intelligent query routing  
✅ **Action & Report Pipeline**: Dr. Egeria command execution and MCP-based report generation  
✅ **Perspective-Aware Responses**: Role selector tailors every response (Developer / Data Engineer / Data Steward / Governance)  
✅ **Universal GPU Support**: Auto-detection for CUDA, ROCm, MPS, and CPU  
✅ **Conversational Agent**: BeeAI framework integration with memory  
✅ **Rich CLI**: 3 interaction modes (query, interactive, agent)  
✅ **MLflow Tracking**: Experiment and query tracking (background, non-blocking)  
✅ **Incremental Indexing**: Fast updates with SQLite-based change detection  
✅ **Monitoring Dashboard**: Real-time metrics and health monitoring  

## Architecture

### Technology Stack

- **LLM**: Ollama (local) @ localhost:11434
  - Primary Model: llama3.1:8b (fast, general purpose)
  - Code Model: codellama:13b (code-specialized)
- **Vector Store**: pgvector (PostgreSQL) @ localhost:5442
  - 9 specialized collections (~88,900 entities)
  - 384-dimensional HNSW embeddings
  - Database: `egeria_advisor`, user: `egeria_advisor`
- **Embeddings**: sentence-transformers/all-MiniLM-L6-v2 (local)
  - Universal device support (CUDA/ROCm/MPS/CPU)
- **Experiment Tracking**: MLflow @ localhost:5025 (optional)
- **Metrics Storage**: SQLite (query metrics, collection health, system resources)
- **Agent Framework**: BeeAI for conversational interactions

### Collections

| Collection | Purpose |
|-----------|---------|
| `pyegeria` | Core Python library code and tests |
| `pyegeria_cli` | hey_egeria CLI commands and tools |
| `pyegeria_drE` | Dr. Egeria markdown-to-pyegeria translator |
| `egeria_java` | Core Java library (OMAS, OMAG, OMRS) |
| `egeria_concepts` | Core concept definitions |
| `egeria_types` | Type system and schema definitions |
| `egeria_general` | Tutorials, guides, and how-tos |
| `egeria_workspaces` | Jupyter notebooks, deployment configs, examples |
| `egeria_templates` | Dr. Egeria markdown command templates |

Collections are defined in `advisor/collection_config.py`. Query routing selects 1–N collections per query based on
classified intent; see `advisor/collection_router.py`.

### Query Flow

```
User Query  [+ perspective + intent_override]
  → Web UI / CLI
  → RAGSystem (advisor/rag_system.py)
      ├─ QueryCache                   ← checked first; instant for repeated queries
      ├─ QueryProcessor               ← pattern-match classifier (config/routing.yaml)
      │    └─ if 'general': LLM intent classifier → refines to code_search / report / command / …
      │
      ├─ Format/role-aware routing ("Show me" disambiguation, before intent dispatch)
      │    ├─ explicit Dr.Egeria/CLI/Java phrasing → routes or clarifies directly
      │    └─ Developer|Data Engineer + explicit code/python signal → ExamplesAgent
      │        (ambiguous "show me" with no format signal → 3-way clarify, any role)
      │
      ├─ quantitative  → Analytics module (direct SQL)
      ├─ relationship  → Relationship graph handler
      ├─ report        → MCP report pipeline  ← semantic pre-check (score ≥ 0.50)
      ├─ command + template/example keyword
      │    → DrEgeriaTemplateAgent    ← returns filesystem markdown template
      ├─ command (no template keyword)
      │    → DrEgeriaActionAgent      ← composes/executes pyegeria command
      ├─ code_search|example
      │    → ExamplesAgent
      │         ├─ method-discovery  → API reference table (classes + methods)
      │         └─ code example      → runnable Python (canonical pyegeria pattern)
      ├─ explanation|debugging|general → DocAgent
      └─ fallback → RAG retrieval + LLM generation
           ├─ CollectionRouter       ← selects relevant collections
           └─ MultiCollectionStore   ← parallel search, pgvector HNSW
```

## Web UI

A browser-based chat interface is the primary way to interact with Egeria Advisor.

```bash
# Start the web server (default: http://localhost:8880)
uvicorn advisor.web.app:app --reload --port 8880
```

### Layout

```
┌─────────────────────────────────────────────────────────┐
│  [Egeria logo]  Egeria Advisor  ●  [ Show DrE specs ]   │  ← Header
├──────────────────────┬──────────────────────────────────┤
│  Available Reports   │                                  │
│  ▶ Glossary          │  [chat messages]                 │
│  ▶ Collections       │                                  │
│  ▶ Assets            │                                  │
│  ▶ ...               │                                  │
│  ─────────────────── │  As: Anyone  Developer  Data     │
│  Recent Queries      │       Engineer  Steward  Gov.   │
│  ...                 │  Intent: Auto  Explain  Report  │
│                      │  [query input]         [Send]   │
└──────────────────────┴──────────────────────────────────┘
```

### Running Reports

**From the sidebar** (recommended): Click any report name to open a modal. Enter an optional *Search string* to filter results (e.g. `finance`) then click **Run**. The report result is rendered as a markdown table in the chat.

**From the chat input**: Type `run report <exact-report-name>` or let the system classify your query as a report query automatically (e.g. *"show me all glossaries"*).

### Perspective Selector

The **As:** row above the input lets you declare your role. This affects both the response framing and the routing:

| Role | What changes |
|------|-------------|
| **Anyone** (default) | General response, no role-specific framing |
| **Developer** | Code signals ("example", "show me", "what methods") are automatically routed to ExamplesAgent — returns runnable Python or API reference tables |
| **Data Engineer** | Same as Developer for code signals; pipeline/connector/ingestion context in explanations |
| **Data Steward** | Ambiguous "show me" / "example" without a Python keyword → clarification offered (Python code vs Dr.Egeria template) |
| **Governance** | Policies, compliance controls, governance zones; same clarification behaviour as Data Steward |

### Intent Override

The **Intent:** row overrides automatic query classification:

| Button | Sends intent | Use when |
|--------|-------------|----------|
| Auto | *(system decides)* | Default — role + signals determine routing |
| Explain | `explanation` | You want a conceptual explanation |
| Show me | `code_search` | Force a pyegeria code example or API reference listing |
| Report | `report` | Force live Egeria data from the MCP report pipeline |
| Act | `command` | Force Dr. Egeria to compose or execute a command |
| Troubleshoot | `debugging` | You're diagnosing a problem |

> **Tip:** When using **Show me**, the system distinguishes between code-example queries ("give me a python example of…") and method-discovery queries ("what methods are available for…") and returns the appropriate output automatically.

### Sample Query Patterns

The advisor generates different responses depending on your role and what you ask. A few examples:

| Role | Intent | Query | Response |
|---|---|---|---|
| Developer | Auto | "Give me a python example to create a governance zone" | Runnable Python using `GovernanceOfficer.create_governance_definition` |
| Developer | Show me | "What methods are available for governance definitions?" | Class + method table (GovernanceOfficer) |
| Data Steward | Act | "Show me a Dr.Egeria template for creating a glossary" | Markdown template for Jupyter |
| Data Steward | Act | "Create a governance zone called Finance" | Dr.Egeria command execution |
| Anyone | Report | "List available glossaries" | Live data table from Egeria |
| Anyone | Explain | "What is a governance zone?" | Concept explanation from docs |

See **[Prompt Patterns Guide](docs/user-docs/PROMPT_PATTERNS_GUIDE.md)** for comprehensive examples covering all roles, intents, multi-turn patterns, and common mistakes.

### Key points for Python code (Developer / Data Engineer role)

Every generated example follows the canonical pyegeria pattern: both constructor options (explicit parameters and zero-argument from `.env`), `create_egeria_bearer_token()` before any API call, try/except `PyegeriaException`, and `close_session()` in `finally`.

Egeria uses a unified creation API for governance definitions — `GovernanceZone`, `GovernancePrinciple`, `GovernanceObligation`, etc. are all created via `GovernanceOfficer.create_governance_definition(body)` with `"typeName": "GovernanceZone"` in the body. The advisor will generate the correct body structure, not an invented `create_governance_zone()` method.

### Dr.Egeria Templates (Data Steward / Governance role + Act intent)

Dr.Egeria templates are markdown commands you paste into an Egeria Workspaces Jupyter cell. They cover all create/update/link operations. Templates are read from `{EGERIA_ROOT_PATH}/Templates/Dr-Egeria-Templates/` and can be regenerated with `generate_md_cmd_templates.py --advanced`.

## Quick Start

### Prerequisites

Ensure these services are running:

```bash
# PostgreSQL with pgvector (vector store)
psql -h localhost -p 5442 -U egeria_advisor -d egeria_advisor -c "SELECT COUNT(*) FROM pyegeria;"

# Ollama (LLM inference)
curl http://localhost:11434/api/tags

# MLflow (optional — for experiment tracking)
curl http://localhost:5025

# Egeria server (if running action/report queries)
curl -k https://localhost:9443/open-metadata/platform-services/users/garygeeke/server-platform/origin
```

### Installation

```bash
# Navigate to project
cd /Users/dwolfson/localGit/egeria-v6/egeria-advisor

# Activate virtual environment
source activate_venv.sh

# Install with dev dependencies
pip install -e ".[dev]"

# Verify setup
python -c "from advisor.config import settings; print('✓ Config loaded')"
```

### Pull Ollama Models

```bash
# If using Docker
docker exec ollama ollama pull llama3.1:8b
docker exec ollama ollama pull codellama:13b

# If using native Ollama
ollama pull llama3.1:8b
ollama pull codellama:13b
```

## Usage

### 1. Query Mode (Direct Questions)

```bash
# Simple question
egeria-advisor "What is a glossary term in Egeria?"

# Ask for code examples
egeria-advisor "How do I create a glossary in pyegeria?"

# With MLflow tracking enabled (default)
egeria-advisor "Show me asset management examples"

# Without MLflow tracking
egeria-advisor --no-track "What is a metadata repository?"

# JSON output
egeria-advisor "What is a collection?" --format=json

# Disable source citations
egeria-advisor --no-citations "Explain governance zones"
```

### 2. Interactive Mode (Multi-turn Conversations)

```bash
egeria-advisor --interactive

# Prompts:
egeria> What is a metadata repository?
egeria> How do I connect to one?
egeria> Show me example code
egeria> /dry-run       # Toggle dry-run for Dr. Egeria commands
egeria> /citations     # Toggle source citations
egeria> /help          # Show all commands
egeria> /exit
```

**Interactive commands:**

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/clear` | Clear conversation context |
| `/history` | Show recent query history |
| `/verbose` | Toggle verbose output |
| `/citations` | Toggle source citations |
| `/dry-run` | Toggle Dr. Egeria dry-run (compose commands without executing) |
| `/feedback` | Provide feedback on last response |
| `/stats` | Show feedback statistics |
| `/exit` | Exit (also Ctrl+D) |

### 3. Agent Mode (Conversational with Memory)

```bash
egeria-advisor --agent

# Agent maintains conversation history across turns:
egeria> I need to create a glossary
egeria> What parameters do I need?
egeria> Show me the complete code
egeria> /tools         # List available MCP tools
egeria> /execute       # Execute an MCP tool directly
egeria> /exit
```

### 4. Action Queries (Dr. Egeria)

Natural-language requests classified as `command` type are handled by the DrEgeriaActionAgent.
It finds the appropriate Dr. Egeria markdown template and composes the command with your parameters.

```bash
# Direct action query
egeria-advisor "Create a glossary called 'Data Governance Terms'"

# In interactive mode with dry-run (preview without executing)
egeria> /dry-run
egeria> Create a project called 'Data Quality Initiative'
```

### 5. Monitoring & Incremental Updates

```bash
# Start real-time monitoring dashboard
python -m advisor.dashboard.terminal_dashboard

# Detect changes in a collection (dry-run)
python -m advisor.incremental_indexer --collection pyegeria --dry-run

# Apply incremental updates to a collection
python -m advisor.incremental_indexer --collection pyegeria

# Update all collections
python -m advisor.incremental_indexer --all
```

### 6. Testing

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
```

## Project Structure

```
egeria-advisor/
├── advisor/                    # Main package
│   ├── agents/                 # Agent implementations
│   │   ├── base.py             # BaseAdvisorAgent (BeeAI RequirementAgent wrapper)
│   │   ├── tools.py            # Shared BeeAI @tool functions + _raw helpers
│   │   ├── dr_egeria_agent.py  # DrEgeriaActionAgent — composes/executes commands via MCP
│   │   ├── dre_template_agent.py  # DrEgeriaTemplateAgent — returns filesystem .md templates
│   │   ├── examples_agent.py   # ExamplesAgent — code examples + API reference listings
│   │   ├── doc_agent.py        # DocAgent — conceptual explanations
│   │   ├── conversation_agent.py  # ConversationAgent — multi-turn BeeAI sessions
│   │   └── cli_command_agent.py   # CLICommandAgent — hey_egeria command lookup
│   ├── llm_intent_classifier.py   # LLM-based intent refinement (general → code_search/command/…)
│   ├── cli/                    # CLI interface
│   ├── web/                    # FastAPI web UI + static SPA
│   ├── data_prep/              # Data preparation pipeline
│   ├── multi_collection_store.py
│   ├── rag_system.py           # Main RAG orchestrator + role-aware routing
│   ├── report_pipeline.py      # MCP report pipeline
│   └── collection_config.py    # Collection definitions
├── config/
│   ├── advisor.yaml            # Primary configuration
│   ├── routing.yaml            # Intent routing rules (CRITICAL/HIGH/MEDIUM/LOW priority patterns)
│   └── report_specs/           # Report specification JSON
├── scripts/
├── data/                       # SQLite metrics database
├── tests/                      # Test suite
└── docs/                       # Architecture and design docs
    ├── user-docs/QUERY_ROUTING_GUIDE.md   # Intent routing reference
    └── design/SYSTEM_ARCHITECTURE.md
```

## Configuration

Primary config: `config/advisor.yaml`. Key sections:

- **data_sources**: Path to egeria-python and egeria-workspaces repositories
- **pgvector**: PostgreSQL connection (host, port, dbname, user, password)
- **vector_store_backend**: `pgvector` (active) or `milvus` (legacy)
- **llm**: Ollama model selection and parameters per agent type
- **embeddings**: Model, device, batch size
- **rag**: chunk_size, top_k, min_score thresholds
- **observability**: MLflow tracking URI and experiment name

- **Service accounts**: `RE_DAEMON_USER_ID` and `TRELLIS_SERVICE_ACCOUNTS` (comma-separated) list Egeria user ids no human may sign in as; EA answers HTTP 403 "this is a service account; sign in as yourself" before any Egeria call (case-insensitive, trimmed; unset refuses nobody).

Settings are managed via Pydantic models in `advisor/config.py`.

## Monitoring & Observability

### Terminal Dashboard

```bash
# Start real-time monitoring (5-second auto-refresh)
python -m advisor.dashboard.terminal_dashboard
```

Displays: collection health, recent queries, query performance (latency, cache hits), and system resources.

### MLflow Tracking

MLflow tracking runs in a background daemon thread and does not block query responses.

- **MLflow UI**: <http://localhost:5025>
  - View experiments and runs
  - Compare query performance over time
  - Monitor collection usage patterns

Disable per-query with `--no-track`. Set `mlflow.enabled: false` in `advisor.yaml` to disable globally.

### SQLite Metrics

All query metrics are stored locally in `data/metrics.db` regardless of MLflow status:
- Query text, timestamp, and latency
- Collections searched and result counts
- Cache hit/miss status

## Documentation

New to Trellis entirely? Start at the [workspace quickstart](../../QUICKSTART.md), which gets
both apps running without needing an Egeria server.

### Getting started

- [Quick Start](docs/user-docs/QUICK_START.md) — this app specifically, in five minutes
- [Ollama setup](docs/user-docs/ollama-setup-guide.md) — the default LLM backend
- [Testing guide](docs/user-docs/TESTING_GUIDE.md)

### Using it

- [Prompt Patterns Guide](docs/user-docs/PROMPT_PATTERNS_GUIDE.md) — examples by role, intent, and use case
- [Query Routing Guide](docs/user-docs/QUERY_ROUTING_GUIDE.md) — how routing works under the hood
- [Interactive Routing Guide](docs/user-docs/INTERACTIVE_ROUTING_GUIDE.md) — when it asks you to disambiguate
- [Data-Driven Routing Guide](docs/user-docs/DATA_DRIVEN_ROUTING_GUIDE.md)
- [Multi-Collection Usage Guide](docs/user-docs/MULTI_COLLECTION_USAGE_GUIDE.md)
- [Literate Governance Guide](docs/user-docs/LITERATE_GOVERNANCE_GUIDE.md) — authoring governance plans
- [Report Spec Guide](docs/user-docs/REPORT_SPEC_GUIDE.md)
- [CLI Command Agent Guide](docs/user-docs/CLI_COMMAND_AGENT_GUIDE.md)
- [MCP Integration Guide](docs/user-docs/MCP_INTEGRATION_GUIDE.md)
- [User Feedback Guide](docs/user-docs/USER_FEEDBACK_GUIDE.md)

### Operating it

- [Collection Maintenance Guide](docs/user-docs/COLLECTION_MAINTENANCE_GUIDE.md)
- [Repo Update Guide](docs/user-docs/REPO_UPDATE_GUIDE.md)
- [RAG Tuning Guide](docs/user-docs/RAG_TUNING_GUIDE.md)
- [Quality Improvement Guide](docs/user-docs/QUALITY_IMPROVEMENT_GUIDE.md)
- [ONNX Migration Guide](docs/user-docs/ONNX_MIGRATION_GUIDE.md) — the faster embedding path
- [MLflow Enhanced Tracking](docs/user-docs/MLFLOW_ENHANCED_TRACKING.md) and
  [experiment tracking](docs/user-docs/mlflow-experiment-tracking-guide.md)

### Design & architecture

- [System Architecture](docs/design/SYSTEM_ARCHITECTURE.md)
- [Multi-Collection Design](docs/design/INDEXING_AND_COLLECTIONS.md)
- [Query Classification & Tracking](docs/design/QUERY_HANDLING_AND_QUALITY.md)
- [Egeria Docs Split Strategy](docs/design/INDEXING_AND_COLLECTIONS.md)

How live subsystems actually work — these were unlinked until 2026-08-30, which is why they
were hard to find rather than out of date:

- [Incremental Indexing](docs/design/INDEXING_AND_COLLECTIONS.md) — `advisor/incremental_indexer.py`
- [Scoped Queries](docs/design/QUERY_HANDLING_AND_QUALITY.md) and its
  [troubleshooting notes](docs/design/QUERY_HANDLING_AND_QUALITY.md)
- [Exhaustive Query Detection](docs/design/QUERY_HANDLING_AND_QUALITY.md)
- [Metadata Filtering](docs/design/INDEXING_AND_COLLECTIONS.md) — `advisor/metadata_filters.py`
- [Agents, routing and code analysis](docs/design/AGENTS_AND_CODE_ANALYSIS.md) — includes the code-analysis update instructions (§4b)
- [Egeria & pyegeria wish list](docs/design/egeria-wishlist.md) — gaps found while integrating

Finished and abandoned work is in [docs/archive/](docs/archive/README.md), which explains what
moved there and why.

## Contributing

This package is a member of the Trellis `uv` workspace — there is one shared `.venv` at the
repo root and no per-package virtualenv to activate. Run everything from the workspace root.

```bash
# Development setup — installs every workspace member
uv sync

# Format and lint
uv run black packages/egeria-advisor/advisor/
uv run ruff check packages/egeria-advisor/advisor/
uv run mypy packages/egeria-advisor/advisor/

# Run tests (the dev extra carries pytest-cov, which a plain `uv sync` omits)
uv run --package egeria-advisor --extra dev pytest packages/egeria-advisor/tests
# or, from the repo root: make test-ea
```

## Support

- **Project Lead**: <dan.wolfson@pdr-associates.com>
- **Egeria Community**: <http://egeria-project.org/guides/community/>
- **Issues**: <https://github.com/odpi/egeria-python/issues>
- **Slack**: #egeria-python on LF AI & Data Slack

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.

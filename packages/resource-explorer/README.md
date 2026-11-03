# Resource Explorer

**Resource Explorer** discovers, understands, and catalogs information resources — Git repositories, PostgreSQL databases, and file systems — using [Egeria](https://egeria-project.org/) as the central metadata catalog.

> Successor to [Project Explorer](https://github.com/LF-AI/project-explorer). Egeria-first architecture; Egeria is **required** — it is both the catalog of record and, since 2026-09-04, the identity provider you sign in against.

---

## Quick Start

New to Trellis? The [workspace quickstart](../../QUICKSTART.md) gets this app running from a
fresh clone, including Postgres and Ollama, without needing an Egeria server. The commands below
assume that is already done.

```bash
# Install (from the workspace root)
uv sync
cp .env.example .env          # set GITHUB_TOKEN, EGERIA_*, LLM_BACKEND, …

# Start the web UI
uv run resource-explorer web  # → http://localhost:8810
```

Start the external services required for RAG and Egeria integration:
```bash
# pgvector — shared with Egeria Advisor's egeria_advisor Postgres database (port 5442).
#
# With egeria-workspaces (recommended if you have it): the shared Postgres container
# (egeria-shared-postgres, from egeria-workspaces-fs's
# compose-configs/shared-infra/shared-infra.yaml) is normally already running, and its
# init script already creates the egeria_advisor role/database + vector extension.
# You still need to create Resource Explorer's own named schema once:
docker exec egeria-shared-postgres psql -U egeria_advisor -d egeria_advisor -p 5442 \
  -c 'CREATE SCHEMA IF NOT EXISTS resource_explorer AUTHORIZATION egeria_advisor;'

# Without egeria-workspaces (standalone/from-scratch): create your own container, then
# the same schema against it:
docker run -p 5442:5432 -e POSTGRES_DB=egeria_advisor -e POSTGRES_USER=egeria_advisor -e POSTGRES_PASSWORD=advisor pgvector/pgvector:pg17
docker exec <container_name_or_id> psql -U egeria_advisor -d egeria_advisor \
  -c 'CREATE SCHEMA IF NOT EXISTS resource_explorer AUTHORIZATION egeria_advisor;'

# Ollama (default LLM backend) — install from https://ollama.com if not already present
ollama serve &                # skip if already running as a background service
ollama pull llama3.1:8b
```

No separate table migration step is needed beyond the schema creation above — Resource
Explorer's vector tables are per-project (`{project_slug}_{collection_type}`) inside the
`resource_explorer` schema and provision themselves lazily the first time a project is
scouted/surveyed (`auto_provision_on_insert=True`), same as its registry tables (projects,
stats, etc.), which self-create on first use. The *schema* itself is different from those
tables — `CREATE SCHEMA IF NOT EXISTS` only runs the first time something actually connects
through `PgVectorStore`, so don't count on it existing before you've triggered real RE usage;
run the explicit command above so it's there from the start.

---

## What it does

Work is organised by **intent**. There are eight canonical intents (the strings used in the activity
log, UI filters and analysis catalog; the count is checked against `configdata/analysis_catalog.yaml`
and `CLAUDE.md` rule 17). The Next UI also has a framing stage, **Investigation**, which is not an
intent.

| Intent | Description |
|--------|-------------|
| **Scouting** | Fast broad inventory across many resources |
| **Discovery** | Cheap derivation over what Scouting collected, to decide what is worth a deeper look |
| **Assessment** | Scored evaluation of a resource against criteria (security, documentation, CI quality) |
| **Analysis** | Structural and quantitative results a person can look at (architecture recovery, schema, dependencies) |
| **Enrichment** | Provide human context; answer open RequestForAction annotations |
| **Understanding** | Visualise and explain what the surveys revealed |
| **Curate** | Ongoing curatorial work: tags, feedback, publishing to Egeria |
| **Automate** | Recurring watch-for-changes and notifications |

---

## Documentation

| Document | Audience |
|----------|----------|
| [docs/user-guide.md](docs/user-guide.md) | Daily users: web UI walkthrough, chat, intents |
| [docs/Architecture.md](docs/Architecture.md) | Developers: module map, query/survey flows, key design decisions |
| [docs/admin-guide.md](docs/admin-guide.md) | Operators: installation, Egeria setup, scheduling, troubleshooting |
| [docs/survey-model.md](docs/survey-model.md) | Architects: the survey/activity/annotation model (read before architectural changes) |
| [docs/surveyor-reference.md](docs/surveyor-reference.md) | Developers: surveyor API reference |
| [docs/architecture-recovery.md](docs/architecture-recovery.md) | Developers: architecture recovery design (consolidated 2026-09-03) |
| [docs/filesystem-and-database-surveying.md](docs/filesystem-and-database-surveying.md) | Developers: file-system and PostgreSQL surveying |
| [docs/README.md](docs/README.md) | Everyone: index of all documents under `docs/` |
| [docs/Backlog.md](docs/Backlog.md) | Anyone: what is being worked on and what is deliberately closed |
| [docs/archive/](docs/archive/README.md) | Historians: finished work, and the test used to decide it was finished |

---

## CLI reference

The post-reset clean-up script is run from this directory as
`uv run python scripts/clear_egeria_pointers_after_reset.py …` (dry run first; it prints the exact `to apply:` line).
A bare `python scripts/…` call fails with ModuleNotFoundError on the `catalogue_commit` import.

```bash
uv run resource-explorer --help

# Add resources
resource-explorer add-project https://github.com/org/repo
resource-explorer add-database --host localhost --port 5432 --name mydb

# Survey
resource-explorer survey <slug>          # full local survey
resource-explorer survey <slug> --publish  # survey + publish to Egeria

# Web + agents
resource-explorer web      # FastAPI web UI on :8810
resource-explorer serve    # A2A agent server
resource-explorer chat     # interactive CLI chat
```

---

## Tech stack

Python 3.12+, FastAPI, PostgreSQL/pgvector (registry + vector store, shared with Egeria Advisor), pyegeria, psycopg2, BeeAI, Ollama (default LLM), Tailwind CSS, Plotly.js.

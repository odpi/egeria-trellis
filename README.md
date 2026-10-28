# Egeria-Trellis

Egeria-Trellis is a `uv` workspace monorepo consolidating two sibling Egeria tools that were
previously developed as separate repositories:

- **[Resource Explorer](packages/resource-explorer/)** (`resource-explorer`) — discovers,
  surveys, and catalogs information resources (Git repositories, PostgreSQL databases,
  filesystems) using [Egeria](https://egeria-project.org/) as the catalog of record.
- **[Egeria Advisor](packages/egeria-advisor/)** (`egeria-advisor`) — a RAG-based assistant
  providing conversational help for Egeria and pyegeria, including literate governance
  plan generation (LGCI) and report-spec authoring.

Both apps remain independently deployable services — the monorepo is a source-organization
and dependency-resolution decision, not a runtime-topology one. They share one `uv` workspace
lockfile so common code can be extracted and consumed with zero version skew, without forcing
either app to be built, deployed, or run as part of the other.

## Why one repo

Both apps talk to Egeria, both increasingly share infrastructure (pgvector, the same LLM
backend, overlapping RAG corpora, perspective/question-driven routing), and — critically —
there is a single maintainer across Resource Explorer, Egeria Advisor, Egeria Workspaces, and
pyegeria. That combination made a shared workspace a much lower-friction way to keep common
patterns (vector store access, intent classification, admin tooling, feedback capture) in sync
than maintaining three near-duplicate implementations across separate repos.

## Repository layout

```
trellis/
├── QUICKSTART.md              # clone → running app; start here
├── docs/trellis-architecture.md   # how the two apps and six libraries fit together
├── docs/deploying-trellis-with-quickstart.md  # demo, Mac-dev and Linux-dev configurations against the Egeria QuickStart
├── pyproject.toml             # workspace root — declares members, holds shared tooling config
├── uv.lock                    # single shared lockfile for the whole workspace
├── .python-version            # pinned Python version for the workspace
├── Makefile                   # wraps the common `uv run --package ...` invocations
└── packages/
    ├── resource-explorer/     # app — own pyproject.toml, tests, CLI/web entry points
    ├── egeria-advisor/        # app — own pyproject.toml, tests, CLI/web entry points
    │
    ├── trellis-artifact-tree/ # containment tree over ingested artifacts — the structure retrieval walks
    ├── trellis-auth/          # JWT auth + Portal SSO token exchange
    ├── trellis-context/       # ContextSpec + deterministic packer — bounded context under a budget
    ├── trellis-microflow/     # resource-sharing primitives for microflow-style step execution
    ├── trellis-querycache/    # TTL + LRU query cache
    └── trellis-vectorstore/   # pgvector-backed vector store
```

The two apps are the products; the six `trellis-*` members are libraries they share. A library
member has no CLI, no web server and no Egeria connection — it is imported, never deployed.

Each package keeps its own `config`/`configdata` directory *inside* its own top-level package
(`resource_explorer/configdata/`, `advisor/configdata/`) rather than as a sibling directory —
this was a deliberate cleanup from each app's original repo layout, done when they were
imported here, so neither app depends on `Path(__file__)` escapes reaching outside its own
package boundary.

## Getting started

**→ [QUICKSTART.md](QUICKSTART.md) takes you from a clone to a running app in about fifteen
minutes.** The rest of this section is the reference version: what each step is doing and why,
for when the quickstart's defaults are not what you want.

**An Egeria platform is required.** Both apps require login and authenticate against Egeria, so
Egeria is the identity provider and a hard dependency — the earlier "runs without an Egeria
server" path was retired on 2026-09-04 (project owner's decision; see
[docs/runtime-architecture-plan.md](docs/runtime-architecture-plan.md) §4 and
[docs/trellis-auth-extraction.md](docs/trellis-auth-extraction.md) §7). Setting
`TRELLIS_ANONYMOUS_READ=true` allows unauthenticated reads on a development box only, and is not
a supported deployment mode.

```bash
git clone https://github.com/odpi/egeria-trellis.git
cd trellis
uv sync
```

`uv sync` resolves and installs dependencies for every workspace member into a single shared
`.venv` at the repo root.

### Configuration

Neither app's real config/secrets are committed (both are gitignored). Before running either
app against a live Egeria instance or LLM backend, copy the example files and fill them in:

```bash
cp packages/resource-explorer/.env.example packages/resource-explorer/.env
cp packages/egeria-advisor/.env.example packages/egeria-advisor/.env
cp packages/egeria-advisor/advisor/configdata/mcp_servers.json.example \
   packages/egeria-advisor/advisor/configdata/mcp_servers.json
```

### Databases

Both apps share one Postgres/pgvector instance (`egeria_advisor` database, default port `5442`).
Setup differs depending on whether you're working inside a full **egeria-workspaces**
checkout or standalone:

**With egeria-workspaces (recommended if you have it):** the shared Postgres container is
already running, managed by egeria-workspaces-fs's
`compose-configs/shared-infra/shared-infra.yaml` (container name `egeria-shared-postgres`).
Its init script (`docker-entrypoint-initdb.d/init_egeria.sql`) already creates the
`egeria_advisor` role/database and runs `CREATE EXTENSION IF NOT EXISTS vector` — but it does
**not** create Resource Explorer's named Postgres schema. Create it once, explicitly (it's
not reliably auto-created by the app itself — see note below):

```bash
docker exec egeria-shared-postgres psql -U egeria_advisor -d egeria_advisor -p 5442 \
  -c 'CREATE SCHEMA IF NOT EXISTS resource_explorer AUTHORIZATION egeria_advisor;'
```

**Without egeria-workspaces (standalone/from-scratch):** run your own Postgres+pgvector
container, then create the same schema against it:

```bash
docker run -p 5442:5432 -e POSTGRES_DB=egeria_advisor -e POSTGRES_USER=egeria_advisor \
  -e POSTGRES_PASSWORD=advisor pgvector/pgvector:pg17

docker exec <container_name_or_id> psql -U egeria_advisor -d egeria_advisor \
  -c 'CREATE SCHEMA IF NOT EXISTS resource_explorer AUTHORIZATION egeria_advisor;'
```

Table creation beyond that is automatic and needs no separate migration step, but the two apps
provision differently:

- **Egeria Advisor** has a fixed set of 9 vector collections plus its metrics/symbol-store
  tables, all unqualified in the `public` schema (no named-schema step needed for EA). Starting
  its web server provisions all of them (`PgVectorStore.provision_schema()` runs on FastAPI
  startup); the metrics tables also self-provision on first use from the CLI.
- **Resource Explorer**'s vector collections are per-project (`{project_slug}_{collection_type}`)
  inside the `resource_explorer` schema created above, so there's no fixed table set to
  pre-create — each table is created lazily the first time that project's data is inserted
  (`auto_provision_on_insert=True`). Its own registry tables (projects, stats, etc.)
  self-provision on first use the same way. The *schema itself* (the namespace, as opposed to
  the tables inside it) is a separate one-time step from table creation — `CREATE SCHEMA IF NOT
  EXISTS` runs as part of `PgVectorStore.connect()` too, but only the first time something
  actually calls it, so don't rely on it firing before you've triggered any real RE usage; run
  the explicit command above so the schema exists from the start.

### LLM backend (Ollama)

Both apps default to a local [Ollama](https://ollama.com) server for LLM inference.

```bash
# Install (macOS)
brew install ollama
# or download from https://ollama.com/download

# Start the server (skip if installed as a background service)
ollama serve &

# Pull the models each app needs
ollama pull llama3.1:8b          # Resource Explorer + Egeria Advisor RAG Q&A
ollama pull qwen2.5-coder:32b    # Egeria Advisor LGCI planning only
```

### Convenience commands

A root `Makefile` wraps the common `uv run --package ...` invocations below — run `make help`
for the full list. The two most useful:

```bash
make up        # preflight, ensure a shared JWT secret, then start both
make check     # preflight only — reports what is missing, starts nothing
make re-web    # Resource Explorer web UI → http://localhost:8810
make ea-web    # Egeria Advisor web UI    → http://localhost:8880
make dev       # both, concurrently, in one terminal — Ctrl-C stops both
```

`make up` wraps `make dev` with the two things it does not do: it makes sure a shared
`TRELLIS_JWT_SECRET` exists in both packages' `.env` (without one, each app derives a secret
from its hostname — no error, but sessions do not survive a restart or a container recreate),
and it reports up front when Egeria, Postgres or Ollama is unreachable. `./trellis-up --help`
covers the rest: `--re` / `--ea` to start one, `--container` for the compose overlay. Once the
environment is known-good, the targets below remain the direct route.

Anything not covered by a target (arbitrary CLI subcommands) still works via
`make re CMD="..."` / `make ea CMD="..."`, or the raw `uv run --package ...` form documented
below.

### Running Resource Explorer

```bash
uv run --package resource-explorer resource-explorer --help
uv run --package resource-explorer resource-explorer web        # → http://localhost:8810
```

### Running Egeria Advisor

```bash
uv run --package egeria-advisor egeria-advisor --help
uv run --package egeria-advisor egeria-advisor-web               # → http://localhost:8880
```

### Running tests

```bash
uv run --package resource-explorer pytest packages/resource-explorer/tests
uv run --package egeria-advisor --extra dev pytest packages/egeria-advisor/tests
# or: make test / make test-re / make test-ea
```

(Egeria Advisor's test config requires the `dev` extra for `pytest-cov`, which isn't installed
by a plain `uv sync`.)

## Notes for contributors

- **Independent deployability is a hard constraint.** Changes that couple Resource Explorer's
  and Egeria Advisor's runtime behavior together (as opposed to sharing a library both import)
  are out of scope for this workspace's design — see each package's own `CLAUDE.md` for its
  architecture.
- **Shared code lives in its own workspace member**, not duplicated between the two apps. This
  has happened six times now — `trellis-vectorstore` was the first extraction and is the worked
  example to follow; `trellis-querycache` and `trellis-auth` are the most recent. A new shared
  library is a `packages/<name>/` member with its own `pyproject.toml` and tests, added to
  `[tool.uv.sources]`, and imported by both apps. It gets no CLI, no web server and no Egeria
  client: the moment a library needs one of those, it is an app, not a library.
- **The workspace root's `pyproject.toml`** carries one deliberate dependency override —
  `numba>=0.60.0` — needed because resolving both members' dependencies together otherwise
  pulls an ancient `numba`/`llvmlite` (via `beeai-framework[rag]` → `unstructured`) that
  doesn't build on modern Python. See the comment there before removing it.

# Resource Explorer — Admin Guide

**Last revised:** 2026-08-21

This guide covers installation, configuration, Egeria integration, scheduled analyses, and troubleshooting.

---

## Requirements

| Component | Minimum | Notes |
|-----------|---------|-------|
| Python | 3.12 | 3.13 supported |
| uv | any recent | package manager |
| PostgreSQL/pgvector | pg17 | RE's own vector store + registry — required for RAG; shared with Egeria Advisor's `egeria_advisor` database in a Trellis checkout, or a standalone instance for a from-scratch environment |
| SQLite | 3.35+ | ships with Python; optional fallback for the registry only, via `REGISTRY_DATABASE_URL` |
| Ollama | any | default LLM backend; can use OpenAI/Anthropic instead |
| Egeria | 5.x | required for catalog/survey; optional for RAG-only use |
| PostgreSQL (target databases) | 12+ | only needed for surveying *external* databases via the database surveyor — unrelated to RE's own storage backend above |
| `ast-grep-cli` | ≥0.45.1 | **not** a system prerequisite — a `uv sync` dependency, same as any Python package. It's wheel-distributed with prebuilt binaries for macOS arm64/x86_64, manylinux aarch64/x86_64, and Windows, so `uv sync` provisions the binary and `uv.lock` pins its version; there's nothing to `brew install`. Backs Architecture Recovery's code-marker detection (see below). |

---

## Installation

```bash
git clone https://github.com/PDR-Associates/resource-explorer
cd resource-explorer
uv sync
uv sync --extra dev --extra phoenix   # dev tools + Arize Phoenix tracing

cp .env.example .env
# Edit .env — see Configuration section below
```

---

## Configuration

All settings are in `.env` (loaded via `resource_explorer/config.py` using Pydantic Settings).

### Required

```bash
GITHUB_TOKEN=ghp_...          # GitHub personal access token (repo scope)
```

### LLM backend (default: Ollama)

```bash
LLM_BACKEND=ollama            # ollama | openai | anthropic
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# If using OpenAI:
LLM_BACKEND=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# If using Anthropic:
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8
```

### pgvector / registry

```bash
# Defaults already point at the shared egeria_advisor Postgres instance
# (localhost:5442, database egeria_advisor, schema resource_explorer) — no
# .env entries needed in a Trellis checkout. Override for a from-scratch
# environment:
PGVECTOR_HOST=localhost
PGVECTOR_PORT=5442
PGVECTOR_DBNAME=egeria_advisor
PGVECTOR_USER=egeria_advisor
PGVECTOR_PASSWORD=advisor

# Registry defaults to the same Postgres instance/database, own schema. To
# fall back to local SQLite instead:
REGISTRY_DATABASE_URL=sqlite:///data/registry.db
```

### Egeria

```bash
EGERIA_PLATFORM_URL=https://localhost:9443
EGERIA_VIEW_SERVER=view-server
EGERIA_ENGINE_HOST=engine-host
EGERIA_USER_ID=garygeeke
EGERIA_USER_PASSWORD=secret
EGERIA_KAFKA_ENDPOINT=localhost:9092

# Default governance zones assigned to new assets when cataloging (JSON list)
EGERIA__DEFAULT_CATALOG_ZONES=["data-lake","analytics"]

# Default governance zones for survey-triggered asset creation
EGERIA__DEFAULT_SURVEY_ZONES=["survey-zone"]
```

Zone names can also be specified per-operation via the Egeria publish panel in the web UI (comma-separated text field), or via the `zone_names` field in the `POST /api/egeria/{slug}/publish` body.

**Egeria is a hard dependency as of 2026-09-04**, because it is also the identity provider — see
"Authentication" below. Beyond signing in, it is required for:
- `resource-explorer survey --publish` (writes to Egeria catalog)
- Egeria-native database survey
- RFA lifecycle management
- Reading Egeria annotation results back into the survey report

### Authentication

Login is required on every non-public path (`docs/runtime-architecture-plan.md` §4). Users sign in
with an **Egeria user id and password**; RE exchanges the password for an Egeria bearer token once
and issues its own session JWT carrying that token. No password is ever stored or signed into a
token.

Set these in `.env` (like every other RE setting) or as real exported process env vars — a real
env var still wins over `.env` either way. `RE_JWT_SECRET`/`RE_PORTAL_SECRET` each have a
`TRELLIS_*`-named alternative (`TRELLIS_JWT_SECRET`/`TRELLIS_PORTAL_SECRET`) for a compose overlay
that feeds in the app-agnostic name instead; the `RE_*` name wins if both are set.

```bash
# Required in any deployment. Without it (neither RE_JWT_SECRET nor TRELLIS_JWT_SECRET) RE
# derives a per-host secret and logs a warning — workable on a single-box checkout, but
# sessions do not survive a move to another machine.
RE_JWT_SECRET=<a long random string>

# Only if this deployment accepts Portal SSO. Must match the Portal's own shared secret.
RE_PORTAL_SECRET=<shared with the Egeria Portal>

# Optional. Default 8; the effective session length is whichever is shorter, and Egeria's
# own bearer tokens last one hour.
RE_JWT_TTL_HOURS=8
```

Policy knobs, read as `TRELLIS_<NAME>` first then `EXPLORER_<NAME>` (the app-specific one wins):

| Variable | Default | Effect |
|---|---|---|
| `EXPLORER_REQUIRE_LOGIN` | `true` | `false` turns the gate off entirely. **Not a supported deployment mode** — it exists so a test or a one-off experiment can opt out explicitly rather than by deleting the middleware |
| `EXPLORER_ANONYMOUS_READ` | `false` | Dev-box override: unauthenticated `GET`/`HEAD` is allowed, **writes still 401**. That asymmetry is the point — an anonymous reader cannot create anything, so nothing lands in Egeria without an identity behind it |
| `EXPLORER_PUBLIC_PATHS` | — | Comma-separated, **added** to the defaults, never replacing them (an operator adding an A2A path must not silently drop the login route) |
| `EXPLORER_EXPOSE_OPENAPI` | `false` | Adds `/docs` and `/openapi.json` to the allowlist |

**Sessions last as long as Egeria's token** — one hour by Egeria's default, and every token dies
when the platform restarts (the quickstart leaves `rsa.key-id` empty, so the signing key is random
per restart). Browsers re-log-in; the CLI caches a session (`resource-explorer login`) and says
`session expired at HH:MM` when it lapses.

### Governance zones

Everything RE publishes lands in a **draft zone** and is promoted on curate-accept:

```bash
# The zone unreviewed publishes land in. The worker creates it at startup if absent.
EXPLORER_DRAFT_ZONE=resource-explorer-draft

# Where curate-accept promotes an element. Default: egeria-runtime, which is what the
# quickstart deployment configures.
EXPLORER_PUBLISH_ZONES=egeria-runtime
```

### Observability (optional)

```bash
MLFLOW_TRACKING_URI=http://localhost:5025
PHOENIX_ENDPOINT=http://localhost:6006
```

---

## Starting external services

### pgvector (usually already running)

In a Trellis monorepo checkout, the shared instance is normally already up — managed by `egeria-workspaces-fs`'s `compose-configs/shared-infra/shared-infra.yaml`. Check with:

```bash
docker ps --filter name=egeria-shared-postgres
```

### Standalone pgvector (from-scratch environment, Docker)

```bash
docker run -d --name resource-explorer-pgvector -p 5442:5432 \
  -e POSTGRES_DB=egeria_advisor -e POSTGRES_USER=egeria_advisor -e POSTGRES_PASSWORD=advisor \
  pgvector/pgvector:pg17
```

### Ollama

```bash
ollama pull llama3.1:8b
ollama serve           # starts on :11434
```

### MLflow (optional)

```bash
mlflow server --port 5025
```

### Arize Phoenix (optional)

```bash
python -m phoenix.server.main    # starts on :6006
```

---

## Running the app

```bash
# Web UI (production-ready with uvicorn)
uv run resource-explorer web

# Development mode with auto-reload
uv run uvicorn resource_explorer.web.app:app --reload --port 8810
```

---

## Database storage

The SQLite registry is created automatically at first run:

```
Default location: ~/.resource-explorer/registry.db
Override: REGISTRY_DB_PATH=/path/to/registry.db
```

Back up this file to preserve all registered resources, survey history, activity log, and schedules.

---

## Adding resources

### Git repositories

```bash
uv run resource-explorer add-project https://github.com/org/repo

# Then index it (downloads + chunks + stores in pgvector)
uv run resource-explorer index my-repo
```

### Discovering repos via the web UI

Adding one repo at a time by URL doesn't answer "which repos should we even be looking at." **Scouting → Discover** (shown when no repo is selected) is a general GitHub search: keyword, min stars, language, license, pushed-after, org, topic. Archived repos and forks are excluded by default. Check the ones you want, pick a target group, and **Import Selected** — registration runs in a background thread (catalog-only, no RAG ingestion) and shows up in the Activity log as it completes.

Repeat searches are worth saving as a **discovery source** (⚙ Admin → Discovery Sources, or "💾 Save as new source" right on the search) so you don't retype the same filters. Two source types:

- **search** — the same structured filters as the ad-hoc form, saved under a name.
- **list** — a manually-curated set of `github_url`s. Needed for foundations that don't fit the "one org, one search" model — Eclipse spreads 450+ projects across hundreds of distinct GitHub orgs, and LF AI & Data curates a *member list* of projects living in unrelated orgs (Egeria itself is `odpi/egeria`, nothing to do with the `lfai` org's own governance repos). A `list` source is also how you'd register your own enterprise/internal repos the same way. There's no auto-fetching of an external structured list (CNCF's `landscape.yml`, LFX Insights' API, Eclipse's own project index) yet — paste the URLs in by hand.

If you're pointed at an Enterprise GitHub instance rather than public GitHub, set the base URL once via the inline "GitHub source: … [edit]" control on the Discover tab — it's a runtime override on top of `.env`'s `GITHUB_BASE_URL`, stored in the registry, no restart needed.

### Disposition and working set

After scouting a repo, **Scouting → Disposition** records whether it's worth pursuing: `undecided` (default) → `tracking` / `investigating` → `recommended`, `abandoned`, or `ignored`, with a full history of every decision, not just the latest. Fully reversible in any direction at any time — this isn't a linear workflow, so any state can move to any other. `recommended` is the positive terminal state — "decided *for* it," distinct from the group/survey/publish activity that only implies "yes" indirectly. `ignored` means passed-on-early; `abandoned` means you went further and then decided against it — kept distinct so the history reads honestly. `ignored`/`abandoned` hide the repo from the sidebar's default list (behind "Show hidden (N)") — reversible, not a delete; `recommended` stays visible, same as `tracking`/`investigating`.

Separately, each sidebar row has a working-set toggle (👁/🚫) — a personal "not in front of me right now" filter, independent of disposition. A repo someone else carried all the way to Curate can still be toggled out of your own daily view without changing its canonical disposition, and vice versa.

### PostgreSQL databases

```bash
uv run resource-explorer add-database \
  --host db.example.com \
  --port 5432 \
  --name production \
  --display-name "Production DB"
```

Or use the web UI: select **Databases** in the left sidebar → **+ Add Database**.

**Which account to register.** The *catalog identity* only — a read-only
account that can see the catalog and statistics with no table `SELECT`
(`docs/security-model.md` §2). On PostgreSQL that is `CONNECT`, `USAGE` on
the schemas of interest and, for the resilience checks, `pg_read_all_stats`.
Resource Explorer probes the account at registration and shows what it can
see; surveys that need to read rows ask for a *data identity* later, at the
point of use. Do not register an owner or superuser account for surveying.

**Where the password goes.** Encrypted at rest in the registry with a key
from `RE_DB_CREDENTIAL_KEY` (or `TRELLIS_DB_CREDENTIAL_KEY`); with neither
set, a per-host key is derived and a warning is logged — set one for any
deployment that might move machines. The same credential is written, in one
operation, to Resource Explorer's own secrets file for Egeria's engine host
(`EGERIA_SECRETS_STORE_LOCAL_PATH`, normally
`/deployments/secrets/resource-explorer.omsecrets` in the quickstart) so a
native Egeria survey can open the database; an edit to that file is picked
up by the next survey run without a restart. The collection name is fixed
at registration and must not be re-derived if a database is renamed.

```bash
# Encryption key for stored database passwords (any long random string)
RE_DB_CREDENTIAL_KEY=…
# Host-visible path of RE's own .omsecrets projection, read by Egeria's engine host
EGERIA_SECRETS_STORE_LOCAL_PATH=/path/to/deployments/secrets/resource-explorer.omsecrets
```

---

## Surveys

### Manual survey

```bash
# Local survey only
uv run resource-explorer survey my-repo

# Local survey + publish to Egeria
uv run resource-explorer survey my-repo --publish
```

The survey writes a row to `activity_log` and updates `project_file_type_counts`. Run it again to accumulate history for the diff banner and survey history chart.

### Scheduled surveys

In the web UI, open **Analyses** for a resource, scroll to the **Schedule** section at the bottom, and set an interval per analysis. The background scheduler (started automatically with `resource-explorer web`) executes due analyses every 15 minutes.

To check what schedules are configured:

```python
from resource_explorer.registry import ProjectRegistry
r = ProjectRegistry()
print(r.get_schedules('repo', 'my-repo'))
print(r.get_due_schedules())
```

### Architecture Recovery — two new steps, and a new resource cost

Architecture Recovery (Analysis intent, `configdata/analysis_catalog.yaml`'s `architecture_recovery` entry) runs as two independent survey steps rather than one:

| Step | Reads | What it does |
|---|---|---|
| `repo_arch_detect` | a zipball checkout | Package manifests, deployment artifacts (Dockerfile/compose), and `ast-grep` code markers — the deterministic half of the recovery. |
| `repo_arch_coupling` | a zipball checkout **and** a git clone | Import and co-change coupling — proposes the components manifests and deployment files structurally can't see (shared libraries, orchestrators). Needs real `git log` history, not just a checkout. |

Both are **`fast` tier**, measured at **5.3 seconds per repo** for the whole toolchain — that number is the reason the tier is `fast` rather than `minutes`, not an aspiration. Both write into the existing `project_analysis_findings`/`project_analysis_metrics` tables under `kind="architecture_recovery"` — no new table.

**`repo_arch_coupling` is the first architecture-recovery step that clones.** Every other repo-analysis step in RE works off a zipball download (`_acquire_zipball_root`) — a snapshot of files, no `.git`. Co-change coupling needs real commit history, which a zipball doesn't have, so `repo_arch_coupling` is the first step to pull in a second resource type: `git_clone_root`. That introduces network and disk cost this feature's other step, and every zipball-only step before it, never had.

It's cheaper than a normal clone, but not free. `git_clone_root` does a **treeless clone** — `git clone --filter=blob:none --no-checkout` — which fetches commit and tree metadata but defers file *contents* to on-demand fetches, and skips checking out a working tree entirely. `--no-checkout` matters specifically: without it, a treeless clone still checks out HEAD's working tree, which under `--filter=blob:none` means fetching every blob in HEAD from the remote during clone — silently paying for exactly what the treeless filter exists to avoid. See `clone_git_root()`'s docstring in `resource_explorer/github/client.py` for the full reasoning, including why nothing downstream of this provider should read file contents out of the clone root (that triggers a blob fetch per file and erodes the whole point of treeless).

Practically, this means: sizing a deployment that will run Architecture Recovery at any scale should budget for one treeless clone per repo per `repo_arch_coupling` run, on top of the zipball download every other step already pays for — cheap per repo, but a real, new, per-run cost that wasn't part of RE's resource profile before this feature.

---

## Egeria integration

### Catalog a repository

```bash
uv run resource-explorer survey my-repo --publish
```

This runs a local survey, then calls `EgeriaPublisher` to create the repository's own `Asset` element in Egeria with file type annotations.

### Survey a database in Egeria

In the web UI, open a database survey report and click **Catalog & Survey in Egeria →**. This:
1. Creates a `PostgreSQLServer` and `PostgreSQLDatabase` element in Egeria (from templates)
2. Triggers `initiate_postgres_database_survey()` — async Egeria-native survey
3. Simultaneously runs a local schema scan for immediate display

Results from the Egeria survey appear with a ☁ badge when the survey completes (poll with **Re-survey in Egeria** button).

**Which account Egeria uses.** Egeria's engine host chooses the connection
through its security connector, for *its own* user, and Resource Explorer
cannot pass one. Until two upstream defects in that selection are fixed
(`docs/security-model.md` §5), the engine host's user must be able to see
exactly one connection per database asset — the catalog identity. A native
Postgres survey reads only the catalog and statistics, so it needs nothing
more; its most-common-values figures come from `pg_stats` and carry the
same "estimate as of the last ANALYZE" caveat as Resource Explorer's own.

### RFA lifecycle

When a survey produces `RequestForAction` annotations, they appear in the **RFAs** panel. Assign and answer them there. Answers are currently stored locally in `resource_context`; Egeria write-back is planned.

### Question catalog (Scouting "Questions" tab)

The questions shown in Scouting's **Questions** checklist tab, and the corresponding `Question` elements published to Egeria, are both generated from `docs/dr-egeria/resource_questions.csv`. See [`docs/dr-egeria/resource_questions_guide.md`](dr-egeria/resource_questions_guide.md) for the full column reference, editing workflow, and regeneration commands.

---

## Observability

### MLflow

Visit `http://localhost:5025` to see experiment runs, query latencies, and retrieval scores. Each query creates an MLflow run automatically (non-blocking background thread).

### Arize Phoenix

Visit `http://localhost:6006` to see LLM traces, token usage, and agent spans. Instrumented via `openinference-instrumentation-beeai`.

---

## Upgrading

```bash
git pull
uv sync
uv run resource-explorer web   # schema migrations run automatically on startup
```

The registry schema uses `CREATE TABLE IF NOT EXISTS` guards — new tables are added transparently. No manual migration steps are needed for additive changes.

---

## Troubleshooting

### "Egeria connection failed"

Check `EGERIA_PLATFORM_URL` and that your Egeria chassis is running. Test connectivity:

```bash
curl -k https://localhost:9443/open-metadata/platform-services/users/garygeeke/server-platform/origin
```

### "No module named 'resource_explorer'"

Run commands via `uv run`:

```bash
uv run resource-explorer web     # correct
resource-explorer web            # wrong — uses system Python
```

### Survey history chart not showing

The diff banner and history chart require at least 2 survey runs. Run the survey twice:

```bash
uv run resource-explorer survey my-repo
# wait a moment, then run again
uv run resource-explorer survey my-repo
```

### pgvector connection refused

Verify the shared instance is running:

```bash
docker ps --filter name=egeria-shared-postgres
psql -h localhost -p 5442 -U egeria_advisor -d egeria_advisor -c '\dn'   # should list resource_explorer schema
```

If it's down and owned by `egeria-workspaces-fs`, start it via that project's own compose file — never a new one owned by Resource Explorer. For a standalone from-scratch instance, see "Standalone pgvector" above.

### Tests failing

```bash
uv run pytest tests/ -v --tb=short
```

Pre-existing external-service tests (pgvector, Egeria, GitHub) are skipped when the services are unavailable. All core tests should pass without external services.

---

## API reference

Interactive API docs are available at `http://localhost:8810/docs` when the web server is running.

Key endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/projects/` | List registered repos |
| `POST` | `/api/projects/{slug}/refresh` | Re-index a repo |
| `GET` | `/api/databases/` | List registered databases |
| `POST` | `/api/databases/{slug}/survey` | Run database survey |
| `GET` | `/api/egeria/{slug}/survey-report` | Get the deep survey report (local SQLite — no Egeria needed) |
| `POST` | `/api/egeria/{slug}/survey` | Run local survey only |
| `POST` | `/api/egeria/{slug}/publish` | Run survey + publish to Egeria (accepts `{zone_names:[…]}`) |
| `GET` | `/api/egeria/{slug}/diff` | Get file count delta vs previous survey |
| `GET` | `/api/databases/{slug}/diff` | Get schema delta vs previous survey |
| `GET` | `/api/projects/{slug}/scouting-overview` | Get the light Scouting-tier overview (description, stats, lifecycle badges) |
| `POST` | `/api/projects/{slug}/scouting-scan` | Run the coarse "Repo Coarse Scout" Survey Definition (repo_health + repo_language only) |
| `POST` | `/api/projects/{slug}/analyses/{analysis_id}/run` | Run only the named analysis's sub-surveyor step(s), not the whole survey |
| `GET` | `/api/projects/{slug}/analyses/{analysis_id}/results` | Get the named analysis's latest structured results |
| `GET` | `/api/projects/{slug}/analyses/{analysis_id}/trend` | Get the named analysis's run history, for the trend chart |
| `GET` | `/api/activity/` | List activity log entries (filterable by entity_type, intent, operation, status) |
| `GET` | `/api/activity/rfas` | List open RequestForAction annotations |
| `GET` | `/api/analyses/{resource_type}` | List available analyses (`?intent=…&perspective=…`) |
| `GET` | `/api/context/{entity_type}/{slug}` | Get resource context |
| `POST` | `/api/context/{entity_type}/{slug}` | Save resource context |
| `GET` | `/api/schedules/{entity_type}/{slug}` | Get analysis schedules |
| `POST` | `/api/schedules/{entity_type}/{slug}` | Save analysis schedule |
| `POST` | `/api/query/` | Submit a question (routed by intent — includes `survey_meta`) |

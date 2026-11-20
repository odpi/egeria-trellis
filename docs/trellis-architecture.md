# Trellis — Workspace Architecture

**Written:** 2026-08-30. There was no workspace-level architecture document before this; the
README covered layout and rationale, and each app documented itself, but nothing described how
the pieces relate.

This is the shape of the whole repo. For either app's internals, follow the links at the end.

- Getting it running: [QUICKSTART.md](../QUICKSTART.md)
- Why one repo, and how to add a member: [README.md](../README.md)
- Cross-session git conventions: [CLAUDE.md](../CLAUDE.md)

---

## What Trellis is

A `uv` workspace holding **three applications** and **ten shared libraries**, with one lockfile
and one `.venv`. (**Updated for the 6.3 Tech Preview, 2026-11-19.** When this document was written,
2026-08-30, there were two apps and six libraries; the paragraphs below describe today's shape.)

The apps are independently deployable services. The monorepo is a source-organisation and
dependency-resolution decision, **not a runtime-topology one** — no app is built,
deployed or run as part of another. That constraint is load-bearing: a change that couples
their runtime behaviour is out of scope for this workspace's design, however convenient.

| | What it does | Egeria |
|---|---|---|
| **Resource Explorer** (RE, port 8810) | Discovers, surveys and catalogs information resources — GitHub repos, PostgreSQL databases, filesystems — publishes them into Egeria and answers questions about them | Catalog of record; optional for RAG-only use |
| **Egeria Advisor** (EA, port 8880) | Conversational assistant for Egeria and pyegeria; governance-plan authoring; report-spec building | Required for action/report queries |
| **Trellis Studio** (port 8840; *emerging preview*) | The deliberate authoring surface for report specs, Reports and dashboard sheets over `pyegeria.authoring`. No LLM of its own; it embeds EA as a panel. Native only, no image yet | Required; every call runs as the signed-in user |

RE and EA meet in **one Postgres instance** and the **shared libraries**. Studio uses Egeria directly through
pyegeria and shares only `trellis-auth` and `trellis-ui`.

---

## The dependency graph

Declared in each package's own `pyproject.toml`; `[tool.uv.sources]` at the root resolves the `trellis-*` names to workspace members. (`trellis-analytics` is in the table as `trellis-analytics-re`, its distribution name.)

| App | Shared libraries it depends on |
|---|---|
| **resource-explorer** | `trellis-microflow`, `trellis-vectorstore`, `trellis-artifact-tree`, `trellis-context`, `trellis-querycache`, `trellis-auth`, `trellis-feedback`, `trellis-ui`, `trellis-accel`, plus the Analytic Library (`egeria-trellis-analytics`, with RE's readers in `trellis-analytics`) |
| **egeria-advisor** | `trellis-vectorstore`, `trellis-querycache`, `trellis-auth`, `trellis-ui`, `trellis-accel`, `trellis-feedback` |
| **studio** | `trellis-auth`, `trellis-ui`, `pyegeria` |

**The arrows only ever point down.** No library imports an app, and only one library imports
another (`trellis-context` → `trellis-artifact-tree`, for `Rung`). A library that needed an app
would mean the boundary was drawn in the wrong place.

### What makes something a library rather than an app

A `trellis-*` member has **no CLI, no web server, no Egeria client and no credentials**. It is
imported and never deployed. The moment one of those is needed, the thing is an app.

This is not bureaucratic: it is what makes the libraries testable as pure functions of their
input, and what stops a shared dependency quietly acquiring a runtime.

---

## The shared libraries

| Library | Owns | Used by |
|---|---|---|
| **trellis-vectorstore** | pgvector access — one `PgVectorStore` where there were two | RE, EA |
| **trellis-querycache** | TTL + LRU query cache, optional Redis backend (an unavailable Redis reads as a miss) | RE, EA |
| **trellis-auth** | JWT auth and Portal SSO token exchange | RE, EA, Studio |
| **trellis-feedback** | Answer ratings and product reports, with append-only triage events | RE, EA |
| **trellis-ui** | The browser pieces the apps share: the three looks, resizable columns, markdown, the run-envelope renderer | RE, EA, Studio |
| **trellis-accel** | One device choice (GPU or CPU) for every Trellis model, and a start-up report of what the machine offers | RE, EA |
| **trellis-artifact-tree** | The containment tree over an ingested artifact — the structure retrieval walks, and the rungs it can degrade to | RE (via `trellis-context`) |
| **trellis-context** | `ContextSpec` + a deterministic packer: bounded context under a budget, plus a manifest saying what it did | RE |
| **trellis-microflow** | Resource-sharing primitives — acquire a zipball or clone once, share it across the steps that need it | RE |
| **trellis-analytics** (distribution `trellis-analytics-re`) | RE's typed readers of its registry and Curate's node set, behind the Analytic Library's `readers` seam (the library itself lives in `odpi/egeria-trellis-analytics`) | RE |

The first extraction was `trellis-vectorstore`, and its README is the worked example: every
constructor parameter traces to a confirmed behavioural difference between the two original
implementations rather than a guess. That is the standard for an extraction — reproduce both
callers' behaviour exactly, then converge deliberately.

---

## Shared infrastructure

One PostgreSQL instance (`egeria_advisor`, port 5442) with the pgvector extension, and the
separation inside it is the thing to understand:

| | Egeria Advisor | Resource Explorer |
|---|---|---|
| Schema | `public`, unqualified | `resource_explorer`, named |
| Collections | A fixed set (the split documentation collections among them), provisioned at web-app startup; built by `clone_repos.py` and `ingest_collections.py --phase all` | Per-resource `{slug}_{type}`, created lazily on first insert |
| Registry | Its own tables in `public` | Its own tables in its schema |

The named schema is why RE's setup has one manual step the quickstart calls out: the shared
container's init script creates the database and the extension, not RE's schema.

Also shared: **Ollama** (LLM), and **MLflow** / **Phoenix** / **Prefect** /
**Kroki** where configured. Prefect is on by default for RE but degrades to in-process with no server; the rest are optional.

---

## Where the two apps genuinely converge

Worth naming, because it is where duplication keeps reappearing:

- **Vector access and caching** — solved, by extraction.
- **Question → evidence.** Both apps answer questions over a corpus with an LLM. RE compiles
  bounded context with a manifest (`trellis-context`); EA routes to specialist agents and RAG.
  These have not converged and there is no current plan that they should.
- **Intent and perspective vocabularies.** Both classify what a user is trying to do. The
  vocabularies are *not* shared, and one attempt to align them has already been reverted: RE
  retired its own four perspectives in favour of Egeria's twelve, and EA has its own roles.
  Aligning these is a real design question, not a refactor.

---

## Conventions that hold across the workspace

**One shared `.venv`.** There is no per-package virtualenv and nothing to activate. Every
command runs from the workspace root, usually via `uv run --package <name> ...` or a `make`
target.

**Absence is a first-class result.** Both apps must distinguish *measured zero* from *never
looked*. RE has explicit vocabularies for it (`result_status.py`, `step_outcome.py`); the
principle is workspace-wide, because a confident wrong answer is worse than an error — nothing
looks broken.

**Several sessions work in this checkout at once.** No whole-tree git operations; name the
paths. See [CLAUDE.md](../CLAUDE.md) — it is a real constraint here, not hygiene advice.

**Archived docs are kept, not deleted**, under a stated test: *nothing points at it*, measured
by reference count, rather than *the work is done*. Both apps have a `docs/archive/README.md`
explaining what moved and why.

---

## Where to go next

| For | Read |
|---|---|
| Resource Explorer's internals | [packages/resource-explorer/docs/Architecture.md](../packages/resource-explorer/docs/Architecture.md) |
| Working on Resource Explorer | [packages/resource-explorer/CLAUDE.md](../packages/resource-explorer/CLAUDE.md) |
| Egeria Advisor's internals | [packages/egeria-advisor/docs/design/SYSTEM_ARCHITECTURE.md](../packages/egeria-advisor/docs/design/SYSTEM_ARCHITECTURE.md) |
| Working on Egeria Advisor | [packages/egeria-advisor/CLAUDE.md](../packages/egeria-advisor/CLAUDE.md) |
| The context-compilation subsystem | [context-compilation-architecture.md](context-compilation-architecture.md), [design](context-compilation-design.md), [usage](context-compilation-usage.md) |
| How a library got extracted | [trellis-vectorstore-extraction.md](trellis-vectorstore-extraction.md), [trellis-auth-extraction.md](trellis-auth-extraction.md) |

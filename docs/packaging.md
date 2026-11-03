# Container images

Step 3 of `docs/runtime-architecture-plan.md`'s Sequencing: one Docker image per app, built for
`linux/amd64` and `linux/arm64`, used **unchanged** by both profiles from §1 of that plan:

- **dev profile** (Dev 1, Dev 2): the image is a build artifact only. Dev boxes keep running the
  apps native (`uv run` / `make re-web` / `make ea-web`) with reload; containerizing the app
  itself in the dev loop is friction the plan explicitly drops.
- **demo profile** (Demo 1, Demo 2, Dev 2-as-demo-box): the image IS the running container, one
  compose service per app in `egeria-workspaces`' `optional-associated-runtimes/trellis` overlay
  (that overlay is written in a sibling repo/session; it references these images by the exact
  names below).

This document is the reference for what the images contain, how their entrypoints map to the
plan's process roles, and what was measured building and running them.

## Image names and tags

| App | Image | Local tag |
|---|---|---|
| Resource Explorer | `trellis/resource-explorer` | `local` (built via `make image-re`) |
| Egeria Advisor | `trellis/egeria-advisor` | `local` (built via `make image-ea`) |

CI (`.github/workflows/images.yml`) pushes multi-arch builds to `ghcr.io/<owner>/trellis-resource-explorer`
and `ghcr.io/<owner>/trellis-egeria-advisor` on push to `main` and on `v*` tags; PRs build both
platforms without pushing, as a compile/build check.

## What's in the image

Both images are two-stage builds (`docker/Dockerfile.resource-explorer`,
`docker/Dockerfile.egeria-advisor`), Python 3.13 (`python:3.13-slim`, matching the workspace's
`requires-python = ">=3.12,<4.0"` and what CI already installs), built with `uv`:

- **Dependencies installed via `uv sync --frozen`** from the workspace's single `uv.lock`, with
  BuildKit cache mounts (`--mount=type=cache,target=/root/.cache/uv`) so a source-only change
  doesn't re-download the dependency set.
- **CPU-only PyTorch, not CUDA, not ONNX.** This was the one open question step 1's measurement
  was supposed to settle, and it did:
  `packages/egeria-advisor/docs/design/RUNTIME_AND_HARDWARE.md` §4a measured PyTorch's own CPU
  path beating ONNX Runtime by roughly 4-7x on the same model/hardware, with ONNX's RSS 10-30x
  larger — so there is no ONNX-vs-torch tradeoff worth shipping both for; PyTorch CPU wins
  outright and EA's `embeddings_onnx.py` path stays switched off and out of the image entirely.
  Separately, the default `uv.lock` resolution of `torch` on Linux pulls PyPI's CUDA-linked wheel
  plus about 15 `nvidia-*`/`triton` packages — several GB, all dead weight, because **LLM
  inference never runs inside this image**: it's Ollama over the network
  (`OLLAMA_URL`/`LLM__OLLAMA__BASE_URL` — see below), never in-process. The builder stage
  excludes `torch`/`torchvision`/`triton`/all `nvidia-*` packages from `uv sync` and reinstalls
  `torch` from PyTorch's own CPU wheel index (`https://download.pytorch.org/whl/cpu`) instead.
  See "Measured sizes" below for the delta this makes.
- **The embedding model is pre-downloaded at build time.** `all-MiniLM-L6-v2`
  (`sentence-transformers/all-MiniLM-L6-v2` for EA) is loaded once during the build into
  `HF_HOME=/app/.cache/huggingface`, baked into the image, and the runtime stage sets
  `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` — a demo box starts with zero network dependency
  for embeddings. RE's `embeddings.py` already tries `local_files_only=True` first; EA's
  `embeddings.py` has no such guard, which is exactly why baking the cache in (rather than
  relying on a lazy download) matters more there — without it, a cold container would make a live
  HuggingFace Hub call on first query.
- **Non-root user** (`trellis`, uid/gid 1000) owns the venv and app code in the runtime stage.
- **`curl`** only, in the runtime stage, for the `HEALTHCHECK`.
- Postgres/pgvector, Kroki, Egeria, and Ollama are **not** in the image — they're separate
  containers (Postgres/Kroki/Egeria, via `egeria-workspaces-fs`'s shared-infra and quickstart
  compose files) or a native service (Ollama), all reached by URL. See "Environment variables"
  below for the exact names and the container-friendly defaults baked in.

## Entrypoint: roles, not one fixed command

Both images share one `ENTRYPOINT` shape: `docker run ... <image> <role> [args...]`, dispatching
on the first argument to a process role from `docs/runtime-architecture-plan.md` §2. This is what
lets the demo profile compose overlay run "the same image" as several different services
(`web`, `worker`, ...) instead of needing one image per role.

### `trellis/resource-explorer` (`docker/entrypoint-resource-explorer.sh`)

| Role | Runs | Notes |
|---|---|---|
| `web` (default `CMD`) | `resource-explorer web --host $HOST --port $PORT` | Honours `WORKERS` and `EMBED_WORKER` — see below |
| `worker` | `resource-explorer worker` | Landed mid-task in a concurrent agent's work — see below |
| `cli` | `resource-explorer <args>` | Full Typer CLI, pass-through |
| `tui` | `resource-explorer tui` | Textual TUI |
| `a2a` | `resource-explorer serve --host $HOST --port $PORT` | AgentStack A2A server (`--all` for all 6 specialist agents) |

### `trellis/egeria-advisor` (`docker/entrypoint-egeria-advisor.sh`)

| Role | Runs | Notes |
|---|---|---|
| `web` (default `CMD`) | `egeria-advisor-web --host $HOST --port $PORT --no-reload` | Forces `--no-reload` unless `RELOAD=1` — the CLI's own default is reload-on, which is wrong for a container |
| `cli` | `egeria-advisor <args>` | Click CLI: one-shot query, REPL, agent REPL |
| `plans` | `egeria-advisor-plans <args>` | The `plans` command group |
| `worker`, `tui` | refused, with an explanation | EA has neither role today — see below |

### `resource-explorer worker` landed mid-task — the entrypoint still probes rather than assumes

`resource-explorer worker`, `--embed-worker`, and `--workers N` were the **target design** from
`docs/runtime-architecture-plan.md` §2 and `packages/resource-explorer/docs/process-model.md` —
the worker role owning the background loops (bootstrap monitor, Egeria resync, outbox drain,
orphaned-run reconciliation) that used to start unconditionally inside the FastAPI lifespan in
`web/app.py`. A sibling agent was adding these concurrently with this packaging work (step 2 of
the plan's Sequencing, running alongside step 3), and **they landed and were confirmed live in
this image during this same task** (2026-09-04) — `resource-explorer worker`,
`web --embed-worker/--no-embed-worker` (default **on**, so `make dev` stays one command), and
`web --workers N` all exist and were exercised directly (see "Exact commands that worked" below).

The entrypoint still **probes at runtime** (`--help` grep) rather than hardcoding that this CLI
shape exists, so a future image built against an older `resource-explorer` wheel degrades with a
clear message instead of a bare "no such option" traceback:

- `worker` role: checks `resource-explorer worker --help`. If present, execs it. If absent, exits
  1 with a message pointing at this doc and at running `web` with `EMBED_WORKER=1` instead.
- `web` role: checks `resource-explorer web --help` for `--workers`/`--embed-worker`. Since the
  CLI's own `--embed-worker` now **defaults on**, and this image's `EMBED_WORKER` defaults to
  **off** (matching the demo profile's separate-`worker`-replica shape, §1), the entrypoint
  explicitly passes `--no-embed-worker` unless `EMBED_WORKER=1` is set — otherwise every `web`
  container would silently also run the background loops. If `WORKERS>1` and the CLI has no
  `--workers` flag (an older image), it falls back to `uvicorn ... --workers N` directly, losing
  the CLI's own SIGUSR1/bounded-shutdown instrumentation for that process.

EA has no `worker` role (the plan brings the worker-role pattern to EA only after it's proven in
RE — Sequencing step 5) and no Textual TUI at all, so those two entrypoint arguments are accepted
and refused with an explanation rather than falling through to "command not found".

## Environment variables

Every one of these has a container-friendly default baked into the image; override per
deployment. Source: `packages/resource-explorer/resource_explorer/config.py` (RE, nested-env via
`env_nested_delimiter="__"`) and `packages/egeria-advisor/advisor/config.py` (EA, flat aliases).

| Purpose | RE var | EA var | Image default |
|---|---|---|---|
| Bind host/port | `HOST`/`PORT` (entrypoint-level, not app config) | same | `0.0.0.0` / `8810` (RE), `8880` (EA) |
| Postgres/pgvector host | `PGVECTOR_HOST` | `PGVECTOR_HOST` | `egeria-shared-postgres` (shared-infra's container/service name) |
| Postgres port | `PGVECTOR_PORT` | `PGVECTOR_PORT` | `5442` (both apps' own default, unchanged) |
| Registry/feedback/metrics DB (RE only) | `REGISTRY_DATABASE_URL`, `FEEDBACK_DATABASE_URL`, `METRICS_DATABASE_URL` | — | Full `postgresql://egeria_advisor:advisor@egeria-shared-postgres:5442/...` URLs — found live, 2026-09-04: `RegistryConfig`/`FeedbackConfig`/`ObservabilityConfig` each hardcode their own `postgresql://...@localhost:5442/...` default independently of `PGVECTOR_HOST`, so setting only `PGVECTOR_HOST` left `/health/ready` reporting `{"status":"error","database":"unreachable", ...localhost...}` from inside the container even with `PGVECTOR_HOST` correctly set. All three need their own override. |
| Kroki | `KROKI_URL` | *(EA has no diagram route)* | `http://egeria-shared-kroki:8000` — the **container-to-container** URL; RE's own doc default (`http://localhost:6002`) is the bare-host published-port form, wrong inside a container on `egeria_network` |
| Egeria platform | `EGERIA_PLATFORM_URL` | `EGERIA_PLATFORM_URL` | `https://egeria-main:9443` — `egeria-quickstart`'s compose service key (container name `quickstart-egeria-main`; both resolve on `egeria_network`) |
| Ollama | `LLM__OLLAMA__BASE_URL` (nested-delimiter form of `LLMConfig.ollama.base_url`) | `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` — Ollama is native (Metal on Dev 1, ROCm/native on Dev 2 per §1), never containerized in the dev profile, so the app container reaches it via the Docker Desktop host gateway. **Linux without Docker Desktop needs `--add-host=host.docker.internal:host-gateway`** (Docker 20.10+) or an explicit override to the host's real address — `host.docker.internal` is a Docker-Desktop-for-Mac/Windows convenience, not a Linux default. |
| Embedding cache | `HF_HOME`, `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | same | `/app/.cache/huggingface`, `1`, `1` — pre-populated at build time, see above |
| Web workers | `WORKERS` | — | `1` |
| Embedded worker loops | `EMBED_WORKER` | — | `0` (off — the CLI's own `--embed-worker` flag defaults **on**; the entrypoint explicitly passes `--no-embed-worker` to keep this image's default matching the demo profile's separate-`worker`-replica shape) |

Secrets (`GITHUB_TOKEN`, `EGERIA_USER_PASSWORD`, `FEEDBACK_ADMIN_TOKEN`, EA's JWT secrets, etc.)
have **no** image-level default and must be supplied at `docker run`/compose time — none are
baked into either Dockerfile.

## How dev and demo use the same image

The image never differs between profiles; the build is identical for both. What differs is who
runs it and how:

- **dev profile**: CI builds and (on `main`/tags) pushes the image, but no dev box runs it as a
  service — dev boxes run `uv run resource-explorer web`/`uv run egeria-advisor-web` natively,
  with `--reload`. The image exists there purely as a build artifact/compile check
  (`pull_request` trigger in `images.yml`) and as what the demo profile will eventually pull.
- **demo profile**: the `egeria-workspaces` `optional-associated-runtimes/trellis` overlay (a
  sibling repo/session's work) runs this exact image as one or more compose services — `web` at N
  replicas or N uvicorn workers, `worker` at one replica, per §1's demo-profile description —
  referencing `trellis/resource-explorer`/`trellis/egeria-advisor` by the names fixed above.

## Health checks

- **Resource Explorer**: `HEALTHCHECK` hits `/health/ready`, not the bare `/health`. RE's
  `/health` is a deliberate pure-liveness probe that returns 200 even with Postgres unreachable
  (see its docstring in `web/app.py` — that's correct for the frontend's own startup banner, but
  useless for a container orchestrator deciding whether the service is actually serving).
  `/health/ready` exercises the registry's DB connection and fails the same way real `/api/`
  routes do.
- **Egeria Advisor**: `HEALTHCHECK` hits `/health` — EA has no separate readiness route today
  (`advisor/web/app.py:321-323`, `{"status": "ok"}`, no DB touch). That's a gap this packaging
  work surfaced but didn't fix; flagged for EA's own backlog rather than invented here.
- Both `HEALTHCHECK`s only make sense for the `web` role. `worker`/`cli`/`tui`/`a2a` containers
  will report unhealthy under the same directive (no HTTP server to hit on that port) — harmless
  for `cli`/`tui` (they're not meant to run as long-lived services), but the demo profile's
  compose overlay for `worker`/`a2a` services should override or disable the inherited
  `HEALTHCHECK` rather than rely on this image's default, which was written for `web`.

## Build context and `.dockerignore`

Both Dockerfiles build from the **repo root**, not `packages/<app>/`, because `resource-explorer`
and `egeria-advisor` depend on the other workspace members (`trellis-microflow`,
`trellis-vectorstore`, `trellis-artifact-tree`, `trellis-context`, `trellis-querycache`,
`trellis-auth`) via `[tool.uv.sources]` path entries in the root `pyproject.toml`, and `uv sync`
needs the whole tree to resolve against the one shared `uv.lock`. The root `.dockerignore`
excludes `.venv`, `data/`, logs, caches, `*.db` (SQLite is retired per §3 of the plan — "the
SQLite fallback ... is retired" — so none belong in an image regardless), `.git`,
and `node_modules`/`htmlcov` for when those exist.

## Local build and run targets

Added to the end of the root `Makefile` (append-only, per this task's scope) as a `## Container
images` section:

```
make image-re          # build trellis/resource-explorer:local for the host arch
make image-ea          # build trellis/egeria-advisor:local for the host arch
make images             # both
make image-run-re      # run the web role on :8811 against the shared-infra network
```

## Measured sizes and build

See the table below for final image sizes (`docker images`), filled in from the local host-arch
build performed as part of this task, plus what fraction is PyTorch specifically and the delta
the CPU-only wheel swap made.

**Measured 2026-09-04, host arch (arm64, Dev 1/Mac, `docker buildx build ... --load`), after the
CPU-torch fix below:**

| Image | Final size | `.venv` size | torch+torchvision within `.venv` |
|---|---|---|---|
| `trellis/resource-explorer:local` | 5.25 GB | 3.3 GB | 642 MB (torch 635 MB + torchvision 7 MB) — about 19% of `.venv`, 12% of the final image |
| `trellis/egeria-advisor:local` | 4.46 GB | 2.8 GB | same 642 MB — about 23% of `.venv`, 14% of the final image |

**The CPU-only wheel swap was not optional — the naive version was 11.4 GB.** The first working
build of the RE image (before the fix below) came in at **11.4 GB**, of which `/app/.venv/lib/.../nvidia`
alone was 2.9 GB and `triton` 652 MB — both dead weight for an image that never runs inference
in-process. After the fix, neither package is present at all (`torch.version.cuda` reports `None`
in the running image) and the image is 5.25 GB, a 54% reduction. Given that reduction, a smaller
CPU-only index isn't a further lever worth pulling here — the CPU wheel index **is** the fix;
there's no smaller alternative index for the same library.

**Getting the swap to actually stick took three attempts, in order:**

1. `uv sync --frozen --no-dev --no-install-project --no-install-package torch --no-install-package
   torchvision --no-install-package triton --no-install-package nvidia-*` (17 flags), then `uv pip
   install torch` from the CPU index. **Failed at runtime**, not at build time: `uv sync` still
   installed the CUDA-linked `torchvision` and `triton` despite being told not to (confirmed via
   `du -sh site-packages/*` inside the built image — 2.9 GB of `nvidia`, 652 MB of `triton`, both
   present), and the resulting environment had a CPU `torch` next to a CUDA-linked `torchvision`,
   which crashed importing `sentence_transformers` → `transformers` → `torchvision.io` with
   `RuntimeError: operator torchvision::nms does not exist` — an ABI mismatch, not a missing
   module. `--no-install-package` under `--frozen` does not reliably exclude a package here.
2. Full `uv sync` (accept whatever it installs), then explicit `uv pip uninstall
   torch torchvision triton nvidia-*` (a real removal, not a resolver hint), then `uv pip install
   torch==2.13.0 torchvision==0.28.0` together from the CPU index (pinned to the exact versions
   `uv.lock` itself resolved, so they stay ABI-matched). **Fixed the ABI crash, but the nvidia/
   triton packages came back** — 2.9 GB and 652 MB, unchanged — because this swap ran *between*
   the two `uv sync` calls in the Dockerfile (one for the dependency layer, one after `COPY` for
   the actual package), and the second `uv sync --package resource-explorer` re-resolved against
   the frozen lock, saw the venv no longer matched it, and silently reinstalled the CUDA build to
   "fix" that.
3. **Working version:** run the uninstall/reinstall swap once, as the very last dependency step in
   the builder stage, after every `uv sync` call. This is what both Dockerfiles do now — see the
   comments at that `RUN` step in `docker/Dockerfile.resource-explorer` and
   `docker/Dockerfile.egeria-advisor`.

## Exact commands that worked

Build (host arch, arm64):
```
docker buildx build -f docker/Dockerfile.resource-explorer -t trellis/resource-explorer:local --load .
docker buildx build -f docker/Dockerfile.egeria-advisor -t trellis/egeria-advisor:local --load .
```

Run RE's `web` role against the shared-infra network (`egeria_network`, port 8811 — 8810/8880 are
held by this box's native dev servers) — the image's own baked-in defaults
(`PGVECTOR_HOST=egeria-shared-postgres`, `EGERIA_PLATFORM_URL=https://egeria-main:9443`,
`REGISTRY_DATABASE_URL`/`FEEDBACK_DATABASE_URL`/`METRICS_DATABASE_URL`, all pointed at
`egeria-shared-postgres`) are enough with no extra `-e` flags once shared-infra is up:
```
docker run -d --rm --name re-smoke -p 8811:8810 --network egeria_network trellis/resource-explorer:local web
curl http://localhost:8811/health/ready
# {"status":"ok","database":"ok"}
```

Verified all three RE roles live in that same run:
```
docker run --rm --network egeria_network trellis/resource-explorer:local cli --help
docker run -d --rm --name re-worker --network egeria_network trellis/resource-explorer:local worker
# logs: "worker role starting (embedded=False): 3 leader-elected loop(s) ..."
```

Run EA's `web` role (Ollama reached via `host.docker.internal`, hence the explicit host-gateway add
— Linux without Docker Desktop needs this too; macOS/Docker Desktop usually doesn't but it's
harmless either way):
```
docker run -d --rm --name ea-smoke -p 8881:8880 --network egeria_network \
  --add-host host.docker.internal:host-gateway trellis/egeria-advisor:local web
curl http://localhost:8881/health
# {"status":"ok"}
docker run --rm --network egeria_network trellis/egeria-advisor:local cli --help
docker run --rm --network egeria_network trellis/egeria-advisor:local plans --help
```

**Two EA-specific things surfaced live, not fixed here (out of scope for this task, flagged for
EA's own backlog):**
- `pgvector schema provisioning failed (queries needing missing collections will error): [Errno 13]
  Permission denied: 'data'` — EA tries to write to a relative `./data` path on startup, which
  doesn't exist (and wouldn't be writable by the non-root `trellis` user even if it did) in this
  image's `/app` working directory. Health still reports `ok` and the CLI/`plans` commands work
  regardless, so this didn't block the smoke test, but a demo-profile deployment should either
  create a writable `data/` under `/app` (a `VOLUME`, or a `mkdir` in the Dockerfile) or point EA's
  data-dir setting elsewhere before relying on pgvector-backed collections.
- `Failed to connect to MCP server dr-egeria/pyegeria: [Errno 2] No such file or directory:
  '/Users/dwolfson/localGit/egeria-python/.venv/bin/python'` — EA's MCP server config
  (`config/mcp_servers.json`) hardcodes a host-specific dev path to a sibling `egeria-python`
  checkout's venv interpreter, which obviously doesn't exist inside this image. EA's MCP tool
  surface (its own LLM-client-facing tools, not the A2A role) is therefore not usable from this
  image as configured today; it needs either a bundled/container-relative interpreter path or a
  config override mechanism, neither of which this packaging task adds.

**Update (2026-09-04): both fixed in EA.** Every relative `data/`-style write (embedding/analytics
cache, feedback logs, incremental-index state) now resolves through
`advisor.config.resolve_advisor_data_root()`/`AdvisorSettings.advisor_cache_dir`, whose default is
derived from the `ADVISOR_DATA_PATH` env var when it's set (`<path>/cache`, etc.) instead of a bare
`./data` relative to cwd; each write site creates its directory lazily via the new
`ensure_writable_dir()` helper, which raises a clear error naming both the path and the controlling
env var on failure instead of surfacing a bare `PermissionError`. The pyegeria MCP server's launch
command is now resolved (`advisor.mcp_config.resolve_pyegeria_mcp_command()`) in priority order —
an explicit `ADVISOR_PYEGERIA_MCP_COMMAND` override, else `sys.executable -m
pyegeria.core.mcp_server` using the *current* interpreter when `pyegeria` is importable in it (true
in this image and in the uv workspace), else the `config/mcp_servers.json` command/args unchanged —
logging which branch fired at startup. Verified live in a rebuilt `trellis/egeria-advisor:local`:
`docker run --rm --network egeria_network --add-host host.docker.internal:host-gateway -e
ADVISOR_DATA_PATH=/tmp/advisor-data trellis/egeria-advisor:local web` provisions all 9 pgvector
collections with no "Permission denied" anywhere in the log, `/health` returns `{"status":"ok"}`,
and the startup log shows `pyegeria MCP command resolved to the current interpreter ... ['/app/.venv/bin/python3',
'-m', 'pyegeria.core.mcp_server']`. That verification run also surfaced a separate, unrelated
packaging gap worth flagging here since it masked the fix on the first attempt: this image's build
context includes whatever real (gitignored, untracked) `packages/egeria-advisor/.env` happens to
exist on the *building* host, because nothing excludes it — no `.dockerignore` entry for `.env`.
A dev host's `.env` explicitly setting `ADVISOR_CACHE_DIR=./data/cache` (an old, real value, not a
placeholder) gets baked into the image and overrides the new default outright, since an explicit
`.env` value always wins over a field default. Worked around for this verification by bind-mounting
`/dev/null` over `/app/packages/egeria-advisor/.env`; the real fix (excluding `.env` from the image
build context, or shipping `.env.example` instead) is `docker/`/Dockerfile territory, out of scope
here. `dr-egeria` (a separate, legacy MCP server entry — see `packages/egeria-advisor/CLAUDE.md`
rule 24 on why pyegeria's report tools are the only MCP-wrapped surface today) still hardcodes the
host-only path and was intentionally left as-is; this task's env-var/current-interpreter resolution
only applies to `mcpServers.pyegeria`.

## What the plan left open, and what was decided here

- **Which health route to `HEALTHCHECK`.** The plan names `web` as a role with a health surface
  but doesn't say which of RE's two routes to use for a container probe (vs. the frontend's own
  use of `/health/ready` specifically). Decided above: `/health/ready` for RE, `/health` (the only
  one) for EA.
- **`--workers`/`--embed-worker`/`resource-explorer worker` not yet in the CLI when this task
  started.** Handled via runtime `--help` probing in the entrypoint rather than pinning this
  image to a fixed CLI version or blocking packaging on the other agent's work landing first —
  and it did land mid-task, confirmed live in the built image (see "`resource-explorer worker`
  landed mid-task" above).
- **CPU-only torch wheel index.** The plan and RUNTIME_AND_HARDWARE.md settle PyTorch-vs-ONNX but
  not CUDA-vs-CPU-wheel; decided here given the "inference is always Ollama over the network"
  constraint makes a GPU-enabled torch build pure waste in this image.
- **PR trigger shape for CI.** `resource-explorer.yml` deliberately has no `pull_request` trigger
  (it collided with `push` on the same branch, doubling runs). This task requires a build-only PR
  path, so `images.yml` splits triggers instead: `pull_request` (build, no push) and `push` scoped
  to `main`/tags only (build + push) — avoiding the same double-fire because `push` no longer
  matches feature branches.
- **`WORKERS`/`EMBED_WORKER` defaults.** Not specified in the plan; defaulted to `1`/`0` (i.e.
  single-process web, no embedded worker) so a plain `docker run ... web` on the demo profile
  behaves predictably rather than silently trying to also own the background loops — this meant
  overriding the CLI's own `--embed-worker` default (on) explicitly at the entrypoint level once
  that flag landed, not just leaving `EMBED_WORKER` unset.
- **RE's registry/feedback/metrics DB URLs needed their own container-friendly defaults, not just
  `PGVECTOR_HOST`.** Not called out anywhere in the plan or in RE's own docs as a
  container-specific gap; found live testing this image (see the env var table above and
  "Exact commands that worked" below for the actual failure and fix).
- **Two EA runtime gaps surfaced by testing but left unfixed, out of scope for this task**: a
  relative `./data` path EA tries to write to on startup (permission-denied inside this image's
  non-root `/app`), and EA's MCP server config hardcoding a host-only `egeria-python` venv path.
  Both are flagged for EA's own backlog in "Exact commands that worked" below rather than patched
  here, since neither blocks the `web`/`cli`/`plans` roles this task is packaging.

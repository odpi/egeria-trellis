#!/usr/bin/env bash
# Entrypoint for trellis/resource-explorer. Dispatches on the first arg to
# one of the process roles from docs/runtime-architecture-plan.md §2:
# web, worker, cli, tui, a2a, prefect-worker. Everything after the role is passed through
# to the underlying command untouched.
#
# `resource-explorer worker` and the `web` command's `--embed-worker`/
# `--workers` flags were being added by a sibling agent concurrently with
# this Dockerfile (both landed and were confirmed live in this image,
# 2026-09-04) — this script still probes for them via `--help` rather than
# assuming a fixed CLI shape, so it keeps working (and degrades with a
# clear message instead of a bare "no such option") if a future image is
# ever built against an older resource-explorer wheel.
#
# `resource-explorer web --embed-worker` now DEFAULTS ON in the CLI itself
# (so `make dev` stays one command) — this entrypoint's own EMBED_WORKER
# default is 0/off, matching the demo profile's shape (§1: one `worker`
# replica, N `web` replicas, not every web replica also running the
# background loops), so it explicitly passes --no-embed-worker unless
# EMBED_WORKER=1 is set. The loops are lock-gated per docs/process-model.md
# either way, so a mismatch here is inefficient, never unsafe.
set -euo pipefail

ROLE="${1:-web}"
[ $# -gt 0 ] && shift

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8810}"

_web_help() {
    resource-explorer web --help 2>&1 || true
}

run_web() {
    local help_text workers extra_args=()
    workers="${WORKERS:-1}"
    help_text="$(_web_help)"

    if echo "$help_text" | grep -q -- "--workers"; then
        extra_args+=(--workers "$workers")
    elif [ "$workers" != "1" ]; then
        # Fallback for an image built against an older resource-explorer
        # wheel with no --workers flag: run uvicorn directly so WORKERS is
        # still honoured, at the cost of the CLI's own SIGUSR1/bounded-
        # shutdown instrumentation for that process (uvicorn's multi-worker
        # mode manages its own worker subprocesses).
        echo "[entrypoint] resource-explorer web has no --workers flag in this image; running uvicorn directly with --workers $workers instead" >&2
        exec uvicorn resource_explorer.web.app:app --host "$HOST" --port "$PORT" --workers "$workers"
    fi

    if echo "$help_text" | grep -q -- "--embed-worker"; then
        if [ "${EMBED_WORKER:-0}" = "1" ] || [ "${EMBED_WORKER:-}" = "true" ]; then
            extra_args+=(--embed-worker)
        else
            extra_args+=(--no-embed-worker)
        fi
    elif [ "${EMBED_WORKER:-0}" = "1" ] || [ "${EMBED_WORKER:-}" = "true" ]; then
        echo "[entrypoint] EMBED_WORKER=1 requested but resource-explorer web has no --embed-worker flag in this image — starting web without it. Run a separate 'worker' role container instead." >&2
    fi

    exec resource-explorer web --host "$HOST" --port "$PORT" "${extra_args[@]}" "$@"
}

run_worker() {
    if resource-explorer worker --help >/dev/null 2>&1; then
        exec resource-explorer worker "$@"
    fi
    cat >&2 <<'EOF'
[entrypoint] 'resource-explorer worker' is not available in this image.

The worker role (background loops: bootstrap monitor, egeria resync,
outbox drain, orphaned-run reconciliation — see
docs/runtime-architecture-plan.md §2 and
packages/resource-explorer/docs/process-model.md) is expected to ship in
resource-explorer's CLI as of the same step this image was built for.
Rebuild the image against a current resource-explorer, or in the
meantime run the web role with EMBED_WORKER=1 to embed the worker loops
in the web process (dev-profile shape).
EOF
    exit 1
}

# Resource Explorer's Prefect worker (2026-11-13; DESIGN-PREFECT-ROLE-AND-RE-WORKER-2026-11-13 §1.6).
# Serves PREFECT_WORK_POOL (default resource-explorer-pool) and, deliberately, only this image's own queue:
#   - refuses to start without TRELLIS_RE_SHA (the image's build commit): RE matches a worker to its own code
#     by it, and a worker it cannot match would never be sent anything;
#   - refuses to start when a catalogued library step is missing from the image (the `uv sync --package`
#     hazard): it would accept a run and fail it;
#   - deploys its own deployment, re-survey-step-<sha12>, whose flows load from this image's source and land
#     on queue re-<sha12>, prunes per-commit deployments beyond the newest 3 (never one in use), then polls only
#     that queue as re-worker-<sha12>-<host>. Deploy and prune are Prefect writes, at every start. RE dispatches there only
#     when it runs the same commit (prefect_adapter._choose_deployment); otherwise it warns and runs locally.
# Needs the Prefect API (PREFECT_API_URL) and whatever a step needs (registry, Egeria) from the environment.
run_prefect_worker() {
    local sha="${TRELLIS_RE_SHA:-}"
    if [ -z "$sha" ]; then
        echo "[entrypoint] prefect-worker: TRELLIS_RE_SHA is not set, so Resource Explorer could never match this worker to its own code. Build the image with --build-arg TRELLIS_RE_SHA=<commit> (make image-re does)." >&2
        exit 1
    fi
    local sha12="${sha:0:12}"
    local pool="${PREFECT_WORK_POOL:-resource-explorer-pool}"
    local steps
    steps="$(python -c 'from resource_explorer.engine_host import library_steps_line; print(library_steps_line())')"
    echo "[entrypoint] prefect-worker ${sha12}: ${steps}" >&2
    if echo "$steps" | grep -q " missing:"; then
        echo "[entrypoint] prefect-worker: library steps are missing from this image; not starting a worker that would fail their runs." >&2
        exit 1
    fi
    resource-explorer prefect deploy --name "re-survey-step-${sha12}" --work-queue "re-${sha12}"
    # One deployment per commit: keep the newest 3, never one in use (Architect ruling 2026-11-13). A failed
    # prune is said and does not stop the worker.
    resource-explorer prefect prune-deployments --keep 3 || echo "[entrypoint] prefect-worker: prune-deployments failed; continuing" >&2
    exec prefect worker start --pool "$pool" --type process --work-queue "re-${sha12}" \
        --name "re-worker-${sha12}-$(hostname)" "$@"
}

run_cli() {
    exec resource-explorer "$@"
}

run_tui() {
    exec resource-explorer tui "$@"
}

run_a2a() {
    exec resource-explorer serve --host "$HOST" --port "${PORT:-8100}" "$@"
}

case "$ROLE" in
    web)    run_web "$@" ;;
    worker) run_worker "$@" ;;
    cli)    run_cli "$@" ;;
    tui)    run_tui "$@" ;;
    a2a)    run_a2a "$@" ;;
    prefect-worker) run_prefect_worker "$@" ;;
    *)
        echo "Unknown role '$ROLE'." >&2
        echo "Usage: docker run ... trellis/resource-explorer:local {web|worker|cli|tui|a2a|prefect-worker} [args...]" >&2
        exit 1
        ;;
esac

#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Resource Explorer's Prefect worker on a Mac host, as launchd runs it (org.odpi.trellis.re-prefect-worker.plist).
# Installed as ~/.re-gate/re-prefect-worker.sh; this copy is the template.
#
# Same code, same settings, same commit as the web app (read-back of 8f5af544, 2026-11-13):
#   - code: RE_TREE's venv (the tree the web app runs from), started from RE_TREE as the web app is;
#   - settings: RE_RUNTIME_ENV (the one env file the web app's launcher sources too), then the package .env
#     through `dotenv run`, exactly as the web app's launcher does;
#   - commit: TRELLIS_RE_SHA from `git rev-parse HEAD` of RE_TREE at start. A KeepAlive restart picks up a new
#     checkout, so restart this worker with every web-app deploy:
#       launchctl kickstart -k gui/$(id -u)/org.odpi.trellis.re-prefect-worker
#
# When RE_TREE's `resource-explorer prefect deploy` takes --work-queue (trellis re/prefect-worker-container),
# the worker advertises its commit like the container worker does: it deploys re-survey-step-<sha12> from
# RE_TREE, prunes per-commit deployments beyond the newest 3 (never one in use), and polls only queue
# re-<sha12> as re-worker-<sha12>-<host>, so RE dispatches to it only on the same commit. Deploy and prune are
# standing Prefect writes, at every start: the owner approves them by installing this agent. Before that, it serves the default deployment as
# re-host-worker-<host> and says, at WARNING, that commit parity is not checked.
#
# Never prints a setting's value: the parity line names settings only.
set -euo pipefail

: "${RE_TREE:?RE_TREE must name the checkout the web app runs from}"
: "${RE_DOTENV:?RE_DOTENV must name the package .env the web app passes to dotenv}"
RE_RUNTIME_ENV="${RE_RUNTIME_ENV:-$HOME/.re-gate/re-runtime-env.sh}"
POOL="${PREFECT_WORK_POOL:-resource-explorer-pool}"
VENV_BIN="$RE_TREE/.venv/bin"
HOST="$(hostname -s)"

[ -f "$RE_RUNTIME_ENV" ] || { echo "re-prefect-worker: $RE_RUNTIME_ENV is missing; not starting a worker on other settings than the web app" >&2; exit 1; }
# shellcheck source=/dev/null
. "$RE_RUNTIME_ENV"
cd "$RE_TREE"
export PATH="$VENV_BIN:$PATH"
export TRELLIS_RE_SHA="$(git -C "$RE_TREE" rev-parse HEAD)"
sha12="${TRELLIS_RE_SHA:0:12}"

run() { "$VENV_BIN/dotenv" -f "$RE_DOTENV" run -- "$@"; }

# The config parity line: which settings this worker runs with, by name. Compare it with the web app's
# (re-config-parity.sh <web pid> <worker pid> compares names and value fingerprints, never values).
names="$(run env | grep -E '^(EGERIA|PYEGERIA|PREFECT|RE|LLM_|REGISTRY|PGVECTOR|GITHUB|TRELLIS)_[A-Z0-9_]*=' | cut -d= -f1 | sort | tr '\n' ' ')"
echo "re-prefect-worker: commit ${sha12} · tree ${RE_TREE} · settings ($(echo "$names" | wc -w | tr -d ' ')): ${names}"
run python -c 'from resource_explorer.engine_host import library_steps_line; print("re-prefect-worker:", library_steps_line())'

if run resource-explorer prefect deploy --help 2>/dev/null | grep -q -- "--work-queue"; then
    run resource-explorer prefect deploy --name "re-survey-step-${sha12}" --work-queue "re-${sha12}"
    # One deployment per commit: keep the newest 3, never one in use (Architect ruling 2026-11-13, §D). Every
    # deleted name is printed; a failed prune is said and does not stop the worker.
    run resource-explorer prefect prune-deployments --keep 3 \
        || echo "re-prefect-worker: prune-deployments failed; continuing" >&2
    exec "$VENV_BIN/dotenv" -f "$RE_DOTENV" run -- prefect worker start --pool "$POOL" --type process \
        --work-queue "re-${sha12}" --name "re-worker-${sha12}-${HOST}"
fi
echo "re-prefect-worker: WARNING commit parity not checked: worker re-host-worker-${HOST} advertises no commit" \
     "(this tree's 'prefect deploy' has no --work-queue); it serves the default deployment" >&2
exec "$VENV_BIN/dotenv" -f "$RE_DOTENV" run -- prefect worker start --pool "$POOL" --type process \
    --name "re-host-worker-${HOST}"

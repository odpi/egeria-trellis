#!/usr/bin/env bash
# Idempotent local Prefect startup: server + work pool + deployment + worker.
#
# Nothing auto-starts these after a reboot (there's no launchd/systemd unit —
# deliberately not built here, see the note at the bottom) — this script is
# the "one command to bring Prefect back up" the CLAUDE.md docs point at.
# Safe to run repeatedly: each step checks whether it's already done before
# doing it, so `make prefect-up` after a reboot and `make prefect-up` while
# everything's already running both just work.
#
# State lives in .prefect-run/ (gitignored) — PID files so `prefect_down.sh`
# can find exactly what this script started, and log files so a background
# `prefect server`/`worker` doesn't dump into whichever terminal happened to
# start it once and then vanish when that terminal closes.
set -euo pipefail

cd "$(dirname "$0")/.."  # packages/resource-explorer/
STATE_DIR=".prefect-run"
mkdir -p "$STATE_DIR"

# `.env` is only loaded by Python's dotenv inside the app process — this
# bash script never sources it, so a bare `${PREFECT_WORK_POOL:-...}` always
# fell through to the default below, silently starting a worker on the wrong
# pool while `.env` configured a different one. Read `.env`'s value as a
# fallback when the shell doesn't already have one (an explicit
# `PREFECT_WORK_POOL=... make prefect-up` still wins).
_env_var() {
  [ -f ".env" ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2-
}

API_URL="${PREFECT_API_URL:-$(_env_var PREFECT_API_URL)}"
API_URL="${API_URL:-http://localhost:4200/api}"
WORK_POOL="${PREFECT_WORK_POOL:-$(_env_var PREFECT_WORK_POOL)}"
WORK_POOL="${WORK_POOL:-default-agent-pool}"
DEPLOYMENT_NAME="RE Survey Flow/re-survey-step-deployment"

_reachable() {
  curl -s -m 2 -o /dev/null -w '%{http_code}' "${API_URL}/health" 2>/dev/null | grep -q '^200$'
}

_pid_alive() {
  [ -f "$STATE_DIR/$1.pid" ] && kill -0 "$(cat "$STATE_DIR/$1.pid")" 2>/dev/null
}

# ── 1. Server ────────────────────────────────────────────────────────────
if _reachable; then
  echo "✓ Prefect server already reachable at ${API_URL}"
else
  echo "→ Starting Prefect server (log: $STATE_DIR/server.log)…"
  nohup uv run --package resource-explorer prefect server start \
    > "$STATE_DIR/server.log" 2>&1 &
  echo $! > "$STATE_DIR/server.pid"
  for _ in $(seq 1 30); do
    _reachable && break
    sleep 1
  done
  _reachable || { echo "✗ Prefect server did not become reachable — see $STATE_DIR/server.log"; exit 1; }
  echo "✓ Prefect server up at ${API_URL}"
fi

# ── 2. Work pool ─────────────────────────────────────────────────────────
if uv run --package resource-explorer prefect work-pool inspect "$WORK_POOL" >/dev/null 2>&1; then
  echo "✓ Work pool '$WORK_POOL' already exists"
else
  echo "→ Creating work pool '$WORK_POOL' (type: process)…"
  uv run --package resource-explorer prefect work-pool create "$WORK_POOL" --type process >/dev/null
  echo "✓ Work pool '$WORK_POOL' created"
fi

# ── 3. Deployment ────────────────────────────────────────────────────────
# Always re-run — cheap, and picks up any code changes to flows.py without
# needing a separate "did the flow change" check.
echo "→ Deploying '$DEPLOYMENT_NAME'…"
uv run --package resource-explorer resource-explorer prefect deploy >/dev/null
echo "✓ Deployed"

# ── 4. Worker ────────────────────────────────────────────────────────────
if _pid_alive worker; then
  echo "✓ Worker already running (pid $(cat "$STATE_DIR/worker.pid"))"
else
  echo "→ Starting worker for pool '$WORK_POOL' (log: $STATE_DIR/worker.log)…"
  nohup uv run --package resource-explorer resource-explorer prefect worker --pool "$WORK_POOL" \
    > "$STATE_DIR/worker.log" 2>&1 &
  echo $! > "$STATE_DIR/worker.pid"
  sleep 2
  _pid_alive worker || { echo "✗ Worker failed to start — see $STATE_DIR/worker.log"; exit 1; }
  echo "✓ Worker up (pid $(cat "$STATE_DIR/worker.pid"))"
fi

echo
echo "Prefect UI: ${PREFECT_UI_URL:-http://localhost:4200}"
echo "Stop with:  scripts/prefect_down.sh  (or: make prefect-down)"

# Not a launchd/systemd unit, and not a container: this is a bare-host
# dev-local convenience script, not RE's likely long-term answer. RE (like
# the rest of Trellis) is expected to be containerized eventually, and
# Prefect already publishes an official image (prefecthq/prefect) nobody's
# using yet — that's almost certainly where "start it after a reboot"
# should actually live (docker-compose, restart:always), not a bespoke
# pidfile script. This exists because that containerization hasn't happened
# yet, not as a design decision to keep bare-host processes long-term. Also
# not something RE depends on being always-up regardless of packaging —
# PREFECT_ENABLED=true degrades to running steps locally with no server at
# all (see prefect_adapter.py) — so this is about convenience, not liveness.

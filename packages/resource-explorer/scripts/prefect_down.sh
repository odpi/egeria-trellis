#!/usr/bin/env bash
# Stops exactly what scripts/prefect_up.sh started (tracked via .prefect-run/*.pid).
# Does nothing to a Prefect server/worker that script didn't start itself.
#
# The pid file holds the PID of `nohup env ... uv run ... prefect worker`, a wrapper: the `prefect worker` process it
# spawned is a CHILD (TR-68). Killing the wrapper alone left that child running while this printed "Stopped", so the
# whole tree is stopped here (children first), and "Stopped" is printed only once every process in it is gone.
# PREFECT_DOWN_WAIT is how many seconds to wait for them to exit after SIGTERM (default 10); one that is still alive
# after that is named, left alone, and makes this exit 1 (a second run tries again).
set -euo pipefail

cd "$(dirname "$0")/.."  # packages/resource-explorer/
STATE_DIR=".prefect-run"
WAIT="${PREFECT_DOWN_WAIT:-10}"

# $1 and every process below it, deepest first
_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    _tree "$child"
  done
  echo "$pid"
}

# running, and not a zombie (a stopped child nobody has reaped yet is gone, though kill -0 still answers for it)
_alive() {
  kill -0 "$1" 2>/dev/null || return 1
  local state
  state="$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')"
  [ -n "$state" ] && [ "${state#Z}" = "$state" ]
}

_stop() {
  local name="$1"
  local pidfile="$STATE_DIR/$name.pid"
  local pid=""
  [ -f "$pidfile" ] && pid="$(cat "$pidfile")"
  if [ -z "$pid" ] || ! _alive "$pid"; then
    echo "- $name not running (or not started by prefect_up.sh)"
    rm -f "$pidfile"
    return 0
  fi
  local pids
  pids="$(_tree "$pid")"
  local p
  for p in $pids; do kill "$p" 2>/dev/null || true; done
  local waited=0 left=""
  while [ "$waited" -lt "$WAIT" ]; do
    left=""
    for p in $pids; do _alive "$p" && left="$left $p"; done
    [ -z "$left" ] && break
    sleep 1
    waited=$((waited + 1))
  done
  left=""
  for p in $pids; do _alive "$p" && left="$left $p"; done
  if [ -z "$left" ]; then
    echo "✓ Stopped $name (pid $pid$( [ "$(echo $pids | wc -w)" -gt 1 ] && echo " and $(( $(echo $pids | wc -w) - 1 )) child process(es)" ))"
    rm -f "$pidfile"
    return 0
  fi
  echo "✗ $name did not stop within ${WAIT}s: still running (pid$left). Stop it by hand (kill -9$left), then re-run."
  return 1
}

failed=0
_stop worker || failed=1
_stop server || failed=1
exit "$failed"

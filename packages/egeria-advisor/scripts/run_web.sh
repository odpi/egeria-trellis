#!/bin/bash
# Launch the Egeria Advisor web UI: ONE process by default.
#
#   scripts/run_web.sh            # --single (default): one process, plain HTTP on $ADVISOR_HTTP_PORT (8880)
#   scripts/run_web.sh --https    # one process, HTTPS on $ADVISOR_HTTPS_PORT (8881), no HTTP
#   scripts/run_web.sh --both     # the old two-process mode: HTTP and HTTPS (see the warning below)
#
# For HTTP and HTTPS together, prefer --single behind a TLS proxy (Caddy, nginx, Apache) that
# terminates HTTPS and forwards to the one HTTP process.
#
# Why one process: uvicorn serves one scheme per process, and two processes are two separate
# copies of the app. They do NOT share the in-memory job table (_jobs, so a job started over
# HTTPS is unknown over HTTP), they each run the startup sync, and they each warm up the MCP
# connection. --both keeps that mode for anyone who relied on it, and says so when it starts.
#
# Both modes go through `egeria-advisor-web`, so ADVISOR_LOG_LEVEL (default INFO) and the
# reload default (off) apply here as everywhere else. HTTPS needs ADVISOR_SSL_CERTFILE and
# ADVISOR_SSL_KEYFILE in .env, pointing at existing files.

set -euo pipefail

MODE=single
for arg in "$@"; do
    case "$arg" in
        --single) MODE=single ;;
        --https)  MODE=https ;;
        --both)   MODE=both ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "run_web.sh: unknown option $arg (use --single, --https or --both)" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
elif [ -f ../../.venv/bin/activate ]; then
    # the trellis uv workspace's venv
    source ../../.venv/bin/activate
fi
if ! command -v egeria-advisor-web >/dev/null 2>&1; then
    echo "run_web.sh: egeria-advisor-web not found; run 'uv sync' at the trellis root first" >&2
    exit 1
fi

# Load .env into this shell (same values advisor.config.settings would see)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

HOST="${ADVISOR_BIND_HOST:-0.0.0.0}"
HTTP_PORT="${ADVISOR_HTTP_PORT:-8880}"
HTTPS_PORT="${ADVISOR_HTTPS_PORT:-8881}"

have_tls() {
    [ -n "${ADVISOR_SSL_CERTFILE:-}" ] && [ -n "${ADVISOR_SSL_KEYFILE:-}" ] \
        && [ -f "${ADVISOR_SSL_CERTFILE}" ] && [ -f "${ADVISOR_SSL_KEYFILE}" ]
}

case "$MODE" in
    single)
        if have_tls; then
            echo "HTTPS → not started (--single). Put a TLS proxy in front of this port, or use --https / --both." >&2
        fi
        echo "HTTP  → http://${HOST}:${HTTP_PORT} (one process)"
        exec egeria-advisor-web --host "$HOST" --port "$HTTP_PORT"
        ;;
    https)
        if ! have_tls; then
            echo "run_web.sh --https: set ADVISOR_SSL_CERTFILE and ADVISOR_SSL_KEYFILE in .env to existing files" >&2
            exit 1
        fi
        echo "HTTPS → https://${HOST}:${HTTPS_PORT} (one process)"
        exec egeria-advisor-web --host "$HOST" --port "$HTTPS_PORT" \
            --ssl-certfile "$ADVISOR_SSL_CERTFILE" --ssl-keyfile "$ADVISOR_SSL_KEYFILE"
        ;;
    both)
        if ! have_tls; then
            echo "run_web.sh --both: set ADVISOR_SSL_CERTFILE and ADVISOR_SSL_KEYFILE in .env to existing files" >&2
            exit 1
        fi
        echo "WARNING: --both runs two separate Advisor processes. They do not share running jobs," >&2
        echo "         and each runs its own startup sync and MCP warm-up. Prefer --single behind a TLS proxy." >&2
        PIDS=()
        cleanup() {
            for pid in "${PIDS[@]:-}"; do
                kill "$pid" 2>/dev/null || true
            done
        }
        trap cleanup EXIT INT TERM
        # $! rather than ${PIDS[-1]}: macOS's bash 3.2 has no negative subscripts.
        egeria-advisor-web --host "$HOST" --port "$HTTP_PORT" &
        PIDS+=("$!")
        echo "HTTP  → http://${HOST}:${HTTP_PORT} (pid $!)"
        egeria-advisor-web --host "$HOST" --port "$HTTPS_PORT" \
            --ssl-certfile "$ADVISOR_SSL_CERTFILE" --ssl-keyfile "$ADVISOR_SSL_KEYFILE" &
        PIDS+=("$!")
        echo "HTTPS → https://${HOST}:${HTTPS_PORT} (pid $!)"
        wait
        ;;
esac

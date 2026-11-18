#!/bin/bash
# Trellis Studio web UI. Port 8840 on laz (default), 7840 for freshstart: scripts/start-studio.sh 7840
# Pattern of ~/.re-gate/start-ea-8880.sh: the settings come from a .env (loaded, never printed), values already
# exported win (--no-override), and the launching shell's Egeria identity is cleared: Studio has no service
# account, and a stray EGERIA_USER must never look like one.
#   STUDIO_ENV_FILE   the .env to load (default: packages/studio/.env)
#   STUDIO_TREE       the checkout to run from (default: this script's own)
PORT="${1:-8840}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TREE="${STUDIO_TREE:-$(cd "$HERE/../.." && pwd)}"
ENV_FILE="${STUDIO_ENV_FILE:-$HERE/.env}"
cd "$TREE/packages/studio" || exit 1
export EGERIA_PLATFORM_URL="${EGERIA_PLATFORM_URL:-https://localhost:9443}"
export EGERIA_VIEW_SERVER="${EGERIA_VIEW_SERVER:-qs-view-server}"
export EGERIA_ADVISOR_URL="${EGERIA_ADVISOR_URL:-http://localhost:8880/}"
export STUDIO_PUBLIC_URL="${STUDIO_PUBLIC_URL:-http://localhost:$PORT}"
unset EGERIA_USER EGERIA_PASSWORD EGERIA_USER_PASSWORD
if [ -f "$ENV_FILE" ]; then
  exec "$TREE/.venv/bin/dotenv" -f "$ENV_FILE" run --no-override -- \
    "$TREE/.venv/bin/studio" web --host 127.0.0.1 --port "$PORT"
fi
exec "$TREE/.venv/bin/studio" web --host 127.0.0.1 --port "$PORT"

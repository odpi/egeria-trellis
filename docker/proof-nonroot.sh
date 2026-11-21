#!/usr/bin/env bash
# TR-112c proof: the Resource Explorer and Egeria Advisor images run as their default user (trellis, 1000:1000, what
# compose uses) AND as an arbitrary UID with GID 0 (what OpenShift assigns), become healthy, and can write every
# written path. Resource Explorer also runs `schema-apply` and then starts in RE_SCHEMA_MODE=external (TR-112b).
# usage: docker/proof-nonroot.sh [re-image] [ea-image]       (needs docker; run through `sg docker -c` if needed)
# Needs a throwaway Postgres that is already running as container $PG_CONTAINER (default tr112b-pg, database re_test,
# user postgres, password pw: test values). Nothing here touches a shared registry.
set -uo pipefail
RE_IMG=${1:-trellis/resource-explorer:tr112c}; EA_IMG=${2:-trellis/egeria-advisor:tr112c}
PG=${PG_CONTAINER:-tr112b-pg}; NET=tr112c-trellis-net; ARB=1000680000
fail=0
ok()  { echo "PASS: $*"; }
bad() { echo "FAIL: $*"; fail=1; }
docker network inspect $NET >/dev/null 2>&1 || docker network create $NET >/dev/null
docker network connect $NET "$PG" >/dev/null 2>&1 || true
trap 'docker rm -f tr112c-app >/dev/null 2>&1 || true' EXIT
ENVS=(-e PGVECTOR_HOST="$PG" -e PGVECTOR_PORT=5432 -e PGVECTOR_DBNAME=re_test -e PGVECTOR_USER=postgres -e PGVECTOR_PASSWORD=pw
      -e EGERIA_PLATFORM_URL=https://127.0.0.1:1 -e TRELLIS_AUTH_SECRET=test-secret-not-real -e JWT_SECRET=test-secret-not-real)

check() {   # image label health-path write-paths... -- docker run args
  local img=$1 label=$2 hp=$3 paths=$4; shift 4
  docker rm -f tr112c-app >/dev/null 2>&1 || true
  docker run -d --name tr112c-app --network $NET "${ENVS[@]}" "$@" "$img" web >/dev/null
  local code=
  for _ in $(seq 1 90); do
    code=$(docker exec tr112c-app sh -c "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:\${PORT}$hp" 2>/dev/null || true)
    [ "$code" = 200 ] && break
    docker ps -q -f name=tr112c-app | grep -q . || break
    sleep 2
  done
  [ "$code" = 200 ] && ok "$label: GET $hp answers 200" || { bad "$label: GET $hp gave '${code:-nothing}'"; docker logs tr112c-app 2>&1 | tail -5; }
  echo "      id: $(docker exec tr112c-app id)"
  for p in $paths; do
    docker exec tr112c-app sh -c "f=\$(mktemp -p $p) && echo x > \$f && rm -f \$f" >/dev/null 2>&1 && ok "$label: $p writable" || bad "$label: $p not writable"
  done
  docker exec tr112c-app sh -c 'f=$(mktemp -p "$HOME") && rm -f $f' >/dev/null 2>&1 && ok "$label: HOME ($(docker exec tr112c-app sh -c 'echo $HOME')) writable" || bad "$label: HOME not writable"
  docker rm -f tr112c-app >/dev/null
}

for user in "" "--user $ARB:0"; do
  who=${user:-default user}
  # RE: schema-apply role, then web in external mode
  docker run --rm --network $NET "${ENVS[@]}" $user "$RE_IMG" schema-apply 2>&1 | grep -E "^schema ready" | sed "s/^/      RE $who: /"
  docker run --rm --network $NET "${ENVS[@]}" $user "$RE_IMG" schema-apply 2>&1 | grep -qE "^schema ready" \
    && ok "RE $who: schema-apply exits 0 (twice)" || bad "RE $who: schema-apply failed"
  check "$RE_IMG" "RE $who" /health/ready "/app/data /tmp /app/.cache/huggingface" $user -e RE_SCHEMA_MODE=external
  check "$EA_IMG" "EA $who" /health "/app/data /tmp /app/.cache/huggingface" $user
done

for i in "$RE_IMG" "$EA_IMG"; do
  for p in $(docker image inspect "$i" --format '{{range $k,$v := .Config.ExposedPorts}}{{$k}} {{end}}' | sed 's#/tcp##g'); do
    [ "$p" -ge 1024 ] && ok "$i exposes $p" || bad "$i exposes privileged port $p"
  done
  u=$(docker run --rm --entrypoint id "$i" -u); [ "$u" = 1000 ] && ok "$i default uid is still 1000 (compose unchanged)" || bad "$i default uid is $u"
done
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }

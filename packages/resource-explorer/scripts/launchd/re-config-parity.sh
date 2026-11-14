#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Do two processes run with the same Resource Explorer settings? Compares the EGERIA/PYEGERIA/PREFECT/RE/LLM/
# REGISTRY/PGVECTOR/GITHUB/TRELLIS variables of two running processes by NAME and by a 12-hex fingerprint of
# each value. Never prints a value. Read only.
#
#   re-config-parity.sh <web-app pid> <worker pid>
#
# On laz: the web app is the python child of `dotenv ... run -- resource-explorer web --port 8813`, the worker
# the python child of `dotenv ... run -- prefect worker start` (pgrep -fl to find both). Exit 0 when the two
# carry the same names with the same values, apart from the names listed in RE_PARITY_IGNORE (space separated;
# default: the ones that are meant to differ between a web app and a worker).
# PREFECT_WORK_POOL is ignored here, but it must equal the worker's --pool: Resource Explorer's own pool check
# (bootstrap.check_prefect_pool_workers, through prefect_adapter.resolve_dispatch) names any of its workers that is
# live on another pool than the one it dispatches to, in the status, the log and the banner.
# Limitation: `ps eww` splits on spaces, so a value containing a space is fingerprinted by its first word.
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <pid-a> <pid-b>" >&2; exit 2; }
IGNORE=" ${RE_PARITY_IGNORE:-PREFECT_WORK_POOL PREFECT_SERVER_EPHEMERAL_ENABLED TRELLIS_RE_SHA} "

prints() {
    ps eww -o command= -p "$1" | tr ' ' '\n' \
        | grep -E '^(EGERIA|PYEGERIA|PREFECT|RE|LLM_|REGISTRY|PGVECTOR|GITHUB|TRELLIS)_[A-Z0-9_]*=' \
        | while IFS= read -r kv; do
              printf '%s %s\n' "${kv%%=*}" "$(printf '%s' "${kv#*=}" | shasum -a 256 | cut -c1-12)"
          done | sort -u || true   # no match is an answer ("no settings read"), not a silent exit
}

a="$(prints "$1")"; b="$(prints "$2")"
[ -n "$a" ] || { echo "no settings read from pid $1 (not running, or not yours)" >&2; exit 2; }
[ -n "$b" ] || { echo "no settings read from pid $2 (not running, or not yours)" >&2; exit 2; }
names_a="$(cut -d' ' -f1 <<<"$a" | sort -u)"; names_b="$(cut -d' ' -f1 <<<"$b" | sort -u)"
only_a="$(comm -23 <(echo "$names_a") <(echo "$names_b"))"
only_b="$(comm -13 <(echo "$names_a") <(echo "$names_b"))"
differ="$(join <(echo "$a") <(echo "$b") | awk '$2 != $3 {print $1}')"
same="$(join <(echo "$a") <(echo "$b") | awk '$2 == $3' | wc -l | tr -d ' ')"

bad=0
report() {
    local label="$1" list="$2" kept=""
    for n in $list; do [[ "$IGNORE" == *" $n "* ]] || kept="$kept $n"; done
    [ -n "$kept" ] && { echo "$label:$kept"; bad=1; }
    return 0
}
echo "same name and value: $same"
report "only in pid $1" "$only_a"
report "only in pid $2" "$only_b"
report "value differs" "$differ"
[ "$bad" -eq 0 ] && echo "parity: OK (ignored: ${IGNORE# })" || echo "parity: DIFFERENT"
exit "$bad"

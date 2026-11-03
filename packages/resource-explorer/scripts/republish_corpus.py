"""Republish RE's corpus to Egeria, one repo at a time, resumably.

Written after 2026-09-02, when an unguarded version of this stalled the Egeria
platform four repos in (5.48 GiB of a 6 GiB cap, 356% CPU) and recorded the
repo it half-published as a success. Both of those are addressed here, and
neither was a subtle failure — they were simply not looked for.

  python scripts/republish_corpus.py --dry-run
  python scripts/republish_corpus.py --limit 5
  python scripts/republish_corpus.py            # everything not already done

Progress is appended to the state file as each repo finishes, so a kill leaves
a usable record and a rerun skips what succeeded.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import subprocess
import sys
import time

from resource_explorer.registry import ProjectRegistry                    # noqa: E402
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher  # noqa: E402
from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator  # noqa: E402

STATE = pathlib.Path("/tmp/re_republish_state.jsonl")

#: Stop when the platform container passes this share of its memory limit.
#: It stalled at 0.91 and idles near 0.63 with no load at all, so the usable
#: band is narrow and the guard has to fire well inside it. This is a headroom
#: check, not a health check: by the time the container reports unhealthy it
#: has already stopped serving requests.
MEM_CEILING = 0.80
CONTAINER = "quickstart-egeria-main"


def container_mem_fraction(name: str = CONTAINER) -> float | None:
    """Used/limit for the platform container, or None if it cannot be read.

    None means "could not measure" and is NOT treated as healthy — the caller
    stops. A guard that passes when it cannot see is not a guard.
    """
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0 or "/" not in out.stdout:
            return None
        used, limit = (p.strip() for p in out.stdout.strip().split("/", 1))
        return _to_bytes(used) / _to_bytes(limit)
    except Exception:
        return None


def _to_bytes(v: str) -> float:
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    for suffix, mult in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if v.endswith(suffix):
            return float(v[: -len(suffix)]) * mult
    return float(v)


def publish_one(registry, slug: str) -> dict:
    """Publish one repo and report what ACTUALLY landed.

    `publish()` returning an asset GUID means an asset exists. It does not mean
    the annotations were written: on 2026-09-02 a repo with 57 of 125
    annotations failed was recorded as ok because a GUID came back. Success
    here is the annotation rows reaching 'done'.
    """
    rec: dict = {"slug": slug}
    t0 = time.time()
    try:
        result = SurveyOrchestrator(registry=registry).run(slug)
        rec["produced"] = len(result.annotations)
        rec["asset_guid"] = EgeriaPublisher(registry=registry).publish(result)
    except Exception as exc:
        rec.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:300],
                   seconds=round(time.time() - t0, 1))
        return rec

    where = ("FROM egeria_outbox WHERE entity_slug=? AND element_kind='annotation' "
             "AND qualified_name !~ '::[0-9]+$'")
    with registry._conn() as conn:
        one = lambda sql, p=(slug,): conn.execute(sql, p).fetchone()["n"]  # noqa: E731
        rec["enqueued"] = one(f"SELECT COUNT(*) n {where}")
        rec["done"] = one(f"SELECT COUNT(*) n {where} AND status='done'")
        rec["unfinished"] = one(
            f"SELECT COUNT(*) n {where} AND status IN ('failed','dead','pending','running')")
        rec["guids"] = one(f"SELECT COUNT(DISTINCT egeria_guid) n {where} AND status='done'")

    rec["ok"] = bool(rec["asset_guid"]) and rec["unfinished"] == 0 and rec["guids"] == rec["done"]
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main() -> int:
    # Quiet the library for this script run only. At import time this was process-wide: importing the module
    # (tests/test_republish_corpus.py does) silenced WARNING for every later test in the session.
    logging.disable(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N repos")
    ap.add_argument("--pause", type=float, default=30.0,
                    help="seconds between repos, to let the platform settle")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slugs", nargs="*", default=None)
    args = ap.parse_args()

    done = {json.loads(l)["slug"] for l in STATE.read_text().split("\n")
            if l.strip() and json.loads(l).get("ok")} if STATE.exists() else set()
    registry = ProjectRegistry()
    todo = [s for s in (args.slugs or [p.slug for p in registry.list_all()]) if s not in done]
    if args.limit:
        todo = todo[: args.limit]

    print(f"{len(todo)} to publish, {len(done)} already succeeded", flush=True)
    if args.dry_run:
        for s in todo:
            print(f"  would publish {s}", flush=True)
        return 0

    for i, slug in enumerate(todo, 1):
        frac = container_mem_fraction()
        if frac is None:
            print(f"STOP before {slug}: cannot read {CONTAINER} memory — "
                  f"a guard that passes when it cannot see is not a guard", flush=True)
            return 2
        if frac > MEM_CEILING:
            print(f"STOP before {slug}: {CONTAINER} at {frac:.0%} of its limit "
                  f"(ceiling {MEM_CEILING:.0%}). Let it settle or raise the cap.", flush=True)
            return 2

        rec = publish_one(registry, slug)
        with STATE.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        flag = "ok  " if rec.get("ok") else "BAD "
        print(f"[{i}/{len(todo)}] {flag}{slug:<26} {rec}", flush=True)
        if not rec.get("ok"):
            print(f"STOP: {slug} did not fully publish. Fix before continuing — "
                  f"a partially published repo that keeps going is how tonight "
                  f"produced 57 silently failed annotations.", flush=True)
            return 1
        if i < len(todo):
            time.sleep(args.pause)

    print("BATCH DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Coverage and discrimination report over the question/analysis catalogs.

Answers three questions that were previously computed by hand, once, and
then went stale:

1. **Reachability** — for each Purpose and each Perspective, how many
   analyses can be reached through the questions carrying that tag, and how
   many are reachable through it *alone*.
2. **Discrimination** — mean pairwise Jaccard overlap of the reachable sets.
   `docs/investigation-framing-design.md` §3 measured Purpose at 0.22 and
   Perspective at 0.37 on 2026-08-24; this recomputes both so a retag that
   changes them is visible rather than silent.
3. **Gaps** — tags that reach nothing at all (`Privacy` reached 0 analyses
   when this was last measured), and questions that no tag reaches.

Reads the generated catalogs, not the CSV. Regenerate first if the CSV has
moved on: `python scripts/csv_to_question_catalog_yaml.py`.

Usage:
    python scripts/question_catalog_coverage.py            # human-readable
    python scripts/question_catalog_coverage.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict

from resource_explorer.surveyors.question_catalog_reader import get_questions


def _answerers(entry: dict) -> list[str]:
    """What answers a question: its analysis ids, or for a catalog question (RS-W1-3) the Portal routines its
    Overview tile rides on. `--resource-type catalog` reports routines where the other types report analyses."""
    answering = entry["answering"]
    return list(answering["analysis_ids"]) + list(answering.get("portal_routines") or [])


def _reachability(entries: list[dict], axis: str) -> dict[str, set[str]]:
    """tag -> set of analysis ids reachable through questions carrying it."""
    reach: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        for tag in e.get(axis) or []:
            reach[tag].update(_answerers(e))
    return dict(reach)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _mean_overlap(reach: dict[str, set[str]]) -> float:
    """Mean pairwise Jaccard over tags that reach at least one analysis.

    Tags reaching nothing are excluded: an empty set overlaps nothing by
    definition, and including them drags the mean toward 0, which would read
    as strong discrimination when it is really absence of data.
    """
    sets = [s for s in reach.values() if s]
    pairs = list(itertools.combinations(sets, 2))
    if not pairs:
        return 0.0
    return sum(_jaccard(a, b) for a, b in pairs) / len(pairs)


def _unique_to(tag: str, reach: dict[str, set[str]]) -> set[str]:
    others: set[str] = set()
    for other, ids in reach.items():
        if other != tag:
            others |= ids
    return reach.get(tag, set()) - others


def build_report(resource_type: str = "repo") -> dict:
    entries = get_questions(resource_type)
    report: dict = {"resource_type": resource_type, "questions": len(entries), "axes": {}}

    for axis in ("purposes", "perspectives"):
        reach = _reachability(entries, axis)
        rows = []
        for tag in sorted(reach, key=lambda t: (-len(reach[t]), t)):
            rows.append({
                "tag": tag,
                "analyses_reachable": len(reach[tag]),
                "unique_to_it": sorted(_unique_to(tag, reach)),
                "questions_tagged": sum(1 for e in entries if tag in (e.get(axis) or [])),
            })
        report["axes"][axis] = {
            "mean_pairwise_overlap": round(_mean_overlap(reach), 4),
            "tags_reaching_nothing": [r["tag"] for r in rows if r["analyses_reachable"] == 0],
            "tags_with_unique_reach": [r["tag"] for r in rows if r["unique_to_it"]],
            "rows": rows,
        }

    report["questions_with_no_analysis"] = sorted(
        e["question"] for e in entries if not _answerers(e)
    )
    report["untagged_questions"] = {
        axis: sorted(e["question"] for e in entries if not (e.get(axis) or []))
        for axis in ("purposes", "perspectives")
    }
    return report


def render(report: dict) -> str:
    out = [
        f"Question catalog coverage — {report['resource_type']}, "
        f"{report['questions']} questions",
        "",
    ]
    for axis, data in report["axes"].items():
        out.append(f"## {axis}   mean pairwise overlap: {data['mean_pairwise_overlap']}")
        out.append(f"{'tag':<26}{'analyses':>9}{'unique':>8}{'questions':>11}")
        for r in data["rows"]:
            out.append(
                f"{r['tag']:<26}{r['analyses_reachable']:>9}"
                f"{len(r['unique_to_it']):>8}{r['questions_tagged']:>11}"
            )
        if data["tags_reaching_nothing"]:
            out.append(f"  reaches nothing: {', '.join(data['tags_reaching_nothing'])}")
        if not data["tags_with_unique_reach"]:
            out.append("  NO tag reaches an analysis another tag does not also reach")
        out.append("")

    gaps = report["questions_with_no_analysis"]
    out.append(f"## questions no analysis answers: {len(gaps)} of {report['questions']}")
    for q in gaps:
        out.append(f"  - {q}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--resource-type", default="repo")
    args = ap.parse_args()

    report = build_report(args.resource_type)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

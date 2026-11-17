"""Score harness results against the brief's criteria and the owner-approved rubric.

    python scripts/ea_rebase/score.py results/results_baseline_live.json results/scores_baseline.json

The scores file is written by a person (builder, then the Architect independently), one entry per
question: {"B01": {"score": "2"|"1"|"0"|"C", "why": "..."}}. Rubric (RUBRIC-EA-ANSWERS-QUESTION-2026-11-13,
approved by the owner as drafted): 70% target; a 1 doesn't count; "column present, condition not applied"
is a 1; C counts as success only on the ambiguous set and as 0 on the clear ones.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def ran_ok(c):
    run = c.get("run") or {}
    return bool(run.get("ran")) and not run.get("error") and run.get("kind") not in (None, "error")


def criteria(rows, scores=None):
    n = len(rows)
    chk = [r.get("check") or {} for r in rows]
    validates_and_runs = sum(1 for c in chk if c.get("validates") and ran_ok(c))
    bad_reaching_run = sorted({f"{r['id']}:{k}" for r, c in zip(rows, chk) if ran_ok(c)
                               for k in (c.get("nonexistent_attributes") or {}).get("keys") or []})
    strict_reaching_run = sorted({f"{r['id']}:{k}" for r, c in zip(rows, chk) if ran_ok(c)
                                  for k in (c.get("nonexistent_attributes") or {}).get("strict_keys") or []})
    amb = [r for r in rows if r["set"] == "ambiguous"]
    clarified = sum(1 for r in amb if (r.get("check") or {}).get("asked_clarifying"))
    clear_asked = [r["id"] for r in rows if r["set"] != "ambiguous" and (r.get("check") or {}).get("asked_clarifying")]
    times = sorted(r.get("total_s") or 0 for r in rows)
    over30 = [r["id"] for r in rows if (r.get("total_s") or 0) > 30]
    out = {
        "n": n,
        "completed": sum(1 for c in chk if c.get("completed")),
        "c1_validates_and_runs": f"{validates_and_runs}/{n} = {validates_and_runs / n:.0%} (target 80%)",
        "c3_nonexistent_attributes_reaching_a_run": bad_reaching_run,
        "c3_strict_nonexistent_attributes_reaching_a_run": strict_reaching_run,
        "c4_ambiguous_clarified": f"{clarified}/{len(amb)}",
        "c4_clear_questions_that_asked_instead": clear_asked,
        "c6_latency_s": {"median": times[n // 2] if n else None, "max": times[-1] if n else None,
                         "over_30s": over30},
    }
    if scores:
        tally = {"2": 0, "1": 0, "0": 0, "C": 0}
        success = 0
        for r in rows:
            s = str((scores.get(r["id"]) or {}).get("score", "?"))
            tally[s] = tally.get(s, 0) + 1
            if s == "2" or (s == "C" and r["set"] == "ambiguous"):
                success += 1
        out["c2_answers_the_question"] = f"{success}/{n} = {success / n:.0%} (target 70%)"
        out["c2_tally"] = tally
        out["unscored"] = [r["id"] for r in rows if r["id"] not in scores]
    return out


def main():
    rows = json.loads(Path(sys.argv[1]).read_text())
    scores = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else None
    print(json.dumps(criteria(rows, scores), indent=1))


if __name__ == "__main__":
    main()

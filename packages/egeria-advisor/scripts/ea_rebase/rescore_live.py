"""Re-score the live runs from the rows they returned (builder's scores; the Architect scores blind).

    python scripts/ea_rebase/rescore_live.py          # writes results/scores_<mode>_live[_holdout].json

Same rubric as the offline scores. For each question:

1. **Spec score.** The offline score when the live conversation produced the same spec as offline
   (type, find method, column keys, conditions). The LLM is re-run live, so some specs differ; those
   are judged afresh in SPEC_JUDGED below, each with its reason.
2. **Outcome**, from what the run actually showed the user:
   - ``rows``    a table with values in it: the spec score stands;
   - ``blank``   rows came back but every sampled cell is empty: 0;
   - ``empty``   no rows, where the Coco content pack has the data: 0;
   - ``error``   the run failed (HTTP 400, missing argument, a method that doesn't exist): 0;
   - ``timeout`` Egeria didn't answer within 90 s: 0 in the strict score (the user got nothing),
                 kept apart so it can be read as "not established" instead;
   - ``no_run``  the conversation didn't reach a spec (crash, error, or a clarifying question):
                 the spec score (0, or C).
   The live score is min(spec, outcome). A live run can keep or lower a score, never raise it.
"""
from __future__ import annotations

import json
from pathlib import Path

RES = Path(__file__).parent / "results"
MODES = ["baseline", "rebased", "rebased_fixes"]

# Live specs that differ from the offline spec, judged with the same rubric.
SPEC_JUDGED = {
    ("baseline", ""): {
        "B03": ("1", "Project + priorityLevel column, not sorted"),
        "B05": ("0", "GlossaryTerm but find_glossaries: lists glossaries, not terms"),
        "C02": ("1", "GovernanceDefinition with impact/likelihood: not the Risk type"),
        "C08": ("1", "Agreement target but find_collections, no condition"),
        "C09": ("0", "find_assets_with_criteria does not exist"),
        "C10": ("0", "conversation did not complete"),
        "A01": ("0", "ambiguous: built a spec (three find methods in one) instead of asking"),
        "A02": ("0", "ambiguous: crashed, did not ask"),
    },
    ("baseline", "_holdout"): {
        "H09": ("0", "Referenceable via find_collections: not teams"),
        "H11": ("0", "ambiguous: crashed, did not ask"),
    },
    ("rebased", ""): {
        "B01": ("0", "Process names: not assets, no lineage"),
        "B02": ("1", "Project + status column, ACTIVE not applied"),
        "C04": ("1", "GovernanceMetric names, no domain"),
        "C05": ("0", "Project for digital products"),
        "A02": ("0", "ambiguous: built a spec instead of asking"),
    },
    ("rebased", "_holdout"): {
        "H04": ("0", "GovernanceDefinition for solution blueprints"),
        "H11": ("0", "ambiguous: built an Asset spec instead of asking"),
    },
    ("rebased_fixes", ""): {
        "B01": ("1", "Asset names + description: lineage not shown"),
        "B04": ("1", "GlossaryTerm names: 'has usage' neither applied nor shown"),
        "B09": ("2", "DigitalProduct names + description"),
        "B15": ("1", "GovernancePolicy, APPROVED only (not ACTIVE); the offline run's sort is gone"),
        "C01": ("1", "Campaign-classified projects, search 'Ninety Day Plan': not Jules Keeper's items"),
        "C05": ("1", "DigitalProduct names, product manager not shown"),
        "C06": ("1", "DigitalProduct names, 'no product manager' not applied"),
    },
    ("rebased_fixes", "_holdout"): {
        "H04": ("1", "SolutionBlueprint names + description: components not shown"),
    },
}

# The quiet re-run (re-base + fixes only, 19:00-19:29Z, tip 3a1b2406: generic-find + paging fixes).
SPEC_JUDGED[("rebased_fixes", "_quiet")] = {
    "B01": ("1", "Asset names + description: lineage not shown"),
    "B09": ("2", "DigitalProduct names + description"),
    "B14": ("1", "GovernanceControl names + description: measurements and targets not shown"),
    "B15": ("1", "GovernancePolicy, APPROVED + ACTIVE: a content status, so the condition isn't expressed"),
    "C01": ("1", "Campaign-classified projects searched 'ninety day plan': not Jules Keeper's items"),
    "C06": ("1", "DigitalProduct names, 'no product manager' not applied"),
    "C07": ("0", "DigitalSubscription names: subscriptions, not the subscription types offered (Architect: 0)"),
    "C09": ("1", "Asset names + description, 'no owner' not applied"),
    "C10": ("1", "Asset oldest-updated first + update time; the 180-day bound not applied"),
}
SPEC_JUDGED[("rebased_fixes", "_holdout_quiet")] = {}

# A column the question is about came back blank in every sampled row, though other columns had values.
CONDITION_COLUMN_BLANK = {
    ("baseline", "", "B03"): "priorityLevel blank in every sampled row: priority not shown",
    ("rebased_fixes", "_quiet", "C10"): "update_time blank in every sampled row: pyegeria's "
                                        "_extract_referenceable_properties (the generic finder's extractor) "
                                        "never sets update_time, though it sets create_time",
}

# Rows with values that still answer wrongly, read from the rows themselves (score, reason).
ROWS_WRONG = {
    ("rebased_fixes", "", "C03"): ("1", "importance is a string, so PROPERTY_DESCENDING sorts it alphabetically: "
                                        "Medium-High first, all four Critical risks last (Architect's blind "
                                        "re-score, 274ab8d1; confirmed from the rows)"),
    ("rebased_fixes", "_quiet", "C03"): ("1", "same spec, same rows as before: importance sorted alphabetically, "
                                              "Critical risks last"),
}


def _response(r):
    t = r.get("transcript") or []
    return t[-1]["response"] if t else ""


def outcome(r):
    c = r.get("check") or {}
    run = c.get("run") or {}
    resp = _response(r)
    if not c.get("completed"):
        return "no_run", "clarified" if c.get("asked_clarifying") else "did not complete"
    err = run.get("error") or ""
    if "TIMEOUT" in err or "TIMEOUT_ERROR_408" in resp:
        return "timeout", "Egeria timed out at 90 s (laz loaded)"
    if err:
        return "error", err[:160]
    if "couldn't run it" in resp:
        return "error", resp.split("couldn't run it:", 1)[1].strip()[:160]
    kind = run.get("kind")
    if kind == "empty" or (kind == "table" and not run.get("shown")):
        return "empty", "no rows returned"
    if kind == "table":
        cells = [v for row in run.get("sample") or [] for v in row.values()]
        if cells and all(v in (None, "") for v in cells):
            return "blank", f"{run.get('shown')} rows, every sampled cell empty"
        return "rows", f"{run.get('shown')} rows with values"
    return "error", "completed but did not run"


def _spec_signature(r):
    c = r.get("check") or {}
    dr = r.get("draft") or {}
    act = (dr.get("format_set") or {}).get("action") or {}
    if not c.get("completed"):
        return ("not done", bool(c.get("asked_clarifying")))
    cols = tuple(x.get("key") for x in (c.get("columns") or dr.get("columns") or []))
    params = act.get("spec_params") or dr.get("conditions") or {}
    return (c.get("target_type"), c.get("action_function"), cols, json.dumps(params, sort_keys=True))


def rescore(mode, tag, off_tag=None):
    off_tag = tag if off_tag is None else off_tag
    off = {r["id"]: r for r in json.loads((RES / f"results_{mode}_offline{off_tag}.json").read_text())}
    live = json.loads((RES / f"results_{mode}_live{tag}.json").read_text())
    off_scores = json.loads((RES / f"scores_{mode}_offline{off_tag}.json").read_text())
    judged = SPEC_JUDGED.get((mode, tag), {})
    out = {}
    for r in live:
        q = r["id"]
        if _spec_signature(r) == _spec_signature(off[q]):
            spec, spec_why = off_scores[q]["score"], "same spec as offline: " + off_scores[q]["why"].split(": ", 1)[-1]
        elif q in judged:
            spec, spec_why = judged[q][0], "live spec differs from offline: " + judged[q][1]
        else:
            raise SystemExit(f"{mode}{tag} {q}: live spec differs from offline and is not judged")
        kind, detail = outcome(r)
        if (mode, tag, q) in CONDITION_COLUMN_BLANK:
            kind, detail = "blank", CONDITION_COLUMN_BLANK[(mode, tag, q)]
        if (mode, tag, q) in ROWS_WRONG:
            score, reason = ROWS_WRONG[(mode, tag, q)]
            score = min(score, spec)
            detail = f"{detail}; {reason}"
        elif kind in ("rows", "no_run"):
            score = spec
        else:
            score = "0" if spec != "C" else "C"
        out[q] = {"score": score, "spec_score": spec, "outcome": kind,
                  "why": f"LIVE: {kind} ({detail}); spec {spec}: {spec_why}"}
    return out


def main():
    runs = [(m, t, t) for m in MODES for t in ("", "_holdout")]
    runs += [("rebased_fixes", "_quiet", ""), ("rebased_fixes", "_holdout_quiet", "_holdout")]
    for mode, tag, off_tag in runs:
        if not (RES / f"results_{mode}_live{tag}.json").exists():
            continue
        if True:
            scores = rescore(mode, tag, off_tag)
            (RES / f"scores_{mode}_live{tag}.json").write_text(json.dumps(scores, indent=1) + "\n")
            n = len(scores)
            two = sum(1 for s in scores.values() if s["score"] == "2")
            c = sum(1 for q, s in scores.items() if s["score"] == "C")
            to = [q for q, s in scores.items() if s["outcome"] == "timeout"]
            # "timeouts not established": drop timed-out questions from numerator and denominator
            est = {q: s for q, s in scores.items() if s["outcome"] != "timeout"}
            est_ok = sum(1 for s in est.values() if s["score"] == "2" or s["score"] == "C")
            tally = {}
            for s in scores.values():
                tally[s["outcome"]] = tally.get(s["outcome"], 0) + 1
            print(f"{mode}{tag}: strict {two + c}/{n} = {(two + c) / n:.0%} (2s {two}, C {c}); "
                  f"timeouts {len(to)}; on the {len(est)} not timed out: {est_ok}/{len(est)}; outcomes {tally}")


if __name__ == "__main__":
    main()

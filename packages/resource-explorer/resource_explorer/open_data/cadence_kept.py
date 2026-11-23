"""od_cadence_kept (TR-54 B3): is a dataset kept to the cadence its publisher states?

Compares the stated cadence (`accrualPeriodicity` in the stored record) with the modified dates RE has OBSERVED over the dataset's
refresh history: each kept `external_change` look is one observation (when RE looked, and the modified date the portal then stated).
Reads that stored history only: no portal call. Stores dataset facts only (no per-user data): the result is one shared row.

- No cadence stated: `nothing_found`, never "not kept". A cadence that is not an interval (irregular, continuous): stated, not judged.
- Fewer than two observations: `not_measured`, "not enough history (1 refresh)", never "kept".
- Otherwise "late" when some LOOK saw a modified date older than the cadence allows (named in the words); else "kept". The allowance
  is half as much again as the cadence (a monthly dataset is late after 45 days), and at least a day: dates are read to the day.
  Lateness is per look, never between two observed modified dates: RE sees a date only when it looks, so dates a look-interval apart
  would report RE's sampling as the publisher's lateness. When RE looked less often than the cadence allows, "kept" says so.
"""
from __future__ import annotations

from datetime import date

from resource_explorer.open_data import analyses
from resource_explorer.open_data.cadence import parse_cadence, placeholder_of

ANALYSIS_ID = "od_cadence_kept"
STEP_KEY = "external_change"          # the refresh look (open_data/refresh.py): one step_runs row per look
TOLERANCE = 1.5


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _median(values: list[int]) -> float:
    v = sorted(values)
    mid = len(v) // 2
    return float(v[mid]) if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def judge(cadence_raw, looks: list[dict]) -> analyses.OpenDataResult:
    """Pure. `looks` are [{looked_at, modified}], any order; a look with no readable modified date is not an observation.

    Lateness is judged per LOOK: RE only sees a modified date when it looks, so two observed dates a look-interval apart say nothing
    about the publisher. A look that saw a modified date older than the cadence allows is the evidence of "late". When RE looked less
    often than the cadence allows, "kept" says what it could not see."""
    cadence = parse_cadence(cadence_raw)
    if cadence is None:
        word = placeholder_of(cadence_raw)
        return analyses.OpenDataResult(
            state="nothing_found", summary="the portal states no update cadence for this dataset",
            explanation=("The record states no cadence, so there is nothing to compare the modified dates with."
                         + (f" The cadence field says '{word}', which states none." if word else "")),
            measures={"cadence_checks": 0, "judged": False, **({"placeholder": word} if word else {})})
    if cadence.days is None:
        return analyses.OpenDataResult(
            state="measured", summary=f"not judged: the stated cadence '{cadence.raw}' is not an interval",
            explanation="A cadence like irregular or continuous names no interval to hold the modified dates to.",
            measures={"cadence_checks": 1, "judged": False, "cadence": cadence.raw})
    seen = sorted((_day(look.get("looked_at")), _day(look.get("modified"))) for look in looks
                  if _day(look.get("looked_at")) and _day(look.get("modified")))
    if len(seen) < 2:
        words = "no refresh yet" if not seen else _plural(len(seen), "refresh", "refreshes")
        return analyses.OpenDataResult(
            state="not_measured", summary=f"not enough history ({words})",
            explanation="Two refreshes that state a modified date are needed before a cadence can be checked.",
            measures={"observations": len(seen), "cadence": cadence.raw})
    allowed = max(cadence.days * TOLERANCE, 1.0)
    ages = [((looked - modified).days, looked, modified) for looked, modified in seen]
    worst_age, worst_look, worst_modified = max(ages, key=lambda a: (a[0], a[1]))
    last_look, last_modified = seen[-1]
    modified_dates = sorted({m for _, m in seen})
    longest_gap = max((b - a).days for a, b in zip(modified_dates, modified_dates[1:])) if len(modified_dates) > 1 else 0
    looks_interval = _median([(b[0] - a[0]).days for a, b in zip(seen, seen[1:])])
    late = worst_age > allowed
    verdict = "late" if late else "kept"
    read = _plural(len(seen), "refresh", "refreshes") + " read"
    if late:
        summary = (f"late: stated cadence {cadence.raw}; the look on {worst_look} saw a modified date of {worst_modified}, "
                   f"{_plural(worst_age, 'day')} old, more than the {allowed:g} the cadence allows ({read})")
    elif looks_interval > allowed:
        summary = (f"kept in the looks RE has; RE looked every ~{int(looks_interval + 0.5)} days, longer than the cadence "
                   f"({cadence.raw}), so a late update between looks cannot be seen; last modified {last_modified} at the look on {last_look} ({read})")
    else:
        summary = (f"kept: stated cadence {cadence.raw}; last modified {last_modified}, {_plural((last_look - last_modified).days, 'day')} "
                   f"before the last look on {last_look} ({read})")
    return analyses.OpenDataResult(
        state="measured", summary=summary,
        explanation=(f"Compared the stated cadence ({cadence.raw}, about {cadence.days:g} days) with the modified date each of RE's looks saw, "
                     f"allowing {allowed:g} days. 'Late' needs a look that saw a date older than that; 'kept' means none did in the looks RE has. "
                     f"The longest gap between the modified dates RE observed was {longest_gap} days: a fact about RE's sampling as much as "
                     f"the publisher, so it is not used to judge lateness."),
        measures={"cadence_checks": 1, "judged": True, "verdict": verdict, "cadence": cadence.raw, "observations": len(seen),
                  "allowed_days": allowed, "worst_age_days": worst_age, "worst_look": str(worst_look),
                  "days_since_modified": (last_look - last_modified).days, "longest_gap_days": longest_gap,
                  "looks_interval_days": looks_interval, "last_modified": str(last_modified), "last_look": str(last_look)})


def looks_of(registry, dataset: dict) -> list[dict]:
    rows = registry.query_step_runs(slug=dataset["slug"], step_key=STEP_KEY, entity_type="open_data")
    return [{"looked_at": r.get("surveyed_at"),
             "modified": (((r.get("metrics") or {}).get("snapshot") or {}).get("fields") or {}).get("modified")} for r in rows]


def run(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    record = ctx.dataset.get("record")
    cadence = record.get("accrualPeriodicity") if isinstance(record, dict) else None
    return judge(cadence, looks_of(ctx.registry, ctx.dataset))


RUNNER = analyses.Runner(ANALYSIS_ID, run, lambda dataset: 0)        # stored history only: no portal call

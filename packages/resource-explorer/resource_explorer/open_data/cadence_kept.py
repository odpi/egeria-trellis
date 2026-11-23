"""od_cadence_kept (TR-54 B3): is a dataset kept to the cadence its publisher states?

Compares the stated cadence (`accrualPeriodicity` in the stored record) with the modified dates RE has OBSERVED over the dataset's
refresh history: each kept `external_change` look is one observation (when RE looked, and the modified date the portal then stated).
Reads that stored history only: no portal call. Stores dataset facts only (no per-user data): the result is one shared row.

- No cadence stated: `nothing_found`, never "not kept". A cadence that is not an interval (irregular, continuous): stated, not judged.
- Fewer than two observations: `not_measured`, "not enough history (1 refresh)", never "kept".
- Otherwise "late" when the latest modified date is older than the cadence allows at the last look, or when two modified dates RE
  saw were further apart than it allows; else "kept". The allowance is half as much again as the cadence (a monthly dataset is late
  after 45 days), and at least a day: dates are read to the day.
"""
from __future__ import annotations

from datetime import date

from resource_explorer.open_data import analyses
from resource_explorer.open_data.cadence import parse_cadence

ANALYSIS_ID = "od_cadence_kept"
STEP_KEY = "external_change"          # the refresh look (open_data/refresh.py): one step_runs row per look
TOLERANCE = 1.5


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _plural(n: int, what: str) -> str:
    return f"{n} {what}{'' if n == 1 else 's'}"


def judge(cadence_raw, looks: list[dict]) -> analyses.OpenDataResult:
    """Pure. `looks` are [{looked_at, modified}], any order; a look with no readable modified date is not an observation."""
    cadence = parse_cadence(cadence_raw)
    if cadence is None:
        return analyses.OpenDataResult(
            state="nothing_found", summary="the portal states no update cadence for this dataset",
            explanation="The record states no cadence, so there is nothing to compare the modified dates with.",
            measures={"cadence_checks": 0, "judged": False})
    if cadence.days is None:
        return analyses.OpenDataResult(
            state="measured", summary=f"not judged: the stated cadence '{cadence.raw}' is not an interval",
            explanation="A cadence like irregular or continuous names no interval to hold the modified dates to.",
            measures={"cadence_checks": 1, "judged": False, "cadence": cadence.raw})
    seen = sorted((_day(look.get("looked_at")), _day(look.get("modified"))) for look in looks
                  if _day(look.get("looked_at")) and _day(look.get("modified")))
    if len(seen) < 2:
        words = "no refresh yet" if not seen else _plural(len(seen), "refresh")
        return analyses.OpenDataResult(
            state="not_measured", summary=f"not enough history ({words})",
            explanation="Two refreshes that state a modified date are needed before a cadence can be checked.",
            measures={"observations": len(seen), "cadence": cadence.raw})
    allowed = max(cadence.days * TOLERANCE, 1.0)
    modified = sorted({m for _, m in seen})
    last_look, last_modified = seen[-1][0], modified[-1]
    age = (last_look - last_modified).days
    gaps = [(b - a).days for a, b in zip(modified, modified[1:])]
    longest = max(gaps) if gaps else 0
    late = age > allowed or longest > allowed
    verdict = "late" if late else "kept"
    where = (f"last modified {last_modified}, {_plural(age, 'day')} before the last look on {last_look}"
             f" ({_plural(len(seen), 'refresh')} read)")
    reason = (f"; {_plural(age, 'day')} is more than the {allowed:g} the cadence allows" if age > allowed else
              f"; two modified dates were {_plural(longest, 'day')} apart, more than the {allowed:g} the cadence allows" if late else "")
    return analyses.OpenDataResult(
        state="measured", summary=f"{verdict}: stated cadence {cadence.raw}; {where}{reason}",
        explanation=(f"Compared the stated cadence ({cadence.raw}, about {cadence.days:g} days) with the modified dates RE observed over "
                     f"its refresh history, allowing {allowed:g} days. 'Kept' means no gap beyond that was seen in the looks RE has."),
        measures={"cadence_checks": 1, "judged": True, "verdict": verdict, "cadence": cadence.raw, "observations": len(seen),
                  "allowed_days": allowed, "days_since_modified": age, "longest_gap_days": longest,
                  "last_modified": str(last_modified), "last_look": str(last_look)})


def looks_of(registry, dataset: dict) -> list[dict]:
    rows = registry.query_step_runs(slug=dataset["slug"], step_key=STEP_KEY, entity_type="open_data")
    return [{"looked_at": r.get("surveyed_at"),
             "modified": (((r.get("metrics") or {}).get("snapshot") or {}).get("fields") or {}).get("modified")} for r in rows]


def run(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    record = ctx.dataset.get("record")
    cadence = record.get("accrualPeriodicity") if isinstance(record, dict) else None
    return judge(cadence, looks_of(ctx.registry, ctx.dataset))


RUNNER = analyses.Runner(ANALYSIS_ID, run, lambda dataset: 0)        # stored history only: no portal call

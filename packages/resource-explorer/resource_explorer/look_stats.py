"""Which look people use, read from the activity rows `POST /api/activity/look-change` writes.

RULING-THEMES-PORTAL-DARK-LIGHT.md section 1: "each change of look writes one activity row. The feedback page
in Admin shows a count per look: how many people are on each, and how many switched away from each." This is
that count. There is no table of its own; the activity log is the record.

What it can and cannot know, stated rather than implied: only a CHANGE leaves a row. Someone who opened RE in
Dark and never touched the control is on Dark but appears nowhere, so `people` is "people who have changed
the look at least once", and the page says so. A count of Dark users built from these rows alone would read as
a measurement of Dark's popularity and would not be one.
"""
from __future__ import annotations

import json

LOOKS = ("dark", "light", "paper")
OPERATION = "look_change"
#: Whose change it was when the request carried no signed-in user. One bucket, not one per request, so an
#: unsigned session that flips twice is one person who switched, not two who each used a look once.
UNSIGNED = "(not signed in)"


def look_counts(rows: list[dict]) -> dict:
    """`rows` are activity rows (any order). Returns, per look, `people` (whose latest change landed there) and
    `switched_away` (how many changes left it), plus the totals the page needs to word the limits honestly."""
    events: list[tuple[str, str, str, str]] = []  # (ts, who, previous, look)
    for r in rows:
        if r.get("operation") != OPERATION:
            continue
        try:
            d = json.loads(r.get("detail") or "{}")
        except (TypeError, ValueError):
            continue
        look = d.get("look")
        if look not in LOOKS:
            continue
        events.append((r.get("ts") or "", d.get("user") or UNSIGNED, d.get("previous") or "", look))
    events.sort(key=lambda e: e[0])

    now_on: dict[str, str] = {}
    away = {k: 0 for k in LOOKS}
    for _ts, who, previous, look in events:
        now_on[who] = look
        if previous in away and previous != look:
            away[previous] += 1
    out = {k: {"people": 0, "switched_away": away[k]} for k in LOOKS}
    for look in now_on.values():
        out[look]["people"] += 1
    return {
        "looks": out,
        "changes": len(events),
        "people_who_changed": len(now_on),
    }

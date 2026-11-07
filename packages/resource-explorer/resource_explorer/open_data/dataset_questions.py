"""The open data questions of one dataset page (BRIEF-OPEN-DATA-QUESTIONS).

Three catalogue questions, each led by an analysis that is declared and not built. So the honest state of a row is
`no_reader` (◌), not "not run yet" (which would claim a runnable analysis). Where the publisher's own record already
states a field that answers part of the question, the row is `partial` (◐) and says it comes from that record.
"""
from __future__ import annotations

from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.surveyors.question_catalog_reader import get_questions

NO_MECHANISM = "No mechanism exists for this yet"
_WILL = {
    "od_licence_usable": "the licence check will judge the publisher's licence against the purpose and say what it requires",
    "od_cadence_kept": "the cadence check will compare the stated cadence with the modified dates",
    "od_profile": "the dataset profile will summarise the columns, size and formats it has read, and name what it has not",
}
STAGE_ORDER = ("Scouting", "Discovery", "Assessment")


def _stated(v) -> bool:
    return v not in (None, "", NOT_REPORTED)


def _flag(v) -> str:
    return "?" if not isinstance(v, bool) else ("✓" if v else "✗")


def _source(d: dict) -> str:
    who = ", ".join(d.get("portals") or []) or "the portal"
    when = d.get("retrieved_at")
    return f"from the publisher's record at {who}, retrieved {when[:10] if when else 'at a time that was not recorded'}"


def _licence_partial(d: dict) -> str | None:
    lic = d.get("licence") or {}
    flags_known = any(isinstance(lic.get(k), bool) for k in ("attribution", "share_alike", "commercial"))
    if not _stated(d.get("licence_id")) and not flags_known:
        return None
    ident = d["licence_id"] if _stated(d.get("licence_id")) else "licence identifier not reported"
    return (f"Licence {ident} · attribution {_flag(lic.get('attribution'))} · share-alike "
            f"{_flag(lic.get('share_alike'))} · commercial {_flag(lic.get('commercial'))} — {_source(d)}")


def _cadence_partial(d: dict) -> str | None:
    cadence = (d.get("record") or {}).get("accrualPeriodicity")
    if isinstance(cadence, (dict, list)):
        cadence = None
    parts = []
    if _stated(d.get("modified")):
        parts.append(f"last modified {str(d['modified'])[:10]}")
    if _stated(cadence):
        parts.append(f"stated cadence {cadence}")
    if not parts:
        return None
    text = "; ".join(parts)
    return f"{text[0].upper()}{text[1:]} — {_source(d)}"


_PARTIAL = {"od_licence_usable": _licence_partial, "od_cadence_kept": _cadence_partial}


def build(d: dict) -> dict:
    rows = []
    for e in get_questions("open_data"):
        lead = (e["answering"]["analysis_ids"] or [""])[0]
        partial = _PARTIAL.get(lead, lambda _d: None)(d)
        will = _WILL.get(lead, "an analysis will answer it")
        rows.append({
            "id": e["id"], "question": e["question"], "stage": e["stage"], "perspectives": e["perspectives"],
            "purposes": e["purposes"], "analysis_ids": e["answering"]["analysis_ids"], "rationale": e["rationale"],
            "state": "partial" if partial else "no_reader",
            "answer": partial or "",
            "words": ("Answered in part by the record alone; " if partial else f"{NO_MECHANISM} — ") + f"{will}.",
        })
    rows.sort(key=lambda r: (STAGE_ORDER.index(r["stage"]) if r["stage"] in STAGE_ORDER else 99, r["id"]))
    partial_n = sum(1 for r in rows if r["state"] == "partial")
    return {"slug": d["slug"], "questions": rows, "total": len(rows), "answered": 0, "partial": partial_n,
            "stages": [{"stage": s, "total": sum(1 for r in rows if r["stage"] == s),
                        "partial": sum(1 for r in rows if r["stage"] == s and r["state"] == "partial")}
                       for s in STAGE_ORDER if any(r["stage"] == s for r in rows)]}

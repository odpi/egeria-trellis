"""The open data questions of one dataset page (BRIEF-OPEN-DATA-QUESTIONS).

Three catalogue questions, each led by an analysis that is declared and not built. So the honest state of a row is
`no_reader` (◌), not "not run yet" (which would claim a runnable analysis). Where the publisher's own record already
states a field that answers part of the question, the row is `partial` (◐) and says it comes from that record.
"""
from __future__ import annotations

from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.resource_types import GENERIC_QUESTION_KINDS, SURVEYED_RESOURCE_TYPES
from resource_explorer.surveyors.question_catalog_reader import get_questions

assert "open_data" in GENERIC_QUESTION_KINDS        # this page is the one user of the capability (TR-54 A2, TR-119)

NO_MECHANISM = "No mechanism exists for this yet"
_WILL = {
    "od_licence_usable": "the licence check will judge the publisher's licence against the purpose and say what it requires",
    "od_cadence_kept": "the cadence check will compare the stated cadence with the modified dates",
    "od_profile": "the dataset profile will summarise the columns, size and formats it has read, and name what it has not",
}
STAGE_ORDER = ("Scouting", "Discovery", "Assessment", "Analysis", "Enrichment")

# The generic (`*`) questions, one decision each (TR-54 A2). A generic question is one every surveyed kind carries (the CSV's
# `*`, expanded when the catalog is generated). Whether it can be asked of a DATASET is a judgement nobody had recorded, so it
# is recorded here, with the reason for each exclusion; a new generic question fails
# `test_every_generic_question_has_a_decision...` until someone decides. These are the builder's calls, for review.
GENERIC_APPLIES = frozenset({
    "Q-0002",   # what is it and what is it for: the publisher's title and description
    "Q-0003",   # who owns and administers it: the publisher
    "Q-0005",   # already cataloged in Egeria, and when
    "Q-0007",   # existing use in the organization
    "Q-0008",   # known feedback
    "Q-0011",   # overlap with other resources: the same data, a subset, a replacement
    "Q-0016",   # cost to run, host or license
    "Q-0025",   # skills to support its use
    "Q-0029",   # fit with governance frameworks
    "Q-0030",   # explicit license and non-standard terms: the record's licence
    "Q-0038",   # already surveyed at any tier (scout, profile)
    "Q-0041",   # how much has changed since the last survey (Architect read-back: asked, no reader yet)
    "Q-0040",   # worth a full survey (a profile)
    "Q-0055",   # where it is, under which jurisdiction (Architect read-back: partial when the record names a place)
    "Q-0057",   # restrictions beyond the license
    "Q-0058",   # ready to be offered as a data product
    "Q-0059",   # demand: subscriptions, requests, feedback, use
})
GENERIC_DOES_NOT_APPLY = {
    "Q-0054": "Egeria's survey engine does not survey a dataset on a data portal; there is nothing for it to reach",
    "Q-0098": "asks about the estate's own platforms, monitoring and security controls, which a published dataset does not run on",
}
_GENERIC_WILL = {
    "human": "a person's answer (Enrichment); RE holds none yet",
    "direct": "a field RE does not hold for a dataset yet",
    "mixed": "no reader combines the sources for a dataset yet",
    "gap": "named as a gap; no reader yet",
    "analysis": "an analysis that is declared and not built for datasets",
    "partial": "only part of it can be read, and not yet for a dataset",
}


# Words the Architect ruled for a question whose reader does not exist: used as they are, in place of the generic sentence.
_GENERIC_WORDS = {
    "Q-0041": "no reader yet: OD4's look-again re-reads the record; comparing it with the record at import is not built",
    "Q-0055": "the publisher's jurisdiction and any transfer terms are not read yet",
}
_PLACE_KEYS = ("spatial", "dct:spatial", "geographic_coverage", "geographicCoverage", "country", "jurisdiction")


def _text(v) -> str | None:
    return v.strip() if isinstance(v, str) and _stated(v) and v.strip() else None


def _description_partial(d: dict) -> str | None:
    rec = d.get("record") or {}
    desc = next((t for t in (_text(rec.get("notes")), _text(rec.get("description"))) if t), None)
    title = _text(d.get("title"))
    if not desc and not title:
        return None
    said = f"{title} — {desc[:240]}{'…' if len(desc) > 240 else ''}" if title and desc else (desc or title)
    return f"The publisher describes it as: {said} — {_source(d)}. What it is for is the publisher's wording, not RE's."


def _publisher_partial(d: dict) -> str | None:
    who = _text(d.get("publisher"))
    return f"Published by {who}; the record names no owner or administrator beyond that — {_source(d)}" if who else None


def _licence_generic_partial(d: dict) -> str | None:
    return _licence_partial(d)



def _stated(v) -> bool:
    return v not in (None, "", NOT_REPORTED)


def _flag(v) -> str:
    return "?" if not isinstance(v, bool) else ("✓" if v else "✗")


def _source(d: dict) -> str:
    who = ", ".join(d.get("portals") or []) or "the portal"
    when = d.get("retrieved_at")
    return f"from the publisher's record at {who}, retrieved {when[:10] if when else 'at a time that was not recorded'}"


def _licence_partial(d: dict) -> str | None:
    from resource_explorer.open_data.licences import licence_statement

    lic = d.get("licence") or {}
    flags_known = any(isinstance(lic.get(k), bool) for k in ("attribution", "share_alike", "commercial"))
    statement = licence_statement(d)
    if not _stated(d.get("licence_id")) and not flags_known and statement["state"] != "stated_in_words":
        return None
    if _stated(d.get("licence_id")):
        ident = d["licence_id"]
    elif statement["state"] == "stated_in_words":
        ident = statement["words"]                       # "licence stated in words, not identified": never "no licence"
    else:
        ident = "licence identifier not reported"
    return (f"{'Licence ' if ident and not ident.startswith('licence ') else ''}{ident} · attribution {_flag(lic.get('attribution'))} · share-alike "
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


def _place_partial(d: dict) -> str | None:
    """A place the publisher's own record states (a country, spatial or jurisdiction field, at the top level or in a CKAN
    extra). A structured value (a GeoJSON object) is not read: it says no more than "a spatial extent is stated"."""
    rec = d.get("record") or {}
    found: list[tuple[str, str]] = []
    for key in _PLACE_KEYS:
        v = _text(rec.get(key))
        if v:
            found.append((key, v))
    for ex in rec.get("extras") or []:
        if isinstance(ex, dict) and any(k in str(ex.get("key", "")).lower() for k in ("spatial", "country", "jurisdiction")):
            v = _text(ex.get("value"))
            if v:
                found.append((str(ex["key"]), v))
    if not found:
        return None
    said = "; ".join(f"{k}: {v[:120]}{'…' if len(v) > 120 else ''}" for k, v in found[:3])
    return (f"The publisher's record states a place — {said} — {_source(d)}. Jurisdiction, residency and transfer "
            "terms are not read from it.")


_GENERIC_PARTIAL = {"Q-0002": _description_partial, "Q-0003": _publisher_partial, "Q-0030": _licence_generic_partial,
                    "Q-0055": _place_partial}


def _generic_rows(d: dict) -> list[dict]:
    by_type = {t: {e["id"]: e for e in get_questions(t)} for t in SURVEYED_RESOURCE_TYPES}
    rows = []
    for qid in sorted(GENERIC_APPLIES):
        e = next((by_type[t][qid] for t in SURVEYED_RESOURCE_TYPES if qid in by_type[t]), None)
        if e is None:                                  # retired from the catalog: not asked, not counted
            continue
        partial = _GENERIC_PARTIAL.get(qid, lambda _d: None)(d)
        kind = e["answering"]["kind"]
        rows.append({
            "id": qid, "kind": "generic", "question": e["question"], "stage": e["stage"], "perspectives": e["perspectives"],
            "purposes": e["purposes"], "analysis_ids": e["answering"]["analysis_ids"], "rationale": e["rationale"],
            "state": "partial" if partial else "no_reader", "answer": partial or "",
            "words": ("Answered in part by the record alone; " + f"{_GENERIC_WILL.get(kind, 'no reader yet')}." if partial
                      else _GENERIC_WORDS.get(qid) or f"{NO_MECHANISM} — {_GENERIC_WILL.get(kind, 'no reader yet')}."),
        })
    return rows


def _not_applicable() -> list[dict]:
    by_id = {e["id"]: e for t in SURVEYED_RESOURCE_TYPES for e in get_questions(t)}
    return [{"id": qid, "question": by_id[qid]["question"], "reason": why}
            for qid, why in sorted(GENERIC_DOES_NOT_APPLY.items()) if qid in by_id]


def _strip_stage(stage: str) -> str:
    return (stage or "").split("/")[0].strip()


def build(d: dict) -> dict:
    rows = []
    for e in get_questions("open_data"):
        lead = (e["answering"]["analysis_ids"] or [""])[0]
        partial = _PARTIAL.get(lead, lambda _d: None)(d)
        will = _WILL.get(lead, "an analysis will answer it")
        rows.append({
            "id": e["id"], "kind": "open_data", "question": e["question"], "stage": e["stage"],
            "perspectives": e["perspectives"],
            "purposes": e["purposes"], "analysis_ids": e["answering"]["analysis_ids"], "rationale": e["rationale"],
            "state": "partial" if partial else "no_reader",
            "answer": partial or "",
            "words": ("Answered in part by the record alone; " if partial else f"{NO_MECHANISM} — ") + f"{will}.",
        })
    rows += _generic_rows(d)
    rows.sort(key=lambda r: (STAGE_ORDER.index(_strip_stage(r["stage"])) if _strip_stage(r["stage"]) in STAGE_ORDER else 99,
                             r["id"]))
    partial_n = sum(1 for r in rows if r["state"] == "partial")
    return {"slug": d["slug"], "questions": rows, "total": len(rows), "answered": 0, "partial": partial_n,
            "not_applicable": _not_applicable(),
            "stages": [{"stage": s, "total": sum(1 for r in rows if _strip_stage(r["stage"]) == s),
                        "partial": sum(1 for r in rows if _strip_stage(r["stage"]) == s and r["state"] == "partial")}
                       for s in STAGE_ORDER if any(_strip_stage(r["stage"]) == s for r in rows)]}

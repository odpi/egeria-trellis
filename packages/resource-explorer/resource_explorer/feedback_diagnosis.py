"""What an admin needs to diagnose a rated answer: its stored compile, the causes the manifest suggests, and the
analyses the item is charged to.

docs/feedback-closed-loop-design.md §2.3, §3.1 and §6 step 1. Three rules hold throughout:

* **Stored, never re-compiled.** Everything here reads the compile row persisted when the answer was given
  (`context_compiles`, with the text the model saw). A re-compile shows today's evidence, not the evidence behind
  the answer being judged.
* **A suggestion is never a diagnosis.** Each one is shown with the rule that produced it, and nothing here
  records anything: the admin confirms or changes it, and only their Save writes a cause.
* **Only four causes have a manifest signal.** `analysis_defect`, `wrong_evidence`, `reasoning` and
  `unclear_question` are a person's call, so nothing is suggested for them.

The charge set is wider than what was packed, because the analysis most often at fault is the one that is NOT in
the answer (the architect's read-back of 9c28a6bc): packed sections, gaps judged absent, dropped sections, and the
deferred sections the catalog names for this question. It is used to group the admin by-cause view; the project
owner decided on 2026-11-10 that diagnosis stays with admins (§8 Q2), so it grants nobody anything.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from trellis_feedback import CAUSE_CHAIN

from resource_explorer.compile_as_of import is_evidence, section_as_ofs

logger = logging.getLogger(__name__)

#: A gap in one of these states had no result: the analysis did not run, or nothing shows it produced the one
#: stored, or it ran over only part of what it covers. `nothing_found` is NOT here: it is a real zero, an answer.
#: A gap with no state was never judged (the fact layer could not say); it is counted as absent, and its rule says so.
ABSENT_STATES = frozenset({"never_run", "not_established", "partial"})

#: A packed section whose evidence is older than this, measured back from when the answer was given, suggests
#: `stale`. The rule shows the date, so the admin judges whether it mattered.
STALE_AFTER_DAYS = 90

#: The order of an analysis's role in the answer, most likely at fault first.
ROLE_ORDER = ("absent", "dropped", "deferred", "IDENTIFIERS", "SUMMARY", "FULL")

_ROLE_WORDS = {
    "absent": "had no result",
    "dropped": "dropped by the packer",
    "deferred": "ranked below the section cap",
    "IDENTIFIERS": "packed at identifiers only",
    "SUMMARY": "packed as a summary",
    "FULL": "packed in full",
}


def _parse(ts: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else None


def _is_absent(gap: dict) -> bool:
    state = gap.get("state")
    return state is None or state in ABSENT_STATES


def charge_set(manifest: dict, catalog_analyses: list[str] | tuple[str, ...] = ()) -> list[dict[str, str]]:
    """The analyses an answer depended on, each once, in ROLE_ORDER: [{analysis_id, role, words}].

    `catalog_analyses` are the analyses the question catalog says answer this question; only those of the
    deferred sections are charged, since the rest were ranked out for being less relevant, not missed.
    Charged to nothing when the catalog had no question for it (`coverage.kind == "none"`): that is a
    catalog gap, which no analysis owns.
    """
    if (manifest.get("coverage") or {}).get("kind") == "none":
        return []
    roles: list[tuple[str, str]] = []
    unjudged = {g.get("key", "") for g in manifest.get("gaps") or [] if g.get("state") is None}
    roles += [(g.get("key", ""), "absent") for g in manifest.get("gaps") or [] if _is_absent(g)]
    roles += [(d.get("key", ""), "dropped") for d in manifest.get("dropped") or []]
    wanted = set(catalog_analyses)
    roles += [(d.get("key", ""), "deferred") for d in manifest.get("deferred") or [] if d.get("key") in wanted]
    roles += [(p.get("key", ""), str(p.get("rung") or "FULL")) for p in manifest.get("packed") or []]
    rank = {r: i for i, r in enumerate(ROLE_ORDER)}
    best: dict[str, str] = {}
    for key, role in roles:
        if key and (key not in best or rank.get(role, 99) < rank.get(best[key], 99)):
            best[key] = role
    ordered = sorted(best.items(), key=lambda kv: (rank.get(kv[1], 99), kv[0]))
    return [{"analysis_id": k, "role": r,
             "words": "had no result (not judged)" if r == "absent" and k in unjudged else _ROLE_WORDS.get(r, r)}
            for k, r in ordered]


def suggestions(manifest: dict, *, answered_at: str = "",
                catalog_analyses: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Every cause the manifest signals, in chain order, each with the rule that fired and the sections it fired
    on: [{cause, rule, sections}]. The first is the suggestion, by the earliest-stage precedence (§2.2); the rest
    are shown so the admin can see what else the evidence says. Empty when nothing fires."""
    out: list[dict[str, Any]] = []

    if (manifest.get("coverage") or {}).get("kind") == "none":
        out.append({"cause": "catalog_gap", "sections": [],
                    "rule": "no catalog question matches this question, so no stored analysis answers it"})

    absent = [g for g in manifest.get("gaps") or [] if _is_absent(g)]
    if absent:
        def _why(g: dict) -> str:
            if g.get("state"):
                return f"{g.get('key')} ({g.get('reason') or g.get('state')})"
            # Not judged: quote what the compile stored (e.g. "no candidate — resolver produced nothing yet").
            return f"{g.get('key')} (not judged: {g.get('reason') or 'the fact layer could not say whether it ran'})"
        out.append({"cause": "missing_evidence", "sections": [g.get("key") for g in absent],
                    "rule": "had no result: " + "; ".join(_why(g) for g in absent)})

    when = _parse(answered_at)
    if when is not None:
        old = []
        # Each section's as-of as it stood when the answer was given (compile_as_of: the packed entry, a repeat
        # compile's history, or `declared` in older manifests).
        for key, value in section_as_ofs(manifest, at=answered_at).items():
            as_of = _parse(value)
            if as_of is not None and when - as_of > timedelta(days=STALE_AFTER_DAYS):
                old.append((key, as_of, (when - as_of).days))
        if old:
            out.append({"cause": "stale", "sections": [k for k, _, _ in old],
                        "rule": "; ".join(f"{k} is as of {d.date().isoformat()}, {n} days before the answer"
                                          for k, d, n in old)
                        + f" (over {STALE_AFTER_DAYS} days)"})

    thin = [(p.get("key"), "was packed at identifiers only") for p in manifest.get("packed") or []
            if p.get("rung") == "IDENTIFIERS"]
    thin += [(d.get("key"), f"was dropped ({d.get('reason') or 'no reason recorded'})")
             for d in manifest.get("dropped") or []]
    wanted = set(catalog_analyses)
    thin += [(d.get("key"), f"answers this question and was deferred ({d.get('reason') or 'below the cap'})")
             for d in manifest.get("deferred") or [] if d.get("key") in wanted]
    if thin:
        out.append({"cause": "over_compressed", "sections": [k for k, _ in thin],
                    "rule": "; ".join(f"{k} {why}" for k, why in thin)})

    order = {c: i for i, c in enumerate(CAUSE_CHAIN)}
    return sorted(out, key=lambda s: order[s["cause"]])


def not_judged(manifest: dict, *, answered_at: str = "") -> list[dict]:
    """The manifest-signalled causes that could NOT be judged for this compile, with why: [{cause, why}], plus
    `partial: True` when the cause was judged for some sections and not others.

    Absence is an answer only when it is stated. Few compiles declare an as-of date for their sections (4 in 400
    on laz, 2026-11-10), so "stale did not fire" usually means "there was nothing to judge staleness by", and the
    panel must say so rather than imply the evidence was fresh. The same holds per section: when only some sections
    are dated, "N of M sections have no as-of date" says which ages were never measured.
    """
    declared = manifest.get("declared") or {}
    if _parse(answered_at) is None:
        return [{"cause": "stale", "why": "the answer's time is not known, so no age can be measured"}]
    dated = section_as_ofs(manifest, at=answered_at)
    if not dated:
        return [{"cause": "stale", "why": "no packed section declares an as-of date"}]
    # Every evidence section the compile names, packed or declared, in the order it names them. The instructions
    # section is always packed and is not evidence: it has no age to measure, so counting it would make every
    # compile report one undated section that no fix could ever date.
    sections = list(dict.fromkeys([p.get("key") for p in manifest.get("packed") or [] if is_evidence(p)]
                                  + list(declared)))
    unreadable = sorted(k for k, v in dated.items() if _parse(v) is None)
    undated = sorted(k for k in sections if k not in dated)
    whys = []
    if unreadable:
        whys.append("the as-of date of " + ", ".join(unreadable) + " cannot be read (not an ISO time with a zone), so "
                    + ("it was" if len(unreadable) == 1 else "they were") + " not judged")
    if undated:
        whys.append(f"{len(undated)} of {len(sections)} sections have no as-of date (" + ", ".join(undated) + "), so "
                    + ("its age was" if len(undated) == 1 else "their ages were") + " not judged")
    if not whys:
        return []
    out: dict = {"cause": "stale", "why": "; ".join(whys)}
    if len(dated) > len(unreadable):
        out["partial"] = True  # some sections were judged: "signals no stale section" is true of those
    return [out]


def catalog_analyses_for(manifest: dict, entity_type: str = "") -> list[str]:
    """The analyses the question catalog names for the question the compile matched (`coverage.question`)."""
    question = (manifest.get("coverage") or {}).get("question") or ""
    if not question:
        return []
    try:
        from resource_explorer.surveyors.question_catalog_reader import find_question

        entry = find_question(question, entity_type or manifest.get("resource_type") or None) or {}
    except Exception as exc:  # noqa: BLE001 -- a catalog that cannot be read costs the deferred charge, not the panel
        logger.warning("question catalog unreadable for diagnosis: %s", exc)
        return []
    return list((entry.get("answering") or {}).get("analysis_ids") or [])


def diagnosis(item: dict, registry: Any) -> dict[str, Any]:
    """The diagnosis panel for one answer item: its stored compile, the suggestions, and the charge set.

    An answer with no compile (a RAG or agent turn, or a compile no longer stored) says which, and has nothing to
    suggest and no charge set: it is diagnosed by reading the answer alone.
    """
    compile_id = (item.get("compile_id") or "").strip()
    empty = {"compile": None, "suggestions": [], "not_judged": [], "charge_set": []}
    if not compile_id:
        return {**empty, "no_compile": "This answer was not built from a compile (a RAG or agent turn), so there "
                                       "is no stored evidence to show and nothing to suggest."}
    row = registry.get_compile(compile_id)
    if row is None:
        return {**empty, "no_compile": f"Compile {compile_id} is no longer stored, so its evidence cannot be shown."}
    manifest = dict(row.get("manifest") or {})
    text = manifest.pop("text", "")
    catalog = catalog_analyses_for(manifest, item.get("entity_type") or "")
    answered_at = item.get("created_at") or row.get("created_at") or ""
    return {
        "compile": {"compile_id": compile_id, "question": row.get("question") or "",
                    "slug": row.get("project_slug") or "", "created_at": row.get("created_at") or "",
                    "manifest": manifest, "text": text},
        # The vote's time stands in for the answer's: a compile row is shared by every repeat of the same compile
        # (`hits`), so its created_at can be weeks before the answer that was rated.
        "suggestions": suggestions(manifest, answered_at=answered_at, catalog_analyses=catalog),
        "not_judged": not_judged(manifest, answered_at=answered_at),
        "charge_set": charge_set(manifest, catalog),
        "catalog_analyses": catalog,
    }

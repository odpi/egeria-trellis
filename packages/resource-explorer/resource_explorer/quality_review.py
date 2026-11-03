"""The review of proposed quality checks on a database's Curate page (DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §10).

Reads the latest `database_quality_proposals` batch and the latest verdict per `check_key`, and says for each check one
of four things, always from persisted rows:

    proposed         no verdict yet: a person has not decided
    accepted         the latest verdict row says accepted
    rejected         the latest verdict row says rejected
    retired          the latest verdict row says retired
    not_established  the selection rule could not be evaluated; NO verdict can be recorded (the reason is shown)

A verdict is a new row every time (append-only; the latest wins), recorded as the signed-in person. It is local: no
Egeria call here, Publish is the verb that writes rules. There is no bulk verdict: the design speaks of accepting,
editing and rejecting one check at a time, so this module records one `check_key` per call.

Each table group also says whether the table is opted into Table Comparison (the baseline-match checks, B1a), and
`set_table_comparison` records a person's opt-in or opt-out from the table's card
(DESIGN-QUALITY-B1A-BASELINE-MATCH.md §3). The next propose run reads it.
"""
from __future__ import annotations

from resource_explorer.quality_words import (
    name_of_test_type,
    parameter_items,
    reason_in_words,
    attribution_of,
)
from resource_explorer.surveyors.database.quality import table_comparison
from resource_explorer.surveyors.database.quality.catalogue import (
    PORTED_TEST_TYPES,
    egeria_dimension,
    load_catalogue,
)

PROPOSALS_TABLE = "database_quality_proposals"


def table_comparison_types() -> list[str]:
    """The ported Table Comparison types an opt-in brings in (B1a and, since profile 1b, B1b), in proposal order."""
    catalogue = load_catalogue()
    return [name for name in PORTED_TEST_TYPES if catalogue[name].is_table_comparison]

#: The two verdicts a person gives here. `retired` belongs to the retire flow, not to this review.
PERSON_VERDICTS = ("accepted", "rejected")
#: The states a check shows, in the order the filter lists them.
STATES = ("proposed", "accepted", "rejected", "retired", "not_established")


#: Why an Accept is refused on a check whose latest proposal carries no parameters: an accept records the proposal's own
#: parameters, and none means this profile did not select the check or could not evaluate it. Reject needs no parameters.
CANNOT_ACCEPT_SENTENCE = ("this profile could not evaluate the check, so there are no parameters to accept; "
                          "accept after a profile that measures it")


def _accept_blocked(proposal: dict) -> str:
    params = proposal.get("params_json")
    return "" if isinstance(params, dict) and params else CANNOT_ACCEPT_SENTENCE


class ReviewError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _dimension_of(test_type: str, catalogue: dict) -> str:
    entry = catalogue.get(test_type)
    return egeria_dimension(entry.dimension) if entry is not None else ""


def _status(proposal: dict, verdict: dict | None) -> str:
    """The word a check shows. A recorded verdict is what a person decided; without one the proposal's own state
    decides (not established stays not established: it cannot be decided)."""
    if verdict and verdict.get("verdict") in ("accepted", "rejected", "retired"):
        return verdict["verdict"]
    return "not_established" if proposal.get("state") == "not_established" else "proposed"


def _row(proposal: dict, verdict: dict | None, catalogue: dict) -> dict:
    status = _status(proposal, verdict)
    return {
        "id": proposal["id"],
        "check_key": proposal["check_key"],
        "schema_name": proposal["schema_name"],
        "table_name": proposal["table_name"],
        "column_name": proposal.get("column_name") or "",
        "test_type": proposal["test_type"],
        # TestGen's own short name and description; "" when the catalogue has no such type (the identifier is shown)
        "test_name": name_of_test_type(catalogue.get(proposal["test_type"])),
        "test_description": getattr(catalogue.get(proposal["test_type"]), "description", "") or "",
        # where the check comes from, said only in this detailed description (never a headline or a row word)
        "attribution": attribution_of(catalogue.get(proposal["test_type"]), proposal.get("origin") or "",
                                           proposal.get("catalogue_ref") or ""),
        "dimension": _dimension_of(proposal["test_type"], catalogue),
        "origin": proposal.get("origin") or "",
        "general_type": proposal.get("general_type") or "",
        "params": proposal.get("params_json") if isinstance(proposal.get("params_json"), dict) else {},
        # the parameters that have a value, in words (None is "not measured", so it is not listed)
        "param_items": parameter_items(catalogue.get(proposal["test_type"]),
                                       proposal.get("params_json") if isinstance(proposal.get("params_json"), dict) else {}),
        "profile_surveyed_at": proposal.get("profile_surveyed_at") or "",
        "proposal_state": proposal.get("state") or "",
        "reason": proposal.get("reason") or "",
        "reason_words": reason_in_words(proposal.get("reason") or ""),
        "status": status,
        # a not-established check has no selector; every other one can be decided (again)
        "selectable": status != "not_established",
        # Accept needs the proposal's own parameters; without them Accept is shown disabled with this sentence and
        # Reject still works. The previous verdict stays in `verdict`/`status`.
        "accept_blocked": "" if status == "not_established" else _accept_blocked(proposal),
        "verdict": ({"id": verdict.get("id"), "verdict": verdict.get("verdict"), "decided_by": verdict.get("decided_by"),
                     "created_at": verdict.get("created_at"), "note": verdict.get("note") or ""}
                    if verdict else None),
    }


def _matches(row: dict, *, test_type: str, dimension: str, state: str) -> bool:
    return ((not test_type or row["test_type"] == test_type)
            and (not dimension or row["dimension"] == dimension)
            and (not state or row["status"] == state))


def _zero() -> dict:
    return {"total": 0, **{s: 0 for s in STATES}}


def build_review(registry, slug: str, *, test_type: str = "", dimension: str = "", state: str = "",
                 schema: str = "", table: str = "") -> dict:
    """The proposals of one database, grouped by table.

    Without `table`: every group with its counts (under the filters) and NO rows, so 233 tables stay cheap to
    draw. With `schema` and `table`: that one group, with its rows. `filters` always counts the whole batch (not
    under the filters) so a filter never hides the options that would undo it."""
    if state and state not in STATES:
        raise ReviewError(400, f"state must be one of {', '.join(STATES)}, got {state!r}")
    proposals = registry.query_detail_rows(PROPOSALS_TABLE, slug)
    verdicts = registry.get_quality_verdicts(slug)
    catalogue = load_catalogue()
    watched = table_comparison.watched_tables(registry, slug)
    rows = [_row(p, verdicts.get(p["check_key"]), catalogue) for p in proposals]

    overall, test_types, dimensions = _zero(), {}, {}
    for r in rows:
        overall["total"] += 1
        overall[r["status"]] += 1
        test_types[r["test_type"]] = test_types.get(r["test_type"], 0) + 1
        dimensions[r["dimension"]] = dimensions.get(r["dimension"], 0) + 1

    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["schema_name"], r["table_name"])
        g = groups.setdefault(key, {"schema_name": key[0], "table_name": key[1], "counts": _zero(), "rows": [],
                                    "table_comparison": _watch_state(watched.get(key))})
        if _matches(r, test_type=test_type, dimension=dimension, state=state):
            g["counts"]["total"] += 1
            g["counts"][r["status"]] += 1
            g["rows"].append(r)

    wanted = (schema, table)
    if table:
        out = [g for k, g in groups.items() if k == wanted]
        if not out:
            raise ReviewError(404, f"no proposals for {schema}.{table} in the latest batch")
    else:
        out = [{**g, "rows": None} for g in groups.values() if g["counts"]["total"]]
    out.sort(key=lambda g: (g["schema_name"], g["table_name"]))
    shown = _zero()
    for g in out:
        for k, v in g["counts"].items():
            shown[k] += v
    return {
        "slug": slug,
        "surveyed_at": proposals[0]["surveyed_at"] if proposals else "",
        "profile_surveyed_at": proposals[0].get("profile_surveyed_at", "") if proposals else "",
        "has_proposals": bool(proposals),
        "tables": len(groups),
        "counts": overall,
        "shown": shown,
        "filters": {
            "test_types": [{"value": k, "count": v, "label": name_of_test_type(catalogue.get(k)) or k}
                           for k, v in sorted(test_types.items())],
            "dimensions": [{"value": k, "count": v} for k, v in sorted(dimensions.items())],
            "states": [{"value": s, "count": overall[s]} for s in STATES],
        },
        "applied": {"test_type": test_type, "dimension": dimension, "state": state},
        "table_comparison": {"test_types": table_comparison_types(), "tables_opted_in": len(watched)},
        "groups": out,
    }


def _watch_state(entry: dict | None) -> dict:
    """A table's Table Comparison opt-in as a group shows it: watched or not, and by whom and when if it is."""
    if not entry:
        return {"watched": False}
    return {"watched": True, "by": entry["by"], "at": entry["at"], "note": entry.get("note") or ""}


def set_table_comparison(registry, slug: str, schema: str, table: str, watch: bool, *, decided_by: str,
                         note: str = "") -> dict:
    """One person's opt-in (or opt-out) of one table for Table Comparison, then read back. `recorded` is true only when
    the table's latest entry, read again, is the one just written. The checks appear at the next propose run."""
    if not decided_by:
        raise ReviewError(401, "an opt-in needs a person to attribute it to")
    try:
        written = table_comparison.set_watch(registry, slug, schema, table, watch, by=decided_by, note=note)
    except ValueError as exc:
        raise ReviewError(400, str(exc)) from exc
    trail = table_comparison.history(registry, slug).get((schema, table)) or []
    latest = trail[-1] if trail else {}
    recorded = latest.get("action") == written["action"] and latest.get("at") == written["at"] \
        and latest.get("by") == decided_by
    state = _watch_state(latest if latest.get("action") == table_comparison.WATCH else None)
    return {"recorded": recorded, "schema_name": schema, "table_name": table, "table_comparison": state,
            "test_types": table_comparison_types(),
            "takes_effect": "at the next propose run (propose_quality_checks)"}


def record_verdict(registry, slug: str, check_key: str, verdict: str, *, decided_by: str, note: str = "") -> dict:
    """One person's accept or reject of one proposed check, as a NEW verdict row, then read back.

    The parameters recorded are the proposal's own (read here from the latest batch, never taken from the caller):
    this is accept-as-proposed. Returns the check as `build_review` would show it plus `recorded`, true only when
    the latest verdict row, read again, is the one just written."""
    if verdict not in PERSON_VERDICTS:
        raise ReviewError(400, f"verdict must be one of {', '.join(PERSON_VERDICTS)}, got {verdict!r}")
    if not decided_by:
        raise ReviewError(401, "a verdict needs a person to attribute it to")
    proposal = next((p for p in registry.query_detail_rows(PROPOSALS_TABLE, slug) if p["check_key"] == check_key), None)
    if proposal is None:
        raise ReviewError(404, f"no proposal {check_key!r} in the latest batch for {slug}")
    if proposal.get("state") == "not_established":
        raise ReviewError(409, f"not established, so it cannot be decided: {proposal.get('reason') or 'no reason recorded'}")
    if verdict == "accepted" and _accept_blocked(proposal):
        raise ReviewError(409, CANNOT_ACCEPT_SENTENCE)
    params = proposal.get("params_json") if isinstance(proposal.get("params_json"), dict) else {}
    written = registry.record_quality_verdict(slug, check_key, verdict, params, proposal_id=proposal["id"],
                                              note=note, decided_by=decided_by)
    latest = registry.get_quality_verdicts(slug).get(check_key) or {}
    recorded = (latest.get("verdict") == verdict and latest.get("decided_by") == decided_by
                and latest.get("created_at") == written["created_at"])
    return {"recorded": recorded, "check": _row(proposal, latest or None, load_catalogue())}

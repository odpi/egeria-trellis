"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

From the newest column survey's keys (`inputs.ods.table_keys`), per table:

- **foreign keys out**: the distinct OTHER tables this table's foreign keys reference. Established only when this
  table's keys were captured (a native survey leaves them unstated, and then "no foreign keys" is not known);
- **foreign keys in**: the distinct other stored tables that reference this one. Exact when EVERY table's keys were
  captured. When some were not, the count from the captured keys is a LOWER BOUND (uncaptured tables can only add
  references), reported as "at least N" with "Referenced By Is Lower Bound": a 0 then says nothing and is left
  unestablished.

Then the second measure, `hub_table_proposal`: each table referenced by at least the manifest's
`hub_min_referencing` parameter of other tables
is proposed as a "hub table" in a ClassificationAnnotation. A table whose lower bound reaches the threshold is
proposed, saying so; a table below it while keys are missing stays not established, never "not a hub". A proposal on the report, never a classification on the
element (owner, 2026-10-15); Curate is where a proposal becomes a fact. Self-references are not counted. A view has no
keys of its own and is not applicable.
"""
from __future__ import annotations

#: The second measure: the hub proposal, published as a ClassificationAnnotation.
PROPOSAL = "hub_table_proposal"
#: The classification proposed.
HUB = "hub table"
#: The manifest parameter naming the threshold: a hub is referenced by at least this many other tables. The value is
#: declared in the manifest (and so in the published declaration) and handed to the code as `inputs.parameters`.
THRESHOLD = "hub_min_referencing"


def _threshold(inputs) -> int:
    value = (getattr(inputs, "parameters", None) or {}).get(THRESHOLD)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"the manifest declares no usable '{THRESHOLD}' parameter (got {value!r}); "
                         "hub_tables needs a whole number of at least 1")
    return value

CAVEAT = ("foreign keys as the column survey stored them; a degree counts distinct other tables, self-references "
          "not counted; 'foreign keys in' counts only stored tables, exact when every table's keys were captured and "
          "otherwise a lower bound (at least N); a hub is a proposal, never a classification on the table")


def _is_view(t: dict) -> bool:
    return "VIEW" in str(t.get("table_type") or "").upper()


def _absent(scope: str, reason: str, words: str, measure: str = "") -> dict:
    out = {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}
    if measure:
        out["measure"] = measure
    return out


def _names(keys) -> str:
    names = sorted(f"{s}.{t}" for s, t in keys)
    return ", ".join(names[:20]) + (f" (and {len(names) - 20} more)" if len(names) > 20 else "")


def run(inputs) -> list[dict]:
    slug = inputs.slug
    hub_min = _threshold(inputs)
    snap = inputs.ods.table_keys(slug)
    if snap is None:
        why = "no survey has stored this database's columns yet, so no keys were read"
        return [_absent(slug, why, "no columns stored"), _absent(slug, why, "no columns stored", PROPOSAL)]
    as_of = snap["surveyed_at"]
    views = [t for t in snap["tables"] if _is_view(t)]
    tables = [t for t in snap["tables"] if not _is_view(t)]
    captured = [t for t in tables if t["keys_captured"]]
    if not captured:
        why = (f"no keys captured for any of {len(tables)} tables in the column survey of {as_of}; hubs cannot be "
               "derived")
        return [_absent(slug, why, "no keys captured; hubs cannot be derived"),
                _absent(slug, why, "no keys captured; hubs cannot be derived", PROPOSAL)]

    keys = {(t["schema_name"], t["table_name"]) for t in tables}
    every = len(captured) == len(tables)
    referencing: dict[tuple[str, str], set] = {k: set() for k in keys}
    referenced: dict[tuple[str, str], set] = {}
    for t in tables:
        me = (t["schema_name"], t["table_name"])
        targets = {(fk["to_schema"], fk["to_table"]) for fk in t["foreign_keys"]} - {me}
        referenced[me] = targets
        if t["keys_captured"]:
            for dst in targets & keys:
                referencing[dst].add(me)
    uncaptured = len(tables) - len(captured)
    # With every table's keys captured the count is exact. With some missing it is a LOWER BOUND: uncaptured tables
    # can only add references, never remove one, so "at least N" is true and a 0 says nothing.
    missing = f"keys not captured for {uncaptured} of {len(tables)} tables" if not every else ""
    in_unknown = f"referenced-by not established: {missing}" if not every else ""
    could = "could reach the threshold through tables whose keys were not captured"

    out: list[dict] = []
    for t in tables:
        me = (t["schema_name"], t["table_name"])
        scope = f"{me[0]}.{me[1]}"
        n_in = len(referencing[me])
        fk_out = len(referenced[me]) if t["keys_captured"] else None
        fk_in = n_in if (every or n_in > 0) else None
        lower = fk_in is not None and not every
        if fk_out is None and fk_in is None:
            out.append(_absent(scope, f"keys not captured for this table, and {in_unknown}",
                               "keys not captured for this table"))
            continue
        value = {"Foreign Keys Out": fk_out, "Foreign Keys In": fk_in,
                 "Referenced Tables": _names(referenced[me]) if fk_out else None,
                 "Referencing Tables": _names(referencing[me]) if fk_in else None,
                 "Referenced By Is Lower Bound": "true" if lower else None,
                 "asOf": as_of, "basis": CAVEAT}
        bits = []
        bits.append(f"references {fk_out} other table{'s' if fk_out != 1 else ''}" if fk_out is not None
                    else "references: not established, keys not captured for this table")
        if lower:
            bits.append(f"referenced by at least {fk_in} other table{'s' if fk_in != 1 else ''}"
                        + (" · hub (lower bound)" if fk_in >= hub_min else f" · {could}"))
        else:
            bits.append(f"referenced by {fk_in} other table{'s' if fk_in != 1 else ''}" if fk_in is not None
                        else in_unknown)
        established = [v for v in (fk_out, fk_in) if v is not None]
        if lower:
            reason = f"referenced by at least {fk_in}; {missing}"
        elif fk_in is not None and fk_out is not None:
            reason = ""
        else:
            reason = in_unknown if fk_in is None else "keys not captured for this table"
        out.append({"scope": scope, "state": "measured" if any(established) else "nothing_found", "value": value,
                    "reason": reason, "summary": f"{'; '.join(bits)} (as of {as_of})"})
    for v in views:
        out.append({"scope": f"{v['schema_name']}.{v['table_name']}", "state": "not_applicable",
                    "reason": "a view has no keys of its own", "summary": "not applicable · a view has no keys"})

    hubs = sorted((k for k in keys if len(referencing[k]) >= hub_min),
                  key=lambda k: (-len(referencing[k]), k))
    if not every:
        # A hub proposed from a lower bound is true: the real count is at least that. What cannot be said is that
        # any other table is NOT a hub, so the verdict for the rest stays not established, never a "no".
        for k in hubs:
            n = len(referencing[k])
            out.append({"scope": f"{k[0]}.{k[1]}", "measure": PROPOSAL, "state": "measured",
                        "candidate_classifications": [HUB],
                        "value": {"Foreign Keys In": n, "Foreign Keys Out": len(referenced[k]),
                                  "Referenced By Is Lower Bound": "true",
                                  "Hub Threshold": hub_min, "Referencing Tables": _names(referencing[k]),
                                  "asOf": as_of, "basis": CAVEAT},
                        "summary": f"proposed: {HUB} · referenced by at least {n} other tables, a lower bound; "
                                   f"{missing} (as of {as_of})"})
        rest = len(keys) - len(hubs)
        out.append(_absent(slug, f"{in_unknown}; the {rest} other tables are below the threshold on the keys "
                                 "captured and could still reach it, so none is called not a hub",
                           (f"{len(hubs)} proposed from a lower bound; " if hubs else "")
                           + f"{rest} not established, keys not captured for {uncaptured} tables", PROPOSAL))
        return out
    if not hubs:
        out.append({"scope": slug, "measure": PROPOSAL, "state": "nothing_found",
                    "value": {"Hub Threshold": hub_min, "asOf": as_of, "basis": CAVEAT},
                    "summary": f"no table is referenced by {hub_min} or more other tables "
                               f"(as of {as_of})"})
        return out
    for k in hubs:
        n = len(referencing[k])
        out.append({"scope": f"{k[0]}.{k[1]}", "measure": PROPOSAL, "state": "measured",
                    "candidate_classifications": [HUB],
                    "value": {"Foreign Keys In": n, "Foreign Keys Out": len(referenced[k]),
                              "Hub Threshold": hub_min, "Referencing Tables": _names(referencing[k]),
                              "asOf": as_of, "basis": CAVEAT},
                    "summary": f"proposed: {HUB} · referenced by {n} other tables (as of {as_of})"})
    return out

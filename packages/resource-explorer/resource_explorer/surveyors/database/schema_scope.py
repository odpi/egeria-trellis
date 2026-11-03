"""Grouping stored database rows by the engine's declared containment level.

REPLY-SCHEMA-AS-SUB-RESOURCE.md shape 1 (§1), with §5's per-engine correction:
a database's structural analyses aggregate at the wrong grain when they read
every visible schema as one flat bag of tables. *"Edge count 0, component count
3"* is correct for three single-table schemas and meaningless as a statement
about the database.

This module is the seam. It holds no domain logic of its own — the checks stay
in `db_derived.py`, the declaration stays in `connection.py` — only:

1. which containers exist in a set of stored rows, with the engine's system
   containers excluded (`containers_in_rows`);
2. the `WHERE`-clause equivalent for already-loaded rows (`rows_in_container`);
3. the rollup envelope every per-container payload carries, so a rollup is
   **labelled as a rollup** and never renders as an average (`rollup_envelope`,
   `undeclared_envelope`);
4. the per-schema reading of the credential-capability probe (§2), shared by
   the analysis readers and by the launcher's capability-shortfall message.

**Nothing here says "schema".** The level's name comes from the engine's
`ContainmentLevel` (`"schema"` for Postgres, `"catalog"` for DuckDB, `"owner"`
for Oracle). The *column* the rows carry is `schema_name` on every structured
database table — that is RE's own storage vocabulary, fixed by
`registry.py`'s DDL, and it is deliberately not re-exported as the level's
name.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from .connection import EngineContainment

#: The column every structured database detail row carries the container in —
#: `database_schemas`, `database_tables`, `database_columns`,
#: `database_column_profiles` and `database_table_activity` all declare
#: `schema_name TEXT NOT NULL` (registry.py's `_DB_FS_DETAIL_TABLE_DDL`).
#: Verified against the DDL rather than assumed: REPLY §1 says the rows carry
#: "`schema`", and the actual column name is `schema_name`.
CONTAINER_COLUMN = "schema_name"

#: `aggregation.reason` when the engine has no containment declaration, so a
#: reader can tell "this engine's hierarchy is not declared" from "this engine
#: has one flat namespace" — the two look identical in any per-container output
#: that simply comes back empty.
REASON_UNDECLARED = "containment_not_declared"


def container_of(row: dict) -> str:
    return row.get(CONTAINER_COLUMN) or ""


def containers_in_rows(
    containment: EngineContainment,
    *row_sets: Iterable[dict],
) -> list[str]:
    """Every non-system container named by any of these rows, sorted.

    Takes several row sets because a container can legitimately appear in one
    table and not another (a schema with rows in `database_schemas` but no
    tables yet is still a container, and a schema whose tables were recovered
    by the catalog-only fallback may have tables and no `database_schemas`
    row).
    """
    names: set[str] = set()
    for rows in row_sets:
        for row in rows or ():
            name = container_of(row)
            if name and not containment.is_system_container(name):
                names.add(name)
    return sorted(names)


def system_containers_in_rows(
    containment: EngineContainment,
    *row_sets: Iterable[dict],
) -> list[str]:
    """The system containers these rows DO carry, sorted.

    Reported rather than silently dropped: excluding `pg_catalog` is right, and
    a reader who sees a table count change should be able to see which
    containers were left out and why. Normally empty — the collection SQL
    already filters them — so a non-empty list is itself a finding about the
    survey that wrote the rows.
    """
    names: set[str] = set()
    for rows in row_sets:
        for row in rows or ():
            name = container_of(row)
            if name and containment.is_system_container(name):
                names.add(name)
    return sorted(names)


def rows_in_container(rows: Iterable[dict], container: str) -> list[dict]:
    """The `WHERE schema_name = ?` of REPLY §1, applied to loaded rows.

    REPLY §1: *"the structured tables from stream 3 carry `schema` on every
    row, so a schema scope is a `WHERE` clause, not new plumbing."* It holds —
    with the column named `schema_name`. Filtering in Python rather than in SQL
    because `db_derived` loads one snapshot's rows once and scopes them N ways;
    N queries would re-read the same rows N times for no gain.
    """
    return [row for row in (rows or ()) if container_of(row) == container]


def rollup_envelope(
    containment: EngineContainment,
    *,
    rollup_kind: str,
    containers: Sequence[str],
    explanation: str,
    excluded_system_containers: Sequence[str] = (),
    **extra: Any,
) -> dict:
    """The envelope every per-container payload carries.

    `is_rollup`/`averaged` are explicit fields rather than something a reader
    infers, because the whole failure this closes is a rollup that *reads* as a
    single measured verdict. A consumer that cannot yet render a breakdown must
    at least be able to see that what it is showing is a rollup over N
    containers — and `averaged: False` is a claim this code can be held to:
    every rollup below is a set, a list or a total, never a mean.

    Every rollup explanation opens with the count it rolled up — "Across N
    schemas: …" — rather than leaving the reader to notice `container_count`
    is a separate field from the sentence. REPLY-COPY-REVIEW-CREDENTIAL-AND-
    FIT-LANGUAGE.md §6(b) calls this "one rule that costs nothing": both
    `container_count` and the containment level's name are already on hand
    here, so every one of this function's callers gets the prefix for free
    instead of writing it into each `explanation` string separately, which is
    exactly the sort of thing one call site would eventually forget. That is
    the failure #266 was built to prevent — "a rollup that reads as a single
    measured verdict" — held in text as well as in the `is_rollup` field.
    """
    grain = containment.aggregation_grain
    grain_name = grain.name if grain else "container"
    count = len(containers)
    plural_grain = grain_name if count == 1 else f"{grain_name}s"
    full_explanation = f"Across {count} {plural_grain}: {explanation}"
    envelope = {
        "is_rollup": True,
        "averaged": False,
        "grain": grain.name if grain else None,
        "grain_level": grain.name if grain else None,
        "engine": containment.engine,
        "rollup_kind": rollup_kind,
        "container_count": count,
        "containers": list(containers),
        "explanation": full_explanation,
    }
    if excluded_system_containers:
        envelope["excluded_system_containers"] = list(excluded_system_containers)
        envelope["explanation"] = (
            f"{full_explanation} Excluded {containment.engine}'s own system "
            f"containers: {', '.join(excluded_system_containers)}."
        )
    envelope.update(extra)
    return envelope


def undeclared_envelope(containment: EngineContainment) -> dict:
    """The rollup envelope for an engine with no containment declaration.

    Deliberately NOT an empty `by_<level>` map: an empty breakdown reads as
    "measured, and there are no containers". This says the hierarchy was never
    declared, which is why there is no breakdown — the absence-as-answer
    distinction this codebase is built around, applied to the grain itself.

    The declaration lives in `connection.py` (only PostgreSQL declares a
    containment hierarchy today; REPLY-SCHEMA-AS-SUB-RESOURCE.md §5 is why
    engine-by-engine is the plan) — that provenance is for whoever maintains
    this code, not for the sentence a user reads, so it stays in this
    docstring rather than in `explanation` (REPLY-COPY-REVIEW-CREDENTIAL-AND-
    FIT-LANGUAGE.md §6(a) / §0's "provenance belongs in the evidence, not in
    the sentence").
    """
    return {
        "is_rollup": False,
        "averaged": False,
        "grain": None,
        "grain_level": None,
        "engine": containment.engine,
        "reason": REASON_UNDECLARED,
        "explanation": (
            f"Resource Explorer doesn't yet know how {containment.engine} "
            "groups its tables, so this result covers the whole database "
            "with no per-schema breakdown. That is an undeclared hierarchy, "
            "not a finding that the database has one flat namespace."
        ),
    }


# ── credential capability, per container (REPLY §2) ─────────────────────────
#
# §2: *"'measured within credential scope' becomes a per-schema state — a
# schema with `USAGE` and no `SELECT` renders as 'structure only' rather than
# being silently counted; the launcher's 'needs read on N of M tables' lists
# them by schema... 'is 3 of 8 schemas readable; 3 of 26 tables' instead of '3
# tables'."*

#: Full `USAGE` and `SELECT` on every table the catalog shows in the container.
SCOPE_READABLE = "readable"
#: `USAGE` but `SELECT` on nothing — the `coco_ods` incident. Structure is
#: visible (names, types, constraints via the catalog), data is not. The state
#: that must not be folded into a database-wide fraction.
SCOPE_STRUCTURE_ONLY = "structure_only"
#: `USAGE` and `SELECT` on some but not all tables.
SCOPE_PARTIALLY_READABLE = "partially_readable"
#: No `USAGE`. Nothing in the container is reachable at all.
SCOPE_NOT_VISIBLE = "not_visible"
#: `USAGE`, and the catalog shows no tables. A measured negative, not a gap.
SCOPE_EMPTY = "empty"

#: Which states are a shortfall for the launcher's message and for the rollup's
#: "N of M readable" count. `SCOPE_EMPTY` is NOT one: a schema with no tables
#: needs no grant.
SHORTFALL_STATES = (
    SCOPE_STRUCTURE_ONLY, SCOPE_PARTIALLY_READABLE, SCOPE_NOT_VISIBLE,
)


def container_scope_states(credential_capability: dict | None) -> dict[str, dict]:
    """Per-container credential state, from the probe's own `by_schema` map.

    Reads `connection.get_credential_capability()`'s stored result (#253,
    extended by #257) — `{"by_schema": {name: {usage_granted, table_total,
    table_select}}}` — and turns each entry into one of the five states above.
    Opens nothing; the probe already did the work, and the per-schema unit was
    always there (`USAGE` is per schema, `SELECT` is per table within it).
    Returns `{}` when there is no probe: nothing to say, not "everything is
    readable".
    """
    by_schema = (credential_capability or {}).get("by_schema") or {}
    states: dict[str, dict] = {}
    for name, entry in by_schema.items():
        usage = bool((entry or {}).get("usage_granted"))
        total = int((entry or {}).get("table_total") or 0)
        selected = int((entry or {}).get("table_select") or 0)
        if not usage:
            state, explanation = SCOPE_NOT_VISIBLE, (
                f"No USAGE on {name}: nothing in it is reachable by this "
                f"credential. The catalog shows {total} table(s), so the blind "
                f"spot has a known size."
            )
        elif total == 0:
            state, explanation = SCOPE_EMPTY, (
                f"USAGE on {name}, and the catalog shows no tables in it. "
                f"Measured, and empty — not a visibility gap."
            )
        elif selected == 0:
            state, explanation = SCOPE_STRUCTURE_ONLY, (
                f"USAGE on {name} but SELECT on none of its {total} table(s): "
                f"structure only. Names, types and constraints are readable "
                f"from the catalog; no value in it has been or can be read."
            )
        elif selected < total:
            state, explanation = SCOPE_PARTIALLY_READABLE, (
                f"SELECT on {selected} of {name}'s {total} table(s); the "
                f"other {total - selected} are structure only."
            )
        else:
            state, explanation = SCOPE_READABLE, (
                f"SELECT on all {total} of {name}'s table(s)."
            )
        states[name] = {
            "state": state,
            "usage_granted": usage,
            "table_total": total,
            "table_select": selected,
            "explanation": explanation,
        }
    return states


def credential_shortfall(
    credential_capability: dict | None,
    containment: EngineContainment | None = None,
) -> dict | None:
    """REPLY §2's legible numbers: which containers are short, and by how much.

    Returns None when there is no probe (nothing to say) or when every
    container is fully readable (nothing to caveat) — both are "stay silent",
    the same contract `_credential_scope_status` already follows.

    `phrase` is §2's own example wording, *"3 of 8 schemas readable; 3 of 26
    tables"* — the schema clause FIRST, because that is what a database owner
    grants on. `short_containers` is the list the launcher's message needs, in
    worst-first order (no visibility before structure-only before partial), so
    a message that can only name a few names the ones that matter.
    """
    states = container_scope_states(credential_capability)
    if not states:
        return None
    cap = credential_capability or {}
    # `relation_total`/`relation_select` (renamed from `table_total`/
    # `table_select`, REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.3): the
    # whole-database, all-relation-kinds figure from `connection.py`'s
    # `get_credential_capability()` -- NOT the same field name as this
    # function's own per-schema `states[name]["table_total"]` below, which
    # stays named `table_total` since it is legitimately schema-scoped.
    # `cap` can be a `credential_capability` blob stored under a survey
    # from before this rename, so the old key is a fallback, not dropped.
    relation_total = int(cap.get("relation_total", cap.get("table_total")) or 0)
    relation_select = int(cap.get("relation_select", cap.get("table_select")) or 0)
    readable = [n for n, s in states.items() if s["state"] == SCOPE_READABLE]
    empty = [n for n, s in states.items() if s["state"] == SCOPE_EMPTY]
    order = {SCOPE_NOT_VISIBLE: 0, SCOPE_STRUCTURE_ONLY: 1, SCOPE_PARTIALLY_READABLE: 2}
    short = sorted(
        (n for n, s in states.items() if s["state"] in SHORTFALL_STATES),
        key=lambda n: (order[states[n]["state"]], n),
    )
    if not short:
        return None
    # `empty` counts as reachable for the readable fraction: a schema with no
    # tables is not withholding anything. Counted separately as well, so the
    # fraction can be disputed.
    reachable = len(readable) + len(empty)
    # "Visible" (USAGE granted: the header's "sees N of M") and "readable" (every table SELECTable) count
    # different things. A schema with USAGE and SELECT on only some tables is visible, not readable.
    visible = sum(1 for s in states.values() if s["usage_granted"])
    # The level's name comes from the engine's declaration, never a literal:
    # the same probe shape against Oracle would be reporting on *owners*.
    # "container" is the honest generic when no engine was passed — a caller
    # that wants the engine's own word supplies the declaration.
    grain = containment.aggregation_grain if containment else None
    level = grain.name if grain else "container"
    return {
        "container_level": level,
        "container_total": len(states),
        "containers_readable": reachable,
        "containers_visible": visible,
        "containers_empty": len(empty),
        "short_containers": short,
        "by_container": {n: states[n] for n in sorted(states)},
        "relation_total": relation_total,
        "relation_select": relation_select,
        "phrase": (
            f"{reachable} of {len(states)} {level}s readable"
            + (f" ({visible} visible)" if visible != reachable else "")
            + "; "
            f"{relation_select} of {relation_total} relation(s)"
        ),
        "short_phrase": (
            f"short on {len(short)} {level}(s): " + ", ".join(
                f"{n} ({states[n]['state']})" for n in short
            )
        ),
    }

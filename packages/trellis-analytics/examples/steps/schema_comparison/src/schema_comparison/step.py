"""The code: `run(inputs) -> results`. It owns the comparison and its reasons, and nothing else.

A set step (Part C): `inputs.members` is the work list's databases in the list's order, member 1 being "database 1".
From each member's newest stored column survey (`inputs.ods.schema_inventory`), never a live read:

- **tables** are compared by name (`schema.table`). With T1, T2 the two sides' tables and U = T1 ∪ T2, the degree is
  `schema_similarity` = |T1 ∩ T2| of |U| tables ("44 of 45 tables match (98%)"), and the relation is the strongest
  the counts support (design §3a): identical (same tables and every column matching), superset / subset (one side's
  tables all on the other, which has more), overlapping, or disjoint;
- **columns** are compared inside the tables both sides hold, by name and type (`data_type`, and `column_size` where
  both sides state one): `column_similarity` = matching columns of the columns on the union of those tables
  ("311 of 312 columns match"), and every column that differs is named in the breakdown, with how;
- nullability and primary-key differences are named too, as other differences: they do not change the degree, which
  is by name and type, and they keep the relation from being called identical;
- each table falls in exactly one category: identical shape · same name, different shape · only in database 1 ·
  only in database 2, adding up to |U|.

A side with no stored inventory is NOT ESTABLISHED, said for that side, and no relation and no number is claimed:
"different" would be a guess. The percentage is never rounded up to 100% (or down to 0%) when the count is not.
"""
from __future__ import annotations

#: The most names a breakdown field lists before saying how many more.
MAX_NAMES = 20

RELATIONS = ("identical", "superset", "subset", "overlapping", "disjoint", "not_established")


def _pct(n: int, d: int) -> int:
    """n of d as a whole percentage that never reads as all (or none) when it is not."""
    p = round(100 * n / d)
    if n < d:
        p = min(p, 99)
    if n > 0:
        p = max(p, 1)
    return p


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _listed(names: list[str]) -> str:
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (f" (and {len(names) - MAX_NAMES} more)" if len(names) > MAX_NAMES else "")


def _type(c: dict) -> str:
    t = " ".join(str(c.get("data_type") or "").lower().split())
    size = c.get("column_size")
    return f"{t}({size})" if size not in (None, "", 0) else t


def _yes_no(v) -> str:
    return "unstated" if v is None else ("yes" if int(v) else "no")


def _tables(inv: dict) -> dict[str, dict]:
    return {f"{t['schema_name']}.{t['table_name']}": {c["column_name"]: c for c in t.get("columns") or []}
            for t in inv["tables"]}


def compare(inv1: dict, inv2: dict) -> dict:
    """The comparison of two stored inventories, as counts and names (no words): see the module docstring."""
    t1, t2 = _tables(inv1), _tables(inv2)
    common = sorted(set(t1) & set(t2))
    union = set(t1) | set(t2)
    only1, only2 = sorted(set(t1) - set(t2)), sorted(set(t2) - set(t1))
    columns_compared = columns_matching = 0
    differing: list[str] = []
    other: list[str] = []
    identical_shape = 0
    for name in common:
        c1, c2 = t1[name], t2[name]
        shape_differs = False
        for col in sorted(set(c1) | set(c2)):
            columns_compared += 1
            a, b = c1.get(col), c2.get(col)
            if a is None or b is None:
                differing.append(f"{name}.{col}: only in database {1 if b is None else 2}")
                shape_differs = True
                continue
            if _type(a) != _type(b):
                differing.append(f"{name}.{col}: {_type(a) or 'no type'} → {_type(b) or 'no type'}")
                shape_differs = True
                continue
            columns_matching += 1
            for field, words in (("is_nullable", "nullable"), ("is_primary_key", "primary key")):
                x, y = a.get(field), b.get(field)
                if x is not None and y is not None and bool(int(x)) != bool(int(y)):
                    other.append(f"{name}.{col}: {words} {_yes_no(x)} → {_yes_no(y)}")
        identical_shape += not shape_differs
    shared = len(common)
    if not union:
        relation = "identical"
    elif not only1 and not only2:
        relation = "identical" if columns_matching == columns_compared and not other else "overlapping"
    elif not only2:
        relation = "superset"
    elif not only1:
        relation = "subset"
    else:
        relation = "overlapping" if shared else "disjoint"
    return {"relation": relation, "tables_in_common": shared, "tables_on_union": len(union),
            "tables_1": len(t1), "tables_2": len(t2), "only1": only1, "only2": only2,
            "columns_compared": columns_compared, "columns_matching": columns_matching,
            "differing": differing, "other": other, "identical_shape": identical_shape,
            "same_name_different_shape": shared - identical_shape}


def headline(cmp: dict, name1: str, name2: str) -> str:
    """The relation and its degree first, then what each side adds: never a yes or a no."""
    n, u = cmp["tables_in_common"], cmp["tables_on_union"]
    degree = f"{n:,} of {_plural(u, 'table')} match" + (f" ({_pct(n, u)}%)" if u else "")
    adds1 = f"{name1} adds {_plural(len(cmp['only1']), 'table')}"
    adds2 = f"{name2} adds {_plural(len(cmp['only2']), 'table')}"
    rel = cmp["relation"]
    if rel == "identical":
        return f"{name1} and {name2} have identical schemas: {degree}"
    if rel == "superset":
        return f"{name1} is a superset of {name2}: {degree}; {adds1}"
    if rel == "subset":
        return f"{name1} is a subset of {name2}: {degree}; {adds2}"
    if rel == "disjoint":
        return f"{name1} and {name2} are disjoint: {degree}; {name1} has {_plural(cmp['tables_1'], 'table')}, " \
               f"{name2} has {_plural(cmp['tables_2'], 'table')}"
    if not cmp["only1"] and not cmp["only2"]:
        if cmp["same_name_different_shape"]:
            return f"{name1} and {name2} overlap: {degree}, and {_plural(cmp['same_name_different_shape'], 'table')} " \
                   "of them differ in shape"
        return f"{name1} and {name2} overlap: {degree} by name and type, and " \
               f"{_plural(len(cmp['other']), 'column')} differ in nullability or key"
    return f"{name1} and {name2} overlap: {degree}; {adds1}; {adds2}"


def sentence(cmp: dict, name1: str, name2: str, as_of1: str, as_of2: str) -> str:
    """The headline, then the first level below it that fails (columns), then what differs, then the as-of."""
    parts = [headline(cmp, name1, name2)]
    cc, cm = cmp["columns_compared"], cmp["columns_matching"]
    if cc:
        parts.append(f"columns: {cm:,} of {_plural(cc, 'column')} match by name and type ({_pct(cm, cc)}%)"
                     + (f" in the {_plural(cmp['tables_in_common'], 'table')} both hold"))
    if cmp["differing"]:
        parts.append(f"differ: {_listed(cmp['differing'])}")
    if cmp["other"]:
        parts.append(f"other differences: {_listed(cmp['other'])}")
    parts.append(f"as of {name1}: {as_of1}, {name2}: {as_of2}")
    return " · ".join(parts)


def _absent(scope: str, state: str, reason: str, summary: str, value: dict | None = None) -> dict:
    out = {"scope": scope, "state": state, "reason": reason, "summary": summary}
    if value:
        out["value"] = value
    return out


def run(inputs) -> list[dict]:
    set_slug = inputs.set_slug
    members = [slug for _, slug in inputs.members]
    if len(members) != 2:
        why = (f"schema_comparison compares exactly two databases; '{set_slug}' has {len(members)} database "
               f"member{'' if len(members) == 1 else 's'}")
        words = "Not applicable: the two members are not both databases." if len(members) < 2 else \
            f"Not applicable: {why}."
        return [_absent(m, "not_applicable", why, words) for m in members] + \
            [_absent(set_slug, "not_applicable", why, words)]
    name1, name2 = members
    names = {"database_1": name1, "database_2": name2}
    # One read per member: the same call on RE's set reader and on a plain OdsReader.
    inventories = {slug: inputs.ods.schema_inventory(slug) for slug in members}
    missing = []
    for slug in members:
        inv = inventories[slug]
        if not inv.get("registered"):
            missing.append((slug, f"{slug} is not registered in RE",
                            f"Not established: {slug} is not registered in RE."))
        elif inv.get("surveyed_at") is None:
            missing.append((slug, f"{slug} has no schema inventory in RE yet",
                            f"Not established: {slug} has no schema inventory in RE yet — survey it, then compare."))
    if missing:
        # No relation and no number: one side was never looked at, so "different" would be a guess.
        why = "; ".join(r for _, r, _ in missing)
        words = " ".join(w for _, _, w in missing)
        out = []
        for slug in members:
            own = next((w for s, _, w in missing if s == slug), None)
            other = name2 if slug == name1 else name1
            out.append(_absent(slug, "not_established", why,
                               own or f"Not established: compared with {other}, which RE cannot compare yet. {words}",
                               {**names, "compared_with": other, "schema_relation": "not_established"}))
        out.append(_absent(set_slug, "not_established", why, words, {**names, "schema_relation": "not_established"}))
        return out

    inv1, inv2 = inventories[name1], inventories[name2]
    cmp = compare(inv1, inv2)
    as_of = min(inv1["surveyed_at"], inv2["surveyed_at"])
    if not cmp["tables_on_union"]:
        words = "Measured: neither database holds a table, so there is no schema to compare."
        return [_absent(s, "nothing_found", "", words, {**names, "asOf": as_of}) for s in (*members, set_slug)]
    value = {
        **names, "schema_relation": cmp["relation"],
        # The degree (the declared measure's value) and its basis: "N of M tables".
        "schema_similarity": cmp["tables_in_common"], "tables_on_union": cmp["tables_on_union"],
        "column_similarity": cmp["columns_matching"], "columns_compared": cmp["columns_compared"],
        "tables_in_common": cmp["tables_in_common"],
        "tables_only_in_1": len(cmp["only1"]), "tables_only_in_2": len(cmp["only2"]),
        "columns_differing": len(cmp["differing"]),
        # The categories: exclusive, adding up to tables_on_union.
        "identical_shape": cmp["identical_shape"], "same_name_different_shape": cmp["same_name_different_shape"],
        "only_in_database_1": len(cmp["only1"]), "only_in_database_2": len(cmp["only2"]),
        # The breakdown, by name.
        "tables_only_in_1_names": _listed(cmp["only1"]) or None,
        "tables_only_in_2_names": _listed(cmp["only2"]) or None,
        "columns_differing_names": _listed(cmp["differing"]) or None,
        "other_differences": _listed(cmp["other"]) or None,
        "as_of_1": inv1["surveyed_at"], "as_of_2": inv2["surveyed_at"], "asOf": as_of,
    }
    words = sentence(cmp, name1, name2, inv1["surveyed_at"], inv2["surveyed_at"])
    out = [{"scope": slug, "state": "measured", "value": {**value, "compared_with": name2 if slug == name1 else name1},
            "summary": words} for slug in members]
    out.append({"scope": set_slug, "state": "measured", "value": value, "summary": words})
    return out

"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

From the latest two walk runs whose per-file rows RE kept (`inputs.ods.filesystem_walk_runs`,
`filesystem_walk_files`): files added, removed and modified (size, modified time or content hash differ), and the
growth in bytes, over the interval between the two walks. One result, for the whole file system; it names no file,
because a result can be published and the paths stay in Resource Explorer.

Never a silent zero:
- no walk, or one walk only: ``not_established``, "a change needs two walks";
- two walks with no interval between them: ``not_established``;
- either walk stopped at a limit (or its limits cannot be read): what both walks saw is still compared, and the
  files added, the files removed and the growth are left out, said as not measured, because a capped walk misses
  files that are there;
``nothing_found`` is a measured "no change": nothing added, removed or modified, and no growth.
"""
from __future__ import annotations

from datetime import datetime, timezone

CAVEAT = ("the per-file rows of the latest two walks Resource Explorer kept, compared on path, size, modified time and "
          "(where both walks hashed the file) its SHA-256; a file renamed reads as one removed and one added")


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _stamp(value) -> str:
    """The page's one time form for words, "MM-DD HH:MM" (W36; fs_activity's W28 copy, as curation_coverage's); the raw
    value when it cannot be read. `fromAsOf`/`toAsOf` in the value stay raw."""
    t = _parse(value)
    return t.strftime("%m-%d %H:%M") if t else str(value)


def _interval(days: float) -> str:
    """"3.5 days", "6.2 hours", "14 minutes": the interval in the unit a person reads it in."""
    if days >= 1:
        return f"{days:,.1f} days"
    if days * 24 >= 1:
        return f"{days * 24:,.1f} hours"
    return f"{max(1, round(days * 1440)):,} minutes"


def _absent(scope: str, reason: str, words: str) -> dict:
    return {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}


def _changed(a: dict, b: dict) -> bool:
    if a.get("size_bytes") != b.get("size_bytes") or (a.get("modified_at") or "") != (b.get("modified_at") or ""):
        return True
    both_hashed = a.get("hash_state") == "hashed" and b.get("hash_state") == "hashed"
    return both_hashed and a.get("content_hash") != b.get("content_hash")


def run(inputs) -> list[dict]:
    slug = inputs.slug
    runs = inputs.ods.filesystem_walk_runs(slug, limit=2)
    if not runs:
        return [_absent(slug, "no walk has kept per-file rows for this file system yet; a change needs two walks",
                        "no walk yet; a change needs two")]
    if len(runs) < 2:
        return [_absent(slug, f"only one walk has kept per-file rows ({_stamp(runs[0]['run_id'])}); a change needs two",
                        "one walk only; a change needs two")]
    new, old = runs
    start, end = _parse(old["run_id"]), _parse(new["run_id"])
    days = (end - start).total_seconds() / 86400.0 if start and end else None
    if not days or days <= 0:
        why = (f"the two walks ({_stamp(old['run_id'])}, {_stamp(new['run_id'])}) have no interval between them"
               if days is not None else f"a walk's time could not be read ({_stamp(old['run_id'])}, {_stamp(new['run_id'])})")
        return [_absent(slug, why, "no interval between the two walks")]

    before = {r["file_path"]: r for r in inputs.ods.filesystem_walk_files(slug, old["run_id"])}
    after = {r["file_path"]: r for r in inputs.ods.filesystem_walk_files(slug, new["run_id"])}
    common = before.keys() & after.keys()
    modified = sum(1 for p in common if _changed(before[p], after[p]))
    unchanged = len(common) - modified
    span = f"over {_interval(days)}, {_stamp(old['run_id'])} to {_stamp(new['run_id'])}"
    base = {"Files Modified": modified, "Files Unchanged": unchanged, "Interval Days": round(days, 4),
            "fromAsOf": old["run_id"], "toAsOf": new["run_id"], "basis": CAVEAT}

    capped = [label for label, r in (("older", old), ("newer", new)) if r.get("capped") != 0]
    if capped:
        cannot = " and ".join(
            f"the {label} walk {'stopped at a limit' if r.get('capped') == 1 else 'has no stored limits to read'}"
            for label, r in (("older", old), ("newer", new)) if r.get("capped") != 0)
        if not common:
            return [_absent(slug, f"{cannot}, and the two walks share no file to compare", "a capped walk, no file "
                                  "both walks saw")]
        return [{"scope": slug, "state": "measured",
                 "value": {**base, "Files Changed": modified, "Files Changed Per Day": round(modified / days, 3),
                           "notMeasured": "files added, files removed and growth: " + cannot},
                 "summary": (f"{modified:,} of the {len(common):,} files both walks saw were modified {span}; files "
                             f"added, removed and growth not measured · {cannot}")}]

    added = after.keys() - before.keys()
    removed = before.keys() - after.keys()
    added_bytes = sum(int(after[p].get("size_bytes") or 0) for p in added)
    removed_bytes = sum(int(before[p].get("size_bytes") or 0) for p in removed)
    growth = int(new.get("bytes") or 0) - int(old.get("bytes") or 0)
    total = len(added) + len(removed) + modified
    value = {**base, "Files Added": len(added), "Files Removed": len(removed), "Files Changed": total,
             "Files Changed Per Day": round(total / days, 3), "Bytes Added": added_bytes, "Bytes Removed": removed_bytes,
             "Bytes Growth": growth}
    if total == 0 and growth == 0:
        return [{"scope": slug, "state": "nothing_found", "value": value,
                 "summary": f"no file added, removed or modified {span}"}]
    sign = "+" if growth >= 0 else "-"
    return [{"scope": slug, "state": "measured", "value": value,
             "summary": (f"{len(added):,} added, {len(removed):,} removed, {modified:,} modified {span}; "
                         f"{sign}{abs(growth):,} bytes")}]

"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

From the newest walk run whose per-file rows RE kept, ages are taken at the WALK's time (its run id), not at the
time this runs, so the same walk always gives the same answer. Two results, each for the whole file system and
naming no file:

- `fs_activity` (the step's own measure): files and bytes by time since last MODIFIED: hot (within `hot_days`),
  warm, and cold (more than `cold_days`);
- `fs_access_activity`: the same by time since last READ, where the walk recorded access times and the mount
  updates them.

Never a silent zero, and "can't tell" said when it is so:
- no walk: ``not_established``;
- a walk that kept no access times (walked before Resource Explorer recorded them): ``not_established``, "access
  times not recorded on this walk";
- no file read after its last modification: ``not_established``, "access times not recorded on this mount" (what a
  mount with access-time updates off shows; a tree nobody read since writing it would look the same, so it cannot
  be told apart and is not called cold);
- Resource Explorer's own walk reads a file to hash it, so a file the PREVIOUS walk hashed, last read at or after
  that walk's start, may show Resource Explorer's read: it is counted apart, never as hot.
"""
from __future__ import annotations

from datetime import datetime, timezone

#: What the S3 walk writes for owner, group and mode (resource_explorer.reach_mechanisms.NOT_REPORTED): the sign that
#: the walk read an object store, as fs_permissions reads it.
NOT_REPORTED = "not reported by this source"
#: The access measure's words for an object store, like fs_permissions' NO_POSIX_WORDS for modes (W46).
NO_ATIME_WORDS = "S3 objects carry no access time; reads cannot be told"

ACCESS = "fs_access_activity"
MODIFY_BASIS = "each file's modified time as the walk's stat read it, aged at the walk's own time"
ACCESS_BASIS = ("each file's access time as the walk's stat read it before the walk opened the file, aged at the "
                "walk's own time; a mount that does not update access times, and Resource Explorer's own earlier "
                "reads, are said rather than counted")


def _n(count: int, noun: str) -> str:
    """A count with its noun in number: "1 file", "2 files"."""
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


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


def _absent(scope: str, reason: str, words: str, measure: str = "") -> dict:
    out = {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}
    return {**out, "measure": measure} if measure else out


def _bands(ages: list[tuple[float, int]], hot: int, cold: int) -> dict:
    """Hot (age <= hot days), warm and cold (age > cold days) files and bytes, for (age in days, bytes) pairs. The keys
    do not carry the day counts (those are `Hot Days` and `Cold Days`), so a changed parameter keeps its fields."""
    out = {"Hot Files": 0, "Hot Bytes": 0, "Warm Files": 0, "Warm Bytes": 0, "Cold Files": 0, "Cold Bytes": 0}
    for age, size in ages:
        band = "Hot" if age <= hot else "Cold" if age > cold else "Warm"
        out[f"{band} Files"] += 1
        out[f"{band} Bytes"] += size
    return out


def _stamp(value) -> str:
    """The page's one time form for words, "MM-DD HH:MM" (W28; as curation_coverage's); the raw value when it cannot
    be read. `asOf` in the value stays raw."""
    t = _parse(value)
    return t.strftime("%m-%d %H:%M") if t else str(value)


def run(inputs) -> list[dict]:
    slug = inputs.slug
    hot = int(inputs.parameters.get("hot_days", 7))
    cold = int(inputs.parameters.get("cold_days", 365))
    runs = inputs.ods.filesystem_walk_runs(slug, limit=2)
    if not runs:
        why = "no walk has kept per-file rows for this file system yet"
        return [_absent(slug, why, "no walk yet"), _absent(slug, why, "no walk yet", ACCESS)]
    walk = runs[0]
    at = _parse(walk["run_id"])
    if at is None:
        why = f"the walk's time could not be read ({walk['run_id']}), so no age can be taken"
        return [_absent(slug, why, "walk time unreadable"), _absent(slug, why, "walk time unreadable", ACCESS)]
    rows = inputs.ods.filesystem_walk_files(slug, walk["run_id"])

    def age(text) -> float | None:
        t = _parse(text)
        return max(0.0, (at - t).total_seconds() / 86400.0) if t else None

    common = {"asOf": walk["run_id"], "Hot Days": hot, "Cold Days": cold, "Files Walked": len(rows)}
    mod = [(a, int(r.get("size_bytes") or 0)) for r in rows if (a := age(r.get("modified_at"))) is not None]
    no_mtime = len(rows) - len(mod)
    out = []
    if not rows:
        out.append({"scope": slug, "state": "nothing_found", "value": {**common, "basis": MODIFY_BASIS},
                    "summary": "the walk found no files"})
    elif not mod:
        out.append(_absent(slug, f"no file on the walk of {_stamp(walk['run_id'])} has a modified time", "no modified times"))
    else:
        v = {**common, **_bands(mod, hot, cold), "Files Without A Modified Time": no_mtime, "basis": MODIFY_BASIS}
        out.append({"scope": slug, "state": "measured", "value": v,
                    "summary": (f"{_n(v['Hot Files'], 'file')} modified within {hot} days; {v['Cold Files']:,} not "
                                f"modified for over {cold} days ({v['Cold Bytes']:,} bytes)"
                                + (f"; {no_mtime:,} without a modified time" if no_mtime else ""))})
    out.append(_access(slug, walk, runs[1] if len(runs) > 1 else None, rows, age, hot, cold, common, inputs))
    return out


def _access(slug, walk, previous, rows, age, hot, cold, common, inputs) -> dict:
    if not rows:
        return {"scope": slug, "state": "nothing_found", "measure": ACCESS, "value": {**common, "basis": ACCESS_BASIS},
                "summary": "the walk found no files"}
    timed = [r for r in rows if r.get("accessed_at")]
    if not timed and all(r.get("mode_octal") == NOT_REPORTED for r in rows):
        # An object store keeps no access time at all, so this is not a walk that predates RE keeping them (W46).
        return _absent(slug, f"the walk of {_stamp(walk['run_id'])} read an object store: {NO_ATIME_WORDS}",
                       f"{NO_ATIME_WORDS}", ACCESS)
    if not timed:
        return _absent(slug, f"access times not recorded on this walk ({_stamp(walk['run_id'])}): it was walked before "
                             "Resource Explorer kept them, so which files were read cannot be told",
                       "access times not recorded on this walk", ACCESS)
    if not any((r.get("accessed_at") or "") > (r.get("modified_at") or "") for r in timed):
        return _absent(slug, "access times not recorded on this mount: no file's access time is later than its "
                             "modification time, which is what a mount with access-time updates off shows; a tree "
                             "nobody read since writing it would look the same, so which files are read cannot be told",
                       "access times not recorded on this mount", ACCESS)
    ours: set[str] = set()
    if previous is not None:
        hashed_then = {r["file_path"] for r in inputs.ods.filesystem_walk_files(slug, previous["run_id"])
                       if r.get("hash_state") == "hashed"}
        ours = {r["file_path"] for r in timed
                if r["file_path"] in hashed_then and (r.get("accessed_at") or "") >= previous["run_id"]}
    pairs = [(a, int(r.get("size_bytes") or 0)) for r in timed
             if r["file_path"] not in ours and (a := age(r.get("accessed_at"))) is not None]
    v = {**common, **_bands(pairs, hot, cold), "Files Without An Access Time": len(rows) - len(timed),
         "Access Possibly Resource Explorer's Own Read": len(ours), "basis": ACCESS_BASIS}
    caveat = (f"; {len(ours):,} last read possibly by Resource Explorer's previous walk, not counted" if ours else "")
    return {"scope": slug, "state": "measured", "measure": ACCESS, "value": v,
            "summary": (f"{_n(v['Hot Files'], 'file')} read within {hot} days; {v['Cold Files']:,} not read for over "
                        f"{cold} days{caveat}")}

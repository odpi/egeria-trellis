"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

From the newest walk run whose per-file rows RE kept (owner, group and mode as the walk's stat read them). Two
results, each for the whole file system, as COUNTS: no owner or group name and no file name is in either, because
a result can be published and those stay in Resource Explorer.

- `fs_permissions` (the step's own measure): distinct owners and groups, world-readable, world-writable and
  group-writable files, setuid/setgid files, and the commonest modes;
- `world_readable_sensitive`: world-readable files whose names look like secrets, a request for action when there
  are any. "Sensitive" is the walk's own name rule today (the names it refuses to hash: `.env`, keys,
  certificates, `*secret*` ...); a data-class match over file profiles (F7) is not joined yet.

Never a silent zero:
- no walk: ``not_established``;
- no stat fields (no row carries a mode): ``not_established``, "no stat fields";
- a walk of an object store (every row's mode is "not reported by this source", the S3 walk's own words):
  ``not_established``, "not established · S3 objects carry no POSIX permissions";
- the walk classifies a name only when hashing is on (a link, or a walk with hashing off, leaves it unclassified):
  with no name classified, the sensitive measure is ``not_established``; unclassified files are counted on it.
"""
from __future__ import annotations

from collections import Counter

SENSITIVE = "world_readable_sensitive"
BASIS = "owner, group and mode from the walk's stat of each file, counted; no name is kept in the result"
SENSITIVE_BASIS = ("world-readable (others may read) files whose names the walk classed as secret-looking and did "
                   "not open; files whose names the walk did not classify (hashing off, links) are counted apart")
#: What the S3 walk writes for owner, group and mode (resource_explorer.reach_mechanisms.NOT_REPORTED).
NOT_REPORTED = "not reported by this source"
NO_POSIX_WORDS = "S3 objects carry no POSIX permissions"
#: Hash states that mean the walk did NOT apply its secret-name rule to the file.
UNCLASSIFIED = ("hashing_off", "symlink", "not_attempted")


def _n(count: int, noun: str) -> str:
    """A count with its noun in number: "1 owner", "2 owners"."""
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _absent(scope: str, reason: str, words: str, measure: str = "") -> dict:
    out = {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}
    return {**out, "measure": measure} if measure else out


def _bits(mode: str) -> int | None:
    try:
        return int(mode, 8)
    except (TypeError, ValueError):
        return None


def run(inputs) -> list[dict]:
    slug = inputs.slug
    runs = inputs.ods.filesystem_walk_runs(slug, limit=1)
    if not runs:
        why = "no walk has kept per-file rows for this file system yet"
        return [_absent(slug, why, "no walk yet"), _absent(slug, why, "no walk yet", SENSITIVE)]
    walk = runs[0]
    rows = inputs.ods.filesystem_walk_files(slug, walk["run_id"])
    stat_rows = [(r, b) for r in rows if (b := _bits(r.get("mode_octal") or "")) is not None]
    if rows and all(r.get("mode_octal") == NOT_REPORTED for r in rows):
        why = f"the walk of {walk['run_id']} read an object store, whose objects carry no owner, group or mode"
        out = {"scope": slug, "state": "not_established", "reason": why,
               "summary": f"not established · {NO_POSIX_WORDS}"}
        return [out, {**out, "measure": SENSITIVE}]
    if not stat_rows:
        why = (f"no stat fields: no file on the walk of {walk['run_id']} carries an owner, group or mode"
               if rows else f"the walk of {walk['run_id']} found no files")
        if not rows:
            empty = {"asOf": walk["run_id"], "Files Walked": 0, "basis": BASIS}
            return [{"scope": slug, "state": "nothing_found", "value": empty, "summary": "the walk found no files"},
                    {"scope": slug, "state": "nothing_found", "measure": SENSITIVE, "value": empty,
                     "summary": "the walk found no files"}]
        return [_absent(slug, why, "no stat fields"), _absent(slug, why, "no stat fields", SENSITIVE)]

    modes = Counter(r["mode_octal"] for r, _ in stat_rows)
    common = {"asOf": walk["run_id"], "Files Walked": len(rows), "Files Without Mode": len(rows) - len(stat_rows)}
    value = {**common,
             "Distinct Owners": len({r.get("owner_name") for r, _ in stat_rows if r.get("owner_name")}),
             "Distinct Groups": len({r.get("group_name") for r, _ in stat_rows if r.get("group_name")}),
             "World Readable Files": sum(1 for _, b in stat_rows if b & 0o004),
             "World Writable Files": sum(1 for _, b in stat_rows if b & 0o002),
             "Group Writable Files": sum(1 for _, b in stat_rows if b & 0o020),
             "Setuid Or Setgid Files": sum(1 for _, b in stat_rows if b & 0o6000),
             "Commonest Modes": "; ".join(f"{m}: {n:,}" for m, n in modes.most_common(5)),
             "basis": BASIS}
    here = {"scope": slug, "state": "measured", "value": value,
            "summary": (f"{_n(value['Distinct Owners'], 'owner')}, {_n(value['Distinct Groups'], 'group')}; "
                        f"{value['World Readable Files']:,} of {_n(len(stat_rows), 'file')} world-readable, "
                        f"{value['World Writable Files']:,} world-writable")}
    return [here, _sensitive(slug, walk, stat_rows, common)]


def _sensitive(slug: str, walk: dict, stat_rows: list, common: dict) -> dict:
    classified = [(r, b) for r, b in stat_rows if (r.get("hash_state") or "not_attempted") not in UNCLASSIFIED]
    unclassified = len(stat_rows) - len(classified)
    if not classified:
        return _absent(slug, f"the walk of {walk['run_id']} classified no file name (hashing was off, or every file "
                             "is a link), so which world-readable files look like secrets cannot be told",
                       "file names not classified on this walk", SENSITIVE)
    found = sum(1 for r, b in classified if b & 0o004 and r.get("hash_state") == "secret_name")
    value = {**common, "World Readable Secret Named Files": found,
             "Secret Named Files": sum(1 for r, _ in classified if r.get("hash_state") == "secret_name"),
             "Files Whose Names Were Not Classified": unclassified, "basis": SENSITIVE_BASIS}
    caveat = (f"; {unclassified:,} {'file name' if unclassified == 1 else 'file names'} not classified, so not checked"
              if unclassified else "")
    if found:
        return {"scope": slug, "state": "measured", "measure": SENSITIVE, "value": value,
                "summary": (f"{found:,} world-readable file{'s' if found != 1 else ''} with a secret-looking name: "
                            "review who can read them (the names are in Resource Explorer, not published)" + caveat)}
    return {"scope": slug, "state": "nothing_found", "measure": SENSITIVE, "value": value,
            "summary": f"no world-readable file has a secret-looking name{caveat}"}

"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

From the newest walk run whose per-file rows RE kept: files whose size and SHA-256 are the same are one content.
Two results, each for the whole file system and naming no file (a result can be published; the paths stay in
Resource Explorer):

- `fs_duplicates` (the step's own measure): sets of two or more files here with the same content, the copies beyond
  the first of each, and the bytes those copies take;
- `fs_duplicates_elsewhere`: files here whose content another registered file system's newest walk also holds.

Never a silent zero:
- no walk: ``not_established``;
- no file hashed on the newest walk ("no hashes": hashing off, every file over the limit, ...): ``not_established``,
  naming why from the walk's own hash states;
- no other registered file system has hashed files: ``not_established`` for the cross-file-system measure;
- files the walk did not hash are counted on every result: a duplicate among them cannot be seen.
Empty files all share one content and are never counted as duplicates; they are counted apart.
"""
from __future__ import annotations

from collections import Counter

ELSEWHERE = "fs_duplicates_elsewhere"
CAVEAT = ("the SHA-256 and size the walk kept for each file it hashed (plain files under the walk's hash limit with "
          "no secret-looking name); a file the walk did not hash is never compared")

#: The walk's hash states (walk_extensions), in words, for "why there are no hashes".
_WHY = {"hashing_off": "hashing off", "over_limit": "over the hash limit", "secret_name": "with a secret-looking name",
        "symlink": "linked", "unreadable": "unreadable", "over_budget": "past the run's hashing budget",
        "not_a_file": "not a plain file", "not_attempted": "not attempted"}


def _absent(scope: str, reason: str, words: str, measure: str = "") -> dict:
    out = {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}
    return {**out, "measure": measure} if measure else out


def _n(count: int, one: str, many: str) -> str:
    return f"{count:,} {one if count == 1 else many}"


def _not_hashed_words(states: Counter) -> str:
    return ", ".join(f"{n:,} {_WHY.get(s, s)}" for s, n in states.most_common() if s != "hashed")


def run(inputs) -> list[dict]:
    slug = inputs.slug
    runs = inputs.ods.filesystem_walk_runs(slug, limit=1)
    if not runs:
        why = "no walk has kept per-file rows for this file system yet"
        return [_absent(slug, why, "no walk yet"), _absent(slug, why, "no walk yet", ELSEWHERE)]
    walk = runs[0]
    rows = inputs.ods.filesystem_walk_files(slug, walk["run_id"])
    states = Counter(r.get("hash_state") or "not_attempted" for r in rows)
    hashed = [r for r in rows if r.get("hash_state") == "hashed"]
    not_hashed = len(rows) - len(hashed)
    if not hashed:
        why = (f"no file was hashed on the walk of {walk['run_id']}"
               + (f" ({_not_hashed_words(states)})" if rows else ": the walk found no files"))
        return [_absent(slug, why, "no hashes"), _absent(slug, why, "no hashes", ELSEWHERE)]

    empty = sum(1 for r in hashed if not int(r.get("size_bytes") or 0))
    groups = Counter((r["content_hash"], int(r["size_bytes"])) for r in hashed if int(r.get("size_bytes") or 0))
    sets = {k: n for k, n in groups.items() if n > 1}
    copies = sum(n - 1 for n in sets.values())
    copy_bytes = sum(size * (n - 1) for (_, size), n in sets.items())
    common = {"asOf": walk["run_id"], "Files Hashed": len(hashed), "Files Not Hashed": not_hashed,
              "Empty Files Not Compared": empty, "basis": CAVEAT}
    caveat = f"; {_n(not_hashed, 'file', 'files')} not hashed, so not compared" if not_hashed else ""
    value = {**common, "Duplicate Sets": len(sets), "Files In Duplicate Sets": sum(sets.values()),
             "Redundant Files": copies, "Redundant Bytes": copy_bytes}
    if sets:
        here = {"scope": slug, "state": "measured", "value": value,
                "summary": (f"{_n(len(sets), 'set', 'sets')} of identical files: {_n(copies, 'copy', 'copies')} "
                            f"beyond the first take {copy_bytes:,} bytes{caveat}")}
    else:
        here = {"scope": slug, "state": "nothing_found", "value": value,
                "summary": f"no two of the {len(hashed):,} hashed files have the same content{caveat}"}
    return [here, _elsewhere(inputs, slug, walk, hashed, common, caveat)]


def _elsewhere(inputs, slug: str, walk: dict, hashed: list[dict], common: dict, caveat: str) -> dict:
    found = inputs.ods.filesystem_hash_matches(slug, walk["run_id"])
    compared = [p for p in found.get("peers") or [] if int(p.get("hashed") or 0)]
    if not compared:
        others = len(found.get("peers") or [])
        why = ("no other registered file system has a walk with per-file rows" if not others else
               f"none of the {others:,} other registered file systems has hashed files on its newest walk")
        return _absent(slug, why, "no other file system's hashes to compare with", ELSEWHERE)
    keys = {(m["content_hash"], int(m["size_bytes"])) for m in found.get("matches") or []}
    mine = [r for r in hashed if (r["content_hash"], int(r.get("size_bytes") or 0)) in keys]
    by_fs: Counter = Counter()
    for m in found.get("matches") or []:
        by_fs[m["filesystem_slug"]] += int(m["files"])
    value = {**common, "Files Also Elsewhere": len(mine), "Bytes Also Elsewhere": sum(int(r["size_bytes"]) for r in mine),
             "File Systems Compared": len(compared), "File Systems With Matches": len(by_fs),
             "Matches By File System": "; ".join(f"{fs}: {n:,} files" for fs, n in sorted(by_fs.items()))}
    if mine:
        return {"scope": slug, "state": "measured", "measure": ELSEWHERE, "value": value,
                "summary": (f"{_n(len(mine), 'file here has', 'files here have')} the same content as files in "
                            f"{_n(len(by_fs), 'other registered file system', 'other registered file systems')} "
                            f"({value['Matches By File System']}){caveat}")}
    return {"scope": slug, "state": "nothing_found", "measure": ELSEWHERE, "value": value,
            "summary": (f"no file here has the same content as a file in the {len(compared):,} other registered "
                        f"file system{'s' if len(compared) != 1 else ''} compared{caveat}")}

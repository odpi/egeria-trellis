"""fs_activity's "possibly Resource Explorer's own read" caveat (D9).

The caveat used to fire only for files the PREVIOUS walk hashed. On a file system with no earlier walk that kept rows it
never fired, though this walk hashed and a data-file profile had opened files minutes before. The rule now: any access at or
after the earliest read Resource Explorer made (this walk's own hashing) is counted apart, with the same words; a file last read before Resource Explorer's first read is not.
Fakes only: no registry, no network.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fs_activity.step import ACCESS, run  # noqa: E402

WALK = "2026-11-19T01:32:00"


def row(path, atime, *, mtime="2025-01-01T00:00:00", hashed=False):
    return {"file_path": path, "folder_path": "", "folder_depth": 0, "size_bytes": 10, "modified_at": mtime,
            "accessed_at": atime, "owner_name": "o", "group_name": "g", "mode_octal": "0644",
            "world_readable": 1, "content_hash": "h" if hashed else "", "hash_state": "hashed" if hashed else "over_limit"}


class Ods:
    def __init__(self, runs, files, reads=()):
        self._runs, self._files, self._reads = runs, files, list(reads)

    def filesystem_walk_runs(self, slug, limit=2):
        return self._runs[:limit]

    def filesystem_walk_files(self, slug, run_id):
        return self._files[run_id]

    def filesystem_re_reads(self, slug, limit=50):
        return self._reads


def access(ods):
    out = run(SimpleNamespace(slug="tree", parameters={}, ods=ods))
    return next(r for r in out if r.get("measure") == ACCESS)


def one_walk(rows, *, hashed, reads=()):
    runs = [{"run_id": WALK, "led_by": "resource_explorer", "files": len(rows), "bytes": 10 * len(rows),
             "hashed": hashed, "with_mode": len(rows), "with_access_time": len(rows), "capped": 0}]
    return Ods(runs, {WALK: rows}, reads)


def test_a_walk_that_hashed_caveats_access_at_or_after_its_own_start():
    a = access(one_walk([row("early", "2026-11-18T00:00:00"), row("read-by-us", "2026-11-19T01:32:05")], hashed=2))
    v = a["value"]
    assert v["Access Possibly Resource Explorer's Own Read"] == 1
    assert v["Hot Files"] == 1                                # only "early" is counted
    assert "possibly by Resource Explorer's previous walk, not counted" in a["summary"]


def test_nothing_read_by_resource_explorer_means_no_caveat():
    a = access(one_walk([row("a", "2026-11-18T00:00:00"), row("b", "2026-11-19T01:10:00")], hashed=0))
    v = a["value"]
    assert v["Access Possibly Resource Explorer's Own Read"] == 0
    assert "possibly by Resource Explorer" not in a["summary"]


def test_with_an_earlier_walk_the_per_file_rule_stands_and_the_broad_one_does_not_apply():
    prev = {"run_id": "2026-11-10T00:00:00", "led_by": "resource_explorer", "files": 1, "bytes": 10, "hashed": 0,
            "with_mode": 1, "with_access_time": 1, "capped": 0}
    cur = {**prev, "run_id": WALK, "hashed": 5}
    ods = Ods([cur, prev], {WALK: [row("a", "2026-11-19T01:40:00")], prev["run_id"]: [row("a", "2026-11-01T00:00:00")]})
    assert access(ods)["value"]["Access Possibly Resource Explorer's Own Read"] == 0

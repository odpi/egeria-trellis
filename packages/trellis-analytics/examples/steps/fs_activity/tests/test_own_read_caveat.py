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
        self._runs, self._files = runs, files
        self._reads = reads if isinstance(reads, Exception) else list(reads)

    def filesystem_walk_runs(self, slug, limit=2):
        return self._runs[:limit]

    def filesystem_walk_files(self, slug, run_id):
        return self._files[run_id]

    def filesystem_own_reads(self, slug, since=""):
        if isinstance(self._reads, Exception):
            raise self._reads
        self.asked = (slug, since)
        return [r for r in self._reads if r["surveyed_at"] >= since]


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
    assert "by Resource Explorer's own reads (its walk, profiles, narrative files or data-class matches), not counted" in a["summary"]


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


# ── TR-114: RE's own later reads (profiles, narrative files, data-class matches), from the library's `filesystem_own_reads` ──

def run_of(step, surveyed_at, started_at=None):
    return {"step": step, "surveyed_at": surveyed_at, "started_at": started_at}


def test_a_profile_run_before_the_walk_counts_accesses_at_or_after_its_start_apart():
    # no earlier walk, nothing hashed: only the profile run explains the 11-19 read
    ods = one_walk([row("early", "2026-11-18T00:00:00"), row("profiled", "2026-11-19T00:30:05"),
                    row("person", "2026-11-19T01:00:00")], hashed=0,
                   reads=[run_of("file_profiles", "2026-11-19T00:30:00")])
    v = access(ods)["value"]
    assert v["Access Possibly Resource Explorer's Own Read"] == 2        # at or after RE's first read, whoever's it was
    assert v["Hot Files"] == 1                                           # "early" only
    assert ods.asked == ("tree", "")                                     # no earlier walk: every run


def test_the_earliest_of_the_own_reads_and_the_walks_own_hashing_leads():
    ods = one_walk([row("a", "2026-11-18T23:59:59"), row("b", "2026-11-19T00:10:00")], hashed=2,
                   reads=[run_of("narrative_docs", "2026-11-19T01:00:00", "2026-11-19T00:05:00")])
    # the walk's own hashing (01:32) is later than the narrative run's start (00:05): that is the first read
    assert access(ods)["value"]["Access Possibly Resource Explorer's Own Read"] == 1


def test_a_run_that_began_after_the_walk_cannot_have_touched_what_the_walk_saw():
    ods = one_walk([row("a", "2026-11-19T01:40:00")], hashed=0,
                   reads=[run_of("file_profiles", "2026-11-19T02:00:00")])
    assert access(ods)["value"]["Access Possibly Resource Explorer's Own Read"] == 0


def test_the_end_of_a_run_is_used_only_when_its_start_was_not_kept():
    # narrative run recorded at its END (00:40) with no start: reads before it are not claimed (all that is known)
    ods = one_walk([row("a", "2026-11-19T00:20:00"), row("b", "2026-11-19T00:50:00")], hashed=0,
                   reads=[run_of("narrative_docs", "2026-11-19T00:40:00")])
    assert access(ods)["value"]["Access Possibly Resource Explorer's Own Read"] == 1


def test_with_an_earlier_walk_the_reads_are_asked_since_that_walk():
    prev = {"run_id": "2026-11-10T00:00:00", "led_by": "resource_explorer", "files": 1, "bytes": 10, "hashed": 0,
            "with_mode": 1, "with_access_time": 1, "capped": 0}
    cur = {**prev, "run_id": WALK}
    ods = Ods([cur, prev], {WALK: [row("a", "2026-11-15T00:00:10"), row("b", "2026-11-12T00:00:00")],
                            prev["run_id"]: [row("a", "2026-11-01T00:00:00")]},
              [run_of("file_profiles", "2026-11-05T00:00:00"), run_of("file_profiles", "2026-11-15T00:00:00")])
    assert access(ods)["value"]["Access Possibly Resource Explorer's Own Read"] == 1   # only the run since the previous walk
    assert ods.asked == ("tree", prev["run_id"])


def test_an_unreadable_own_reads_is_said_never_read_as_no_own_reads():
    ods = one_walk([row("a", "2026-11-19T00:30:05")], hashed=0, reads=RuntimeError("the registry did not answer"))
    a = access(ods)
    assert a["state"] == "measured"
    assert "own reads could not be read" in a["summary"] and "not separated" in a["summary"]
    assert a["value"]["Access Possibly Resource Explorer's Own Read"] == 0


def test_a_host_without_the_read_is_said_the_same_way():
    class Old(Ods):
        filesystem_own_reads = None
    ods = Old([{"run_id": WALK, "led_by": "re", "files": 1, "bytes": 10, "hashed": 0, "with_mode": 1,
                "with_access_time": 1, "capped": 0}], {WALK: [row("a", "2026-11-19T00:30:05")]})
    assert "not separated" in access(ods)["summary"]

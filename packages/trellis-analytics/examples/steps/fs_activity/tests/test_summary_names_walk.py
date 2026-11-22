"""TR-113: both summaries name the walk the bands are aged at, so "within 7 days" is not read as relative to today."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fs_activity.step import ACCESS, run  # noqa: E402
from test_own_read_caveat import Ods, row  # noqa: E402

WALK = "2026-11-19T02:13:07"


def results(rows):
    runs = [{"run_id": WALK, "led_by": "resource_explorer", "files": len(rows), "bytes": 10 * len(rows), "hashed": 0,
             "with_mode": len(rows), "with_access_time": len(rows), "capped": 0}]
    return run(SimpleNamespace(slug="tree", parameters={}, ods=Ods(runs, {WALK: rows})))


def test_both_summaries_say_which_walk_they_are_aged_at():
    out = results([row("a", "2026-11-18T00:00:00", mtime="2026-11-17T00:00:00"),
                   row("b", "2026-11-18T01:00:00", mtime="2026-11-01T00:00:00")])
    modified = next(r for r in out if r.get("measure") != ACCESS)
    read = next(r for r in out if r.get("measure") == ACCESS)
    assert modified["state"] == "measured" and read["state"] == "measured"
    assert "on the walk of 11-19 02:13" in modified["summary"]
    assert "on the walk of 11-19 02:13" in read["summary"]
    assert modified["value"]["asOf"] == WALK        # the raw value is unchanged

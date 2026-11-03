"""On-screen capability claims in /next must not promise controls /next lacks
(CLASSIC-VS-NEXT-PARITY-2026-09-30.md C-02..C-04 and R-42). Source-level pins
(CI has no Node), same style as test_curate_honest_non_repo_degrade.py."""
from __future__ import annotations

import re
from pathlib import Path

NEXT = (Path(__file__).resolve().parents[1] / "resource_explorer" / "web"
        / "static" / "next")
CURATE = (NEXT / "stages" / "curate.js").read_text()
APP = (NEXT / "app.js").read_text()


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def test_curate_does_not_claim_header_has_tags_feedback_notes():
    # Superseded by CURATE-UI-DATABASES: the bands are now the real controls,
    # so neither the false sentence nor the "has no controls" one may remain.
    assert "reachable from the resource header" not in _flat(CURATE)
    assert "regardless of type" not in _flat(CURATE)
    assert "offers none of them" not in _flat(CURATE)
    assert "no controls for search tags" not in _flat(CURATE)


def test_publish_stale_does_not_point_at_a_missing_publish_control():
    assert "publish again from the Analysis pane" not in _flat(APP)
    assert "has no publish control" in _flat(APP)


def test_tag_note_feedback_calls_live_only_in_the_curate_bands_module():
    # The controls exist now (stages/curate-bands.js, via re-api.js). The
    # claim "the header has them" stays false: no other /next file may call
    # the routes, so the controls are reached from Curate and nowhere implied.
    for f in NEXT.rglob("*.js"):
        if "admin" in f.parts:
            continue  # admin/feedback.js is a cross-resource list
        if f.name == "curate-bands.js":
            continue  # its comments name the routes; calls go through re-api.js
        t = f.read_text()
        for route in ("/api/curate/tags", "/api/curate/notes", "/api/curate/feedback/"):
            assert route not in t, (f, route)
    bands = (NEXT / "stages" / "curate-bands.js").read_text()
    for fn in ("addCurateTag", "removeCurateTag", "addCurateFeedback", "getCurateNotes"):
        assert fn in bands

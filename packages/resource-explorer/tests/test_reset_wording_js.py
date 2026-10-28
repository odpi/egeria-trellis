"""The wording the screens use after an Egeria reset, run for real under node (format.js is a plain ES module).

The server derives `published_state` / `reset_since` from the `egeria_reset` marker; format.js only chooses how
to say it: a short word on the element, the sentence on demand.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


_MOD: list[str] = []


@pytest.fixture(autouse=True)
def _module(tmp_path):
    # format.js is a plain ES module with no package.json beside it, so node needs it as .mjs
    (tmp_path / "format.mjs").write_text((NEXT / "format.js").read_text(encoding="utf-8"), encoding="utf-8")
    _MOD[:] = [(tmp_path / "format.mjs").as_uri()]


def _run(expr: str):
    mod = _MOD[0]
    out = subprocess.run(["node", "--input-type=module", "-e",
                          f"import * as F from '{mod}'; console.log(JSON.stringify({expr}));"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_a_publish_before_the_marker_reads_published_earlier_and_one_after_reads_published():
    earlier = _run("F.publishedBadge({last_published_at:'2026-09-01T00:00:00', published_state:'published_earlier', "
                   "egeria_reset_at:'2026-10-08T18:30:00'})")
    assert earlier["earlier"] is True and earlier["text"] == "published earlier"
    assert earlier["title"].startswith("published earlier \u00b7 Egeria was reset")
    live = _run("F.publishedBadge({last_published_at:'2026-10-08T20:00:00', published_state:'published', egeria_reset_at:'2026-10-08T18:30:00'})")
    assert live["text"] == "Published" and live["earlier"] is False
    assert _run("F.publishedBadge({last_published_at:'', published_state:''})")["text"] == ""


def test_the_stored_copy_line_appears_only_when_the_server_says_the_marker_postdates_the_read():
    quiet = _run("F.storedCopyWords({stored_copy_read_at:'2026-10-01T10:00:00', reset_since:false})")
    assert quiet == {"head": "stored copy \u00b7 read from Egeria 2026-10-01T10:00:00", "resetLine": ""}
    since = _run("F.storedCopyWords({stored_copy_read_at:'2026-10-01T10:00:00', reset_since:true})")
    assert since["resetLine"] == "Egeria was reset since \u00b7 the element is not in Egeria now"
    assert since["head"] == quiet["head"]            # ONE added line; the heading is the same
    assert "unrecorded" in _run("F.storedCopyWords({reset_since:false})")["head"]


def test_the_unbound_words_and_the_commit_blocker():
    assert _run("F.UNBOUND_WORDS") == "unbound by reset \u00b7 rebind to recreate"
    src = (NEXT / "stages" / "repo-manifest.js").read_text(encoding="utf-8")
    assert "plan.project.status === 'unbound'" in src and "UNBOUND_PROJECT_SENTENCE" in src


def test_every_screen_that_shows_a_published_badge_reads_the_marker_fields():
    app = (NEXT / "app.js").read_text(encoding="utf-8")
    assert "ov.published_state === 'published_earlier'" in app
    curate = (NEXT / "stages" / "curate.js").read_text(encoding="utf-8")
    assert "w.published_state === 'published_earlier'" in curate
    ns = (NEXT / "stages" / "native-surveys.js").read_text(encoding="utf-8")
    assert "storedCopyWords(rep)" in ns and "data-stored-copy-reset" in ns
    legacy = (NEXT.parent / "index.html").read_text(encoding="utf-8")
    for needle in ("_pubWord(la)", "_pubWord(c)", "_pubWord(d)"):
        assert needle in legacy

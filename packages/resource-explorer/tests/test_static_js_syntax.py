"""Syntax guard for the no-build static JS under `web/static/`.

That tree (`re-api.js`, `next/app.js`, `next/stages/*.js`, `next/admin/*.js`,
...) ships straight to the browser with no bundler and no compile step. The
other JS-facing tests in this suite (`test_next_prerequisite_proposal_ui.py`,
`test_re_api_entity_type_threading.py`, `test_fact_answer_rendering.py`)
read that source as TEXT and assert on substrings -- a good technique for
pinning behaviour, but blind to syntax. On 2026-09-24 a malformed JSDoc block
in `re-api.js` (a `*/` followed by more ` * ` continuation lines, no longer
inside a comment) left the module unparseable, and the entire text-substring
suite still passed green -- the break would only have shown up as a blank
page in the browser.

This test runs `node --check` (via stdin, `--input-type=module`) over each
shipped `.js` file and fails if any of them fail to parse. It excludes
`static/vendor/` -- third-party, minified, not ours to pin -- and treats
every remaining file as an ES module: `next/**/*.js` are loaded with
`<script type="module">` in `next/index.html`, and the one plain-script file
(`auth.js`, loaded as a classic script) still parses cleanly under module
mode, since nothing in it uses syntax module mode forbids.

**This is a local-developer guard, not CI coverage.** `.github/workflows/
resource-explorer.yml` has no Node setup step, so `node` is not on PATH
there -- the workflow only ever hits the skip branch below. Catching this
class of bug depends on running the suite locally with a Node install
present.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = (
    Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
)
# The files RE shares with EA (packages/trellis-ui) are scanned too: they left next/ but are still RE's code.
SHARED_UI = Path(__file__).resolve().parents[2] / "trellis-ui" / "trellis_ui" / "static"

_JS_FILES = sorted(
    [p for p in STATIC_DIR.rglob("*.js") if "vendor" not in p.relative_to(STATIC_DIR).parts]
    + list(SHARED_UI.rglob("*.js"))
)


def _js_id(p: Path) -> str:
    return f"trellis-ui/{p.relative_to(SHARED_UI)}" if SHARED_UI in p.parents else str(p.relative_to(STATIC_DIR))


@pytest.mark.parametrize("js_path", _JS_FILES, ids=[_js_id(p) for p in _JS_FILES])
def test_static_js_file_parses(js_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip(
            "node not on PATH -- CI has no Node setup step either, so this "
            "check only ever runs for a developer with Node installed locally"
        )

    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=js_path.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode == 0, (
        f"{js_path.relative_to(STATIC_DIR)} failed to parse as a JS module:\n"
        f"{result.stdout.decode(errors='replace')}"
    )


def test_static_js_files_were_discovered() -> None:
    """Guards the discovery glob itself -- an empty parametrize list would
    make every test above vacuously absent rather than failing."""
    assert len(_JS_FILES) >= 20

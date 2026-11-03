"""Source-level regression test for `loadPane`'s `getScoutingOverview` call
(`resource_explorer/web/static/next/app.js`) — it must be gated on
`state.resourceType === 'repo'`, since `/api/projects/{slug}/scouting-overview`
is a repo-only endpoint (docs/Backlog.md, "/next probes scouting-overview for
database slugs and always gets a 404").

Before this fix, `/next` called `getScoutingOverview(slug)` on every page load
regardless of resource type, 404ing every time for a database or filesystem
slug — the same class of bug as the earlier 'db'/'database' resourceType
mismatch: a call written against one resource type and never gated for the
others.

No JS test runner is wired into this suite for `loadPane` specifically (it is
not exported, and is too large/DOM-entangled to run standalone under node —
see `test_next_resource_header_publish_note.py`'s own note on the same
limitation for a different function), so this pins the fix at the source
level, following that file's established pattern.
"""
from __future__ import annotations

from pathlib import Path

NEXT_DIR = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT_DIR / "app.js"


def _balanced(js: str, start: int) -> str:
    depth = 0
    i = start
    started = False
    while True:
        ch = js[i]
        if ch in "([{":
            depth += 1
            started = True
        elif ch in ")]}":
            depth -= 1
        if started and depth == 0:
            return js[start:i + 1]
        i += 1


def _fn_decl(js: str, signature: str) -> str:
    start = js.index(signature)
    paren = js.index("(", start)
    params_end = paren + len(_balanced(js, paren))
    brace = js.index("{", params_end)
    return js[start:brace] + _balanced(js, brace)


def _load_pane_source() -> str:
    """The overview read moved out of `loadPane` into `primeRepoHeaderReads` (the Curate tabs' pane reads the same
    header data); `loadPane` calls it. This returns the function that now holds the call, and the loadPane test
    below pins that the call is still reached from there."""
    return _fn_decl(APP_JS.read_text(), "function primeRepoHeaderReads(")


class TestScoutingOverviewGatedToRepoOnly:
    def test_the_call_is_gated_on_resource_type_repo(self):
        src = _load_pane_source()
        # The exact call site: `getScoutingOverview(slug)` must sit behind a
        # condition naming `state.resourceType === 'repo'` in the same
        # guarding `if`, not merely appear somewhere else in the function.
        call_index = src.index("getScoutingOverview(slug)")
        # Walk backwards to the nearest `if (` that guards this call.
        if_index = src.rindex("if (", 0, call_index)
        guard = src[if_index:call_index]
        assert "state.resourceType === 'repo'" in guard, (
            "getScoutingOverview(slug) is not gated on state.resourceType === "
            "'repo' -- it will be called for database/filesystem resources "
            "too, 404ing every time")

    def test_the_call_still_exists_at_all(self):
        # Guards against a future refactor silently deleting the whole
        # overview-fetch block rather than gating it -- the fix is "don't
        # call it for the wrong type", not "never call it".
        src = _load_pane_source()
        assert "getScoutingOverview(slug)" in src


    def test_load_pane_still_reaches_it(self):
        src = _fn_decl(APP_JS.read_text(), "async function loadPane(")
        assert "primeRepoHeaderReads(slug)" in src

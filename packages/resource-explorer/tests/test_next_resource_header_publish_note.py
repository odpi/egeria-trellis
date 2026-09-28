"""Source-level regression test for `resourceHeaderHtml`'s "published to
Egeria" text (`resource_explorer/web/static/next/app.js`) — it must prefer
the summary row's own `egeria_publish_note` (set by `egeria_linkage.
describe_publish_status`, every resource type) over the plain
`is_published` boolean text, since `p?.is_published` alone cannot express
"published, but the link is now stale."

Before this fix, only `state.overview?.egeria_link_stale` (a separate,
best-effort, REPO-ONLY fetch) could show a stale-link caveat at all, so
databases and filesystems never showed one — found live 2026-09-26,
`coco_pharma` (stale since 2026-09-22) read a plain "published to Egeria"
throughout.

No JS test runner is wired into this suite (see
`test_next_egeria_native_processes_ui.py`'s own note), so this pins the fix
at the source level.
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


def _resource_header_source() -> str:
    return _fn_decl(APP_JS.read_text(), "export function resourceHeaderHtml(")


class TestPublishNotePreferredOverPlainBoolean:
    def test_the_summary_rows_own_publish_note_is_read(self):
        src = _resource_header_source()
        assert "p?.egeria_publish_note" in src or "p.egeria_publish_note" in src

    def test_the_note_is_rendered_when_present(self):
        src = _resource_header_source()
        assert "p.egeria_publish_note" in src

    def test_the_repo_only_overview_staleness_check_is_still_present(self):
        """`ov?.egeria_link_stale` (repo's own scouting-overview) must not be
        removed — it carries `egeria_link_stale_guid`, extra detail the
        summary-row note doesn't have, and stays as the more specific
        signal for repos."""
        src = _resource_header_source()
        assert "ov?.egeria_link_stale" in src

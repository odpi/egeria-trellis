"""The classic UI must name the resource kind on every call to the five routes
whose server-side `entity_type='repo'` default was dropped (422 when omitted):
`/api/projects/{slug}/analyses/{id}/trend`, `/analyses-index`,
`/members/{id}`, `/members/{id}/children`, `/members/{id}/promote`.

The Python suite only exercises Python callers, so index.html's three trend
fetches shipped without it and would have 422'd. This is a source-text check:
no browser needed.
"""
from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "index.html"

# A JS template literal (or quoted string) that targets /api/projects/<slug>/<route>.
_URL = re.compile(r"""[`'"](/api/projects/[^`'"]*)[`'"]""")
_ROUTES = {
    "trend": re.compile(r"/analyses/[^/]+/trend\b"),
    "analyses-index": re.compile(r"/analyses-index\b"),
    "members": re.compile(r"/members/[^/?]+(/children|/promote)?(\?|$)"),
}


def _calls() -> list[tuple[str, str]]:
    out = []
    for m in _URL.finditer(INDEX.read_text()):
        url = m.group(1)
        for name, rx in _ROUTES.items():
            if rx.search(url):
                out.append((name, url))
    return out


def test_every_call_to_the_dropped_default_routes_sends_entity_type():
    calls = _calls()
    missing = [u for _, u in calls if "entity_type=" not in u]
    assert not missing, f"classic UI calls without entity_type (would 422): {missing}"


def test_the_scan_actually_finds_the_trend_calls():
    # Guard the guard: a regex that matches nothing passes the test above.
    trend = [u for n, u in _calls() if n == "trend"]
    assert len(trend) >= 3, trend

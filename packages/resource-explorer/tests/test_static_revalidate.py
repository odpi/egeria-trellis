"""F61 (2026-11-05): every `/static/` file and the HTML shells answer
`Cache-Control: no-cache`, so a browser revalidates after a deploy instead of
running a weeks-old copy from its heuristic cache.

Seen live: a 2026-10-28 `auth.js` still executing on 2026-11-05 through two
deploys of that file, because the app sent no `Cache-Control` on it and the
browser never asked 8813 again. `no-cache` means revalidate, not "do not
store": the 304 path below proves a warm cache still costs no bytes.

`/api/*` and `/health*` are deliberately outside the rule.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/static/auth.js",
        "/static/next/themes.css",
        "/static/next/app.js",
        "/static/re-api.js",
        "/",
        "/next",
    ],
)
def test_static_files_and_shells_revalidate(client, path):
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    assert r.headers.get("cache-control") == "no-cache", (path, dict(r.headers))


def test_admin_feedback_shell_revalidates(client):
    r = client.get("/admin/feedback")
    # The page may sit behind the login gate in some configurations; either
    # way the response for that path tells the browser to revalidate.
    assert r.headers.get("cache-control") == "no-cache", (r.status_code, dict(r.headers))


def test_api_and_health_do_not_gain_cache_control(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "cache-control" not in r.headers, dict(r.headers)


def test_conditional_get_is_304_and_still_says_no_cache(client):
    first = client.get("/static/auth.js")
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag, "StaticFiles should emit an ETag for a real file"

    second = client.get("/static/auth.js", headers={"If-None-Match": etag})
    assert second.status_code == 304, (second.status_code, dict(second.headers))
    assert second.headers.get("cache-control") == "no-cache", dict(second.headers)
    assert second.content == b""

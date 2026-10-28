"""Next is the default UI; Classic is explicit (owner, 2026-10-07).

`/` serves the Next shell, `/classic` the Classic one, `/next` keeps working
(served directly, not redirected, so a bookmark's query string and a Portal
`#sso=` fragment reach the page untouched). RE_DEFAULT_UI=classic flips `/`
back without a code change; `/next` and `/classic` do not move with it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import resource_explorer
from resource_explorer.auth import RE_PUBLIC_PATHS
from resource_explorer.web import app as app_module

STATIC = Path(resource_explorer.__file__).parent / "web" / "static"
CLASSIC = (STATIC / "index.html").read_text()
NEXT = (STATIC / "next" / "index.html").read_text()


@pytest.fixture
def client():
    return TestClient(app_module.app, follow_redirects=False)


def test_the_two_shells_are_distinguishable():
    assert CLASSIC != NEXT


def test_root_serves_next_by_default(client):
    assert app_module._DEFAULT_UI == "next"
    r = client.get("/")
    assert r.status_code == 200 and r.text == NEXT


def test_classic_is_explicit(client):
    r = client.get("/classic")
    assert r.status_code == 200 and r.text == CLASSIC


def test_next_still_serves_next_without_redirect(client):
    r = client.get("/next?resource=x&stage=curate")
    assert r.status_code == 200 and r.text == NEXT


def test_classic_default_switch_flips_only_root(client, monkeypatch):
    monkeypatch.setattr(app_module, "_DEFAULT_UI", "classic")
    assert client.get("/").text == CLASSIC
    assert client.get("/next").text == NEXT
    assert client.get("/classic").text == CLASSIC


@pytest.mark.parametrize("raw,expected", [
    (None, "next"), ("", "next"), ("next", "next"), ("classic", "classic"),
    (" Classic ", "classic"), ("bogus", "next"),
])
def test_resolve_default_ui(raw, expected):
    assert app_module._resolve_default_ui(raw) == expected


def test_all_three_shells_are_public_so_login_can_render():
    for p in ("/", "/next", "/classic"):
        assert p in RE_PUBLIC_PATHS


def test_next_header_link_goes_to_classic_with_honest_wording():
    app = (STATIC / "next" / "app.js").read_text()
    assert "/next · open current UI" not in app
    assert "export function oldUiHref() {" in app
    block = app[app.index("export function oldUiHref()"):][:300]
    assert "/classic?resource=" in block and "'/classic'" in block
    assert 'href="/classic"' in NEXT
    assert "switch to current UI" not in NEXT


def test_next_classic_link_is_not_hidden_on_narrow_screens():
    assert "#switch-ui { display: none; }" not in NEXT


def test_classic_header_links_to_new_ui():
    assert 'id="open-new-ui"' in CLASSIC and 'href="/next"' in CLASSIC

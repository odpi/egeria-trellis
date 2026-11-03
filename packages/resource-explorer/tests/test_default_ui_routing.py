"""There is one UI (owner, 2026-10-21: Classic retired).

`/` and `/next` both serve the Next shell. `/next` is served directly, not redirected, so a bookmark's
query string and a Portal `#sso=` fragment reach the page untouched. `/classic` and `RE_DEFAULT_UI` are gone.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import resource_explorer
from resource_explorer.auth import RE_PUBLIC_PATHS
from resource_explorer.web import app as app_module

STATIC = Path(resource_explorer.__file__).parent / "web" / "static"
NEXT = (STATIC / "next" / "index.html").read_text()


@pytest.fixture
def client():
    return TestClient(app_module.app, follow_redirects=False)


def test_root_serves_the_next_shell(client):
    r = client.get("/")
    assert r.status_code == 200 and r.text == NEXT


def test_next_still_serves_next_without_redirect(client):
    r = client.get("/next?resource=x&stage=curate")
    assert r.status_code == 200 and r.text == NEXT


def test_classic_is_gone(client):
    assert client.get("/classic").status_code in (401, 404)
    assert not (STATIC / "index.html").exists()
    assert not (STATIC / "tailwind.css").exists()


def test_no_default_ui_switch_is_left():
    assert not hasattr(app_module, "_DEFAULT_UI")
    assert not hasattr(app_module, "_resolve_default_ui")
    from resource_explorer.config import WebUIConfig

    assert "default_ui" not in WebUIConfig.model_fields


def test_both_shells_are_public_so_login_can_render():
    for p in ("/", "/next"):
        assert p in RE_PUBLIC_PATHS
    assert "/classic" not in RE_PUBLIC_PATHS and "/index.html" not in RE_PUBLIC_PATHS


def test_next_header_has_no_link_into_classic():
    app = (STATIC / "next" / "app.js").read_text()
    assert "oldUiHref" not in app
    assert "/classic" not in NEXT and "switch-ui" not in NEXT and "switch-ui" not in app

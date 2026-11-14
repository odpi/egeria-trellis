"""trellis-ui serves pyegeria's egeria-table.js when the installed pyegeria ships it (D3), else its vendored copy,
and says which."""
from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

import trellis_ui


def _client():
    app = Starlette()
    trellis_ui.mount(app)
    return TestClient(app)


def test_vendored_copy_when_pyegeria_has_no_renderer(monkeypatch):
    monkeypatch.setattr(trellis_ui, "_pyegeria_renderer", lambda: None)
    src = trellis_ui.egeria_table_source()
    assert src["source"] == "vendored" and len(src["commit"]) == 40
    r = _client().get("/static/trellis-ui/egeria-table.js")
    assert r.status_code == 200
    assert r.headers["X-Egeria-Table-Source"].startswith("vendored copy")
    assert r.content == (trellis_ui.static_dir() / "egeria-table.js").read_bytes()


def test_pyegeria_copy_wins_when_shipped(monkeypatch, tmp_path):
    f = tmp_path / "egeria-table.js"
    f.write_text("/* pyegeria's */ (function(){})();\n")
    monkeypatch.setattr(trellis_ui, "_pyegeria_renderer", lambda: (Path(f), "9.9.9"))
    r = _client().get("/static/trellis-ui/egeria-table.js")
    assert r.status_code == 200
    assert r.headers["X-Egeria-Table-Source"] == "pyegeria 9.9.9"
    assert r.text.startswith("/* pyegeria's */")


def test_other_files_still_come_from_trellis_ui(monkeypatch):
    monkeypatch.setattr(trellis_ui, "_pyegeria_renderer", lambda: None)
    c = _client()
    assert c.get("/static/trellis-ui/themes.css").status_code == 200
    assert c.get("/static/trellis-ui/look.js").status_code == 200


def test_real_pyegeria_probe_never_raises():
    src = trellis_ui.egeria_table_source()
    assert src["source"] in ("pyegeria", "vendored") and Path(src["path"]).is_file()

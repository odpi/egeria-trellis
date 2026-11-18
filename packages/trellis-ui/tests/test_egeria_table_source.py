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


def test_import_and_mount_never_import_pyegeria():
    """RE's invariant (test_web_startup_under_uvloop): its web modules, which import trellis_ui and mount it at
    import time, must not import pyegeria. Checked in a fresh interpreter."""
    import subprocess
    import sys
    code = ("import sys, trellis_ui\n"
            "from starlette.applications import Starlette\n"
            "trellis_ui.mount(Starlette())\n"
            "trellis_ui.egeria_table_source()\n"
            "bad = sorted(m for m in sys.modules if m == 'pyegeria' or m.startswith('pyegeria.'))\n"
            "print(','.join(bad))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"pyegeria imported: {out.stdout.strip()}"


def test_found_from_the_package_directory(monkeypatch, tmp_path):
    """The file is located under pyegeria's directory without importing it."""
    import importlib.util
    pkg = tmp_path / "pyegeria"
    (pkg / "view" / "static").mkdir(parents=True)
    (pkg / "view" / "static" / "egeria-table.js").write_text("/* x */")

    class _Spec:
        submodule_search_locations = [str(pkg)]
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _Spec() if name == "pyegeria" else None)
    path, _ver = trellis_ui._pyegeria_renderer()
    assert path == pkg / "view" / "static" / "egeria-table.js"

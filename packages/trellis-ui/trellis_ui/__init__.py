"""The browser pieces Resource Explorer and Egeria Advisor share, served at `/static/trellis-ui/`.

The files live in `trellis_ui/static/` and ship as package data, so an app finds them with
`importlib.resources` wherever the package is installed (a checkout, a wheel, an image), never by a
path relative to another package.

    from trellis_ui import mount
    mount(app)          # BEFORE the app's own "/static" mount: Starlette matches mounts in order,
    app.mount("/static", StaticFiles(directory=...))   # and "/static" would otherwise take these paths

What is here (each file says more in its own header):
  themes.css    the three looks (Dark, Light, Paper) as CSS variables, per `data-look` on <html>
  look.js       the look picker; fires `trellis-look-change` (and `re-look-change`, kept as an alias).
                Each page also carries a few inline lines in <head> that set `data-look` from
                localStorage["trellis.look"] before first paint.
  colresize.js  resizable table columns
  markdown.js   markdown to sanitised HTML (needs /static/vendor/marked.min.js, which both apps vendor)
  envelope.js   the run-envelope renderer
  format.js     the time and number formatting envelope.js and RE's pages share
  egeria-table.js  pyegeria's structured TABLE (kind "table") as a table. Its home is pyegeria's package data
                (owner decision D3, 2026-11-14: pyegeria.view.renderers), so the renderer and the shape it
                draws ship together. `mount` serves THAT copy at /static/trellis-ui/egeria-table.js when the
                installed pyegeria has it, and the vendored copy here (egeria-table.js.source.json; never edited)
                only when it doesn't. Which one is served is said, never hidden: `egeria_table_source()`, the
                X-Egeria-Table-Source response header, and one log line at mount.
"""
from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path
from typing import Any, Dict

__all__ = ["STATIC_URL", "egeria_table_source", "mount", "static_dir"]

STATIC_URL = "/static/trellis-ui"
EGERIA_TABLE = "egeria-table.js"

logger = logging.getLogger(__name__)


def static_dir() -> Path:
    """The directory holding the shared static files, wherever the package is installed."""
    return Path(str(resources.files("trellis_ui") / "static"))


def _pyegeria_renderer():
    """pyegeria's egeria-table.js and its version, or None when the installed pyegeria predates D3.

    Found WITHOUT importing pyegeria (gate b408): pyegeria does event-loop work at import time, and Resource
    Explorer's web modules must not import it (test_web_startup_under_uvloop). `find_spec("pyegeria")` on the
    top-level name only locates the package; the file is then looked up under its directory, and the version comes
    from the installed distribution's metadata."""
    from importlib.metadata import PackageNotFoundError, version
    from importlib.util import find_spec
    try:
        spec = find_spec("pyegeria")
    except (ImportError, ValueError):
        return None
    locations = list(getattr(spec, "submodule_search_locations", None) or []) if spec else []
    for loc in locations:
        path = Path(loc) / "view" / "static" / EGERIA_TABLE
        if path.is_file():
            try:
                ver = version("pyegeria")
            except PackageNotFoundError:
                ver = "unknown"
            return path, ver
    return None


def egeria_table_source() -> Dict[str, str]:
    """Which egeria-table.js is served: {"source": "pyegeria"|"vendored", "path", "version"|"commit"}."""
    found = _pyegeria_renderer()
    if found is not None:
        return {"source": "pyegeria", "path": str(found[0]), "version": found[1]}
    import json
    vendored = static_dir() / EGERIA_TABLE
    try:
        commit = json.loads((static_dir() / (EGERIA_TABLE + ".source.json")).read_text())["commit"]
    except Exception:
        commit = "unknown"
    return {"source": "vendored", "path": str(vendored), "commit": commit}


def mount(app: Any, path: str = STATIC_URL) -> None:
    """Serve the shared files at `path` on a Starlette/FastAPI app. Mount it before the app's own
    "/static", which would otherwise answer `/static/trellis-ui/...` itself (with a 404).

    egeria-table.js is answered by its own route, added before the static mount (Starlette matches in order), from
    pyegeria's package data when the installed pyegeria has it, else from the vendored copy."""
    from starlette.responses import FileResponse
    from starlette.staticfiles import StaticFiles

    src = egeria_table_source()
    label = (f"pyegeria {src['version']}" if src["source"] == "pyegeria"
             else f"vendored copy (odpi/egeria-workspaces@{src['commit'][:8]}); this pyegeria has no renderer")
    logger.info("trellis-ui: egeria-table.js from %s", label)

    async def egeria_table(_request):
        return FileResponse(src["path"], media_type="text/javascript",
                            headers={"X-Egeria-Table-Source": label, "Cache-Control": "no-cache"})

    app.add_route(f"{path}/{EGERIA_TABLE}", egeria_table, methods=["GET", "HEAD"])
    app.mount(path, StaticFiles(directory=static_dir()), name="trellis-ui")

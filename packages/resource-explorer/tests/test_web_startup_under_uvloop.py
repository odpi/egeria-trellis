"""The web app must IMPORT under uvicorn's running uvloop loop.

Importing pyegeria runs `pyegeria.view`'s `nest_asyncio.apply()`, which raises "Can't patch loop of type
uvloop.Loop" inside a running uvloop. `uvicorn.run("resource_explorer.web.app:app")` imports the app INSIDE
that loop, so one module-level `import pyegeria` anywhere reachable from `web.app` kills startup (#562 did,
main 3106ed26..586bb4bc) while the whole suite stayed green, because tests never run under uvloop.

Three guards, cheapest last: (1) boot the real ASGI app under `uvicorn --loop uvloop` on a free port with a
temp SQLite registry and answer /health; (2) import the app with `nest_asyncio.apply` made to RAISE; (3)
`pyegeria` is not in sys.modules after the import. The gate restart on 8813 remains the real end-to-end check.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

MODULES = ["resource_explorer.web.app", "resource_explorer.worker",
           "resource_explorer.native_survey_run", "resource_explorer.catalog_and_survey"]


def _env(tmp_path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("EGERIA", "REGISTRY", "METRICS", "FEEDBACK", "PGVECTOR"))}
    env.update(
        REGISTRY_DATABASE_URL=f"sqlite:///{tmp_path}/reg.db",
        METRICS_DATABASE_URL=f"sqlite:///{tmp_path}/met.db",
        FEEDBACK_DATABASE_URL=f"sqlite:///{tmp_path}/fb.db",
        PGVECTOR_PORT="1",
    )
    return env


def _run(code: str, tmp_path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], env=_env(tmp_path), capture_output=True,
                          text=True, timeout=120)


def test_no_module_load_path_imports_pyegeria(tmp_path):
    code = (f"import importlib, sys\nfor m in {MODULES!r}: importlib.import_module(m)\n"
            "bad = sorted(m for m in sys.modules if m == 'pyegeria' or m.startswith('pyegeria.'))\n"
            "assert not bad, bad[:5]\n")
    r = _run(code, tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]


def test_import_survives_nest_asyncio_apply_raising(tmp_path):
    code = ("import nest_asyncio\n"
            "def boom(*a, **k): raise ValueError(\"Can't patch loop of type uvloop.Loop\")\n"
            "nest_asyncio.apply = boom\n"
            f"import importlib\nfor m in {MODULES!r}: importlib.import_module(m)\n")
    r = _run(code, tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]


def test_app_boots_under_uvicorn_with_uvloop(tmp_path):
    pytest.importorskip("uvloop", reason="uvloop not installed: the two import guards above cover the invariant")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "resource_explorer.web.app:app", "--loop", "uvloop",
         "--host", "127.0.0.1", "--port", str(port)],
        env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 60
        body = None
        while time.time() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    body = resp.read().decode()
                break
            except OSError:
                time.sleep(0.3)
        out = ""
        if body is None:
            proc.terminate()
            out = proc.communicate(timeout=15)[0]
        assert body is not None and '"ok"' in body, f"app did not answer /health:\n{out[-3000:]}"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

"""trellis-up's start check (TR-47, TR-48).

Newcomer install (Rocky, 2026-11-18): `make up` went on when EA refused to start (its ADVISOR_CACHE_DIR was
inside a git checkout) and printed nothing; only EA's own [EA] lines showed it. trellis-up now starts each app
itself, and reports an app that exits or doesn't answer /health in time by name, with the first error line of its
output. It then stops the others and exits non-zero.

Fake apps stand in for RE, EA and Studio (TRELLIS_UP_CMD_<APP> / TRELLIS_UP_HEALTH_<APP>): a tiny HTTP server
answering /health, or a command that fails the way EA did. --no-init-secrets and --no-preflight, so nothing reads
or writes a real .env and nothing reaches a real service.
"""
from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "trellis-up"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash script")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server(port: int) -> str:
    """A command that serves /health on `port` until it is stopped."""
    code = ("import http.server as h\n"
            "class H(h.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
            "    def log_message(self, *a): pass\n"
            f"h.HTTPServer(('127.0.0.1', {port}), H).serve_forever()\n")
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _run(tmp_path, apps, *args, timeout=60, start_timeout=20):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRELLIS_UP_")}
    env.update({"TRELLIS_UP_LOG_DIR": str(tmp_path / "logs"), "TRELLIS_UP_START_TIMEOUT": str(start_timeout)})
    for key, (cmd, health) in apps.items():
        env[f"TRELLIS_UP_CMD_{key}"] = cmd
        env[f"TRELLIS_UP_HEALTH_{key}"] = health
    return subprocess.run(["bash", str(SCRIPT), "--no-init-secrets", "--no-preflight", "--start-check", *args],
                          env=env, capture_output=True, text=True, timeout=timeout, cwd=ROOT)


def _health(port):
    return f"http://127.0.0.1:{port}/health"


def _port_free(port) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_all_three_start_and_the_check_passes(tmp_path):
    ports = [_free_port() for _ in range(3)]
    apps = {k: (_server(p), _health(p)) for k, p in zip(("RE", "EA", "STUDIO"), ports)}
    r = _run(tmp_path, apps)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("Resource Explorer", "Egeria Advisor", "Trellis Studio"):
        assert f"{name}: started" in r.stdout
    assert "start check: all 3 app(s) started" in r.stdout
    time.sleep(0.5)
    assert all(_port_free(p) for p in ports), "an app was left running"


def test_an_app_that_refuses_to_start_is_named_with_its_first_error_and_the_rest_stop(tmp_path):
    """EA as Rocky saw it: it exits at start with its reason; RE and Studio were fine."""
    re_port, studio_port, ea_port = _free_port(), _free_port(), _free_port()
    ea = ("echo 'INFO starting Egeria Advisor'; "
          "echo 'ERROR: ADVISOR_CACHE_DIR /x/trellis/data/cache is inside a git checkout; refusing to start' >&2; "
          "exit 1")
    apps = {"RE": (_server(re_port), _health(re_port)), "EA": (ea, _health(ea_port)),
            "STUDIO": (_server(studio_port), _health(studio_port))}
    r = _run(tmp_path, apps)
    assert r.returncode == 1, r.stdout + r.stderr
    assert ("Egeria Advisor failed to start: ERROR: ADVISOR_CACHE_DIR /x/trellis/data/cache is inside a git "
            "checkout; refusing to start") in r.stderr
    assert "1 of 3 app(s) did not start; stopping the others." in r.stderr
    assert "[EA] ERROR: ADVISOR_CACHE_DIR" in r.stdout                  # its own output is shown too
    assert (tmp_path / "logs" / "ea.log").read_text().count("ADVISOR_CACHE_DIR") == 1
    time.sleep(0.5)
    assert _port_free(re_port) and _port_free(studio_port), "the apps that did start were left running"


def test_an_app_that_never_answers_is_reported_after_the_timeout(tmp_path):
    port = _free_port()
    apps = {"EA": ("echo 'still loading models'; sleep 60", _health(port))}
    r = _run(tmp_path, apps, "--ea", start_timeout=3)
    assert r.returncode == 1
    assert f"Egeria Advisor failed to start: no answer at {_health(port)} within 3s; last output: still loading models" \
        in r.stderr


def test_one_named_app_starts_only_that_one(tmp_path):
    port = _free_port()
    r = _run(tmp_path, {"STUDIO": (_server(port), _health(port)),
                        "RE": ("exit 1", _health(_free_port())), "EA": ("exit 1", _health(_free_port()))}, "--studio")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Trellis Studio: started" in r.stdout and "Resource Explorer" not in r.stdout


def test_the_secret_goes_to_studio_too():
    src = SCRIPT.read_text()
    assert 'STUDIO_ENV="$REPO_ROOT/packages/studio/.env"' in src
    assert 'for f in "$RE_ENV" "$EA_ENV" "$STUDIO_ENV"; do\n    if [ "$(env_get "$f" TRELLIS_JWT_SECRET)"' in src


def test_make_up_covers_studio_and_has_its_own_target():
    mk = (ROOT / "Makefile").read_text()
    assert "up-studio:" in mk and "./trellis-up --studio" in mk and "studio-web:" in mk
    assert "start RE, EA and Studio natively" in mk

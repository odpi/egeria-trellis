"""Parity P1 shell wiring (PI-124, PI-137, PI-138, PI-139): the pieces exist and are started at boot."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer/web/static"
NEXT = STATIC / "next"
APP = (NEXT / "app.js").read_text()
HTML = (NEXT / "index.html").read_text()
API = (STATIC / "re-api.js").read_text()


def test_boot_starts_the_health_bootstrap_and_activity_watchers():
    start = APP[APP.index("async function start()"):]
    for call in ("installHealthBanner(document)", "installBootstrapBanner(document)", "startActivityWatch({"):
        assert call in start


def test_whoami_is_a_control_wired_once_to_the_popover():
    assert re.search(r'<button id="whoami"', HTML)
    assert "function wireWhoamiButton()" in APP and "openConnectionPopover(document, btn" in APP
    assert "if (whoamiButtonWired) return;" in APP


def test_the_header_has_an_unread_badge_slot():
    assert 'id="activity-unread"' in HTML


def test_bootstrap_run_never_sends_force_true():
    run = API[API.index("export const runBootstrapMissingOnly"):]
    run = run[:run.index(";") + 1]
    assert "force: false" in run and "force: true" not in run
    for js in NEXT.rglob("*.js"):
        text = js.read_text()
        assert "bootstrap/run" not in text or js.name == "re-api.js", f"{js} posts the bootstrap run itself"
    assert "bootstrap/run" not in "".join(p.read_text() for p in NEXT.rglob("*.js"))


def test_the_bootstrap_route_is_the_one_the_client_calls():
    from resource_explorer.web.app import app

    paths = set(app.openapi()["paths"])
    assert "/api/bootstrap/run" in paths and "/api/bootstrap/status" in paths
    assert "/health/ready" in paths and "/api/egeria/whoami" in paths

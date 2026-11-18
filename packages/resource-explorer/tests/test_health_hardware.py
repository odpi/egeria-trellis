"""The start-up hardware report (packages/trellis-accel; docs/hardware-acceleration-proposal.md §5a).

The report is made once, off the startup path, logged as one line and served at /health/hardware to signed-in
users only (GPU names and versions are not for anyone who can reach the port). It reports; it never installs."""
import asyncio
import logging
import threading

import pytest
import trellis_accel
from trellis_auth.policy import DEFAULT_PUBLIC_PATHS

import resource_explorer.web.app as web

pytestmark = pytest.mark.unit

REPORT = {"machine": {"amd": ["gfx1150"], "nvidia": [], "apple": False, "platform": "linux/x86_64"},
          "build": {"torch": "2.13.0+cu130", "kind": "cuda"}, "choice": {"device": "cpu", "reason": "no GPU"},
          "findings": [{"level": "warn", "text": "AMD GPU gfx1150 found, but ..."}]}


def test_the_endpoint_serves_the_report_made_at_start_up(monkeypatch):
    made = []
    monkeypatch.setattr(trellis_accel, "report_once", lambda purposes=None: made.append(purposes) or REPORT)
    assert asyncio.run(web.health_hardware()) == REPORT
    assert made == [web._hardware_purposes()]   # the app's real settings, per purpose (Architect C1)


def test_it_is_signed_in_only_unlike_health():
    # RE's tests run with sign-in relaxed (tests/conftest.py), so the allowlists are checked, not a live 401; outside
    # the tests the same request is refused with 401 (checked by hand on the branch).
    from resource_explorer.auth import RE_PUBLIC_PATHS
    assert "/health/hardware" not in DEFAULT_PUBLIC_PATHS and "/health/hardware" not in RE_PUBLIC_PATHS
    assert "/health" in DEFAULT_PUBLIC_PATHS


def _drain():
    for t in threading.enumerate():
        if t.name == "hardware-report":
            t.join(timeout=5)


def test_the_start_up_line_is_logged_off_the_startup_path(monkeypatch, caplog):
    monkeypatch.setattr(trellis_accel, "report_once", lambda purposes=None: REPORT)
    monkeypatch.setattr(trellis_accel, "startup_line", lambda r: "hardware: PyTorch 2.13.0 (cuda); device cpu")
    seen = []
    log = logging.getLogger("hw-test")
    monkeypatch.setattr(log, "info", lambda *a: seen.append(a[-1] if len(a) > 1 else a[0]))
    web._log_hardware_in_background(log)
    _drain()
    assert seen == ["hardware: PyTorch 2.13.0 (cuda); device cpu"]


def test_a_report_that_fails_is_a_warning_not_a_stop(monkeypatch):
    def broken(purposes=None):
        raise RuntimeError("torch import failed")
    monkeypatch.setattr(trellis_accel, "report_once", broken)
    warned = []
    log = logging.getLogger("hw-test-2")
    monkeypatch.setattr(log, "warning", lambda *a: warned.append(" ".join(str(x) for x in a)))
    web._log_hardware_in_background(log)
    _drain()
    assert warned and "torch import failed" in warned[0]


def test_re_reports_its_embeddings_with_its_configured_setting():
    """Architect C1: the report states RE's own embeddings setting, not a generic "auto"."""
    from resource_explorer.config import get_config

    assert web._hardware_purposes() == {"embeddings": get_config().embeddings.device, "docling": "auto"}

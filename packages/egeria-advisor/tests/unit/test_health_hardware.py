"""The start-up hardware report (packages/trellis-accel; hardware-acceleration-proposal §5a).

The report is made once, off the startup path, logged as one line and served at
/health/hardware to signed-in users only (GPU names and versions are not for anyone who can
reach the port). It reports; it never installs."""
import asyncio
import logging
import threading

import pytest
import trellis_accel
from fastapi.testclient import TestClient
from trellis_auth.policy import DEFAULT_PUBLIC_PATHS

import advisor.web.app as web

pytestmark = pytest.mark.unit

REPORT = {"machine": {"amd": ["gfx1150"], "nvidia": [], "apple": False,
                      "platform": "linux/x86_64"},
          "build": {"torch": "2.13.0+cu130", "kind": "cuda"},
          "choice": {"device": "cpu", "reason": "no GPU"},
          "findings": [{"level": "warn", "text": "AMD GPU gfx1150 found, but ..."}]}


def test_the_endpoint_serves_the_report_made_at_start_up(monkeypatch):
    made = []
    monkeypatch.setattr(trellis_accel, "report_once",
                        lambda purposes=None: made.append(purposes) or REPORT)
    assert asyncio.run(web.health_hardware()) == REPORT
    assert made == [web._hardware_purposes()]   # the app's real settings, per purpose (C1)


def test_it_is_signed_in_only_unlike_health():
    assert "/health/hardware" not in DEFAULT_PUBLIC_PATHS
    client = TestClient(web.app)
    assert client.get("/health/hardware").status_code == 401
    assert client.get("/health").status_code == 200


def _drain():
    for t in threading.enumerate():
        if t.name == "hardware-report":
            t.join(timeout=5)


def test_the_start_up_line_is_logged_off_the_startup_path(monkeypatch, caplog):
    monkeypatch.setattr(trellis_accel, "report_once", lambda purposes=None: REPORT)
    line = "hardware: PyTorch 2.13.0 (cuda); device cpu"
    monkeypatch.setattr(trellis_accel, "startup_line", lambda r: line)
    seen = []
    log = logging.getLogger("hw-test")
    monkeypatch.setattr(log, "info", lambda *a: seen.append(a[-1] if len(a) > 1 else a[0]))
    web._log_hardware_in_background(log)
    _drain()
    assert seen == [line]


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


def test_ea_reports_the_embeddings_setting_it_really_runs_with(monkeypatch):
    """Architect C1: the report states each purpose with EA's real setting, not one generic choice.
    EA's embeddings read advisor.yaml's embeddings.device (shipped "auto"); EMBEDDING_DEVICE's
    "cpu" default is not on that path. With a "cpu" setting the report says cpu even where
    Apple's GPU is free, and with "auto" it says what auto picks."""
    import types

    import trellis_accel as ta

    from advisor.config import get_full_config

    assert web._hardware_purposes() == {"embeddings": get_full_config()["embeddings"].device,
                                        "docling": "auto"}
    fake = types.SimpleNamespace(
        __version__="2.13.0", version=types.SimpleNamespace(cuda=None, hip=None),
        cuda=types.SimpleNamespace(is_available=lambda: False, get_device_name=lambda i=0: ""),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True)))
    monkeypatch.setattr(ta, "_torch", lambda: fake)
    monkeypatch.delenv(ta.DEVICE_ENV, raising=False)
    assert ta.hardware_report({"embeddings": "cpu"})["choices"]["embeddings"]["device"] == "cpu"
    assert ta.hardware_report({"embeddings": "auto"})["choices"]["embeddings"]["device"] == "mps"

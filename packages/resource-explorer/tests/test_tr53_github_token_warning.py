"""TR-53: GITHUB_TOKEN is not a nice-to-have, and RE says so where it matters.

Without it GitHub allows 60 requests an hour, which one survey of a modest repo spends. config.py defaults it to "", and
nothing said so: no startup warning, no status field, and QUICKSTART, the deploy guide and the admin guide presented it as
optional. Now: a WARNING at web and worker start-up, a
yellow line at the start of the CLI commands that call GitHub, and the three docs say what happens without one.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner

from resource_explorer.github import token_status

SENTINEL = "ghp_SENTINEL_TOKEN_VALUE_0123456789"
REPO = Path(__file__).resolve().parents[3]


def _cfg(token: str):
    return SimpleNamespace(token=token, requests_per_hour=5000)


def _patch(token: str):
    return patch("resource_explorer.github.token_status._config", return_value=_cfg(token))


@pytest.fixture
def no_token():
    with _patch(""):
        yield


@pytest.fixture
def with_token():
    with _patch(SENTINEL):
        yield


def test_status_without_a_token_says_60_an_hour(no_token):
    st = token_status.status()
    assert st["token_set"] is False and st["requests_per_hour"] == 60
    assert "GITHUB_TOKEN" in st["words"] and "60" in st["words"]


def test_status_with_a_token_has_no_warning_and_never_the_value(with_token):
    st = token_status.status()
    assert st["token_set"] is True and st["requests_per_hour"] == 5000
    assert SENTINEL not in repr(st) and SENTINEL not in token_status.startup_line()


def test_the_startup_line_is_a_warning_without_a_token_and_info_with_one(no_token, caplog):
    with caplog.at_level(logging.INFO):
        token_status.log_at_startup(logging.getLogger("t"))
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "GITHUB_TOKEN" in caplog.records[0].getMessage()


def test_startup_with_a_token_logs_info_and_no_secret(with_token, caplog):
    with caplog.at_level(logging.INFO):
        token_status.log_at_startup(logging.getLogger("t"))
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert SENTINEL not in caplog.text


def test_web_and_worker_startup_call_it():
    import inspect

    from resource_explorer import worker
    from resource_explorer.web import app as web_app

    assert "log_at_startup" in inspect.getsource(web_app._lifespan)
    assert "log_at_startup" in inspect.getsource(worker.run_worker)


def test_health_ready_says_nothing_about_github(no_token):
    """W-53-1: /health/ready is the unauthenticated readiness probe (k8s, the chart canary, the Portal tile). Whether a
    token exists and its rate budget is a configuration fact, not readiness, and not for anyone who can reach /health."""
    from resource_explorer.web.app import app

    registry = MagicMock()
    with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
        text = TestClient(app).get("/health/ready").text
    assert "github" not in text.lower() and "token" not in text.lower() and "requests_per_hour" not in text
    with _patch(SENTINEL), patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
        assert SENTINEL not in TestClient(app).get("/health/ready").text


def test_the_console_warning_prints_once_per_process_without_a_token(no_token):
    token_status._WARNED.clear()
    console = Console(record=True, width=200, force_terminal=False)
    assert token_status.warn_on_console(console) is True
    assert token_status.warn_on_console(console) is False
    assert "GITHUB_TOKEN" in console.export_text()


def test_the_console_warning_is_silent_with_a_token(with_token):
    token_status._WARNED.clear()
    console = Console(record=True, width=200, force_terminal=False)
    assert token_status.warn_on_console(console) is False and console.export_text() == ""


@pytest.mark.parametrize("argv", [["survey", "nope"], ["refresh", "nope"]])
def test_survey_and_refresh_warn_at_the_start_without_a_token(no_token, argv):
    from resource_explorer.cli.main import app

    token_status._WARNED.clear()
    registry = MagicMock()
    registry.get.return_value = None
    registry.list.return_value = []
    with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
        result = CliRunner().invoke(app, argv)
    assert "GITHUB_TOKEN" in result.output and "60" in result.output, result.output


# ── the docs: no longer "optional" ──

def _near_token(path: str, first_only: bool = True) -> str:
    text = (REPO / path).read_text()
    i = text.index("GITHUB_TOKEN")
    return text[max(0, i - 400):i + 700]


@pytest.mark.parametrize("path", ["QUICKSTART.md", "docs/deploying-trellis-with-quickstart.md",
                                  "packages/resource-explorer/docs/admin-guide.md"])
def test_the_docs_say_what_happens_without_a_token(path):
    near = _near_token(path)
    assert "60" in near, f"{path}: does not say GitHub allows 60 requests an hour without a token"
    assert "warns at start" in near, f"{path}: does not say RE warns at startup"
    assert "nice" not in near.lower() and "optional" not in near.lower().replace("not optional", "").replace("not as optional", "")


def test_quickstart_says_set_it_and_that_re_warns_at_startup():
    text = (REPO / "QUICKSTART.md").read_text()
    assert "Set `GITHUB_TOKEN`" in text or "Set a `GITHUB_TOKEN`" in text
    assert "warns at start" in text

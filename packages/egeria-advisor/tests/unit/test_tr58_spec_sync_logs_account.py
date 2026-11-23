"""TR-58: EA's report-spec sync log line names the Egeria account it read as, never its password.

The TR-34 deploy check (2026-11-18) could not show from the log that the sync signed in as EA's service account: the
line held only counts. Now the success line and the failure line both say "as <account>".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_egeria_spec_sync import FakeEgeria, _isolated, _no_real_explorer  # noqa: E402,F401  (autouse fixtures)

from advisor import egeria_spec_sync as ess  # noqa: E402


@pytest.fixture
def said():
    from loguru import logger
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lines
    logger.remove(sink)


def _sync_lines(said):
    return [line for line in said if line.startswith("Report spec sync from Egeria")]


def test_a_completed_sync_names_the_account(said):
    ess.sync_now(client_factory=lambda: FakeEgeria(members=[]))
    (line,) = _sync_lines(said)
    assert " as ea-service " in line


def test_a_failed_sync_names_the_account_it_tried(said, monkeypatch):
    monkeypatch.setattr(ess, "_service_connection", lambda: {
        "view_server": "v", "platform_url": "https://fake-egeria:9443", "user_id": "egeriaadvisornpa",
        "user_pwd": "hunter2-secret", "token": None})
    monkeypatch.setattr("advisor.report_pipeline._conn_is_complete", lambda conn: True)

    def _refused(conn):
        raise ConnectionError("Egeria is starting")
    monkeypatch.setattr(ess, "_build_client", _refused)
    ess.sync_now()
    (line,) = _sync_lines(said)
    assert " as egeriaadvisornpa " in line and "Egeria is starting" in line
    assert "hunter2" not in "\n".join(said)


def test_a_failure_before_any_account_is_known_says_so(said):
    def boom():
        raise ConnectionError("Egeria is starting")
    ess.sync_now(client_factory=boom)
    (line,) = _sync_lines(said)
    assert " as (account not known) " in line

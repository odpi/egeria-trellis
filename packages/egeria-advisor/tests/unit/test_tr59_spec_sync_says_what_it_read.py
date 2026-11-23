"""TR-59: the report-spec sync log line tells "the folder holds no specs" from "nothing was read".

On laz the line read "0 added, 0 updated, 0 unchanged, 0 pruned, 0 skipped" on every startup (11-17 16:53, 20:44,
11-18 22:22Z). A read-only check (2026-11-23T20:59Z, laz's Egeria store) found ReportSpecFolder::Consumer::EgeriaAdvisor
ACTIVE with no members (only the Portal folder holds specs, 3), so the 0 was true. But the same line would have been
printed had the folder been missing or unreadable (absence-as-answer). The line now ends with each folder's result:
found and how many members were read, not found (or not visible to the account), or could not be read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_egeria_spec_sync import FOLDER, FakeEgeria, _isolated, _no_real_explorer, _spec_el  # noqa: E402,F401

from advisor import egeria_spec_sync as ess  # noqa: E402


@pytest.fixture
def said():
    from loguru import logger
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lines
    logger.remove(sink)


def _line(said):
    (line,) = [x for x in said if x.startswith("Report spec sync from Egeria")]
    return line


def test_an_empty_folder_says_it_was_found_and_read_and_holds_nothing(said):
    ess.sync_now(client_factory=lambda: FakeEgeria(members=[]))
    assert f"{FOLDER}: found, 0 members read, holds no active report specs" in _line(said)


def test_a_missing_folder_says_nothing_was_read(said):
    ess.sync_now(client_factory=lambda: FakeEgeria(folder=False))
    line = _line(said)
    assert f"{FOLDER}: not found, or not visible to ea-service; nothing read" in line
    assert "members read" not in line


def test_a_folder_with_a_spec_says_how_many_it_read_and_holds(said):
    fake = FakeEgeria(members=[_spec_el("EA Held One", "a0000000-0000-0000-0000-000000000001")])
    ess.sync_now(client_factory=lambda: fake)
    assert f"{FOLDER}: found, 1 member read, holds 1 active report spec" in _line(said)


def test_a_folder_of_drafts_counts_them_read_but_not_held(said):
    fake = FakeEgeria(members=[_spec_el("EA Draft", "d0000000-0000-0000-0000-000000000009", status="DRAFT")])
    ess.sync_now(client_factory=lambda: fake)
    assert f"{FOLDER}: found, 1 member read, holds no active report specs" in _line(said)


def test_no_folder_configured_says_so(said, monkeypatch):
    monkeypatch.setenv(ess.ADVISOR_FOLDERS_ENV, "")
    ess.sync_now(client_factory=lambda: FakeEgeria())
    assert "no folder configured; nothing read" in _line(said)

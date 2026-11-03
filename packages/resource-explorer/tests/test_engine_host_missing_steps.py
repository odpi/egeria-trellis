"""A library step whose package is not installed where the engine host runs is said at start, and never claimed.

2026-10-21: the four library step packages were silently uninstalled from the serving venv and nobody knew until runs
failed "step not installed here". The engine host now logs one line at start (installed / missing), leaves a missing
step's engine actions APPROVED and unclaimed (as a native survey with no host stays), and the card's Run is disabled
with the same sentence before a press. Only the pyegeria boundary is faked (the autospec'd `FakeEgeria`).
"""
from __future__ import annotations

import pytest

from tests.library_step_install import library_installed, staleness_installed  # noqa: F401 - the fixtures
from tests.test_engine_host_e4 import DB_GUID, _sweep, egeria, reg  # noqa: F401 - fixtures and helpers reused

ALL = "change_rates, database_staleness, hub_tables, statistics_freshness"
FS_ALL = "fs_activity, fs_change_rates, fs_duplicates, fs_layout_signals, fs_permissions"
FILES_NONE = f"Files host: none installed (5 missing: {FS_ALL})"
FILES_ALL = f"Files host: {FS_ALL} (none missing)"


def test_none_installed_the_line_names_every_missing_step():
    from resource_explorer import engine_host

    assert engine_host.library_steps_line() == (
        f"library steps \u00b7 Egeria host: none installed (4 missing: {ALL}) \u00b7 {FILES_NONE}")
    assert set(engine_host.missing_library_steps()) == {
        "re-change-rates", "re-database-staleness", "re-hub-tables", "re-statistics-freshness"}


def test_some_installed_the_line_names_only_the_missing_ones(staleness_installed):
    from resource_explorer import engine_host

    assert engine_host.library_steps_line() == (
        "library steps \u00b7 Egeria host: database_staleness (3 missing: change_rates, hub_tables, "
        f"statistics_freshness) \u00b7 {FILES_NONE}")


def test_all_installed_says_none_missing(library_installed):
    from resource_explorer import engine_host

    assert engine_host.library_steps_line() == (
        f"library steps \u00b7 Egeria host: {ALL} (none missing) \u00b7 {FILES_ALL}")
    assert engine_host.missing_library_steps() == {}


def test_start_logs_the_one_line(caplog, monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.setattr(engine_host.EngineHostLoop, "_run", lambda self: None)
    loop = engine_host.EngineHostLoop()
    with caplog.at_level("INFO", logger="resource_explorer.engine_host"):
        loop.start()
        loop.stop()
    lines = [r for r in caplog.records if "library steps" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelname == "INFO"
    assert f"Egeria host: none installed (4 missing: {ALL})" in lines[0].getMessage()


def test_a_missing_steps_action_is_left_approved_and_unclaimed_and_said_once(staleness_installed, reg, egeria,
                                                                              caplog):
    from resource_explorer.engine_host import EngineHost

    egeria.add_action("ea-hub", target=DB_GUID, request_type="re-hub-tables")
    host = EngineHost(registry=reg)
    try:
        with caplog.at_level("INFO", logger="resource_explorer.engine_host"):
            first, second = host.sweep_once(), host.sweep_once()
    finally:
        host.close()
    assert first.claimed == [] and first.skipped.get("library_step_not_installed") == ["ea-hub"]
    assert second.skipped.get("library_step_not_installed") == ["ea-hub"]
    assert egeria.actions["ea-hub"]["processingEngineUserId"] is None
    assert not egeria.method_calls("claim_engine_action")
    said = [r.getMessage() for r in caplog.records if "ea-hub" in r.getMessage()]
    assert len(said) == 1 and "'hub_tables' is not installed" in said[0]


def test_an_installed_steps_action_is_still_claimed(staleness_installed, reg, egeria):
    egeria.add_action("ea-st", target=DB_GUID, request_type="re-database-staleness")
    assert _sweep(reg).claimed == ["ea-st"]


def test_a_truly_unknown_type_stays_unknown(reg, egeria):
    egeria.add_action("ea-x", request_type="re-no-such-step")
    assert _sweep(reg).skipped.get("unknown_request_type") == ["ea-x"]


# ── the card ──────────────────────────────────────────────────────────────────

def test_the_cards_run_is_disabled_with_the_reason_when_the_package_is_absent():
    from resource_explorer.workflows.stage_page import runnable_and_reason

    runnable, reason = runnable_and_reason("database_staleness", "database")
    assert runnable is False
    assert reason.startswith("step not installed here: 'database_staleness'")
    assert "trellis-step-database-staleness" in reason


def test_the_cards_run_is_offered_when_the_package_is_installed(staleness_installed):
    from resource_explorer.workflows.stage_page import runnable_and_reason

    assert runnable_and_reason("database_staleness", "database") == (True, "")
    # a step still missing keeps its refusal
    assert runnable_and_reason("hub_tables", "database")[0] is False


def test_a_built_in_analysis_is_unaffected():
    from resource_explorer.workflows.stage_page import runnable_and_reason

    assert runnable_and_reason("schema_inventory", "database") == (True, "")

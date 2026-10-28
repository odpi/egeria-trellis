"""Tests for the filesystem reachability check — Phase 1 slice #13
(COORDINATOR-BRIEF-MULTI-RESOURCE.md, egeria-support-for-multi-resource.md
§5/§9/§10, `RESOURCE-REACHABILITY-IMPLEMENTED.md`).

`classify_check_asset_result` is pure (no network/registry dependency) and
is tested directly against the live-confirmed raw response shapes recorded
in `PROBES-2026-09-21.md`'s "Probes 7 and 8, run live" section — these are
not guessed strings, they are the actual completion messages Egeria
returned. The registry read/write path is tested against a throwaway
SQLite file, same fixture shape as test_db_fs_structured_tables.py.

`check_filesystem_reachability` itself (the live-network function) is NOT
unit-tested here — it needs a real Egeria platform, which is exactly what
the module docstring's "never raises for an Egeria-side failure" contract
exists to make safe to call speculatively. What IS fully unit-testable
without a live server, per the task's own framing, is the three-state
absence-discipline logic — that is what this file covers.
"""
from __future__ import annotations

import pytest

from resource_explorer.reachability import (
    ReachabilityCheckScopeError,
    ReachabilityResult,
    classify_check_asset_result,
)
from resource_explorer.registry import FileSystemEntity, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_filesystem(FileSystemEntity(
        slug="drop_zone", display_name="Drop Zone",
        local_mount_point="/tmp/drop",
    ))
    return r


# ── classify_check_asset_result: the three-state discipline ────────────────


class TestNeverCheckedIsNotThisFunctionsConcern:
    """"Never checked" is the absence of any row -- covered by the registry
    tests below, not by this function, which only ever runs after a check
    was actually attempted."""


class TestCheckedWithDeterminateOutcome:
    def test_reachable_completed(self):
        # Live-confirmed message (probe7_with_connection_v2.py, third pass,
        # Connection pointed at an engine-host-visible path):
        # "OMES-SURVEY-ACTION-0019 ... completed the analysis of asset
        # ... in 3407 milliseconds; the results are stored in survey report
        # ..."
        result = classify_check_asset_result(
            initiated=True,
            final_status="COMPLETED",
            completion_message=(
                "OMES-SURVEY-ACTION-0019 The survey action service "
                "folder-survey-service has completed the analysis of asset "
                "e0eb9167-cc01-4360-9870-6121d3421cca with request type "
                "survey-folder in 3407 milliseconds; the results are stored "
                "in survey report bc2fdec2-11dd-4f85-b409-1bbd408f0030"
            ),
        )
        assert result.outcome == "reachable"
        assert result.error_code == ""

    def test_reachable_actioned_alias(self):
        # ACTIONED is Egeria's own terminal-success symbolic name in some
        # server versions per egeria_delegated_step.py's docstring; accepted
        # defensively here for the same reason.
        result = classify_check_asset_result(
            initiated=True, final_status="ACTIONED", completion_message="done",
        )
        assert result.outcome == "reachable"

    def test_no_connection(self):
        # Live-confirmed message (probe7_run.py, first pass, no Connection
        # attached at all):
        # "OPEN-SURVEY-0009 The folder-survey-service Survey Acton Service
        # has been supplied with asset ... which has no connection, so
        # there is no way to reach the resource it describes"
        result = classify_check_asset_result(
            initiated=True,
            final_status="INVALID",
            completion_message=(
                "OPEN-SURVEY-0009 The folder-survey-service Survey Acton "
                "Service has been supplied with asset "
                "2175e567-92a7-4b5b-9eac-bc2f41910aa2 which has no "
                "connection, so there is no way to reach the resource it "
                "describes"
            ),
        )
        assert result.outcome == "no_connection"
        assert result.error_code == "OPEN-SURVEY-0009"
        # Raw message preserved verbatim, not paraphrased (peer review note).
        assert "no way to reach the resource" in result.error_detail

    def test_network_unreachable_from_engine_host(self):
        # Live-confirmed message (probe7_with_connection.py, second pass,
        # Connection pointed at a macOS host path the container-based engine
        # host cannot see):
        # "OMES-SURVEY-ACTION-0018 ... FileException ... BASIC-FILE-
        # CONNECTOR-404-001 The folder named /tmp/re_reachability_probe7b
        # in the Connection object ... does not exist"
        result = classify_check_asset_result(
            initiated=True,
            final_status="FAILED",
            completion_message=(
                "OMES-SURVEY-ACTION-0018 The survey action service "
                "folder-survey-service threw a "
                "org.odpi.openmetadata.adapters.connectors.datastore."
                "basicfile.ffdc.exception.FileException exception during "
                "the generation of survey report ... BASIC-FILE-CONNECTOR-"
                "404-001 The folder named /tmp/re_reachability_probe7b in "
                "the Connection object "
                "ResourceExplorer::Probe7bReachabilityScratch::Connection "
                "does not exist"
            ),
        )
        assert result.outcome == "network_unreachable"
        assert result.error_code == "BASIC-FILE-CONNECTOR-404-001"

    def test_unresolvable_secret_not_live_confirmed_but_classified(self):
        # Not confirmed live for the folder-survey path (§5's vocabulary was
        # written for the database/secrets-store case) -- this pins that a
        # message carrying the secrets-store failure signature is still
        # classified rather than falling through to 'unknown', in case a
        # future templated folder Connection hits it.
        result = classify_check_asset_result(
            initiated=True,
            final_status="FAILED",
            completion_message="... secretsCollectionName was not supplied ...",
        )
        assert result.outcome == "unresolvable_secret"

    def test_auth_rejected(self):
        result = classify_check_asset_result(
            initiated=True,
            final_status="FAILED",
            completion_message="401 UNAUTHORIZED: invalid credentials",
        )
        assert result.outcome == "auth_rejected"


class TestCheckedButCouldNotBeDetermined:
    """The third state: distinct from BOTH 'never checked' (no row) and a
    determinate 'not reachable' outcome. A CHECK_ASSET call that itself
    fails to complete must never be silently reported as `no_connection`/
    `network_unreachable` -- that would be a confident wrong answer."""

    def test_raised_exception(self):
        result = classify_check_asset_result(
            initiated=False, final_status=None, completion_message="",
            raised_exception=ConnectionError("platform unreachable"),
        )
        assert result.outcome == "unknown"
        assert result.error_code == "ConnectionError"
        assert "platform unreachable" in result.error_detail

    def test_not_initiated(self):
        result = classify_check_asset_result(
            initiated=False, final_status=None, completion_message="",
        )
        assert result.outcome == "unknown"
        assert result.error_code == "NOT_INITIATED"

    def test_timeout(self):
        result = classify_check_asset_result(
            initiated=True, final_status="TIMEOUT", completion_message="",
        )
        assert result.outcome == "unknown"
        assert result.error_code == "TIMEOUT"

    def test_still_active_status_is_unknown_not_reachable(self):
        # Defensive: a caller that (incorrectly) invokes this before the
        # action reaches a terminal status must not have that read as
        # success just because it isn't a failure code either.
        result = classify_check_asset_result(
            initiated=True, final_status="IN_PROGRESS", completion_message="",
        )
        assert result.outcome == "unknown"

    def test_unrecognized_terminal_status_is_unknown_not_guessed(self):
        # A terminal failure whose message doesn't match any known error
        # code must not be silently mapped to whichever outcome happens to
        # sound plausible -- 'unknown' is the honest answer.
        result = classify_check_asset_result(
            initiated=True, final_status="IGNORED",
            completion_message="some future Egeria error code we've never seen",
        )
        assert result.outcome == "unknown"
        assert result.error_code == "IGNORED"


def test_reachability_result_validates_outcome_vocabulary():
    with pytest.raises(ValueError):
        ReachabilityResult(
            outcome="definitely_not_a_real_outcome",
            error_code="", error_detail="", latency_ms=None, engine_action_guid="",
        )


# ── registry persistence: the "never checked" state and history ────────────


def test_never_checked_is_absence_of_a_row_not_a_sentinel(registry):
    assert registry.get_latest_reachability("drop_zone") is None
    assert registry.list_reachability_history("drop_zone") == []


def test_record_and_read_latest(registry):
    registry.record_reachability_check(
        "drop_zone", probed_at="2026-09-22T10:00:00", outcome="no_connection",
        error_code="OPEN-SURVEY-0009", error_detail="has no connection",
        latency_ms=6000, engine_action_guid="guid-1",
    )
    latest = registry.get_latest_reachability("drop_zone")
    assert latest is not None
    assert latest["outcome"] == "no_connection"
    assert latest["error_code"] == "OPEN-SURVEY-0009"
    assert latest["latency_ms"] == 6000


def test_checked_and_unreachable_differs_from_never_checked(registry):
    """The task's core absence-discipline requirement: "checked, and
    unreachable" must render as a real, present, different finding from
    "never checked" -- not collapse to the same falsy/empty state."""
    before = registry.get_latest_reachability("drop_zone")
    assert before is None  # never checked

    registry.record_reachability_check(
        "drop_zone", probed_at="2026-09-22T10:00:00", outcome="network_unreachable",
        error_code="BASIC-FILE-CONNECTOR-404-001", error_detail="does not exist",
    )
    after = registry.get_latest_reachability("drop_zone")
    assert after is not None  # checked
    assert after["outcome"] == "network_unreachable"  # and unreachable
    assert after != before


def test_latest_returns_most_recent_by_probed_at(registry):
    registry.record_reachability_check(
        "drop_zone", probed_at="2026-09-22T09:00:00", outcome="no_connection",
    )
    registry.record_reachability_check(
        "drop_zone", probed_at="2026-09-22T10:00:00", outcome="reachable",
    )
    latest = registry.get_latest_reachability("drop_zone")
    assert latest["outcome"] == "reachable"

    history = registry.list_reachability_history("drop_zone")
    assert [h["outcome"] for h in history] == ["reachable", "no_connection"]


def test_record_rejects_unknown_outcome_value(registry):
    with pytest.raises(ValueError):
        registry.record_reachability_check(
            "drop_zone", probed_at="2026-09-22T10:00:00", outcome="definitely_wrong",
        )


def test_remove_filesystem_cleans_up_reachability_rows(registry):
    registry.record_reachability_check(
        "drop_zone", probed_at="2026-09-22T10:00:00", outcome="reachable",
    )
    assert registry.get_latest_reachability("drop_zone") is not None
    registry.remove_filesystem("drop_zone")
    # Re-register under the same slug to query without hitting a FK error --
    # remove_filesystem's job is to leave no orphaned rows, so a fresh
    # registration must see a clean slate.
    registry.register_filesystem(FileSystemEntity(
        slug="drop_zone", display_name="Drop Zone",
        local_mount_point="/tmp/drop",
    ))
    assert registry.get_latest_reachability("drop_zone") is None


# ── scope guard ──────────────────────────────────────────────────────────


def test_check_unregistered_filesystem_raises_scope_error(registry):
    from resource_explorer.reachability import check_filesystem_reachability

    with pytest.raises(ReachabilityCheckScopeError):
        check_filesystem_reachability("does_not_exist", registry)

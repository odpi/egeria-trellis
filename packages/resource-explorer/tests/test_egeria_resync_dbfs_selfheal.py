"""Scheduled self-heal for dead database/filesystem Egeria GUIDs.

Found live 2026-10-03 after an Egeria reset: two databases kept
`egeria_asset_guid` values Egeria no longer held and still read as published,
because the scheduled resync scan only read the `projects` table. These tests
use a temp SQLite registry and a stub Egeria client; no live Egeria.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from resource_explorer.egeria_linkage import describe_publish_status
from resource_explorer.egeria_resync import (
    SAFE_SCHEDULED_STEPS, EgeriaResync, ScanResult, scan_and_clear,
)
from resource_explorer.registry import (
    DatabaseEntity, FileSystemEntity, Project, ProjectRegistry,
)

STEP = "flag_stale_dbfs_assets"


class NotFound(Exception):
    pass


class StubClassification:
    """get_element_by_guid: live guids return an element, dead raise NotFound,
    `boom` guids raise a connection error."""

    def __init__(self, live=(), boom=()):
        self.live, self.boom, self.calls = set(live), set(boom), []

    def get_element_by_guid(self, guid, graph_query_depth=0):
        self.calls.append(guid)
        if guid in self.boom:
            raise ConnectionError("connection refused")
        if guid in self.live:
            return {"elementHeader": {"guid": guid}}
        raise NotFound(f"OMRS-REPOSITORY-404-002 {guid} not known")


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    assert str(reg.db_path).startswith(str(tmp_path))  # never the shared registry
    return reg


def _db(reg, slug, guid=""):
    reg.register_database(DatabaseEntity(
        slug=slug, display_name=slug, db_type="postgresql", host="h", port=5432,
        database_name=slug, egeria_asset_guid=guid))


def _fs(reg, slug, guid=""):
    reg.register_filesystem(FileSystemEntity(
        slug=slug, display_name=slug, local_mount_point="/tmp/x",
        egeria_asset_guid=guid))


def _resync(reg, stub):
    r = EgeriaResync(registry=reg)
    r._clients = {"classification": stub}
    return r


def _guid(reg, kind, slug):
    return (reg.get_database(slug) if kind == "database" else reg.get_filesystem(slug)).egeria_asset_guid


def test_a_database_with_a_dead_guid_is_found_and_flagged(registry):
    _db(registry, "dead", "g-dead")
    _db(registry, "alive", "g-alive")
    r = _resync(registry, StubClassification(live={"g-alive"}))
    res = ScanResult()
    finding = r._scan_dbfs_assets(res)
    assert [i["slug"] for i in finding.items] == ["dead"]
    assert finding.repair_step == STEP
    out = r.apply([STEP])["applied"][STEP]
    assert out["flagged"] == 1
    assert registry.get_egeria_linkage("database", "dead")["status"] == "stale"
    assert registry.get_egeria_linkage("database", "alive") is None
    assert describe_publish_status(registry, "database", "dead", "g-dead")["is_published"] is False


def test_a_filesystem_with_a_dead_guid_is_found_and_flagged(registry):
    _fs(registry, "dead", "g-dead")
    r = _resync(registry, StubClassification())
    assert [i["entity_type"] for i in r._scan_dbfs_assets(ScanResult()).items] == ["filesystem"]
    r.apply([STEP])
    assert registry.get_egeria_linkage("filesystem", "dead")["status"] == "stale"


def test_unreachable_egeria_is_undetermined_and_flags_nothing(registry, monkeypatch):
    _db(registry, "d1", "g1")
    _fs(registry, "f1", "g2")
    r = _resync(registry, StubClassification(boom={"g1", "g2"}))
    res = ScanResult()
    assert r._scan_dbfs_assets(res).items == []
    assert {u["ref"] for u in res.undetermined} == {"d1", "f1"}
    out = r.apply([STEP])["applied"][STEP]
    assert out["flagged"] == 0 and out["undetermined"] == 2
    assert registry.list_egeria_linkages("database") == []
    assert registry.list_egeria_linkages("filesystem") == []
    # And the whole pass, when the connection itself cannot be made:
    monkeypatch.setattr(EgeriaResync, "_connect", lambda self: (False, "ConnectionError: down"))
    result = scan_and_clear(registry)
    assert result["reachable"] is False and result["applied"] == {}
    assert registry.list_egeria_linkages("database") == []


def test_a_live_database_stays_unflagged_while_another_is_unreachable(registry):
    _db(registry, "alive", "g-alive")
    _db(registry, "flaky", "g-flaky")
    r = _resync(registry, StubClassification(live={"g-alive"}, boom={"g-flaky"}))
    r.apply([STEP])
    assert registry.get_egeria_linkage("database", "alive") is None
    assert registry.get_egeria_linkage("database", "flaky") is None


def test_a_flagged_row_whose_guid_resolves_again_is_cleared(registry):
    _db(registry, "back", "g-back")
    registry.mark_egeria_linkage_stale("database", "back", "g-back", "was gone")
    r = _resync(registry, StubClassification(live={"g-back"}))
    # The heal must be reachable although the dead-GUID finding is empty:
    assert r._scan_dbfs_assets(ScanResult()).items == []
    assert [i["slug"] for i in r._scan_flagged_dbfs_rows().items] == ["back"]
    out = r.apply([STEP])["applied"][STEP]
    assert out["healed"] == 1
    assert registry.get_egeria_linkage("database", "back") is None
    assert describe_publish_status(registry, "database", "back", "g-back")["is_published"] is True


def test_a_resource_with_no_guid_is_skipped_not_flagged(registry):
    _db(registry, "never", "")
    _fs(registry, "never-fs", "")
    stub = StubClassification()
    r = _resync(registry, stub)
    assert r._scan_dbfs_assets(ScanResult()).items == []
    r.apply([STEP])
    assert stub.calls == []
    assert registry.list_egeria_linkages("database") == []
    assert registry.list_egeria_linkages("filesystem") == []


def test_no_guid_is_deleted_by_the_pass(registry):
    _db(registry, "dead", "g-dead")
    _fs(registry, "dead-fs", "g-dead-fs")
    _resync(registry, StubClassification()).apply([STEP])
    assert _guid(registry, "database", "dead") == "g-dead"
    assert _guid(registry, "filesystem", "dead-fs") == "g-dead-fs"


def test_repos_behave_as_before_the_step_does_not_touch_projects(registry):
    registry.add(Project(slug="r", display_name="r", github_url="https://github.com/o/r", description=""))
    registry.set_egeria_asset_guid("r", "g-repo")
    r = _resync(registry, StubClassification())  # g-repo would resolve False
    assert r._scan_dbfs_assets(ScanResult()).items == []
    r.apply([STEP])
    assert registry.get("r").egeria_asset_guid == "g-repo"
    assert registry.get_egeria_linkage("repo", "r") is None


def test_the_step_is_scheduled_safe_and_never_expensive_or_a_decision(registry):
    from resource_explorer.egeria_resync import EXPENSIVE_STEPS, REPAIR_STEPS
    assert STEP in SAFE_SCHEDULED_STEPS and STEP in REPAIR_STEPS
    assert STEP not in EXPENSIVE_STEPS
    _db(registry, "dead", "g-dead")
    f = _resync(registry, StubClassification())._scan_dbfs_assets(ScanResult())
    assert f.needs_decision is False and f.as_dict()["scheduled"] is True


def test_the_unattended_pass_applies_it_end_to_end(registry, monkeypatch):
    _db(registry, "dead", "g-dead")
    stub = StubClassification()

    def fake_scan(self):
        self._clients = {"classification": stub}
        res = ScanResult()
        res.findings = [f for f in (self._scan_dbfs_assets(res), self._scan_flagged_dbfs_rows()) if f.count]
        return res

    monkeypatch.setattr(EgeriaResync, "scan", fake_scan)
    monkeypatch.setattr(EgeriaResync, "_connect", lambda self: (True, ""))
    result = scan_and_clear(registry)
    assert result["applied"][STEP]["flagged"] == 1
    assert registry.get_egeria_linkage("database", "dead")["status"] == "stale"

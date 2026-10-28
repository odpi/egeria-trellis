"""Tests for egeria_resync.py — currently just _scan_unlinked_members, the
one bug found live (2026-08-26): the scan reported the same "investigation
members that can be attached" finding forever, even immediately after a
successful relink_investigation_members repair had actually attached them
(22 linked, zero errors), because it only checked "does this member have an
asset" rather than "is this member already in the Collection". Confirmed via
`apply(['relink_investigation_members'])` followed by `scan()` against a real
Egeria instance — the finding persisted unchanged. Fixed to check actual
Collection membership via CollectionManager.get_member_list().

No other part of this module has test coverage yet — out of scope for this
bug fix, not an oversight being repeated here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from resource_explorer.egeria_resync import EgeriaResync, ScanResult


def _investigation(slug):
    return {"slug": slug, "egeria_project_guid": f"proj-{slug}"}


def _resync_with(registry, member_list_return):
    r = EgeriaResync(registry=registry)
    r._clients = {"collection": MagicMock(get_member_list=MagicMock(return_value=member_list_return))}
    return r


class TestScanUnlinkedMembers:
    def test_a_member_already_in_the_collection_is_not_reported_again(self):
        """The regression guard. Before the fix, this returned the same
        finding whether or not the member had actually been linked."""
        registry = MagicMock()
        registry.list_investigations.return_value = [_investigation("q3")]
        registry.get_or_create_working_set.return_value = {"egeria_collection_guid": "coll-1"}
        registry.list_investigation_members.return_value = [
            {"entity_type": "repo", "entity_slug": "kafka"},
        ]
        registry.get.return_value = SimpleNamespace(egeria_asset_guid="asset-kafka")

        r = _resync_with(registry, member_list_return=[{"guid": "asset-kafka"}])
        finding = r._scan_unlinked_members(ScanResult())

        assert finding.items == []

    def test_a_member_with_an_asset_not_yet_in_the_collection_is_reported(self):
        registry = MagicMock()
        registry.list_investigations.return_value = [_investigation("q3")]
        registry.get_or_create_working_set.return_value = {"egeria_collection_guid": "coll-1"}
        registry.list_investigation_members.return_value = [
            {"entity_type": "repo", "entity_slug": "kafka"},
        ]
        registry.get.return_value = SimpleNamespace(egeria_asset_guid="asset-kafka")

        r = _resync_with(registry, member_list_return=[])  # collection is empty
        finding = r._scan_unlinked_members(ScanResult())

        assert finding.items == [{"investigation": "q3", "linkable": 1}]

    def test_a_member_with_no_asset_yet_is_never_reported_regardless_of_membership(self):
        registry = MagicMock()
        registry.list_investigations.return_value = [_investigation("q3")]
        registry.get_or_create_working_set.return_value = {"egeria_collection_guid": "coll-1"}
        registry.list_investigation_members.return_value = [
            {"entity_type": "repo", "entity_slug": "unpublished"},
        ]
        registry.get.return_value = SimpleNamespace(egeria_asset_guid="")  # not published

        r = _resync_with(registry, member_list_return=[])
        finding = r._scan_unlinked_members(ScanResult())

        assert finding.items == []

    def test_a_collection_whose_membership_cannot_be_read_goes_to_undetermined_not_items(self):
        """Reporting a repair as still-needed when we couldn't verify it's
        not already done would be a guess dressed as a fact — the same shape
        this module's docstring warns about for the other scans."""
        registry = MagicMock()
        registry.list_investigations.return_value = [_investigation("q3")]
        registry.get_or_create_working_set.return_value = {"egeria_collection_guid": "coll-1"}
        registry.list_investigation_members.return_value = [
            {"entity_type": "repo", "entity_slug": "kafka"},
        ]
        registry.get.return_value = SimpleNamespace(egeria_asset_guid="asset-kafka")

        r = EgeriaResync(registry=registry)
        r._clients = {"collection": MagicMock(get_member_list=MagicMock(side_effect=ConnectionError("down")))}
        res = ScanResult()
        finding = r._scan_unlinked_members(res)

        assert finding.items == []
        assert len(res.undetermined) == 1
        assert res.undetermined[0]["kind"] == "collection membership"

    def test_no_collection_guid_is_skipped_entirely(self):
        """Nothing to check membership against — same as before the fix."""
        registry = MagicMock()
        registry.list_investigations.return_value = [_investigation("q3")]
        registry.get_or_create_working_set.return_value = {}  # no collection yet
        registry.list_investigation_members.return_value = [
            {"entity_type": "repo", "entity_slug": "kafka"},
        ]

        r = _resync_with(registry, member_list_return=[])
        finding = r._scan_unlinked_members(ScanResult())

        assert finding.items == []
        r._clients["collection"].get_member_list.assert_not_called()


class TestScanAndFlagVanishedPublishes:
    """PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md /
    REPLY-PUBLISH-STATE-GO-AHEAD.md §2: a publish claim that is locally
    coherent (its egeria_report_guid IS in project_egeria_surveys) but no
    longer resolves in Egeria is flagged, never deleted or cleared by
    _do_clear_orphan_publish_claims, which only ever touches the OTHER
    condition (locally unmoored)."""

    def _resync_with(self, registry, resolve_return):
        r = EgeriaResync(registry=registry)
        r._clients = {"classification": MagicMock(
            get_element_by_guid=MagicMock(return_value=resolve_return))}
        return r

    def test_a_resolving_report_is_not_flagged(self):
        registry = MagicMock()
        registry.get_latest_egeria_surveys_all_projects.return_value = {
            "kafka": {"egeria_report_guid": "report-1"},
        }
        r = self._resync_with(registry, resolve_return={"guid": "report-1"})
        finding = r._scan_vanished_publishes(ScanResult())

        assert finding.items == []

    def test_a_vanished_report_is_flagged_not_deleted(self):
        registry = MagicMock()
        registry.get_latest_egeria_surveys_all_projects.return_value = {
            "kafka": {"egeria_report_guid": "report-1"},
        }
        r = self._resync_with(registry, resolve_return="No elements found")
        finding = r._scan_vanished_publishes(ScanResult())

        assert finding.items == [{"slug": "kafka", "guid": "report-1"}]
        assert finding.repair_step == "flag_vanished_publishes"

    def test_a_lookup_failure_is_undetermined_not_flagged(self):
        registry = MagicMock()
        registry.get_latest_egeria_surveys_all_projects.return_value = {
            "kafka": {"egeria_report_guid": "report-1"},
        }
        r = EgeriaResync(registry=registry)
        r._clients = {"classification": MagicMock(
            get_element_by_guid=MagicMock(side_effect=ConnectionError("down")))}
        res = ScanResult()
        finding = r._scan_vanished_publishes(res)

        assert finding.items == []
        assert res.undetermined == [{"kind": "publish", "ref": "kafka", "reason": "lookup failed"}]

    def test_do_flag_writes_the_linkage_flag_and_heals_when_republished(self):
        """The repair writes the flag for what's vanished, and clears any
        stale flag for a project whose latest publish now resolves again —
        a republish must not leave the old badge stuck forever."""
        registry = MagicMock()
        registry.get_latest_egeria_surveys_all_projects.return_value = {
            "kafka": {"egeria_report_guid": "report-1"},
            "storm": {"egeria_report_guid": "report-2"},
        }
        r = EgeriaResync(registry=registry)
        r._clients = {"classification": MagicMock(get_element_by_guid=MagicMock(
            side_effect=lambda g, **kw: "No elements found" if g == "report-1" else {"guid": g}))}
        registry.list_egeria_linkages.return_value = [  # storm had a stale flag from before
            {"entity_slug": "storm", "status": "stale"}]

        result = r._do_flag_vanished_publishes()

        registry.mark_egeria_linkage_stale.assert_called_once_with(
            "repo_publish", "kafka", stale_guid="report-1",
            detail="the published report no longer resolves in Egeria")
        registry.clear_egeria_linkage_status.assert_called_once_with("repo_publish", "storm")
        assert (result["flagged"], result["healed"], result["undetermined"]) == (1, 1, 0)

    def test_flag_vanished_publishes_is_never_in_a_delete_step(self):
        """It must never delete a row — the counterpart to
        _do_clear_orphan_publish_claims, not a replacement for it."""
        registry = MagicMock()
        registry.get_latest_egeria_surveys_all_projects.return_value = {}
        r = EgeriaResync(registry=registry)
        r._clients = {"classification": MagicMock()}
        registry.get_egeria_linkage.return_value = None

        result = r._do_flag_vanished_publishes()

        registry.mark_egeria_linkage_stale.assert_not_called()
        assert "deleted" not in result and "cleared" not in result


class TestHealKeyedOnFlaggedRows:
    """The docstring's promise — "a republish is not stuck showing 'no longer
    in the store' forever" — as a test against a REAL registry. The bug: the
    heal loop only ran when the current scan's vanished finding was non-empty,
    and scan() drops empty findings, so once a republish resolved the heal
    never ran. These go through scan_and_clear (the real pass), not the
    private step, so the gating is under test too."""

    def _registry(self, tmp_path):
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.registry import Project
        reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
        for slug in ("egeria_python", "kafka", "sqlglot"):
            reg.add(Project(slug=slug, display_name=slug,
                            github_url=f"https://github.com/t/{slug}", collections=[]))
        return reg

    def _patched_pass(self, registry, present_guids, monkeypatch):
        from resource_explorer import egeria_resync as mod

        def fake_get(guid, **kw):
            return {"guid": guid} if guid in present_guids else "No elements found"

        def fake_connect(self):
            self._clients = {"classification": MagicMock(get_element_by_guid=fake_get),
                             "asset": MagicMock(), "project": MagicMock(),
                             "collection": MagicMock()}
            return True, ""

        monkeypatch.setattr(mod.EgeriaResync, "_connect", fake_connect)
        # Only the publish scans matter here; the rest are not under test.
        for name in ("_scan_assets", "_scan_investigation_guids", "_scan_contexts",
                     "_scan_unlinked_members", "_scan_unpublished_but_expected",
                     "_scan_unpublishable", "_scan_registration_only",
                     "_scan_local_investigations", "_scan_definition_drift",
                     "_scan_specification_gap", "_scan_orphan_publish_claims"):
            monkeypatch.setattr(mod.EgeriaResync, name,
                                lambda self, *a, **k: mod.Finding(key="x", title="", detail=""))
        return mod

    def test_flag_clears_after_republish_in_one_pass(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path)
        reg.record_egeria_survey("egeria_python", "2026-09-29T00:00:00", "old-report")
        reg.mark_egeria_linkage_stale("repo_publish", "egeria_python", stale_guid="old-report")
        # Republished: a NEW latest report that resolves; the old one is gone.
        reg.record_egeria_survey("egeria_python", "2026-10-01T00:00:00", "new-report")
        mod = self._patched_pass(reg, {"new-report"}, monkeypatch)

        mod.scan_and_clear(reg)

        assert reg.get_egeria_linkage("repo_publish", "egeria_python") is None
        # The clear leaves visible evidence naming the report it resolved to.
        entries = [a for a in reg.list_activity(entity_type="repo", entity_slug="egeria_python")
                   if a["operation"] == "refresh"]
        assert len(entries) == 1
        assert "new-report" in entries[0]["summary"]

    def test_still_vanished_keeps_flag_and_advances_last_checked(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path)
        reg.record_egeria_survey("kafka", "2026-09-29T00:00:00", "gone")
        reg.mark_egeria_linkage_stale("repo_publish", "kafka", stale_guid="gone")
        with reg._conn() as c:
            c.execute("UPDATE egeria_linkage_status SET last_checked_at='2026-09-29T00:00:00+00:00'")
        mod = self._patched_pass(reg, set(), monkeypatch)

        mod.scan_and_clear(reg)

        row = reg.get_egeria_linkage("repo_publish", "kafka")
        assert row["status"] == "stale"
        assert row["last_checked_at"] > "2026-09-29T00:00:00+00:00"

    def test_flagged_slug_with_no_report_becomes_uncatalogued(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path)
        reg.mark_egeria_linkage_stale("repo_publish", "amundsen", stale_guid="x")
        mod = self._patched_pass(reg, set(), monkeypatch)

        mod.scan_and_clear(reg)

        row = reg.get_egeria_linkage("repo_publish", "amundsen")
        assert row["status"] == "uncatalogued"

    def test_uncatalogued_heals_once_a_resolvable_report_exists(self, tmp_path, monkeypatch):
        reg = self._registry(tmp_path)
        reg.mark_egeria_linkage_uncatalogued("repo_publish", "sqlglot")
        reg.record_egeria_survey("sqlglot", "2026-10-01T00:00:00", "r1")
        mod = self._patched_pass(reg, {"r1"}, monkeypatch)

        mod.scan_and_clear(reg)

        assert reg.get_egeria_linkage("repo_publish", "sqlglot") is None

"""Tests for the catalog/republish repairs and the readiness question behind them.

The bug these exist for is not a crash. `_scan_unpublished_but_expected` used
to compute

    publish_ready = row["status"] != "unset"

which reads the context row correctly and answers a different question from the
one it is labelled with. The publish route accepts a repo when its context is
set **or** when a bound investigation supplies one by inheritance, so the two
agreed for as long as nobody used inheritance. Measured live 2026-08-31, right
after three investigations were promoted: the panel reported 25 of 27 repos
blocked while all 27 would in fact have published. The panel said the work was
blocked precisely because the user had just unblocked it.

So the first test below is the one that would have failed then and passes now,
and `test_the_check_can_actually_fail` is its known-negative: a repo with
neither a context nor an inheritance must still be reported blocked, or the
first test is passing for the trivial reason that everything is "ready".
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from resource_explorer.egeria_resync import (
    CATALOG_STEPS,
    EXPENSIVE_STEPS,
    REGISTRATION_ONLY_ANALYSES,
    REPAIR_STEPS,
    EgeriaResync,
)


def _registry(rows, *, inherits=(), published_analyses=None, catalogued_before=(),
              live_reports=("r-live",), surveyed=()):
    """A registry whose _conn() serves a real in-memory SQLite.

    Real SQL rather than a mocked cursor: the scans under test are mostly SQL,
    and a mock that returns whatever the test says would verify the test's idea
    of the query instead of the query.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE projects (slug TEXT, egeria_asset_guid TEXT);"
        "CREATE TABLE entity_egeria_project_context "
        "  (entity_type TEXT, entity_slug TEXT, status TEXT);"
        "CREATE TABLE activity_log "
        "  (entity_slug TEXT, operation TEXT, status TEXT);"
        "CREATE TABLE project_published_analyses "
        "  (project_slug TEXT, analysis_id TEXT, egeria_report_guid TEXT);"
        "CREATE TABLE project_egeria_surveys "
        "  (project_slug TEXT, egeria_report_guid TEXT);"
        "CREATE TABLE project_published_annotation_types "
        "  (project_slug TEXT, annotation_type TEXT, egeria_report_guid TEXT);")
    for guid in live_reports:
        conn.execute("INSERT INTO project_egeria_surveys VALUES (NULL, ?)", (guid,))
    for slug in surveyed:
        # A repo published by a path that writes no per-analysis claims.
        conn.execute("INSERT INTO project_egeria_surveys VALUES (?, ?)",
                     (slug, f"r-{slug}"))
    for slug, asset, ctx in rows:
        conn.execute("INSERT INTO projects VALUES (?,?)", (slug, asset))
        if ctx is not None:
            conn.execute("INSERT INTO entity_egeria_project_context VALUES ('repo',?,?)",
                         (slug, ctx))
    for slug in catalogued_before:
        conn.execute("INSERT INTO activity_log VALUES (?,'catalog','ok')", (slug,))
    for entry in (published_analyses or []):
        # (slug, analysis_id) defaults to a LIVE report; a third element names
        # the report explicitly so a test can point a claim at a dead one.
        slug, aid, *rest = entry
        conn.execute("INSERT INTO project_published_analyses VALUES (?,?,?)",
                     (slug, aid, rest[0] if rest else "r-live"))
    conn.commit()

    class _Ctx:
        def __enter__(self): return conn
        def __exit__(self, *a): return False

    reg = MagicMock()
    reg._conn.side_effect = lambda: _Ctx()
    reg.inherited_egeria_project_context.side_effect = (
        lambda _t, slug: ({"egeria_project_guid": "p-1",
                           "egeria_project_qualified_name": "Project::One",
                           "_inherited_from_name": "Corpus baseline"}
                          if slug in inherits else None))
    return reg


class TestPublishReadiness:
    def test_unset_context_is_ready_when_an_investigation_supplies_one(self):
        """The regression guard for the live misreport.

        `status='unset'` was the whole basis of the old answer. Inheritance
        makes it publishable, and the panel must say so.
        """
        reg = _registry([("kafka", "", "unset")], inherits={"kafka"})
        (item,) = EgeriaResync(registry=reg)._publish_readiness()
        assert item["publish_ready"] is True
        assert "Corpus baseline" in item["ready_via"]

    def test_the_check_can_actually_fail(self):
        """The known-negative. Without this, the test above passes for a
        scanner that simply calls everything ready."""
        reg = _registry([("orphan", "", "unset")], inherits=set())
        (item,) = EgeriaResync(registry=reg)._publish_readiness()
        assert item["publish_ready"] is False
        assert item["ready_via"] == ""

    def test_a_repo_with_no_context_row_at_all_is_seen(self):
        """`egeria_trellis` live: no context row, inherits fine, and was
        invisible because the old scan INNER JOINed the context table."""
        reg = _registry([("egeria_trellis", "", None)], inherits={"egeria_trellis"})
        items = EgeriaResync(registry=reg)._publish_readiness()
        assert [i["slug"] for i in items] == ["egeria_trellis"]
        assert items[0]["publish_ready"] is True

    def test_a_failed_inheritance_lookup_is_undetermined_not_blocked(self):
        """The module's standing rule: could-not-ask is never a negative."""
        reg = _registry([("kafka", "", "unset")])
        reg.inherited_egeria_project_context.side_effect = RuntimeError("boom")
        (item,) = EgeriaResync(registry=reg)._publish_readiness()
        assert item["publish_ready"] is None
        assert "undetermined" in item["ready_via"]

    def test_published_repos_are_not_in_the_population_at_all(self):
        reg = _registry([("done", "asset-1", "linked")], inherits=set())
        assert EgeriaResync(registry=reg)._publish_readiness() == []


class TestFindingsSplit:
    def test_the_repairable_finding_holds_only_repos_the_repair_can_complete(self):
        """A "fix" tick over rows the fix must fail on is the thing this split
        exists to prevent."""
        reg = _registry(
            [("ready", "", "unset"), ("blocked", "", "unset")], inherits={"ready"})
        r = EgeriaResync(registry=reg)
        assert [i["slug"] for i in r._scan_unpublished_but_expected().items] == ["ready"]
        assert [i["slug"] for i in r._scan_unpublishable().items] == ["blocked"]

    def test_undetermined_lands_in_the_decision_finding_with_its_own_reason(self):
        reg = _registry([("kafka", "", "unset")])
        reg.inherited_egeria_project_context.side_effect = RuntimeError("boom")
        (item,) = EgeriaResync(registry=reg)._scan_unpublishable().items
        assert "could not determine" in item["blocked_reason"]

    def test_lost_and_never_catalogued_are_counted_apart_in_the_title(self):
        reg = _registry([("lost", "", "linked"), ("fresh", "", "linked")],
                        catalogued_before={"lost"})
        title = EgeriaResync(registry=reg)._scan_unpublished_but_expected().title
        assert "1 lost an asset" in title and "1 never had one" in title


class TestRegistrationOnly:
    def test_an_asset_with_only_repository_health_is_offered_a_full_publish(self):
        reg = _registry([("kafka", "asset-1", "linked")],
                        published_analyses=[("kafka", "repository_health")])
        (item,) = EgeriaResync(registry=reg)._scan_registration_only().items
        assert item["slug"] == "kafka"

    def test_an_asset_with_real_results_is_left_alone(self):
        """The known-negative for the subset test."""
        reg = _registry([("kafka", "asset-1", "linked")],
                        published_analyses=[("kafka", "repository_health"),
                                            ("kafka", "dependency_analysis")])
        assert EgeriaResync(registry=reg)._scan_registration_only().items == []

    def test_membership_is_a_subset_test_not_a_count(self):
        """One analysis that is NOT the registration one must not qualify,
        even though the row count matches the registration-only case."""
        reg = _registry([("kafka", "asset-1", "linked")],
                        published_analyses=[("kafka", "dependency_analysis")])
        assert EgeriaResync(registry=reg)._scan_registration_only().items == []

    def test_an_unpublished_repo_is_not_swept_in(self):
        reg = _registry([("nope", "", "linked")])
        assert EgeriaResync(registry=reg)._scan_registration_only().items == []


class TestRepairOrderAndCost:
    def test_cataloguing_runs_before_relinking(self):
        """Order is the whole reason apply() ignores caller order: a member
        cannot be attached to an asset that does not exist yet."""
        assert REPAIR_STEPS.index("catalog_assets") < \
               REPAIR_STEPS.index("relink_investigation_members")

    def test_cataloguing_runs_after_the_clears(self):
        """Publishing before stale pointers are cleared reuses a dead GUID."""
        for step in [s for s in REPAIR_STEPS if s.startswith("clear_")]:
            assert REPAIR_STEPS.index(step) < REPAIR_STEPS.index("catalog_assets")

    def test_every_repair_step_has_an_implementation(self):
        r = EgeriaResync(registry=MagicMock())
        for step in REPAIR_STEPS:
            assert callable(getattr(r, f"_do_{step}", None)), step

    def test_the_full_republish_is_marked_expensive_and_the_cheap_one_is_not(self):
        """`catalog_assets` staying OFF this list is deliberate: it downloads
        nothing, and relinking cannot work without it."""
        assert "republish_survey_results" in EXPENSIVE_STEPS
        assert "catalog_assets" not in EXPENSIVE_STEPS

    def test_catalog_publishes_no_download_tier_step(self):
        """The cheapness claim, checked against what the steps actually declare
        rather than trusted from the constant's name."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            ANALYSIS_KINDS,
        )
        infos = {}
        for kind in ANALYSIS_KINDS.values():
            info = getattr(kind, "step_info", None)
            if info is not None:
                infos[kind.step_key] = info
        for key in CATALOG_STEPS:
            info = infos.get(key)
            if info is None:
                pytest.skip(f"no StepInfo exposed for {key}")
            assert info.fetch_cost != "download", key


class TestPublishManyIsolatesFailures:
    def test_one_bad_repo_does_not_stop_the_rest_and_is_counted_honestly(self):
        """A repair that reports 3 when it did 2 is the class of claim this
        module exists to stop making."""
        r = EgeriaResync(registry=MagicMock())
        r._refusal = lambda et, slug, write="publish": ""     # Brief Z2 round 2: the access check, open here
        calls = []

        def fake(slug, steps):
            calls.append(slug)
            if slug == "bad":
                return {"slug": slug, "ok": False, "error": "publish failed: nope"}
            return {"slug": slug, "ok": True, "report_guid": "g", "annotations": 1}

        r._publish_one = fake
        out = r._publish_many(["a", "bad", "c"], CATALOG_STEPS)
        assert calls == ["a", "bad", "c"]
        assert (out["attempted"], out["published"], out["failed"]) == (3, 2, 1)
        assert out["errors"] == [{"slug": "bad", "error": "publish failed: nope"}]

    def test_a_repo_that_lost_its_context_between_scan_and_apply_is_reported(self):
        """Not raised — one drifted repo must not abort the other twenty-six."""
        reg = MagicMock()
        reg.get_project_context.return_value = {"status": "unset"}
        reg.inherited_egeria_project_context.return_value = None
        out = EgeriaResync(registry=reg)._publish_one("kafka", CATALOG_STEPS)
        assert out["ok"] is False and "428" in out["error"]


def test_registration_only_constant_names_a_real_analysis():
    """Guards the subset test against a typo silently making it match nothing."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_STEP_MAP,
    )
    assert REGISTRATION_ONLY_ANALYSES <= set(REPO_ANALYSIS_STEP_MAP)


class TestStaleClaimsDoNotSuppressWork:
    """A publish claim is a claim, not a fact.

    `project_published_analyses` rows do not stop existing when the report they
    name is destroyed. After the 2026-08-31 redeploy the table held 64 rows of
    which 36 pointed at SurveyReports that no longer existed, and reading it raw
    excluded 5 repos from the republish worklist on the strength of claims dated
    27-31 August. The scan said "these already have survey results" about the
    repos whose results had just been destroyed.

    Silent exclusion is the dangerous part: a repo dropping off a worklist looks
    identical to a repo that never belonged on it.
    """

    def test_a_claim_naming_a_destroyed_report_does_not_count_as_published(self):
        """The regression guard for the live misreport."""
        reg = _registry(
            [("egeria_git", "asset-1", "linked")],
            published_analyses=[("egeria_git", "repository_health", "r-live"),
                                ("egeria_git", "chaoss_metrics", "r-destroyed")],
            live_reports=("r-live",))
        items = EgeriaResync(registry=reg)._scan_registration_only().items
        assert [i["slug"] for i in items] == ["egeria_git"], (
            "a dead claim for chaoss_metrics excluded this repo from the worklist")

    def test_the_check_can_actually_fail(self):
        """Known-negative: the SAME shape with a LIVE second claim must still
        be excluded, or the test above passes for a scan that ignores claims."""
        reg = _registry(
            [("egeria_git", "asset-1", "linked")],
            published_analyses=[("egeria_git", "repository_health", "r-live"),
                                ("egeria_git", "chaoss_metrics", "r-live")],
            live_reports=("r-live",))
        assert EgeriaResync(registry=reg)._scan_registration_only().items == []


class TestOrphanClaimRepairCannotBeOutrun:
    def test_it_clears_claims_for_a_repo_that_now_HAS_an_asset(self):
        """Keying on asset-absence meant cataloguing a repo closed the window:
        its pre-wipe claims became permanently unreachable. That is exactly
        what happened live — the claims survived because the situation had
        been half-repaired."""
        reg = _registry(
            [("egeria_git", "asset-fresh", "linked")],
            published_analyses=[("egeria_git", "chaoss_metrics", "r-destroyed")],
            live_reports=("r-live",))
        out = EgeriaResync(registry=reg)._do_clear_orphan_publish_claims()
        assert out["claims_deleted"] == 1
        with reg._conn() as c:
            assert c.execute(
                "SELECT count(*) FROM project_published_analyses").fetchone()[0] == 0

    def test_it_leaves_a_claim_whose_report_still_exists(self):
        """Known-negative. Without it, a repair that deletes everything passes
        the test above."""
        reg = _registry(
            [("egeria_git", "asset-fresh", "linked")],
            published_analyses=[("egeria_git", "repository_health", "r-live")],
            live_reports=("r-live",))
        out = EgeriaResync(registry=reg)._do_clear_orphan_publish_claims()
        assert out["claims_deleted"] == 0
        with reg._conn() as c:
            assert c.execute(
                "SELECT count(*) FROM project_published_analyses").fetchone()[0] == 1


class TestOrphanClaimScanMatchesItsRepair:
    """The scan and its repair must key on the same thing.

    `_do_clear_orphan_publish_claims` was fixed to key on the claim; the scan
    that surfaces it was left keyed on asset absence. Measured live
    2026-08-31 after `catalog_assets`: the scan reported 0 while 36 orphaned
    claims existed. Giving the repos assets back made their pre-wipe claims
    invisible, the card never rendered, and the repair became unreachable from
    the panel at the exact moment it had become able to do the job.

    A finding reporting zero reads as "nothing to do", so this failure costs
    nothing to produce and everything to miss.
    """

    def test_a_claim_naming_a_dead_report_is_found_even_when_the_repo_has_an_asset(self):
        reg = _registry(
            [("egeria_git", "asset-fresh", "linked")],
            published_analyses=[("egeria_git", "chaoss_metrics", "r-destroyed")],
            live_reports=("r-live",))
        f = EgeriaResync(registry=reg)._scan_orphan_publish_claims()
        assert [i["slug"] for i in f.items] == ["egeria_git"]
        assert f.repair_step == "clear_orphan_publish_claims"

    def test_the_check_can_actually_fail(self):
        """Known-negative: a live claim must NOT be reported, or the test above
        passes for a scan that flags everything."""
        reg = _registry(
            [("egeria_git", "asset-fresh", "linked")],
            published_analyses=[("egeria_git", "repository_health", "r-live")],
            live_reports=("r-live",))
        assert EgeriaResync(registry=reg)._scan_orphan_publish_claims().items == []

    def test_scan_and_repair_agree_on_the_same_rows(self):
        """The property that actually matters: whatever the card offers to fix,
        the repair removes — and nothing else."""
        reg = _registry(
            [("a", "asset-1", "linked"), ("b", "asset-2", "linked")],
            published_analyses=[("a", "chaoss_metrics", "r-destroyed"),
                                ("b", "repository_health", "r-live")],
            live_reports=("r-live",))
        r = EgeriaResync(registry=reg)
        offered = sum(i["claims"] for i in r._scan_orphan_publish_claims().items)
        assert r._do_clear_orphan_publish_claims()["claims_deleted"] == offered
        assert r._scan_orphan_publish_claims().items == []


class TestEmptyCollectionIsZeroNotUnknown:
    """pyegeria returns the STRING "No members found" for an empty collection.

    That is a definite answer — zero members — and treating it as a malformed
    response sent all three investigations to `undetermined`, so the finding
    reported 0 and the relink card never rendered. An empty collection means
    every member is unlinked, which is the strongest reason to show the card.

    Measured live 2026-08-31: 3 investigations undetermined, 0 reported; after
    the fix, 3 investigations with 35 linkable members between them.
    """

    def _scanner(self, member_list_return):
        from types import SimpleNamespace
        reg = MagicMock()
        reg.list_investigations.return_value = [
            {"slug": "q3", "egeria_project_guid": "proj-q3"}]
        reg.get_or_create_working_set.return_value = {"egeria_collection_guid": "coll-1"}
        reg.list_investigation_members.return_value = [
            {"entity_type": "repo", "entity_slug": "kafka"}]
        reg.get.return_value = SimpleNamespace(egeria_asset_guid="asset-kafka")
        r = EgeriaResync(registry=reg)
        r._clients = {"collection": MagicMock(
            get_member_list=MagicMock(return_value=member_list_return))}
        return r

    def test_an_empty_collection_reports_its_members_as_linkable(self):
        from resource_explorer.egeria_resync import ScanResult
        res = ScanResult()
        f = self._scanner("No members found")._scan_unlinked_members(res)
        assert res.undetermined == [], "an empty collection is not an unknown one"
        assert f.count == 1 and f.items[0]["linkable"] == 1

    def test_a_genuinely_malformed_response_is_still_undetermined(self):
        """Known-negative. The relaxation must not swallow real failures —
        that would trade a false unknown for a false zero, which is worse."""
        from resource_explorer.egeria_resync import ScanResult
        res = ScanResult()
        f = self._scanner("CLIENT_ERROR_400 something went wrong")._scan_unlinked_members(res)
        assert len(res.undetermined) == 1
        assert f.count == 0

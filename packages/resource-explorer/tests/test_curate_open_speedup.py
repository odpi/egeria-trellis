"""Curate-page open speed-up: batched recovery reads + a DATA-keyed snapshot cache.

Opening Curate on `egeria_git` fired eight requests that each rebuilt the whole architecture-recovery
result (~7,300 SQL statements, 11-18 s apiece): one `query_findings_all_runs` and one `query_metrics`
per component, three scans per rebuild, nothing shared between requests. The fix has three parts and
this file pins each one and, more importantly, pins what must NOT have changed:

  1. the bulk reads return exactly what the per-scope reads return (`TestBulkReadsMatchPerScope`),
     and the readers built on them return exactly what they returned before (`TestReadersUnchanged`);
  2. the snapshot cache is keyed on the DATA, not on a process or an instance, so a write by anyone
     is seen by the next read (`TestSnapshotCacheIsKeyedOnData`);
  3. nothing but the two findings/metrics tables is cached, so a verdict, a materialization, a
     reclassification, a dependency confirmation or a scope choice shows on the very next read with
     the cache warm (`TestNothingElseIsCached`).

Every stale-cache guard here has a negative control or was made to fail on purpose before it was
trusted; the control tests say how. Temp SQLite throughout; no Egeria; no shared Postgres.
"""
from __future__ import annotations

import copy
import json
import threading
import time

import pytest

from resource_explorer import component_tree as ct
from resource_explorer import dependency_table as dt
from resource_explorer import node_admission
from resource_explorer.registry import WITHDRAWN_LABEL, Project, ProjectRegistry
from resource_explorer.surveyors import repo_survey_definition_adapter as A
from tests.test_dependency_two_ends import _mixed, _survey, _workspaces

KIND = "architecture_recovery"


@pytest.fixture
def registry(tmp_path_factory):
    return ProjectRegistry(db_path=str(tmp_path_factory.mktemp("db") / "t.db"))


@pytest.fixture(autouse=True)
def _empty_cache():
    """Every test starts cold: the cache is process state, so one test's snapshot must not be another's."""
    A._recovery_snapshot_cache.clear()
    A._recovery_snapshot_build_locks.clear()
    yield
    A._recovery_snapshot_cache.clear()
    A._recovery_snapshot_build_locks.clear()


def _add(registry, slug="p"):
    registry.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}"))


def _component(registry, slug, scope, ts, *, run_label="detect", type_="Software Library", name=None,
               evidence=(), confidence=70):
    """One survey step's rows for one scope: the component row plus its evidence rows, all at `ts`."""
    rows = [{"check_name": "component", "label": type_ or "component", "summary": scope, "confidence": confidence,
             "detail": {"slug": f"code::{scope}", "name": name or scope, "type": type_, "run_label": run_label,
                        "perspective": "logical", "depth": 0, "parent_slug": ""}}]
    for check_name, label in evidence:
        rows.append({"check_name": check_name, "label": label, "summary": f"{label}: {check_name}", "confidence": 40})
    registry.upsert_finding(slug, KIND, rows, surveyed_at=ts, scope_locator=scope)


def _withdraw(registry, slug, scope, ts, run_label="detect"):
    registry.upsert_finding(slug, KIND, [{"check_name": "component_withdrawn", "label": WITHDRAWN_LABEL,
                                          "detail": {"run_label": run_label}}],
                            surveyed_at=ts, scope_locator=scope)


def _seed(registry, slug="p"):
    """A small recovery with every shape the readers distinguish: two runs, an equal-timestamp tie between two
    run labels, evidence at equal timestamps, a withdrawn scope, a structural node, metrics over two runs."""
    _add(registry, slug)
    _component(registry, slug, "svc/a", "2026-10-01T00:00:00", evidence=[("identity", "package-name")])
    _component(registry, slug, "svc/a", "2026-10-02T00:00:00", run_label="coupling", type_="Console Command",
               evidence=[("identity", "package-name"), ("shape", "connective-seam")])
    # an equal-timestamp tie whose two rows are NOT interchangeable (different type, same run label)
    registry.upsert_finding(slug, KIND, [
        {"check_name": "component", "label": "Software Library", "confidence": 65,
         "detail": {"slug": "code::svc/tie", "name": "tie", "type": "Software Library", "run_label": ""}},
        {"check_name": "component", "label": "Console Command", "confidence": 75,
         "detail": {"slug": "code::svc/tie", "name": "tie", "type": "Console Command", "run_label": ""}},
    ], surveyed_at="2026-10-01T00:00:00", scope_locator="svc/tie")
    _component(registry, slug, "svc/b", "2026-10-01T00:00:00")
    _component(registry, slug, "svc/gone", "2026-10-01T00:00:00")
    _withdraw(registry, slug, "svc/gone", "2026-10-03T00:00:00")
    registry.upsert_finding(slug, KIND, [{"check_name": "structural_node", "label": "structural",
                                          "detail": {"slug": "code::svc", "name": "svc", "path": "svc", "depth": 0}}],
                            surveyed_at="2026-10-01T00:00:00", scope_locator="svc")
    registry.upsert_metric(slug, KIND, {"confidence": 70.0, "evidence_count": 1.0}, surveyed_at="2026-10-01T00:00:00",
                            scope_locator="svc/a")
    registry.upsert_metric(slug, KIND, {"confidence": 40.0, "evidence_count": 2.0, "cochange_cohesion": 0.2},
                            surveyed_at="2026-10-02T00:00:00", scope_locator="svc/a", detail={"note": "latest"})
    registry.upsert_metric(slug, KIND, {"detect_component_count": 4.0}, surveyed_at="2026-10-02T00:00:00",
                            scope_locator="", detail={"outcome": "ok", "run_scope": ""})


class _PerScopeReads:
    """The registry as it was before: bulk reads answered by the per-scope methods, never cached.
    Everything else passes through, so the readers run unchanged on top of it."""

    _n = 0

    def __init__(self, registry):
        self._r = registry
        type(self)._n += 1
        self.database_url = f"per-scope://{type(self)._n}"

    def __getattr__(self, name):
        return getattr(self._r, name)

    def analysis_data_fingerprint(self, slug, kinds):
        type(self)._n += 1
        return ("never-equal", type(self)._n)

    def query_findings_all_runs_by_scope(self, slug, kind, order_class=None):
        scopes = {r["scope_locator"] for r in self._rows(slug, kind)}
        return {s: self._r.query_findings_all_runs(slug, kind, s) for s in sorted(scopes)}

    def query_metrics_by_scope(self, slug, kind):
        out = {}
        for s in sorted({r["scope_locator"] for r in self._metric_scopes(slug, kind)}):
            m = self._r.query_metrics(slug, kind, s)
            if m:
                out[s] = m
        return out

    def _rows(self, slug, kind):
        with self._r._conn() as conn:
            return [dict(x) for x in conn.execute(
                "SELECT DISTINCT scope_locator FROM project_analysis_findings WHERE project_slug=? AND kind=?",
                (slug, kind)).fetchall()]

    def _metric_scopes(self, slug, kind):
        with self._r._conn() as conn:
            return [dict(x) for x in conn.execute(
                "SELECT DISTINCT scope_locator FROM project_analysis_metrics WHERE project_slug=? AND kind=?",
                (slug, kind)).fetchall()]


# ── 1. the bulk reads are the per-scope reads ───────────────────────────────────────────────

class TestBulkReadsMatchPerScope:
    def test_every_scope_has_exactly_the_rows_the_per_scope_query_returns(self, registry):
        _seed(registry)
        bulk = registry.query_findings_all_runs_by_scope("p", KIND, order_class=lambda r: r["check_name"] == "component")
        with registry._conn() as conn:
            scopes = [r["scope_locator"] for r in conn.execute(
                "SELECT DISTINCT scope_locator FROM project_analysis_findings WHERE project_slug='p' AND kind=?",
                (KIND,)).fetchall()]
        assert set(bulk) == set(scopes) and len(scopes) >= 5
        for s in scopes:
            assert bulk[s] == registry.query_findings_all_runs("p", KIND, s), s

    def test_a_kind_with_no_rows_is_empty_not_an_error(self, registry):
        _add(registry)
        assert registry.query_findings_all_runs_by_scope("p", KIND) == {}
        assert registry.query_metrics_by_scope("p", KIND) == {}

    def test_other_projects_and_kinds_do_not_leak_in(self, registry):
        _seed(registry, "p")
        _add(registry, "q")
        _component(registry, "q", "other", "2026-10-01T00:00:00")
        registry.upsert_finding("p", "something_else", [{"check_name": "x", "label": "y"}], scope_locator="svc/a")
        assert "other" not in registry.query_findings_all_runs_by_scope("p", KIND)
        assert all(r["check_name"] != "x" for rows in registry.query_findings_all_runs_by_scope("p", KIND).values()
                   for r in rows)

    def test_metrics_bulk_equals_the_per_scope_dict_including_latest_run_only_and_detail(self, registry):
        _seed(registry)
        bulk = registry.query_metrics_by_scope("p", KIND)
        assert set(bulk) == {"svc/a", ""}
        for s in bulk:
            assert bulk[s] == registry.query_metrics("p", KIND, s), s
        # the older run's `confidence=70` is gone; the later run's detail is there; the scope with no metrics is absent
        assert bulk["svc/a"]["confidence"] == 40.0 and bulk["svc/a"]["detail"] == {"note": "latest"}
        assert registry.query_metrics("p", KIND, "svc/b") == {} and "svc/b" not in bulk

    def test_a_metric_written_twice_with_different_values_at_one_timestamp_is_reread_per_scope(self, registry):
        """Two survey steps in one batch can both write `evidence_count` at the scope's latest instant. The
        per-scope query then reports whichever row the database returns last (physical order), which one bulk
        query cannot reproduce -- measured on genaiexamples, 44 such scopes -- so those scopes are re-read."""
        _add(registry)
        ts = "2026-10-01T00:00:00"
        registry.upsert_metric("p", KIND, {"evidence_count": 2.0}, surveyed_at=ts, scope_locator="dup")
        registry.upsert_metric("p", KIND, {"evidence_count": 1.0}, surveyed_at=ts, scope_locator="dup")
        registry.upsert_metric("p", KIND, {"evidence_count": 5.0}, surveyed_at=ts, scope_locator="same")
        registry.upsert_metric("p", KIND, {"evidence_count": 5.0}, surveyed_at=ts, scope_locator="same")
        registry.upsert_metric("p", KIND, {"evidence_count": 9.0}, surveyed_at=ts, scope_locator="single")
        reread = []
        orig = registry.query_metrics
        registry.query_metrics = lambda slug, kind, scope_locator="": reread.append(scope_locator) or orig(slug, kind, scope_locator)
        bulk = registry.query_metrics_by_scope("p", KIND)
        assert reread == ["dup"]
        for scope in ("dup", "same", "single"):
            assert bulk[scope] == orig("p", KIND, scope)

    # The tie rule. Rows written in one batch share a `surveyed_at`; the per-scope query leaves their order to
    # Postgres' sort, which one bulk query cannot reproduce (measured on egeria_git: 105 of 1,094 components). So a
    # scope whose tie is OBSERVABLE is re-read with the per-scope query itself.

    class _Spy:
        def __init__(self, registry):
            self.calls = []
            orig = registry.query_findings_all_runs

            def spy(slug, kind, scope):
                self.calls.append(scope)
                return orig(slug, kind, scope)
            registry.query_findings_all_runs = spy

    def test_a_tie_between_different_rows_of_one_class_is_reread_per_scope(self, registry):
        _seed(registry)
        spy = self._Spy(registry)
        registry.query_findings_all_runs_by_scope("p", KIND, order_class=lambda r: r["check_name"] == "component")
        assert sorted(spy.calls) == ["svc/a", "svc/tie"]      # svc/a: two different evidence rows at one timestamp

    def test_a_tie_between_identical_rows_is_not_observable_and_not_reread(self, registry):
        _add(registry)
        same = {"check_name": "component", "label": "L", "detail": {"slug": "s", "run_label": "x"}}
        registry.upsert_finding("p", KIND, [same, dict(same)], surveyed_at="2026-10-01T00:00:00", scope_locator="dup")
        spy = self._Spy(registry)
        registry.query_findings_all_runs_by_scope("p", KIND)
        assert spy.calls == []

    def test_a_component_and_an_evidence_row_at_one_timestamp_are_two_classes_when_the_caller_says_so(self, registry):
        _add(registry)
        _component(registry, "p", "x", "2026-10-01T00:00:00", evidence=[("identity", "package-name")])
        spy = self._Spy(registry)
        registry.query_findings_all_runs_by_scope("p", KIND, order_class=lambda r: r["check_name"] == "component")
        assert spy.calls == []
        registry.query_findings_all_runs_by_scope("p", KIND)          # default: one class, so that tie is observable
        assert spy.calls == ["x"]


class TestFingerprint:
    def _fp(self, registry, slug="p"):
        return registry.analysis_data_fingerprint(slug, (KIND,))

    def test_it_is_stable_when_nothing_is_written(self, registry):
        _seed(registry)
        assert self._fp(registry) == self._fp(registry)

    def test_a_new_finding_row_moves_it(self, registry):
        _seed(registry)
        before = self._fp(registry)
        _component(registry, "p", "svc/new", "2026-10-04T00:00:00")
        assert self._fp(registry) != before

    def test_a_new_metric_row_moves_it(self, registry):
        _seed(registry)
        before = self._fp(registry)
        registry.upsert_metric("p", KIND, {"confidence": 1.0}, surveyed_at="2026-10-04T00:00:00", scope_locator="svc/b")
        assert self._fp(registry) != before

    def test_stamping_a_row_superseded_in_place_moves_it(self, registry):
        """supersede is an UPDATE: no row is added or removed and no timestamp changes, so count/max(id) alone miss it."""
        _seed(registry)
        before = self._fp(registry)
        registry.upsert_finding("p", KIND, [], surveyed_at="2026-10-05T00:00:00", scope_locator="svc/b",
                                supersedes_previous=True)
        assert self._fp(registry) != before

    def test_removing_the_project_moves_it(self, registry):
        _seed(registry)
        before = self._fp(registry)
        registry.remove("p")
        assert self._fp(registry) != before

    def test_a_different_supersede_stamp_moves_it_even_when_the_count_is_unchanged(self, registry):
        _seed(registry)
        registry.upsert_finding("p", KIND, [], surveyed_at="2026-10-05T00:00:00", scope_locator="svc/b",
                                supersedes_previous=True)
        before = self._fp(registry)
        with registry._conn() as conn:
            conn.execute("UPDATE project_analysis_findings SET superseded_at = '2026-10-09T00:00:00' "
                         "WHERE superseded_at IS NOT NULL")
        assert self._fp(registry) != before

    def test_another_kind_or_another_project_does_not(self, registry):
        _seed(registry)
        _add(registry, "q")
        before = self._fp(registry)
        registry.upsert_finding("p", "unrelated_kind", [{"check_name": "x", "label": "y"}])
        _component(registry, "q", "elsewhere", "2026-10-04T00:00:00")
        assert self._fp(registry) == before


# ── the readers built on the bulk reads return what they returned before ─────────────────────

def _readers(reg, slug="p"):
    return {
        "recovery_default": A._architecture_recovery_results(reg, slug),
        "recovery_full": A._architecture_recovery_results(reg, slug, max_depth=None),
        "blueprints": A._candidate_blueprints_results(reg, slug),
        "slug_to_scope": A._blueprint_slug_to_scope_map(reg, slug),
        "diagram": A._architecture_diagram_results(reg, slug),
        "coverage": A._architecture_verdict_coverage(reg, slug),
        "headline": A._architecture_recovery_headline(reg, slug),
    }


def _same(a, b):
    """Byte-for-byte: same values AND same key order (the JSON the UI receives)."""
    assert json.dumps(a, default=str) == json.dumps(b, default=str)


class TestReadersUnchanged:
    def test_synthetic_recovery_reads_identically_through_the_snapshot_and_per_scope(self, registry):
        _seed(registry)
        new = _readers(registry)
        old = _readers(_PerScopeReads(registry))
        for k in new:
            _same(new[k], old[k])
        assert {c["path"] for c in new["recovery_full"]["components"]} >= {"svc/a", "svc/b", "svc/tie", "svc"}
        assert "svc/gone" not in {c["path"] for c in new["recovery_full"]["components"]}

    def test_a_surveyed_repository_reads_identically(self, registry, tmp_path):
        _survey(registry, "mixed", _mixed(tmp_path))
        new = _readers(registry, "mixed")
        old = _readers(_PerScopeReads(registry), "mixed")
        for k in new:
            _same(new[k], old[k])
        assert new["recovery_full"]["components"], "the fixture recovered nothing, so this compared nothing"

    def test_verdicts_and_materialization_still_merge_onto_components(self, registry):
        _seed(registry)
        registry.record_component_verdict("repo", "p", "svc/a", "accepted", decided_by="dan")
        registry.record_materialized_component("repo", "p", "svc/a", "qn", "guid-1")
        new = _readers(registry)
        old = _readers(_PerScopeReads(registry))
        for k in new:
            _same(new[k], old[k])
        a = [c for c in new["recovery_full"]["components"] if c["path"] == "svc/a"][0]
        assert a["verdict"]["verdict"] == "accepted" and a["materialized"]["guid"] == "guid-1"

    def test_the_tree_totals_equal_the_tree(self, registry):
        _seed(registry)
        registry.record_component_verdict("repo", "p", "svc/a", "accepted", decided_by="dan")
        t = ct.component_tree(registry, "p")
        assert ct.totals(registry, "p") == {"total_components": t["total_components"], "accepted": t["accepted"]}
        assert t["accepted"] == 1 and t["total_components"] >= 3

    def test_the_depth_offer_reads_the_same_counts_without_building_the_tree(self, registry, monkeypatch):
        from resource_explorer.workflows.catalogue_depth_offer import build_catalogue_depth_offer
        _seed(registry)
        registry.record_component_verdict("repo", "p", "svc/a", "accepted", decided_by="dan")
        monkeypatch.setattr(ct, "component_tree",
                            lambda *a, **k: pytest.fail("the depth offer rebuilt the whole tree for two counts"))
        offer = build_catalogue_depth_offer(registry, "p")
        want = ct.totals(registry, "p")
        assert (offer["total_components"], offer["accepted"]) == (want["total_components"], want["accepted"])

    def test_the_blueprints_route_passes_one_read_of_the_components_to_both_node_admission_readers(
            self, registry, tmp_path, monkeypatch):
        from resource_explorer.web.routes import projects as P
        _survey(registry, "ws", _workspaces(tmp_path))
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        reads = []
        real = node_admission._component_paths
        monkeypatch.setattr(node_admission, "_component_paths", lambda r, s: reads.append(s) or real(r, s))
        out = P.components_blueprints("ws")
        assert out["admission"] and len(reads) == 1, reads


# ── 2. the cache is keyed on the data ───────────────────────────────────────────────────────

class _CountBulk:
    """Counts the bulk row reads a registry instance performs (a snapshot BUILD, not a hit)."""

    def __init__(self, registry, delay=0.0):
        self.n = 0
        orig = registry.query_findings_all_runs_by_scope

        def counting(*a, **k):
            self.n += 1
            if delay:
                time.sleep(delay)         # widen the race window so concurrent cold readers really overlap
            return orig(*a, **k)
        registry.query_findings_all_runs_by_scope = counting


class TestSnapshotCacheIsKeyedOnData:
    def test_a_second_read_is_served_without_reading_the_rows_again(self, registry):
        _seed(registry)
        c = _CountBulk(registry)
        first = A._architecture_recovery_results(registry, "p", max_depth=None)
        hits = A.recovery_snapshot_stats["hits"]
        second = A._architecture_recovery_results(registry, "p", max_depth=None)
        assert c.n == 1 and A.recovery_snapshot_stats["hits"] > hits
        _same(first, second)

    def test_every_reader_shares_the_one_snapshot(self, registry):
        _seed(registry)
        c = _CountBulk(registry)
        _readers(registry)
        assert c.n == 1

    def test_a_new_registry_instance_per_request_still_hits(self, registry, tmp_path_factory):
        """The web layer builds a fresh ProjectRegistry() per request, so an instance-held cache would never hit."""
        _seed(registry)
        A._architecture_recovery_results(registry, "p")
        other = ProjectRegistry(db_path=registry.database_url.replace("sqlite:///", ""))
        c = _CountBulk(other)
        A._architecture_recovery_results(other, "p")
        assert c.n == 0

    def test_a_write_through_ANOTHER_instance_is_seen_by_a_long_lived_holder(self, registry):
        """run_queue's worker loop, its reconciler and egeria_resync hold one registry for the process lifetime."""
        _seed(registry)
        holder = registry
        before = {c["path"] for c in A._architecture_recovery_results(holder, "p", max_depth=None)["components"]}
        writer = ProjectRegistry(db_path=registry.database_url.replace("sqlite:///", ""))
        _component(writer, "p", "svc/late", "2026-10-06T00:00:00")
        after = {c["path"] for c in A._architecture_recovery_results(holder, "p", max_depth=None)["components"]}
        assert after - before == {"svc/late"}

    @pytest.mark.parametrize("name,write,check", [
        ("a new component row (a survey ran)",
         lambda r: _component(r, "p", "svc/new", "2026-10-06T00:00:00"),
         lambda res: "svc/new" in {c["path"] for c in res["components"]}),
        ("a withdrawal (a survey stopped proposing a scope)",
         lambda r: _withdraw(r, "p", "svc/b", "2026-10-06T00:00:00"),
         lambda res: "svc/b" not in {c["path"] for c in res["components"]}),
        ("a retype by a later run at the same scope",
         lambda r: _component(r, "p", "svc/b", "2026-10-06T00:00:00", run_label="coupling", type_="Console Command"),
         lambda res: [c for c in res["components"] if c["path"] == "svc/b"][0]["type"] == "Console Command"),
        ("a new metrics run",
         lambda r: r.upsert_metric("p", KIND, {"confidence": 99.0}, surveyed_at="2026-10-06T00:00:00",
                                    scope_locator="svc/b"),
         lambda res: [c for c in res["components"] if c["path"] == "svc/b"][0]["metrics"] == {"confidence": 99.0}),
        ("a run-summary outcome",
         lambda r: r.upsert_metric("p", KIND, {"detect_component_count": 0.0}, surveyed_at="2026-10-06T00:00:00",
                                    scope_locator="", detail={"outcome": "unverified", "run_scope": ""}),
         lambda res: res["unverified"] == ["detect"]),
    ])
    def test_each_findings_or_metrics_write_is_seen_by_the_next_read(self, registry, name, write, check):
        _seed(registry)
        warm = A._architecture_recovery_results(registry, "p", max_depth=None)
        assert not check(warm), f"{name}: the write was already visible before it happened"
        hits = A.recovery_snapshot_stats["hits"]
        A._architecture_recovery_results(registry, "p", max_depth=None)
        assert A.recovery_snapshot_stats["hits"] == hits + 1, "the cache was not warm, so this proves nothing"
        write(registry)
        assert check(A._architecture_recovery_results(registry, "p", max_depth=None)), name

    def test_removing_the_project_is_seen(self, registry):
        _seed(registry)
        assert A._architecture_recovery_results(registry, "p")["components"]
        registry.remove("p")
        assert A._architecture_recovery_results(registry, "p")["_status"]["state"] == "never_run"

    def test_NEGATIVE_CONTROL_with_the_fingerprint_frozen_the_same_write_goes_unseen(self, registry, monkeypatch):
        """The guard above is only worth something if it can fail. Freeze the fingerprint (a cache keyed on
        nothing) and the very same write is NOT seen -- this is the stale read the data key exists to prevent."""
        _seed(registry)
        monkeypatch.setattr(type(registry), "analysis_data_fingerprint", lambda self, slug, kinds: ("frozen",))
        A._architecture_recovery_results(registry, "p", max_depth=None)
        _component(registry, "p", "svc/new", "2026-10-06T00:00:00")
        stale = A._architecture_recovery_results(registry, "p", max_depth=None)
        assert "svc/new" not in {c["path"] for c in stale["components"]}

    def test_two_databases_with_the_same_slug_do_not_share_a_snapshot(self, registry, tmp_path_factory):
        _seed(registry)
        elsewhere = ProjectRegistry(db_path=str(tmp_path_factory.mktemp("db2") / "t.db"))
        _add(elsewhere)
        _component(elsewhere, "p", "only/here", "2026-10-01T00:00:00")
        a = {c["path"] for c in A._architecture_recovery_results(registry, "p", max_depth=None)["components"]}
        b = {c["path"] for c in A._architecture_recovery_results(elsewhere, "p", max_depth=None)["components"]}
        assert "only/here" not in a and b == {"only/here"}

    def test_the_cache_is_bounded(self, registry):
        for i in range(A._RECOVERY_SNAPSHOT_CACHE_MAX + 3):
            _add(registry, f"s{i}")
            _component(registry, f"s{i}", "x", "2026-10-01T00:00:00")
            A._architecture_recovery_results(registry, f"s{i}")
        assert len(A._recovery_snapshot_cache) == A._RECOVERY_SNAPSHOT_CACHE_MAX
        assert len(A._recovery_snapshot_build_locks) <= A._RECOVERY_SNAPSHOT_CACHE_MAX + 1

    def test_concurrent_cold_readers_build_the_snapshot_once(self, registry):
        _seed(registry)
        c = _CountBulk(registry, delay=0.3)
        errors, barrier = [], threading.Barrier(6)

        def go():
            try:
                barrier.wait()
                A._architecture_recovery_results(registry, "p", max_depth=None)
            except Exception as exc:        # noqa: BLE001
                errors.append(exc)
        ts = [threading.Thread(target=go) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors and c.n == 1

    @staticmethod
    def _thaw(o):
        from types import MappingProxyType
        if isinstance(o, MappingProxyType):
            return {k: TestSnapshotCacheIsKeyedOnData._thaw(v) for k, v in o.items()}
        if isinstance(o, tuple):
            return [TestSnapshotCacheIsKeyedOnData._thaw(v) for v in o]
        return o

    def test_the_snapshot_is_read_only_by_construction(self, registry):
        """One `row["label"] = ...` in a future reader would corrupt every later request in the process
        until the next survey write. Frozen: it raises instead."""
        _seed(registry)
        snap = A._recovery_snapshot(registry, "p")
        row = snap["rows"]["svc/a"][0]
        for bad in (lambda: row.__setitem__("label", "x"),
                    lambda: snap["rows"].__setitem__("zzz", ()),
                    lambda: snap["metrics"]["svc/a"].__setitem__("confidence", 0),
                    lambda: snap["scopes"].__setitem__("component", ()),
                    lambda: snap["rows"]["svc/a"].append(row)):
            with pytest.raises((TypeError, AttributeError)):
                bad()

    def test_no_reader_changes_the_shared_snapshot(self, registry, tmp_path, monkeypatch):
        """Compare the snapshot with a thawed copy taken before, after EVERY reader that consumes it: results,
        evidence, blueprints, diagram fact, component tree and leaves, depth offer, plan, dependencies and
        blueprint_shape.component_nodes."""
        from resource_explorer import blueprint_shape
        from resource_explorer.curate_plan import build_plan
        from resource_explorer.facts import FactLayer
        from resource_explorer.workflows.catalogue_depth_offer import build_catalogue_depth_offer
        monkeypatch.setattr("resource_explorer.gaps.record_gaps_for", lambda *a, **k: [])
        _seed(registry)
        registry.upsert_finding("p", "architecture_blueprints", [{
            "check_name": "candidate_blueprint", "label": "logical-1",
            "detail": {"perspective": "logical", "name": "logical-1", "members": ["code::svc/a"], "children": []}}],
            surveyed_at="2026-10-02T00:00:00", scope_locator="")
        A._architecture_recovery_results(registry, "p")
        key = (registry.database_url, "p")
        before = self._thaw(A._recovery_snapshot_cache[key][1])
        readers = {
            "results": lambda: _readers(registry),
            "tree": lambda: ct.component_tree(registry, "p"),
            "leaves": lambda: ct.leaves(registry, "p", "svc"),
            "depth_offer": lambda: build_catalogue_depth_offer(registry, "p"),
            "plan": lambda: build_plan(registry, "p"),
            "dependencies": lambda: dt.build_table(registry, "p"),
            "component_nodes": lambda: blueprint_shape.component_nodes(registry, "p"),
            "admission": lambda: node_admission.summary(registry, "p"),
            "diagram_fact": lambda: FactLayer(registry).fact("p", "architecture_diagram"),
        }
        for name, read in readers.items():
            read()
            assert self._thaw(A._recovery_snapshot_cache[key][1]) == before, f"{name} changed the snapshot"

    def test_what_is_cached_is_only_findings_derived_data(self, registry):
        """Verdicts, materialization, promotions, reclassifications and confirmations are re-read every call; the
        snapshot therefore must not carry any of them."""
        _seed(registry)
        registry.record_component_verdict("repo", "p", "svc/a", "accepted", decided_by="dan")
        A._architecture_recovery_results(registry, "p")
        snap = A._recovery_snapshot_cache[(registry.database_url, "p")][1]
        assert set(snap) == {"rows", "metrics", "scopes"}
        assert "accepted" not in json.dumps(snap, default=str)


# ── 3. nothing else is cached: every other write shows on the very next read, cache warm ─────

class TestNothingElseIsCached:
    def _warm(self, registry, slug="p"):
        A._architecture_recovery_results(registry, slug, max_depth=None)
        before = A.recovery_snapshot_stats["hits"]
        A._architecture_recovery_results(registry, slug, max_depth=None)
        assert A.recovery_snapshot_stats["hits"] == before + 1, "the cache was not warm, so this proves nothing"

    def _hit(self, before):
        assert A.recovery_snapshot_stats["hits"] > before, "the read after the write did not come from the cache"

    def test_a_verdict(self, registry):
        _seed(registry)
        self._warm(registry)
        h = A.recovery_snapshot_stats["hits"]
        registry.record_component_verdict("repo", "p", "svc/b", "rejected", decided_by="dan")
        res = A._architecture_recovery_results(registry, "p", max_depth=None)
        self._hit(h)
        assert [c for c in res["components"] if c["path"] == "svc/b"][0]["verdict"]["verdict"] == "rejected"
        assert res["verdict_coverage"]["components"]["rejected"] == 1
        assert ct.totals(registry, "p")["accepted"] == 0
        registry.record_component_verdict("repo", "p", "svc/b", "accepted", decided_by="dan")
        assert ct.totals(registry, "p")["accepted"] == 1

    def test_a_blueprint_verdict_and_a_blueprint_materialization(self, registry):
        _seed(registry)
        registry.upsert_finding("p", "architecture_blueprints", [{
            "check_name": "candidate_blueprint", "label": "logical-1",
            "detail": {"perspective": "logical", "name": "logical-1", "members": ["code::svc/a"], "children": []}}],
            surveyed_at="2026-10-02T00:00:00", scope_locator="")
        self._warm(registry)
        h = A.recovery_snapshot_stats["hits"]
        registry.record_component_verdict("repo", "p", "logical::logical-1", "accepted", verdict_target="blueprint",
                                          decided_by="dan")
        bp = A._candidate_blueprints_results(registry, "p")
        self._hit(h)
        assert bp[0]["verdict"]["verdict"] == "accepted"
        assert A._architecture_verdict_coverage(registry, "p")["blueprints_by_perspective"]["logical"]["accepted"] == 1
        registry.record_materialized_blueprint("repo", "p", "logical", "logical-1", "qn", "bp-guid")
        assert A._candidate_blueprints_results(registry, "p")[0]["materialized"]["guid"] == "bp-guid"

    def test_a_materialization(self, registry):
        _seed(registry)
        self._warm(registry)
        registry.record_materialized_component("repo", "p", "svc/a", "qn", "guid-1")
        res = A._architecture_recovery_results(registry, "p", max_depth=None)
        assert [c for c in res["components"] if c["path"] == "svc/a"][0]["materialized"]["guid"] == "guid-1"

    def test_a_reclassification(self, registry):
        _seed(registry)
        self._warm(registry)
        h = A.recovery_snapshot_stats["hits"]
        assert node_admission.summary(registry, "p")["counts"]["built_here"] >= 2
        node_admission.reclassify(registry, "p", "svc/b", "referenced_only", "it is a vendored service", "dan")
        s = node_admission.summary(registry, "p")
        self._hit(h)
        assert s["referenced"] == 1
        assert "svc/b" not in {c["path"] for c in ct._components(registry, "p")}

    def test_a_dependency_confirmation(self, registry, tmp_path):
        _survey(registry, "ws", _workspaces(tmp_path))
        self._warm(registry, "ws")
        h = A.recovery_snapshot_stats["hits"]
        row = [r for r in dt.build_table(registry, "ws")["rows"] if r["state"] == "proposed"][0]
        dt.record_confirmations(registry, "ws", [row["key"]], "confirmed", "dan")
        again = [r for r in dt.build_table(registry, "ws")["rows"] if r["key"] == row["key"]][0]
        self._hit(h)
        assert again["state"] != "proposed" and "dan" in json.dumps(again)

    def test_a_scope_choice(self, registry):
        _seed(registry)
        self._warm(registry)
        h = A.recovery_snapshot_stats["hits"]
        registry.append_resource_scope_event("repo", "p", locator="svc", kind="folder", choice="leave_out",
                                             author="dan")
        A._architecture_recovery_results(registry, "p", max_depth=None)
        self._hit(h)
        assert [e["choice"] for e in registry.list_resource_scope_events("repo", "p")] == ["leave_out"]

    def test_NEGATIVE_CONTROL_a_cache_that_did_hold_verdicts_would_hide_one(self, registry, monkeypatch):
        """If verdicts were folded into the cached value, the verdict test above would fail exactly like this:
        serve a stale result (memoised before the write) and the new verdict is invisible."""
        _seed(registry)
        stale = copy.deepcopy(A._architecture_recovery_results(registry, "p", max_depth=None))
        registry.record_component_verdict("repo", "p", "svc/b", "rejected", decided_by="dan")
        monkeypatch.setattr(A, "_architecture_recovery_results", lambda *a, **k: stale)
        got = A._architecture_recovery_results(registry, "p", max_depth=None)
        assert [c for c in got["components"] if c["path"] == "svc/b"][0]["verdict"] is None or \
            [c for c in got["components"] if c["path"] == "svc/b"][0]["verdict"]["verdict"] != "rejected"


# ── 3b. the per-fact memo (FactLayer reads results and headline from one analysis) ───────────

class TestPerFactReadMemo:
    def _layer(self, registry):
        from resource_explorer.facts import FactLayer
        layer = FactLayer(registry)
        # Skip the activity-log lookup: pretend the analysis ran, so fact() reads results AND headline.
        layer._run_cache["p"] = {"architecture_recovery": {"last_run_at": "2026-10-02T00:00:00", "partial": False,
                                                           "basis": "measured"},
                                 "architecture_diagram": {"last_run_at": "2026-10-02T00:00:00", "partial": False,
                                                          "basis": "measured"}}
        return layer

    @pytest.mark.parametrize("analysis_id", ["architecture_recovery", "architecture_diagram"])
    def test_one_fact_builds_the_result_once_and_the_next_fact_builds_it_again(self, registry, monkeypatch, analysis_id):
        _seed(registry)
        builds = {"n": 0}
        real = A._architecture_interfaces_results if analysis_id == "architecture_recovery" else A._read_arch_recovery_ir

        def counting(*a, **k):
            builds["n"] += 1
            return real(*a, **k)
        if analysis_id == "architecture_recovery":
            monkeypatch.setattr(A, "_architecture_interfaces_results", counting)
        else:
            monkeypatch.setattr(A, "_read_arch_recovery_ir", counting)
        layer = self._layer(registry)
        fact = layer.fact("p", analysis_id)
        assert fact.headline, "no headline was read, so this proved nothing about sharing it"
        per_fact = builds["n"]
        assert per_fact >= 1
        layer.fact("p", analysis_id)
        assert builds["n"] == 2 * per_fact, "the second fact() was answered from the first one's memo"
        if analysis_id == "architecture_recovery":
            assert per_fact == 1, "results + headline built the recovery twice inside one fact"
        else:
            assert per_fact == 2, "results + headline read the IR more than once per perspective"

    def test_the_headline_is_what_it_was_without_the_memo(self, registry):
        _seed(registry)
        layer = self._layer(registry)
        with_memo = layer.fact("p", "architecture_recovery")
        assert with_memo.headline == A._architecture_recovery_headline(registry, "p")["label"]
        _same(with_memo.value, A._architecture_recovery_results(registry, "p"))

    def test_the_memo_is_closed_when_the_fact_returns(self, registry):
        from resource_explorer import read_memo
        _seed(registry)
        self._layer(registry).fact("p", "architecture_recovery")
        assert read_memo._memo.get() is None

    def test_the_memo_hands_out_copies_so_a_mutation_cannot_cross_between_callers(self):
        from resource_explorer import read_memo

        @read_memo.memoize
        def f(registry, slug):
            return {"a": [1, 2]}
        with read_memo.scope():
            x = f(object(), "s")
            r = object()
            y = f(r, "s")
            y["a"].append(3)
            z = f(r, "s")
        assert x == {"a": [1, 2]} and z == {"a": [1, 2]}

    def test_outside_a_scope_it_is_a_pass_through(self):
        from resource_explorer import read_memo
        n = {"c": 0}

        @read_memo.memoize
        def f(registry, slug):
            n["c"] += 1
            return n["c"]
        r = object()
        assert (f(r, "s"), f(r, "s")) == (1, 2)

    def test_different_arguments_are_different_entries(self):
        from resource_explorer import read_memo
        n = {"c": 0}

        @read_memo.memoize
        def f(registry, slug, max_depth=3):
            n["c"] += 1
            return (slug, max_depth)
        r = object()
        with read_memo.scope():
            assert f(r, "s") == ("s", 3) and f(r, "s", max_depth=None) == ("s", None) and f(r, "t") == ("t", 3)
            f(r, "s")
        assert n["c"] == 3

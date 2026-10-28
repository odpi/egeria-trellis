"""Findings supersession: a new run of the same analysis on the same resource
retires that resource's previous rows for that analysis
(FINDINGS-SUPERSESSION-IMPLEMENTED.md).

Every test uses a temp SQLite registry created inside the test, with the URL
passed explicitly. Nothing here can reach a real database, Egeria or Prefect.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.sub_surveyors.contribution_provenance import (
    FINDING_KIND as PROV_KIND,
    ContributionProvenanceSurveyor,
)
from resource_explorer.surveyors.sub_surveyors.secret_scan import (
    FINDING_KIND as SECRET_KIND,
    SecretScanSurveyor,
)
from resource_explorer.surveyors.sub_surveyors.sla_content import (
    FINDING_KIND as SLA_KIND,
    SlaContentSurveyor,
)
from resource_explorer.surveyors.sub_surveyors.telemetry_scan import (
    FINDING_KIND as TEL_KIND,
    TelemetryScanSurveyor,
)

T1 = "2026-09-02T10:00:00"
T2 = "2026-10-02T10:00:00"
T3 = "2026-10-02T11:00:00"

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "repair_findings_supersession.py"
_spec = importlib.util.spec_from_file_location("repair_findings_supersession", _SCRIPT)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'registry.db'}"
    assert url.startswith("sqlite:///") and str(tmp_path) in url
    return url


@pytest.fixture
def registry(db_url, tmp_path):
    reg = ProjectRegistry(database_url=db_url)
    # The guard every test in this file leans on: a temp SQLite file under
    # tmp_path, never a server.
    assert reg.database_url == db_url
    assert reg.database_url.startswith("sqlite:///")
    assert str(tmp_path) in reg.database_url
    return reg


def _add(registry, slug):
    registry.add(Project(slug=slug, display_name=slug,
                         github_url=f"https://github.com/test/{slug}", collections=[]))
    return registry.get(slug)


@pytest.fixture
def project(registry):
    return _add(registry, "myrepo")


def _inventory(registry, slug, root, files):
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
        paths.append((rel, len(content)))
    registry.upsert_file_inventory(slug, paths)


def _raw(registry, slug, kind, scope=""):
    """Every row incl. superseded_at, oldest first: (surveyed_at, superseded_at)."""
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT surveyed_at, superseded_at FROM project_analysis_findings "
            "WHERE project_slug = ? AND kind = ? AND scope_locator = ? "
            "ORDER BY surveyed_at, id", (slug, kind, scope)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _current_runs(registry, slug, kind, scope=""):
    return {s for s, sup in _raw(registry, slug, kind, scope) if sup is None}


ANALYSES = [
    pytest.param(TEL_KIND, TelemetryScanSurveyor,
                 {"a.py": "x = 1\n"}, {"a.py": "sentry_dsn = 'https://x@o1.ingest.sentry.io/1'\n"},
                 id="telemetry"),
    pytest.param(PROV_KIND, ContributionProvenanceSurveyor,
                 {"README.md": "# hi\n"}, {"README.md": "# hi\n", "CONTRIBUTING.md": "Please sign off (DCO).\n"},
                 id="provenance"),
    pytest.param(SLA_KIND, SlaContentSurveyor,
                 {"README.md": "# hi\n"}, {"README.md": "# hi\n", "SLA.md": "Uptime 99.9% response time 4h\n"},
                 id="sla"),
]


def _run(cls, project, registry, root, ts):
    return cls(project, registry, local_path=str(root), surveyed_at=ts).run()


class TestWritePath:
    @pytest.mark.parametrize("kind,cls,files1,files2", ANALYSES)
    def test_second_run_supersedes_first_runs_rows(
        self, tmp_path, registry, project, kind, cls, files1, files2
    ):
        root = tmp_path / "root"
        _inventory(registry, project.slug, root, files1)
        _run(cls, project, registry, root, T1)
        first = _raw(registry, project.slug, kind)
        assert first and all(s == T1 and sup is None for s, sup in first)

        _inventory(registry, project.slug, root, files2)
        _run(cls, project, registry, root, T2)

        rows = _raw(registry, project.slug, kind)
        assert {s for s, sup in rows if sup is None} == {T2}
        older = [(s, sup) for s, sup in rows if s == T1]
        assert older and all(sup == T2 for _, sup in older)
        # The reader sees only the latest run.
        assert {r["surveyed_at"] for r in registry.query_findings(project.slug, kind)} == {T2}
        # History is untouched: nothing deleted.
        assert len(registry.query_findings_history_raw(project.slug, kind)) == len(rows)

    @pytest.mark.parametrize("kind,cls,files1,files2", ANALYSES)
    def test_failed_second_run_leaves_first_run_current_KNOWN_NEGATIVE(
        self, tmp_path, registry, project, monkeypatch, kind, cls, files1, files2
    ):
        root = tmp_path / "root"
        _inventory(registry, project.slug, root, files1)
        _run(cls, project, registry, root, T1)

        def boom(*a, **k):
            raise RuntimeError("inventory read failed")
        monkeypatch.setattr(registry, "get_file_inventory", boom)
        _run(cls, project, registry, root, T2)

        rows = _raw(registry, project.slug, kind)
        assert {s for s, _ in rows} == {T1}, "a failed run must write nothing"
        assert all(sup is None for _, sup in rows), "and must retire nothing"
        assert registry.query_findings(project.slug, kind)

    @pytest.mark.parametrize("kind,cls,files1,files2", ANALYSES)
    def test_measured_empty_second_run_supersedes_the_first(
        self, tmp_path, registry, project, kind, cls, files1, files2
    ):
        root = tmp_path / "root"
        _inventory(registry, project.slug, root, files1)
        _run(cls, project, registry, root, T1)

        cls(project, registry, local_path=str(root), surveyed_at=T2)._persist([])

        assert registry.query_findings(project.slug, kind) == []
        assert _current_runs(registry, project.slug, kind) == set()
        assert all(sup == T2 for _, sup in _raw(registry, project.slug, kind))

    @pytest.mark.parametrize("kind,cls,files1,files2", ANALYSES)
    def test_other_resources_are_not_superseded(
        self, tmp_path, registry, project, kind, cls, files1, files2
    ):
        other = _add(registry, "otherrepo")
        for p, ts in ((project, T1), (other, T1)):
            root = tmp_path / p.slug
            _inventory(registry, p.slug, root, files1)
            _run(cls, p, registry, root, ts)
        _run(cls, project, registry, tmp_path / project.slug, T2)

        assert _current_runs(registry, "otherrepo", kind) == {T1}
        assert _current_runs(registry, "myrepo", kind) == {T2}

    def test_scoped_findings_supersede_only_within_their_scope(self, registry, project):
        f = [{"check_name": "c", "label": "present", "summary": "s"}]
        registry.upsert_finding("myrepo", TEL_KIND, f, surveyed_at=T1, scope_locator="a",
                                supersedes_previous=True)
        registry.upsert_finding("myrepo", TEL_KIND, f, surveyed_at=T1, scope_locator="b",
                                supersedes_previous=True)
        registry.upsert_finding("myrepo", TEL_KIND, f, surveyed_at=T2, scope_locator="a",
                                supersedes_previous=True)
        assert _current_runs(registry, "myrepo", TEL_KIND, "a") == {T2}
        assert _current_runs(registry, "myrepo", TEL_KIND, "b") == {T1}

    def test_secret_scan_behaviour_is_unchanged(self, tmp_path, registry, project):
        root = tmp_path / "root"
        _inventory(registry, project.slug, root, {"a.py": "x = 1\n"})
        SecretScanSurveyor(project, registry, local_path=str(root), surveyed_at=T1).run()
        SecretScanSurveyor(project, registry, local_path=str(root), surveyed_at=T2).run()
        rows = _raw(registry, project.slug, SECRET_KIND)
        assert {s for s, sup in rows if sup is None} == {T2}
        assert all(sup == T2 for s, sup in rows if s == T1) and any(s == T1 for s, _ in rows)

    def test_an_unconverted_kind_still_appends_without_superseding(self, registry, project):
        """Scope pin: only the three analyses (plus the pre-existing security
        family) opted in. A kind that did not is untouched by this change."""
        f = [{"check_name": "c", "label": "x", "summary": "s"}]
        registry.upsert_finding("myrepo", "cii_badge", f, surveyed_at=T1)
        registry.upsert_finding("myrepo", "cii_badge", f, surveyed_at=T2)
        assert _current_runs(registry, "myrepo", "cii_badge") == {T1, T2}


class TestReaders:
    """Counting current findings must not depend on the data having been
    repaired. Fixtures here are UNREPAIRED: older runs have superseded_at NULL."""

    @staticmethod
    def _rows(registry, kind, slug="myrepo"):
        return registry.analysis_result_summary([slug], [kind]).get((slug, kind), {}).get("rows", 0)

    def test_summary_counts_only_the_latest_run_on_unrepaired_rows(self, registry, project):
        mk = lambda n: [{"check_name": f"c{i}", "label": "x", "summary": "s"} for i in range(n)]
        registry.upsert_finding("myrepo", TEL_KIND, mk(5), surveyed_at=T1)
        registry.upsert_finding("myrepo", TEL_KIND, mk(7), surveyed_at=T2)
        registry.upsert_finding("myrepo", TEL_KIND, mk(2), surveyed_at=T3)
        assert self._rows(registry, TEL_KIND) == 2
        assert len(registry.query_findings("myrepo", TEL_KIND)) == 2

    def test_summary_excludes_superseded_rows_and_keeps_measured_at(self, registry, project):
        f = [{"check_name": "c", "label": "x", "summary": "s"}]
        registry.upsert_finding("myrepo", TEL_KIND, f, surveyed_at=T1)
        registry.upsert_finding("myrepo", TEL_KIND, [], surveyed_at=T2, supersedes_previous=True)
        out = registry.analysis_result_summary(["myrepo"], [TEL_KIND])[("myrepo", TEL_KIND)]
        assert out["rows"] == 0
        assert out["measured_at"] == T1

    def test_summary_is_per_scope_latest_run(self, registry, project):
        f = [{"check_name": "c", "label": "x", "summary": "s"}]
        registry.upsert_finding("myrepo", "scoped_kind", f, surveyed_at=T1, scope_locator="a")
        registry.upsert_finding("myrepo", "scoped_kind", f * 2, surveyed_at=T2, scope_locator="a")
        registry.upsert_finding("myrepo", "scoped_kind", f, surveyed_at=T1, scope_locator="b")
        # a: latest run (2 rows); b: its only run (1 row); a's T1 row not counted.
        assert self._rows(registry, "scoped_kind") == 3

    def test_summary_still_counts_across_runs_for_all_runs_kinds(self, registry, project):
        f = [{"check_name": "c", "label": "x", "summary": "s"}]
        registry.upsert_finding("myrepo", "architecture_recovery", f, surveyed_at=T1, scope_locator="a")
        registry.upsert_finding("myrepo", "architecture_recovery", f, surveyed_at=T2, scope_locator="a")
        assert self._rows(registry, "architecture_recovery") == 2


class TestRepairScript:
    SECRET_TEXT = "DISTINCTIVE-FINDING-TEXT-123"

    @pytest.fixture
    def unrepaired(self, registry):
        for slug in ("one", "two"):
            _add(registry, slug)
        mk = lambda n: [{"check_name": f"c{i}", "label": "x", "summary": self.SECRET_TEXT}
                        for i in range(n)]
        for ts, n in ((T1, 3), (T2, 2), (T3, 4)):
            registry.upsert_finding("one", TEL_KIND, mk(n), surveyed_at=ts)
        registry.upsert_finding("one", SLA_KIND, mk(1), surveyed_at=T1)   # single run
        registry.upsert_finding("two", PROV_KIND, mk(2), surveyed_at=T1)
        registry.upsert_finding("two", PROV_KIND, mk(2), surveyed_at=T2)
        registry.upsert_finding("two", "cii_badge", mk(1), surveyed_at=T1)  # not a target kind
        registry.upsert_finding("two", "cii_badge", mk(1), surveyed_at=T2)
        return registry

    def test_dry_run_is_the_default_and_prints_counts_only(self, unrepaired, db_url, capsys):
        before = {k: _raw(unrepaired, "one", k) for k in (TEL_KIND, SLA_KIND)}
        assert repair.main(["--database-url", db_url]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "nothing written" in out
        assert "one  telemetry_scan_findings: 5 row(s) from 2 older run(s)" in out
        assert "two  contribution_provenance_findings: 2 row(s) from 1 older run(s)" in out
        assert "resources affected: 2" in out and "marked superseded: 7" in out
        assert self.SECRET_TEXT not in out
        assert "cii_badge" not in out and "one  sla_content" not in out
        assert {k: _raw(unrepaired, "one", k) for k in before} == before
        assert _current_runs(unrepaired, "one", TEL_KIND) == {T1, T2, T3}

    def test_apply_is_idempotent(self, unrepaired, db_url, capsys):
        assert repair.main(["--database-url", db_url, "--apply"]) == 0
        assert "applied: 7 row(s)" in capsys.readouterr().out
        assert _current_runs(unrepaired, "one", TEL_KIND) == {T3}
        assert _current_runs(unrepaired, "two", PROV_KIND) == {T2}
        assert _current_runs(unrepaired, "one", SLA_KIND) == {T1}
        assert _current_runs(unrepaired, "two", "cii_badge") == {T1, T2}  # untouched
        snapshot = _raw(unrepaired, "one", TEL_KIND)
        assert all(sup == T3 for s, sup in snapshot if s != T3)

        assert repair.main(["--database-url", db_url, "--apply"]) == 0
        out = capsys.readouterr().out
        assert "applied: 0 row(s)" in out and "affected: 0" in out
        assert _raw(unrepaired, "one", TEL_KIND) == snapshot

    def test_refuses_a_server_url_without_the_flag_and_never_connects(
        self, monkeypatch, capsys
    ):
        def no_connect(*a, **k):
            raise AssertionError("must not even build an engine")
        monkeypatch.setattr(repair, "create_engine", no_connect)
        url = "postgresql://u:p@localhost:5442/anything"
        assert repair.main(["--database-url", url]) == 2
        assert "shared registry" in capsys.readouterr().err
        monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
        assert repair.main(["--apply"]) == 2

    def test_the_flag_is_what_lets_a_server_url_through(self, monkeypatch):
        class Passed(Exception):
            pass

        def stop(*a, **k):
            raise Passed
        monkeypatch.setattr(repair, "create_engine", stop)
        with pytest.raises(Passed):
            repair.main(["--database-url", "postgresql://u@localhost:1/x",
                         repair.SHARED_FLAG])

    def test_refuses_when_no_registry_is_named(self, monkeypatch, capsys):
        monkeypatch.delenv("REGISTRY_DATABASE_URL", raising=False)
        assert repair.main([]) == 2
        assert "no registry named" in capsys.readouterr().err

    def test_never_creates_a_missing_sqlite_file(self, tmp_path, capsys):
        missing = tmp_path / "nope.db"
        assert repair.main(["--database-url", f"sqlite:///{missing}"]) == 2
        assert not missing.exists()

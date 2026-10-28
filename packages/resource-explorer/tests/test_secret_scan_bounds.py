"""Bounds on the secret scanner (resource_explorer/surveyors/sub_surveyors/
secret_ruleset.py + secret_scan.py).

Background: on 2026-10-03 repo_secret_scan ran ~50 minutes on docling_core,
pure CPU, unbounded, in the web process. Profile and decisions:
docs/design-notes/implemented/SECRET-SCAN-BOUNDS-IMPLEMENTED.md.

Every registry here is a temp SQLite file created inside the test; nothing
here touches a database server, Prefect or Egeria.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import NO_SIGNAL, PARTIAL, RECOVERED
from resource_explorer.surveyors.sub_surveyors import secret_ruleset as rs_mod
from resource_explorer.surveyors.sub_surveyors.secret_ruleset import (
    ScanBounds,
    SecretRule,
    SecretRuleset,
    load_ruleset,
)
from resource_explorer.surveyors.sub_surveyors.secret_scan import (
    FINDING_KIND,
    SecretScanSurveyor,
)

# Assembled at runtime so no literal credential-shaped string sits in the repo.
FAKE_AWS = "AKIA" + "TESTFAKEKEY2345Q"
AWS_LINE = f'aws_key = "{FAKE_AWS}"\n'


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "bounds.db"))
    # Safety guard: this file must only ever talk to a temp SQLite registry.
    assert str(reg.db_path).startswith(str(tmp_path)), reg.db_path
    assert str(reg.db_path).endswith(".db")
    return reg


@pytest.fixture
def project(registry):
    p = Project(slug="boundsrepo", display_name="Bounds Repo",
                github_url="https://github.com/test/boundsrepo", collections=[])
    registry.add(p)
    return p


def _inventory(registry, slug, root: Path, files: dict[str, bytes | str]) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for rel, content in files.items():
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content)
        rows.append((rel, full.stat().st_size))
    registry.upsert_file_inventory(slug, rows)
    return [r[0] for r in rows]


def _rows(registry, slug, check_name):
    out = []
    for r in registry.query_findings(slug, FINDING_KIND):
        if r["check_name"] == check_name:
            r["detail"] = json.loads(r["detail_json"]) if r.get("detail_json") else {}
            out.append(r)
    return out


def _run(project, registry, root):
    return SecretScanSurveyor(project, registry, str(root), surveyed_at="2026-10-03T00:00:00").run()


def _pathological_ruleset() -> SecretRuleset:
    """One exponential-backtracking rule keyed on 'xxxx'. Real RE rules are
    not exponential (they are ~50x50 polynomial), but `re` is equally
    uninterruptible, so this proves the MECHANISM bounds a rule that does not
    return."""
    rule = SecretRule(
        rule_id="pathological", description="exponential",
        pattern=re.compile(r"(x+x+)+y"), keywords=("xxxx",))
    return SecretRuleset(rules=[rule], excluded_path_patterns=[],
                         excluded_value_patterns=[], source_path=Path("synthetic"))


@pytest.fixture
def tiny_windows(monkeypatch):
    monkeypatch.setattr(rs_mod, "WHOLE_FILE_CHARS", 100)
    monkeypatch.setattr(rs_mod, "WINDOW_CHARS", 12)
    monkeypatch.setattr(rs_mod, "WINDOW_OVERLAP_CHARS", 3)


class TestBoundsConfig:
    def test_defaults_are_the_documented_ones(self):
        b = ScanBounds()
        assert (b.max_file_bytes, b.file_budget_seconds, b.total_budget_seconds) == (
            5 * 1024 * 1024, 30.0, 900.0)

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv(rs_mod.ENV_MAX_FILE_BYTES, "1234")
        monkeypatch.setenv(rs_mod.ENV_FILE_BUDGET_SECONDS, "2.5")
        monkeypatch.setenv(rs_mod.ENV_TOTAL_BUDGET_SECONDS, "77")
        b = ScanBounds.from_environment()
        assert (b.max_file_bytes, b.file_budget_seconds, b.total_budget_seconds) == (1234, 2.5, 77.0)

    @pytest.mark.parametrize("bad", ["abc", "0", "-5", ""])
    def test_bad_env_falls_back_to_default_KNOWN_NEGATIVE(self, monkeypatch, bad):
        monkeypatch.setenv(rs_mod.ENV_MAX_FILE_BYTES, bad)
        assert ScanBounds.from_environment().max_file_bytes == rs_mod.DEFAULT_MAX_FILE_BYTES


class TestPathologicalInputIsBounded:
    def test_file_budget_stops_a_rule_that_does_not_return(self, tmp_path, tiny_windows):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "evil.txt").write_text("x" * 4000)       # ~330 windows of 12 chars
        rs = _pathological_ruleset()
        t0 = time.monotonic()
        matches, report = rs.scan_paths(
            root, ["evil.txt"],
            ScanBounds(max_file_bytes=10**6, file_budget_seconds=1.0, total_budget_seconds=60))
        elapsed = time.monotonic() - t0
        assert elapsed < 20, f"budget did not bound the scan: {elapsed:.1f}s"
        assert report.partial and len(report.timed_out) == 1
        assert report.timed_out[0][0] == "evil.txt"
        assert report.files_scanned == 0          # not counted as fully scanned

    def test_total_budget_counts_files_it_never_opened(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        for n in "abc":
            (root / f"{n}.py").write_text("print(1)\n")
        rs = load_ruleset()
        matches, report = rs.scan_paths(
            root, ["a.py", "b.py", "c.py"],
            ScanBounds(max_file_bytes=10**6, file_budget_seconds=5, total_budget_seconds=1e-9))
        assert report.total_budget_hit and report.partial
        assert sorted(p for p, _ in report.not_reached) == ["a.py", "b.py", "c.py"]
        assert report.files_scanned == 0

    def test_surveyor_says_partial_not_no_signal_when_a_budget_hit(
            self, registry, project, tmp_path, tiny_windows, monkeypatch):
        root = tmp_path / "zip"
        _inventory(registry, project.slug, root, {"evil.txt": "x" * 4000})
        monkeypatch.setenv(rs_mod.ENV_FILE_BUDGET_SECONDS, "1")
        monkeypatch.setattr(rs_mod, "load_ruleset", lambda *a, **k: _pathological_ruleset())
        # the surveyor reaches load_ruleset through ruleset_mod; self-test needs real rules
        monkeypatch.setattr(rs_mod, "run_self_test",
                            lambda rules: rs_mod.SelfTestResult(True, frozenset(), frozenset(), frozenset()))
        results = _run(project, registry, root)
        summary = _rows(registry, project.slug, "scan_summary")[0]
        assert summary["label"] == PARTIAL
        assert summary["label"] != NO_SIGNAL
        assert summary["detail"]["files_timed_out"] == 1
        assert "PARTIAL" in summary["summary"]


class TestSkipsAreRecordedNeverSilent:
    def test_oversize_file_skipped_counted_and_scan_is_partial(
            self, registry, project, tmp_path, monkeypatch):
        monkeypatch.setenv(rs_mod.ENV_MAX_FILE_BYTES, "2000")
        root = tmp_path / "zip"
        _inventory(registry, project.slug, root, {
            "small.py": "print('ok')\n",
            "data/huge.json": '{"k": "' + "z" * 5000 + '"}\n',
        })
        _run(project, registry, root)
        s = _rows(registry, project.slug, "scan_summary")[0]
        assert s["label"] == PARTIAL and s["label"] not in (NO_SIGNAL, RECOVERED)
        d = s["detail"]
        assert d["files_skipped_size"] == 1
        assert d["top_skipped_size"][0][0] == "data/huge.json"
        assert d["scan_bounds"]["max_file_bytes"] == 2000
        assert "data/huge.json" in s["summary"] and "NOT a clean result" in s["summary"]

    def test_binary_file_skipped_and_counted(self, registry, project, tmp_path):
        root = tmp_path / "zip"
        _inventory(registry, project.slug, root, {
            "app.py": "print('ok')\n",
            "payload.dat": b"\x00\x01\x02" + FAKE_AWS.encode() + b"\x00" * 50,
        })
        _run(project, registry, root)
        s = _rows(registry, project.slug, "scan_summary")[0]
        assert s["detail"]["files_skipped_binary"] == 1
        assert s["detail"]["top_skipped_binary"][0][0] == "payload.dat"
        assert "1 binary file(s) skipped" in s["summary"]
        assert _rows(registry, project.slug, "secret_pattern") == []   # not read
        # A binary file alone does not make the scan partial (documented choice).
        assert s["label"] == NO_SIGNAL

    def test_findings_before_a_budget_hit_are_kept(self, registry, project, tmp_path, monkeypatch):
        monkeypatch.setenv(rs_mod.ENV_MAX_FILE_BYTES, "2000")
        root = tmp_path / "zip"
        _inventory(registry, project.slug, root, {
            "a_config.py": AWS_LINE,
            "z_huge.txt": "q" * 5000,
        })
        results = _run(project, registry, root)
        hits = _rows(registry, project.slug, "secret_pattern")
        assert len(hits) == 1 and hits[0]["detail"]["path"] == "a_config.py"
        s = _rows(registry, project.slug, "scan_summary")[0]
        assert s["label"] == PARTIAL
        assert s["detail"]["outcome_detail"]["matched"] == 1

    def test_no_secret_value_in_summary_detail_or_logs(
            self, registry, project, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv(rs_mod.ENV_MAX_FILE_BYTES, "2000")
        root = tmp_path / "zip"
        _inventory(registry, project.slug, root, {"a.py": AWS_LINE, "big.txt": "q" * 5000})
        with caplog.at_level("DEBUG"):
            _run(project, registry, root)
        blob = json.dumps([dict(r) for r in registry.query_findings(project.slug, FINDING_KIND)],
                          default=str) + caplog.text
        assert FAKE_AWS not in blob


class TestRegression:
    FILES = {
        "src/app.py": AWS_LINE + "x = 1\n" + 'gh = "ghp_' + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + '"\n',
        "README.md": "nothing to see\n" * 10,
        "conf/keys.pem": ("-----BEGIN RSA PRIVATE KEY-----\n" + ("MIIEowIBAAKCAQEAfake" * 8 + "\n") * 3
                          + "-----END RSA PRIVATE KEY-----\n"),
    }

    def test_normal_repo_matches_are_identical_to_unbounded_scan_text(self, tmp_path):
        root = tmp_path / "repo"
        for rel, content in self.FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(content)
        rs = load_ruleset()
        matches, report = rs.scan_paths(root, sorted(self.FILES))
        expected = []
        for rel in sorted(self.FILES):
            expected.extend(rs.scan_text(rel, self.FILES[rel]))
        assert expected, "fixture must contain real matches"
        assert [(m.rule_id, m.path, m.line, m.offset, m.excerpt) for m in matches] == \
               [(m.rule_id, m.path, m.line, m.offset, m.excerpt) for m in expected]
        assert not report.partial and report.files_scanned == 3
        # Lines are the legacy `text.count("\n") + 1`, not a re-derivation.
        for m in matches:
            text = self.FILES[m.path]
            assert m.line == text.count("\n", 0, m.offset) + 1

    def test_unskipped_scan_persists_no_new_keys(self, registry, project, tmp_path):
        root = tmp_path / "zip"
        _inventory(registry, project.slug, root, {"a.py": AWS_LINE})
        _run(project, registry, root)
        s = _rows(registry, project.slug, "scan_summary")[0]
        assert s["label"] == RECOVERED
        assert "files_skipped_size" not in s["detail"] and "scan_bounds" not in s["detail"]

    def test_windowed_scan_equals_whole_scan(self, tmp_path, monkeypatch):
        rs = load_ruleset()
        filler = "# padding line with no credentials\n" * 3
        text = (filler * 4 + AWS_LINE + filler * 5 + 'gh = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"\n'
                + filler * 3 + AWS_LINE)
        whole = [(m.rule_id, m.line, m.offset, m.excerpt) for m in rs.scan_text("f.py", text)]
        assert len(whole) >= 3
        monkeypatch.setattr(rs_mod, "WHOLE_FILE_CHARS", 100)
        monkeypatch.setattr(rs_mod, "WINDOW_CHARS", 200)
        monkeypatch.setattr(rs_mod, "WINDOW_OVERLAP_CHARS", 80)
        windowed, complete, _d, _t = rs._scan_text_bounded("f.py", text, None)
        assert complete
        assert [(m.rule_id, m.line, m.offset, m.excerpt) for m in windowed] == whole

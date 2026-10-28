"""Tests for the Question checklist catalog loader (Discovery-tier Part 3
plan — question_catalog_reader.py, backing the per-phase Questions
checklist). Mirrors test_analysis_catalog_reader.py's loading conventions
(frozen dataclass, lru_cache-backed loader, clear_cache() testing hook).

No test coverage existed for this module before this change — closing a
real gap alongside the CSV reorg/regeneration.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from resource_explorer.surveyors import question_catalog_reader as qcr

# Captured before any test monkeypatches qcr._load, so a test that swaps in
# its own fixture catalog (via monkeypatch.setattr(qcr, "_load", ...)) can
# still load a DIFFERENT fixture file for its own purposes without calling
# through its own patched, argument-less stand-in.
_REAL_LOAD = qcr._load


@pytest.fixture(autouse=True)
def _clear_cache():
    qcr.clear_cache()
    yield
    qcr.clear_cache()


def _write_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "question_catalog.yaml"
    path.write_text(textwrap.dedent("""
        repo_questions:
          - question: "Is this repo alive?"
            stage: Scouting
            perspectives: [Steward, Consumer]
            answering:
              kind: analysis
              analysis_ids: [repository_health]
              note: repository_health
            answering_mechanism: Git Statistics
          - question: "What license risk tier applies?"
            stage: Assessment
            perspectives: [Governance, Security]
            answering:
              kind: analysis
              analysis_ids: [license_classification]
              note: license_classification
            answering_mechanism: Code Analysis
          - question: "How mature is it?"
            stage: Assessment
            perspectives: [Architecture]
            answering:
              kind: mixed
              analysis_ids: [maturity]
              note: "MIXED: maturity + Understanding trend chart"
            answering_mechanism: Code Analysis
          - question: "Are there outstanding CVEs?"
            stage: Analysis
            perspectives: [Security, Governance]
            answering:
              kind: gap
              analysis_ids: []
              note: "GAP: CVE scan"
            answering_mechanism: Gap
          - question: "Should we validate the license text if non-standard?"
            stage: Analysis/Enrichment
            perspectives: [Governance]
            answering:
              kind: mixed
              analysis_ids: [license_classification]
              note: "MIXED: license_classification + human validation"
            answering_mechanism: Code Analysis+Human-Supplied
    """))
    return path


class TestLoadAndGetQuestions:
    def test_loads_all_entries_unfiltered(self, tmp_path):
        path = _write_fixture(tmp_path)
        entries = qcr.get_questions("repo", phase=None, perspectives=None)
        # get_questions() always reads the module-default path unless the
        # caller monkeypatches _load — exercise via the real default loader
        # against the packaged catalog instead of the fixture, then use the
        # fixture path directly through _load() for the isolated cases below.
        assert isinstance(entries, list)

    def test_missing_config_authors_no_resource_type_at_all(self, tmp_path):
        # Used to return {"repo": []}, which claimed repo questions had been
        # authored and had come to nothing. With no catalog file, no resource
        # type is authored -- which is what {} says.
        missing = tmp_path / "nope.yaml"
        assert qcr._load(missing) == {}

    def test_phase_filter_matches_single_stage(self, tmp_path):
        path = _write_fixture(tmp_path)
        data = qcr._load(path)
        assert len(data["repo"]) == 5

        entries = [e.to_dict() for e in data["repo"]]
        scouting = [e for e in entries if e["stage"] == "Scouting"]
        assert {e["question"] for e in scouting} == {"Is this repo alive?"}
        assessment = [e for e in entries if e["stage"] == "Assessment"]
        assert {e["question"] for e in assessment} == {
            "What license risk tier applies?", "How mature is it?",
        }

    def test_phase_filter_matches_slash_combined_stage(self, tmp_path):
        # A slash-combined stage (e.g. "Analysis/Enrichment") should show up
        # under both of its component phases — see get_questions()'s docstring.
        # get_questions() always reads the module-default path — exercise the
        # slash-combined matching directly against _load()'s raw entries instead.
        path = _write_fixture(tmp_path)
        data = qcr._load(path)
        entries = [e.to_dict() for e in data["repo"]]

        def _matches(entry, phase):
            return phase.lower() in {p.strip().lower() for p in entry["stage"].split("/")}

        analysis_matches = {e["question"] for e in entries if _matches(e, "analysis")}
        assert analysis_matches == {
            "Are there outstanding CVEs?", "Should we validate the license text if non-standard?",
        }
        enrichment_matches = {e["question"] for e in entries if _matches(e, "enrichment")}
        assert enrichment_matches == {"Should we validate the license text if non-standard?"}

    def test_perspective_filter_is_or_matched(self, tmp_path):
        path = _write_fixture(tmp_path)
        data = qcr._load(path)
        entries = [e.to_dict() for e in data["repo"]]
        security = [e for e in entries if set(e["perspectives"]) & {"Security"}]
        assert {e["question"] for e in security} == {
            "What license risk tier applies?", "Are there outstanding CVEs?",
        }

    def test_case_insensitive_phase_match(self):
        # get_questions() lowercases both sides — verify against the real
        # packaged catalog (stage is always title-case there).
        entries = qcr.get_questions("repo", phase="SCOUTING")
        assert entries  # the real catalog has scouting-tier questions
        assert all(
            "scouting" in {part.strip().lower() for part in e["stage"].split("/")}
            for e in entries
        )

    def test_unknown_resource_type_returns_empty(self):
        assert qcr.get_questions("nonexistent") == []


class TestNotAuthoredIsNotTheSameAsEmpty:
    """docs/multi-resource-questions-design.md §1.1 item 3.

    Originally written when `get_questions("database")` on the real packaged
    catalog returned `[]` for the same reason `get_questions("repo",
    perspectives=["NoSuchPerspective"])` did. One meant "nobody has written
    database questions yet"; the other meant "there are 52 repo questions and
    none of them are tagged that". Same length, opposite answers -- the shape
    this codebase keeps finding in new places.

    Stream 4 (re/db-questions-csv, 2026-09-21) has since authored real
    database questions, so the tests that need "database" to be NOT_AUTHORED
    now install a repo-only fixture catalog (`_use_repo_only_catalog`) rather
    than relying on the real packaged one lacking a section -- that state has
    to exist somewhere regardless of what the production CSV now contains.
    """

    @staticmethod
    def _use_repo_only_catalog(tmp_path, monkeypatch):
        """Not autouse: `test_an_authored_but_empty_section_is_its_own_state`
        installs its own, different fake catalog and must not have this one
        applied first -- two independent overrides on the same monkeypatch
        fixture are fine, but only one should run per test."""
        path = _write_fixture(tmp_path)  # repo_questions only, no database_questions
        loaded = _REAL_LOAD(path)
        fake = lambda: loaded          # noqa: E731
        fake.cache_clear = lambda: None  # the autouse clear_cache fixture calls this
        monkeypatch.setattr(qcr, "_load", fake)

    def test_a_type_with_no_section_is_not_authored(self, tmp_path, monkeypatch):
        self._use_repo_only_catalog(tmp_path, monkeypatch)
        result = qcr.get_questions("database")
        assert result == []                              # still a list, for every existing caller
        assert result.authored is False
        assert result.absence == qcr.NOT_AUTHORED
        assert "authored" in result.absence_reason.lower()

    def test_a_real_type_filtered_to_nothing_looks_different(self, tmp_path, monkeypatch):
        self._use_repo_only_catalog(tmp_path, monkeypatch)
        result = qcr.get_questions("repo", perspectives=["NoSuchPerspectiveExists"])
        assert result == []                              # the same emptiness on the surface
        assert result.authored is True                   # and a different answer underneath
        assert result.absence == qcr.FILTERED_TO_NOTHING
        assert result.absence != qcr.get_questions("database").absence

    def test_an_authored_but_empty_section_is_its_own_state(self, tmp_path, monkeypatch):
        # `_load`'s config_path default is bound at def time, so the fixture
        # goes in by replacing the loader, not the path constant. Overrides
        # the class fixture's repo-only catalog with an explicitly-empty
        # database section instead.
        path = tmp_path / "question_catalog.yaml"
        path.write_text("database_questions: []\n")
        loaded = _REAL_LOAD(path)
        fake = lambda: loaded          # noqa: E731
        fake.cache_clear = lambda: None  # the autouse clear_cache fixture calls this
        monkeypatch.setattr(qcr, "_load", fake)
        result = qcr.get_questions("database")
        assert result.authored is True
        assert result.absence == qcr.AUTHORED_BUT_EMPTY

    def test_a_populated_type_reports_authored(self):
        result = qcr.get_questions("repo")
        assert result
        assert result.authored is True
        assert result.absence == qcr.AUTHORED
        assert result.absence_reason == ""

    def test_the_envelope_carries_the_state_for_a_ui_caller(self, tmp_path, monkeypatch):
        self._use_repo_only_catalog(tmp_path, monkeypatch)
        env = qcr.get_questions("database").as_envelope()
        assert env["resource_type"] == "database"
        assert env["count"] == 0
        assert env["authored"] is False
        assert env["absence"] == qcr.NOT_AUTHORED
        assert env["absence_reason"]

    def test_is_authored_and_authored_resource_types_agree(self):
        types = qcr.authored_resource_types()
        assert "repo" in types
        assert qcr.is_authored("repo") is True
        assert qcr.is_authored("database") is ("database" in types)


class TestMultiTypeLoading:
    def test_every_questions_section_becomes_a_resource_type(self, tmp_path):
        path = tmp_path / "question_catalog.yaml"
        path.write_text(textwrap.dedent("""
            repo_questions:
              - question: "Is this repo alive?"
                stage: Scouting
                perspectives: [Steward]
                answering: {kind: analysis, analysis_ids: [repository_health]}
            database_questions:
              - question: "How big is it?"
                stage: Scouting
                perspectives: [Data Expert]
                answering: {kind: analysis, analysis_ids: [schema_inventory]}
            dataset_questions: []
        """))
        data = qcr._load(path)
        assert set(data) == {"repo", "database", "dataset"}
        assert [e.question for e in data["database"]] == ["How big is it?"]
        assert data["dataset"] == []

    def test_non_questions_keys_are_ignored(self, tmp_path):
        path = tmp_path / "question_catalog.yaml"
        path.write_text("repo_questions: []\nsome_other_config: {a: 1}\n")
        assert set(qcr._load(path)) == {"repo"}


class TestRealPackagedCatalog:
    """Regression guard against the CSV reorg — spot-checks specific
    entries that were deliberately changed (docs/dr-egeria/
    resource_questions.csv -> question_catalog.yaml regeneration)."""

    def test_license_question_is_now_a_closed_analysis_not_a_gap(self):
        # Reworded 2026-09-21 (re/db-questions-csv, design §4) from "What
        # explicit license does the repository use...?" to the cross-type,
        # British-spelling wording below -- update this text again if it's
        # reworded further, not the assertions.
        entries = qcr.get_questions("repo")
        license_q = next(e for e in entries if e["question"].startswith("What explicit license"))
        assert license_q["answering"]["kind"] == "analysis"
        assert "license_classification" in license_q["answering"]["analysis_ids"]

    def test_maturity_question_stage_is_assessment_not_understanding(self):
        entries = qcr.get_questions("repo")
        maturity_q = next(e for e in entries if e["question"] == "How mature is it?")
        assert maturity_q["stage"] == "Assessment"
        assert "maturity" in maturity_q["answering"]["analysis_ids"]

    def test_new_repo_conventions_questions_present(self):
        entries = qcr.get_questions("repo")
        questions = {e["question"] for e in entries}
        assert "Does the repository publish a clear process for reporting security vulnerabilities?" in questions
        assert "Does the repository have automated build tooling in place?" in questions
        assert any(q.startswith("Is this repository already self-described") for q in questions)

    def test_deep_gap_questions_moved_off_scouting(self):
        # Part of the "move some questions off Scouting" reorg — these are
        # real, unbuilt gaps with no automatic answer; raising them at
        # Scouting was noise, not actionable there.
        entries = qcr.get_questions("repo")
        cve_q = next(e for e in entries if e["question"] == "Are there outstanding CVEs?")
        assert cve_q["stage"] == "Analysis"

        scouting_entries = qcr.get_questions("repo", phase="scouting")
        scouting_questions = {e["question"] for e in scouting_entries}
        assert "Are there outstanding CVEs?" not in scouting_questions

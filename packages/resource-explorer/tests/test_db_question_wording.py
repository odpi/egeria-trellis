"""A database screen must read about databases (RULING-DB-QUESTION-CATALOG-
CONSISTENCY.md §1).

The owner saw, on a database's Context pane, for "Who owns this resource
(accountable owner), and who administers it?":

    No mechanism exists yet — no analysis produces it. (GAP: repository_health +
    chaoss_metrics (contributor identities and concentration) -- repository_health
    + chaoss_metrics are not a real analysis for database resources ...

That was one CSV row, authored for repositories and stamped into every
resource type, with `kind: gap` produced by the generator's automatic
"this analysis is not real for this type" downgrade. The 2026-09-23 ruling says
the answer is `mixed` for databases (a measured administering role plus a
human accountable owner) and `human` for file systems, datasets and models,
and that the license row is the same case.
"""
from __future__ import annotations

import csv
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"
ANALYSES = ROOT / "resource_explorer" / "configdata" / "analysis_catalog.yaml"
CSV_PATH = ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
SCRIPT = ROOT / "scripts" / "csv_to_question_catalog_yaml.py"

OWNER_Q = "Who owns this resource (accountable owner), and who administers it?"
LICENSE_Q = "What explicit license does this resource use, and are there non-standard or copyleft terms?"

#: What a person must never read about a database: repository mechanisms.
REPO_WORDING = ("repository_health", "chaoss_metrics", "project_commits", "Git Statistics")


def _catalog() -> dict:
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))


def _row(resource_type: str, question: str) -> dict:
    return next(q for q in _catalog()[f"{resource_type}_questions"] if q["question"] == question)


def _shown_text(q: dict) -> str:
    """Every field of a row a screen can carry about how it is answered."""
    a = q["answering"]
    return " | ".join([a.get("note") or "", q.get("answering_mechanism") or "",
                       q.get("rationale") or "", q.get("catalog_history") or ""])


def _load_script():
    spec = importlib.util.spec_from_file_location("csv_to_question_catalog_yaml", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestTheOwnerQuestionOnADatabase:
    def test_the_row_is_mixed_not_a_gap(self):
        a = _row("database", OWNER_Q)["answering"]
        assert a["kind"] == "mixed"

    def test_it_names_both_halves_in_database_terms(self):
        q = _row("database", OWNER_Q)
        note = q["answering"]["note"]
        assert "pg_database.datdba" in note
        assert "Enrichment" in note
        assert q["answering_mechanism"] == "Database Catalog Query + Human-Supplied"

    def test_no_repository_wording_survives_on_the_database_row(self):
        text = _shown_text(_row("database", OWNER_Q))
        for word in REPO_WORDING:
            assert word not in text, word

    def test_it_invents_no_mechanism(self):
        """The measured half is the existing `database_owner` fact, which is
        not an analysis-catalog id, so no analysis id is claimed."""
        assert _row("database", OWNER_Q)["answering"]["analysis_ids"] == []

    def test_the_context_text_is_about_databases(self, tmp_path):
        from resource_explorer.facts import FactLayer
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry(db_path=str(tmp_path / "w.db"))
        env = FactLayer(registry, resource_type="database").answer(
            "any_db", _row("database", OWNER_Q))
        shown = env.blocked_reason
        assert shown, "a mixed row with no analysis must say why nothing is read"
        assert "No mechanism exists" not in shown
        for word in REPO_WORDING:
            assert word not in shown, word
        assert "pg_database.datdba" in shown and "Enrichment" in shown
        assert not env.answerable

    def test_the_repository_row_is_unchanged(self):
        q = _row("repo", OWNER_Q)
        assert q["answering"]["kind"] == "analysis"
        assert q["answering"]["analysis_ids"] == ["repository_health", "chaoss_metrics"]
        assert q["answering_mechanism"] == "Git Statistics"
        assert "project_commits" in q["rationale"]

    def test_the_csv_row_stays_one_row_and_stays_the_repositorys_answer(self):
        """The ruling forbids splitting the row (the writer assumes one row
        per question text), so the per-type answer must live in the generator."""
        rows = [r for r in csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8"))
                if r["Question"] == OWNER_Q]
        assert len(rows) == 1
        assert rows[0]["Answering Mechanism"] == "Git Statistics"
        assert "repository_health" in rows[0]["Answering Analysis"]


class TestTheOtherTypesAndTheLicenseRow:
    @pytest.mark.parametrize("rtype", ["filesystem", "dataset", "model"])
    def test_ownership_is_human_for_the_types_with_no_measured_half(self, rtype):
        q = _row(rtype, OWNER_Q)
        assert q["answering"]["kind"] == "human"
        assert q["answering_mechanism"] == "Human-Supplied"
        for word in REPO_WORDING:
            assert word not in _shown_text(q), (rtype, word)

    @pytest.mark.parametrize("rtype", ["database", "filesystem", "dataset", "model"])
    def test_license_is_human_with_no_repository_wording(self, rtype):
        q = _row(rtype, LICENSE_Q)
        assert q["answering"]["kind"] == "human"
        text = _shown_text(q)
        assert "license_classification" not in text
        assert "GitHub" not in text

    def test_the_repository_license_row_is_unchanged(self):
        q = _row("repo", LICENSE_Q)
        assert q["answering"]["analysis_ids"] == ["license_classification"]
        assert "GitHub API license field" in q["rationale"]


#: Repo vocabulary that is still shown on a database row this slice did not
#: touch, pinned so it cannot grow. Each is outside RULING-...-CONSISTENCY §1
#: and is reported, not fixed, in DB-HUB-TABLES-AND-OWNER-TEXT-IMPLEMENTED.md.
KNOWN_LEFTOVERS = {
    ("What is this resource, and what is it for?", "GitHub"),
    ("Under what license or agreement may this resource be used?", "GitHub"),
    ("Does it fit into our security infrastructure?", "security_scan"),
    ("Does it fit into our governance frameworks?", "egeria_publish"),
}


def _repo_only_ids() -> set[str]:
    cat = yaml.safe_load(ANALYSES.read_text(encoding="utf-8"))
    repo_only, elsewhere = set(), set()
    for section, entries in cat.items():
        if not section.endswith("_analyses") or not isinstance(entries, list):
            continue
        for e in entries:
            (repo_only if e.get("resource_types") == ["repo"] else elsewhere).add(e["id"])
    return repo_only - elsewhere


class TestEveryDatabaseRowIsFreeOfRepoOnlyVocabulary:
    def test_the_scan_has_a_vocabulary_to_scan_with(self):
        ids = _repo_only_ids()
        assert {"repository_health", "chaoss_metrics", "license_classification"} <= ids

    def test_no_database_row_shows_a_repo_only_mechanism(self):
        vocab = sorted(_repo_only_ids()) + ["GitHub", "Git Statistics", "project_commits"]
        found = set()
        for q in _catalog()["database_questions"]:
            if q.get("retired"):
                continue
            a = q["answering"]
            # What a screen shows about HOW a row is answered. The question
            # text is shared wording and the history is a changelog.
            text = " | ".join([a.get("note") or "", q.get("answering_mechanism") or "",
                               q.get("rationale") or ""])
            for word in vocab:
                if re.search(rf"\b{re.escape(word)}\b", text):
                    found.add((q["question"], word))
        assert found == KNOWN_LEFTOVERS, (
            f"new repo vocabulary on a database row: {sorted(found - KNOWN_LEFTOVERS)}; "
            f"fixed leftovers to drop from KNOWN_LEFTOVERS: {sorted(KNOWN_LEFTOVERS - found)}"
        )

    def test_no_database_gap_row_carries_the_generators_not_a_real_analysis_text(self):
        """The downgrade sentence is how the owner row got its repo wording:
        any database row still carrying it is a row with the same defect."""
        offenders = [
            q["question"] for q in _catalog()["database_questions"]
            if "is not a real analysis for" in (q["answering"].get("note") or "")
            or "are not a real analysis for" in (q["answering"].get("note") or "")
        ]
        assert offenders == []


class TestThePerTypeOverridesAreAnchoredToTheCsv:
    def test_every_override_names_a_real_row_and_a_type_it_applies_to(self):
        mod = _load_script()
        rows = {r["Question"]: r for r in csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8"))}
        for question, per_type in mod.PER_TYPE_ANSWERING_OVERRIDES.items():
            assert question in rows, f"override for a question the CSV does not have: {question!r}"
            applies = mod.parse_resource_types(rows[question].get("Resource Types", ""))
            for rtype in per_type:
                assert rtype in applies, (question, rtype)
                assert rtype != "repo", "the CSV row IS the repository's answer"

    def test_an_override_is_parsed_by_the_same_guards_as_the_csv(self):
        """An override that names something built as a GAP, or a check that is
        not registered, fails generation exactly as the CSV text would."""
        mod = _load_script()
        entry = {"question": OWNER_Q, "answering": {}, "answering_mechanism": "",
                 "rationale": "", "catalog_history": ""}
        mod.PER_TYPE_ANSWERING_OVERRIDES[OWNER_Q]["database"] = dict(
            mod.PER_TYPE_ANSWERING_OVERRIDES[OWNER_Q]["database"],
            **{"Answering Analysis": "GAP: nothing, though db_hub_tables exists"},
        )
        with pytest.raises(ValueError, match="names something that exists"):
            mod._apply_type_override(entry, "database", set())


class TestMixedRowsHaveARealHasData:
    """Regression: the owner row became `mixed` with no analysis id, so
    `question_has_data` returned None ("nothing to check") and
    `TestBuildQuestionChecklistIsGeneralized` failed on `None in (True, False)`.
    The row has a measured half (the `database_owner` fact), so has_data must be
    a real True/False that follows it."""

    def _registry(self, tmp_path):
        from resource_explorer.registry import DatabaseEntity, ProjectRegistry

        r = ProjectRegistry(db_path=str(tmp_path / "m.db"))
        r.register_database(DatabaseEntity(
            slug="d", display_name="D", db_type="postgresql", host="h",
            port=1, database_name="d"))
        return r

    def _owner_entry(self, registry):
        from resource_explorer.workflows.scouting import build_question_checklist

        got = build_question_checklist(registry, "database", "d", phase="scouting")
        return next((q for q in got["questions"] if q["question"] == OWNER_Q), None)

    def test_false_when_no_owner_was_measured(self, tmp_path):
        entry = self._owner_entry(self._registry(tmp_path))
        if entry is None:
            pytest.skip("owner question not in the scouting phase")
        assert entry["kind"] == "mixed"
        assert entry["has_data"] is False

    def test_true_when_the_owner_role_was_measured(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path)
        monkeypatch.setattr(
            registry, "find_latest_database_survey_with_key",
            lambda slug, key: {"survey_data": '{"database_owner": {"owner": "postgres"}}'},
            raising=False)
        entry = self._owner_entry(registry)
        if entry is None:
            pytest.skip("owner question not in the scouting phase")
        assert entry["has_data"] is True

    def test_every_mixed_or_partial_database_row_is_true_or_false(self, tmp_path):
        from resource_explorer.workflows.scouting import build_question_checklist

        got = build_question_checklist(self._registry(tmp_path), "database", "d", phase="scouting")
        for q in got["questions"]:
            if q["kind"] in ("analysis", "partial", "mixed"):
                assert q["has_data"] in (True, False), q["question"]

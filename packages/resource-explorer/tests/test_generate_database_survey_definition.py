"""The database generator must never destroy a definition it did not write
— the same guard `test_survey_definition_generator_guard.py` pins for the
repo generator, mirrored for `generate_database_survey_definition.py`
(Phase 1b coordinator brief, slice 12).

`database_survey_types.csv` is a specification of what surveys are needed.
The Dr.Egeria document is the definition, and it can carry guards, request
parameters and branching the CSV has no column for.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_database_survey_definition.py"
DEFS = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "survey-definitions-database"
PROVENANCE = DEFS / ".generated_database.json"


def _run(*args) -> str:
    out = subprocess.run([sys.executable, str(SCRIPT), *args],
                         capture_output=True, text=True, timeout=300)
    return out.stdout + out.stderr


@pytest.fixture
def restore():
    """Snapshot every database-* document and the sidecar; restore afterwards."""
    saved = {p: p.read_text() for p in DEFS.glob("database-*.md")}
    saved_prov = PROVENANCE.read_text() if PROVENANCE.exists() else None
    yield
    for p, text in saved.items():
        p.write_text(text)
    if saved_prov is None:
        PROVENANCE.unlink(missing_ok=True)
    else:
        PROVENANCE.write_text(saved_prov)


def test_a_clean_tree_regenerates_to_nothing(restore):
    before = {p: p.read_text() for p in DEFS.glob("database-*.md")}
    out = _run()
    assert "SKIPPED" not in out
    assert all(p.read_text() == text for p, text in before.items())


def test_a_hand_authored_guard_survives_regeneration(restore):
    # scouting has 3 chained steps (2 Link Next blocks with Guard: Any) --
    # unlike assessment's single step, which has no Link Next at all.
    target = DEFS / "database-survey-definition-scouting.md"
    edited = target.read_text().replace(
        "### Guard\nAny", "### Guard\nprivilege-audit-present", 1)
    assert edited != target.read_text(), "fixture did not apply — guard format changed?"
    target.write_text(edited)

    out = _run()

    assert "privilege-audit-present" in target.read_text(), \
        "the generator destroyed a hand-authored guard"
    assert "SKIPPED database-survey-definition-scouting.md" in out
    assert "--force" in out


def test_force_is_required_and_sufficient_to_discard_edits(restore):
    target = DEFS / "database-survey-definition-scouting.md"
    original = target.read_text()
    target.write_text(original.replace(
        "GovActionProcess::DatabaseScoutingSurvey",
        "GovActionProcess::DatabaseScoutingSurveyBespoke",
    ))

    _run()
    assert "Bespoke" in target.read_text()       # refused without --force
    _run("--force")
    assert "Bespoke" not in target.read_text()   # discarded, deliberately
    assert target.read_text() == original


def test_a_csv_change_regenerates_silently_when_the_file_is_untouched(restore):
    target = DEFS / "database-survey-definition-scouting.md"
    original = target.read_text()
    target.write_text(original.replace("Database Scouting Scan", "Database Scouting Scan X"))
    prov = json.loads(PROVENANCE.read_text())
    prov["database-survey-definition-scouting.md"] = hashlib.sha256(
        target.read_text().encode()).hexdigest()
    PROVENANCE.write_text(json.dumps(prov))

    out = _run()
    assert "SKIPPED" not in out
    assert target.read_text() == original


def test_a_missing_sidecar_refuses_rather_than_overwrites(restore):
    target = DEFS / "database-survey-definition-scouting.md"
    target.write_text(target.read_text().replace(
        "GovActionProcess::DatabaseScoutingSurvey",
        "GovActionProcess::DatabaseScoutingSurveyX",
    ))
    PROVENANCE.unlink(missing_ok=True)

    out = _run()
    assert "SKIPPED" in out
    assert "DatabaseScoutingSurveyX" in target.read_text()


class TestQuestionScopingStaysPerSurveyGroup:
    """`postgres_operations` is chained into all three documents (it
    bundles analyses across three different intent tiers) -- each document
    must only claim ScopedBy links for the analyses it actually owns, not
    every analysis the shared step happens to answer."""

    def test_assessment_only_claims_privilege_audit_questions(self):
        content = (DEFS / "database-survey-definition-assessment.md").read_text()
        # privilege_audit's own questions must be present...
        assert "Who can read and write what" in content
        # ...but db_activity_signals/db_resilience's (scouting-only) must not.
        assert "Is this database alive" not in content
        assert "primary or a replica" not in content

    def test_scouting_does_not_claim_privilege_audit_or_external_dependencies_questions(self):
        content = (DEFS / "database-survey-definition-scouting.md").read_text()
        assert "Who can read and write what" not in content
        assert "What does this database depend on outside itself" not in content

    def test_analysis_claims_external_dependencies_questions(self):
        content = (DEFS / "database-survey-definition-analysis.md").read_text()
        assert "What does this database depend on outside itself" in content

    def test_analysis_and_assessment_both_legitimately_claim_a_mixed_kind_question(self):
        """"Who can read and write what...?" names BOTH privilege_audit
        (assessment) and data_class_match (analysis) as answering.
        analysis_ids -- both documents genuinely contribute part of the
        answer, so both are expected to claim it. Not a scoping leak."""
        analysis = (DEFS / "database-survey-definition-analysis.md").read_text()
        assessment = (DEFS / "database-survey-definition-assessment.md").read_text()
        assert "Who can read and write what" in analysis
        assert "Who can read and write what" in assessment

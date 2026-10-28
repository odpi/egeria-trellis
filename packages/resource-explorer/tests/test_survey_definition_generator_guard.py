"""The generator must never destroy a definition it did not write.

`repo_survey_types.csv` is a specification of what surveys are needed. The
Dr.Egeria document is the definition, and it can carry guards, request
parameters and branching that the CSV has no column for. The generator used to
end in an unconditional `write_text`, so the first hand-authored guard would be
destroyed by the next regeneration — and then un-authored from Egeria by the
resync repair, which executes these same documents.

The provenance sidecar exists to tell two otherwise identical states apart:
a file that differs from what the CSV now produces has either been overtaken by
a CSV change (regenerating is right) or hand-authored into (regenerating is
destruction). Content still matching what we last wrote proves the former.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_repo_survey_definition.py"
REAL_DEFS = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "survey-definitions"
DEFS = REAL_DEFS  # rebound to a tmp copy by the `restore` fixture
PROVENANCE = DEFS / ".generated.json"


def _run(*args) -> str:
    out = subprocess.run([sys.executable, str(SCRIPT), *args],
                         capture_output=True, text=True, timeout=300)
    return out.stdout + out.stderr


@pytest.fixture
def restore(tmp_path, monkeypatch):
    """Run the generator on a COPY of the definitions (via RE_SURVEY_DEFS_DIR), so no test
    ever rewrites the tracked tree. Rebinds DEFS/PROVENANCE to the copy for this test."""
    work = tmp_path / "defs"
    shutil.copytree(REAL_DEFS, work)
    monkeypatch.setenv("RE_SURVEY_DEFS_DIR", str(work))
    monkeypatch.setitem(globals(), "DEFS", work)
    monkeypatch.setitem(globals(), "PROVENANCE", work / ".generated.json")
    yield


def test_a_clean_tree_regenerates_to_nothing(restore):
    """Committed documents match their CSV, so a plain run changes no file."""
    before = {p: p.read_text() for p in DEFS.glob("*.md")}
    out = _run()
    assert "SKIPPED" not in out
    assert all(p.read_text() == text for p, text in before.items())


def test_a_hand_authored_guard_survives_regeneration(restore):
    """The exact hazard: a real guard, which the CSV cannot express."""
    target = DEFS / "repo-survey-definition-assessment.md"
    edited = target.read_text().replace(
        "### Guard\nAny", "### Guard\nsecurity-policy-present", 1)
    assert edited != target.read_text(), "fixture did not apply — guard format changed?"
    target.write_text(edited)

    out = _run()

    assert "security-policy-present" in target.read_text(), \
        "the generator destroyed a hand-authored guard"
    assert "SKIPPED repo-survey-definition-assessment.md" in out
    assert "--force" in out


def test_force_is_required_and_sufficient_to_discard_edits(restore):
    target = DEFS / "repo-survey-definition-assessment.md"
    original = target.read_text()
    target.write_text(original.replace("### Guard\nAny", "### Guard\nbespoke", 1))

    _run()
    assert "bespoke" in target.read_text()      # refused without --force
    _run("--force")
    assert "bespoke" not in target.read_text()  # discarded, deliberately
    assert target.read_text() == original


def test_a_csv_change_regenerates_silently_when_the_file_is_untouched(restore):
    """Provenance earns its keep here. Without it, every legitimate CSV change
    would need --force, and people would pass --force by reflex — which puts
    the original hazard straight back."""
    target = DEFS / "repo-survey-definition-assessment.md"
    original = target.read_text()
    # Simulate "the CSV moved on": the recorded hash is the file's own content,
    # so it is provably untouched, while the file no longer matches generation.
    target.write_text(original.replace("Assessment Survey", "Assessment Survey X"))
    prov = json.loads(PROVENANCE.read_text())
    import hashlib
    prov["repo-survey-definition-assessment.md"] = hashlib.sha256(
        target.read_text().encode()).hexdigest()
    PROVENANCE.write_text(json.dumps(prov))

    out = _run()
    assert "SKIPPED" not in out
    assert target.read_text() == original


def test_a_missing_sidecar_refuses_rather_than_overwrites(restore):
    """No provenance means we cannot prove anything is untouched. Failing
    towards "leave it alone" is the whole point of the mechanism."""
    target = DEFS / "repo-survey-definition-assessment.md"
    target.write_text(target.read_text().replace("### Guard\nAny", "### Guard\nx", 1))
    PROVENANCE.unlink(missing_ok=True)

    out = _run()
    assert "SKIPPED" in out
    assert "\nx" in target.read_text()

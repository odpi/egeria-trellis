"""The file-system generator must never destroy a definition it did not write
— the same guard `test_survey_definition_generator_guard.py` pins for the
repo generator, mirrored for `generate_filesystem_survey_definition.py`
(item D2).

`filesystem_survey_types.csv` is a specification of what surveys are needed.
The Dr.Egeria document is the definition, and it can carry guards, request
parameters and branching the CSV has no column for.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_filesystem_survey_definition.py"
REAL_DEFS = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "survey-definitions-filesystem"
DEFS = REAL_DEFS  # rebound to a tmp copy by the `restore` fixture
PROVENANCE = DEFS / ".generated_filesystem.json"


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
    monkeypatch.setitem(globals(), "PROVENANCE", work / ".generated_filesystem.json")
    yield


def test_a_clean_tree_regenerates_to_nothing(restore):
    before = {p: p.read_text() for p in DEFS.glob("filesystem-*.md")}
    out = _run()
    assert "SKIPPED" not in out
    assert all(p.read_text() == text for p, text in before.items())


def test_a_hand_authored_guard_survives_regeneration(restore):
    # scouting has 3 chained steps, so 2 Link Next blocks with Guard: Any.
    target = DEFS / "filesystem-survey-definition-scouting.md"
    edited = target.read_text().replace(
        "### Guard\nAny", "### Guard\nprofiles-present", 1)
    assert edited != target.read_text(), "fixture did not apply — guard format changed?"
    target.write_text(edited)

    out = _run()

    assert "profiles-present" in target.read_text(), \
        "the generator destroyed a hand-authored guard"
    assert "SKIPPED filesystem-survey-definition-scouting.md" in out
    assert "--force" in out


def test_force_is_required_and_sufficient_to_discard_edits(restore):
    target = DEFS / "filesystem-survey-definition-scouting.md"
    original = target.read_text()
    target.write_text(original.replace(
        "GovActionProcess::FilesystemScoutingSurvey",
        "GovActionProcess::FilesystemScoutingSurveyBespoke",
    ))

    _run()
    assert "Bespoke" in target.read_text()       # refused without --force
    _run("--force")
    assert "Bespoke" not in target.read_text()   # discarded, deliberately
    assert target.read_text() == original


def test_a_csv_change_regenerates_silently_when_the_file_is_untouched(restore):
    target = DEFS / "filesystem-survey-definition-scouting.md"
    original = target.read_text()
    target.write_text(original.replace("Filesystem Scouting Survey", "Filesystem Scouting Survey X"))
    prov = json.loads(PROVENANCE.read_text())
    prov["filesystem-survey-definition-scouting.md"] = hashlib.sha256(
        target.read_text().encode()).hexdigest()
    PROVENANCE.write_text(json.dumps(prov))

    out = _run()
    assert "SKIPPED" not in out
    assert target.read_text() == original


def test_a_missing_sidecar_refuses_rather_than_overwrites(restore):
    target = DEFS / "filesystem-survey-definition-scouting.md"
    target.write_text(target.read_text().replace(
        "GovActionProcess::FilesystemScoutingSurvey",
        "GovActionProcess::FilesystemScoutingSurveyX",
    ))
    PROVENANCE.unlink(missing_ok=True)

    out = _run()
    assert "SKIPPED" in out
    assert "FilesystemScoutingSurveyX" in target.read_text()




def _generator_module():
    import importlib.util

    name = "generate_filesystem_survey_definition_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # a dataclass resolves its own module by name
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class TestTheDefinitionsAreRunnableAndHonest:
    def test_every_chained_step_has_a_runnable_step_in_the_adapter(self):
        """R1's consequence for D2: a definition may not name a step the executor reports as 'not run: unknown step'."""
        from resource_explorer.surveyors.survey_definition_executor import get_adapter

        runnable = set(get_adapter("filesystem").re_analysis_steps)
        for spec in _generator_module().SPECS:
            assert set(spec.step_keys) <= runnable, (spec.survey_group, set(spec.step_keys) - runnable)

    def test_a_state_with_no_step_is_refused_rather_than_chained(self, tmp_path):
        gen = _generator_module()
        bad = tmp_path / "bad.csv"
        bad.write_text(
            "survey_kind,survey_group,survey_display_name,description,output_filename,step_key,step_order\n"
            "scouting,FilesystemScoutingSurvey,X,d,x.md,resource_reachability,1\n")
        with pytest.raises(gen.SurveyTypesCsvError, match="resource_reachability"):
            gen.load_specs_from_csv(bad)

    def test_the_walk_comes_first_in_both_definitions_and_profiles_precede_the_class_match(self):
        specs = {s.survey_group: s.step_keys for s in _generator_module().SPECS}
        assert all(steps[0] == "filesystem_inventory" for steps in specs.values())
        analysis = specs["FilesystemAnalysisSurvey"]
        assert analysis.index("file_profiles") < analysis.index("file_data_class_match")

    def test_every_derived_step_is_in_a_definition_or_named_here_as_left_out(self):
        derived = {"fs_activity", "fs_change_rates", "fs_duplicates", "fs_layout_signals", "fs_permissions"}
        chained = {k for s in _generator_module().SPECS for k in s.step_keys}
        assert derived <= chained

    def test_every_step_is_resource_explorer_led_and_every_document_declares_its_survey_kind(self):
        for path, kind in ((DEFS / "filesystem-survey-definition-scouting.md", "scouting"),
                           (DEFS / "filesystem-survey-definition-analysis.md", "analysis")):
            text = path.read_text()
            assert f"| survey_kind | {kind} |" in text
            assert text.count("| executes_at | resource-explorer |") == text.count("| re_analysis_step |")
            assert "| executes_at | egeria" not in text


class TestAQuestionTheDefinitionNamesMustBeAuthored:
    def test_a_missing_question_term_fails_the_generator(self, monkeypatch):
        gen = _generator_module()
        monkeypatch.setattr(gen, "_authored_question_text", lambda: "")
        with pytest.raises(gen.SurveyTypesCsvError, match="not authored"):
            gen._require_questions_authored("FilesystemScoutingSurvey", ["A question nobody authored?"])

    def test_every_question_the_documents_name_is_authored(self):
        gen = _generator_module()
        step_questions = gen._build_step_key_to_questions()
        for spec in gen.SPECS:
            gen._require_questions_authored(
                spec.survey_group, gen._answered_questions(spec.survey_group, spec.step_keys, step_questions))


class TestQuestionScopingStaysPerSurveyGroup:
    def test_scouting_claims_the_walk_questions_and_not_the_content_questions(self):
        content = (DEFS / "filesystem-survey-definition-scouting.md").read_text()
        assert "What kinds of files does this filesystem contain" in content
        assert "Which files are duplicates of each other" not in content
        assert "Who owns or can read what" not in content

    def test_analysis_claims_the_content_questions_and_not_the_walk_only_ones(self):
        content = (DEFS / "filesystem-survey-definition-analysis.md").read_text()
        assert "Which files are duplicates of each other" in content
        assert "Who owns or can read what" in content
        assert "What kinds of files does this filesystem contain" not in content

    def test_a_state_only_question_is_in_no_definition(self):
        for name in ("scouting", "analysis"):
            content = (DEFS / f"filesystem-survey-definition-{name}.md").read_text()
            assert "Can Egeria's survey engine reach this resource" not in content
            assert "How much would it cost to survey fully" not in content


def test_both_reconcilers_read_the_same_specs_as_the_generator():
    import importlib.util

    scripts = SCRIPT.parent
    for name in ("reconcile_filesystem_survey_definition_links", "reconcile_filesystem_survey_definition_scopes"):
        spec = importlib.util.spec_from_file_location(name, scripts / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert [s.survey_group for s in module._load_specs()] == [
            "FilesystemScoutingSurvey", "FilesystemAnalysisSurvey"]

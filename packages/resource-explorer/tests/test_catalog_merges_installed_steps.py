"""F1 (2026-10-29): the analysis catalog merges the INSTALLED `pyegeria.analytics` step manifests when it is read, so
adding a step needs no commit in Resource Explorer.

The fixture installs a fake step package the way pip would (a dist-info with an entry point) under a name the
committed catalog has never heard of, and shows it appears in the catalog, with no configdata change.
"""
from __future__ import annotations

import sys

import pytest

from resource_explorer.surveyors import analysis_catalog_reader as reader
from resource_explorer.surveyors import survey_step_host

NAME = "fake_zz_step"


def _install_fake(tmp_path, monkeypatch, resource_type="database", version="0.3.0", questions=("Q-0041",),
                  question_defs=()):
    site = tmp_path / "site"
    pkg = site / NAME
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        "from trellis_analytics.manifest import StepManifest\n"
        f"MANIFEST = StepManifest(name={NAME!r}, display_name='Fake ZZ', function='fake_zz_step.run',\n"
        "    description='A step no committed catalog names.', returns='one measure', source='ods',\n"
        f"    version={version!r}, resource_types=[{resource_type!r}], annotation_type='ResourceMeasureAnnotation',\n"
        f"    credential_tier='none', inputs=['ods'], measures=['fake_zz_step'], stage='analysis', questions={list(questions)!r},\n"
        f"    question_defs={list(question_defs)!r},\n"
        "    request_type='re-fake-zz')\n"
        "def run(inputs):\n    return []\n")
    d = site / f"trellis_step_fake_zz-{version}.dist-info"
    d.mkdir(exist_ok=True)
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: trellis-step-fake-zz\nVersion: {version}\n")
    (d / "entry_points.txt").write_text(f"[pyegeria.analytics]\n{NAME} = {NAME}:MANIFEST\n")
    monkeypatch.syspath_prepend(str(site))
    survey_step_host.refresh("database")
    survey_step_host.refresh("filesystem")


@pytest.fixture(autouse=True)
def _put_back(monkeypatch):
    yield
    monkeypatch.undo()          # the path entry goes first, then the re-attach sees the step gone
    sys.modules.pop(NAME, None)
    survey_step_host.refresh("database")
    survey_step_host.refresh("filesystem")


def _ids(resource_type):
    return [a["id"] for a in reader.get_analyses(resource_type, include_egeria_live=False)]


def test_committed_catalog_has_no_entry_for_the_fake_step():
    assert NAME not in _ids("database")


def test_installed_step_appears_in_the_catalog_with_no_configdata_row(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch)
    entry = next(a for a in reader.get_analyses("database", include_egeria_live=False) if a["id"] == NAME)
    assert entry["name"] == "Fake ZZ"
    assert entry["provided_by"]["package"] == "trellis-step-fake-zz"
    assert entry["provided_by"]["questions"] == ["Q-0041"]
    assert entry["availability"] == "queued"
    assert NAME not in _ids("filesystem")


def test_installed_step_is_attached_so_it_gets_a_card(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch)
    assert NAME in survey_step_host.attached_user_steps("database")


def test_installed_step_binds_its_declared_question_in_the_catalog(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch)
    entry = next(e for e in reader._load()["database"] if e.id == NAME)
    assert entry.provided_by["questions"] == ["Q-0041"]


def test_uninstalling_removes_it_again(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch)
    monkeypatch.undo()
    survey_step_host.refresh("database")
    assert NAME not in _ids("database")


def test_manifest_replaces_the_committed_generated_entry_in_place(tmp_path, monkeypatch):
    """The committed entry is only a fallback: with the package installed the manifest is the source."""
    reader.clear_cache()
    before = reader._load()["database"]
    committed = [e for e in before if e.provided_by]
    if not committed:
        pytest.skip("no committed generated entry to compare")
    _install_fake(tmp_path, monkeypatch)
    after = reader._load()["database"]
    assert [e.id for e in after][: len(before)] == [e.id for e in before]


def test_an_uninstalled_step_leaves_the_results_map_too(tmp_path, monkeypatch):
    """The gate's failure (b186): a step that was installed for one test left its results reader behind, so the
    map's size depended on test order."""
    from resource_explorer.surveyors.database import survey_definition_adapter as db

    before = set(db.DATABASE_ANALYSIS_RESULTS_MAP)
    _install_fake(tmp_path, monkeypatch)
    assert NAME in db.DATABASE_ANALYSIS_RESULTS_MAP
    monkeypatch.undo()
    sys.modules.pop(NAME, None)
    survey_step_host.refresh("database")
    assert set(db.DATABASE_ANALYSIS_RESULTS_MAP) == before
    assert NAME not in db.DATABASE_ANALYSIS_RE_STEP_MAP and NAME not in db.DATABASE_ANALYSIS_KINDS

# ── a question the step brings (owner, 2026-10-29: a namespaced id in the manifest) ─────────────────────────────

def test_a_question_a_step_declares_is_in_the_catalogue_with_no_configdata_row(tmp_path, monkeypatch):
    from resource_explorer.surveyors import question_catalog_reader as qr

    assert qr.find_question_by_id(f"{NAME}:Q1") is None
    _install_fake(tmp_path, monkeypatch, question_defs=[{"id": f"{NAME}:Q1", "question": "Which zz is oldest?",
                                                        "rationale": "zz counts only what the walk kept."}])
    found = qr.find_question_by_id(f"{NAME}:Q1", "database")
    assert found["question"] == "Which zz is oldest?" and found["stage"] == "Analysis"
    assert found["answering"]["analysis_ids"] == [NAME] and found["rationale"] == "zz counts only what the walk kept."
    assert f"{NAME}:Q1" in qr.all_question_ids()
    assert qr.find_question_by_id(f"{NAME}:Q1", "filesystem") is None          # only the kinds the step measures
    entry = next(e for e in reader._load()["database"] if e.id == NAME)
    assert f"{NAME}:Q1" in entry.provided_by["questions"]                       # and the step answers it
    assert entry.declaration is None or f"{NAME}:Q1" in entry.declaration.answers


def test_the_question_leaves_with_the_package(tmp_path, monkeypatch):
    from resource_explorer.surveyors import question_catalog_reader as qr

    _install_fake(tmp_path, monkeypatch, question_defs=[{"id": f"{NAME}:Q1", "question": "Which zz is oldest?"}])
    assert qr.find_question_by_id(f"{NAME}:Q1")
    monkeypatch.undo()
    sys.modules.pop(NAME, None)
    survey_step_host.refresh("database")
    assert qr.find_question_by_id(f"{NAME}:Q1") is None


# ── F11: a library step's measure reads as the manifest's type, not "generic" ────────────────────────────────

def test_a_library_db_step_writes_as_its_manifests_type():
    from resource_explorer import publish_table as pt

    entry = next(e for e in reader._load()["database"] if e.id == "change_rates")
    w = pt.writes_as(entry.declaration.measures[0], "measured")
    assert w["route"] == "native" and w["type"] == "ResourceMeasureAnnotation" and w["writable"]
    assert w["words"] != pt.GENERIC_WORDS

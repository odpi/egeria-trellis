"""Several scan steps in ONE definition run each emit a `scan_summary`
(owner's Full Survey of egeria_workspaces_git, 2026-10-08): the executor
stamped every keyless annotation with the definition's qualified name, which
made the four scans' keys identical and so defeated the per-step
disambiguation. The publish guard refused, correctly. These tests pin the fix
(distinct keys per step) and that the guard itself is untouched.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter, SurveyDefinitionExecutor, register_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep
from resource_explorer.surveyors.survey_report import (
    ClassificationAnnotation, annotation_qualified_name, assert_unique_qualified_names,
)
from resource_explorer.surveyors.sub_surveyors.contribution_provenance import (
    ContributionProvenanceSurveyor,
)
from resource_explorer.surveyors.sub_surveyors.secret_scan import SecretScanSurveyor
from resource_explorer.surveyors.sub_surveyors.sla_content import SlaContentSurveyor
from resource_explorer.surveyors.sub_surveyors.telemetry_scan import TelemetryScanSurveyor

DEF_QN = "GovActionProcess:RepoFullSurvey"


def _ann(step, item_key="", check="scan_summary"):
    return ClassificationAnnotation(check_name=check, summary="s", analysis_step=step,
                                    candidate_classifications=["x"], confidence=100,
                                    explanation="e", json_properties={}, item_key=item_key)


def test_four_scans_stamped_with_the_definition_publish_distinct_names():
    anns = [_ann(s, DEF_QN) for s in
            ("SecretScan", "ContributionProvenance", "SlaContent", "TelemetryScan")]
    assert_unique_qualified_names("P", anns)  # must not raise
    names = [annotation_qualified_name("P", i, a) for i, a in enumerate(anns)]
    assert len(set(names)) == 4
    assert all(n.startswith("P::scan_summary::") for n in names)


def test_an_unambiguous_stamped_annotation_keeps_its_published_name():
    anns = [_ann("SecretScan", DEF_QN), _ann("SlaContent", DEF_QN, check="other")]
    assert_unique_qualified_names("P", anns)
    assert annotation_qualified_name("P", 0, anns[0]) == "P::scan_summary::" + DEF_QN
    assert annotation_qualified_name("P", 1, anns[1]) == "P::other::" + DEF_QN


def test_truly_identical_key_is_still_refused():
    anns = [_ann("SecretScan", DEF_QN), _ann("SecretScan", DEF_QN)]
    with pytest.raises(ValueError, match="same qualifiedName"):
        assert_unique_qualified_names("P", anns)


def test_real_scan_surveyors_through_the_executor_publish_without_collision(tmp_path):
    registry = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    project = Project(slug="fx", display_name="Fx", github_url="https://github.com/t/fx",
                      collections=[])
    registry.add(project)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("hello\nSLA: 99.9% uptime\n")
    (root / "app.py").write_text("x = 1\n")
    registry.upsert_file_inventory("fx", [("README.md", 24), ("app.py", 6)])

    classes = {"s_secret": SecretScanSurveyor, "s_prov": ContributionProvenanceSurveyor,
               "s_sla": SlaContentSurveyor, "s_tel": TelemetryScanSurveyor}

    def runner_for(cls):
        def run(entity, reg, **_):
            return {"annotations": cls(project, registry, local_path=str(root)).run()}
        return run

    collected = []
    adapter = ResourceTypeAdapter(
        entity_type="fx_scans", technology_type="Fx Tech",
        re_analysis_steps={k: runner_for(c) for k, c in classes.items()},
        get_entity=lambda reg, slug: object(),
        publish=lambda entity, step_outputs, surveyed_at, reg:
            collected.extend(step_outputs) or "g",
    )
    register_adapter(adapter)
    sdef = SurveyDefinition(
        process_guid="p", display_name="Full", qualified_name=DEF_QN,
        supported_technology_type="Fx Tech",
        steps=[SurveyStep(guid=k, display_name=k, qualified_name="Step::" + k,
                          executes_at="resource-explorer", re_analysis_step=k)
               for k in classes])
    reader = MagicMock()
    reader.fetch.return_value = sdef
    reader.find_candidate_process_guids.return_value = []
    reg = MagicMock()
    reg.get_survey_definition_guid.return_value = None
    reg.has_assigned_egeria_project.return_value = True
    SurveyDefinitionExecutor(reg, reader=reader).run(
        entity_type="fx_scans", slug="fx", survey_definition_ref=DEF_QN)

    anns = [a for o in collected for a in o.get("annotations", [])]
    # On a clean fixture SecretScan and ContributionProvenance both emit one
    # (the other two use their own check names on their normal path).
    summaries = [a for a in anns if a.check_name == "scan_summary"]
    assert len({a.analysis_step for a in summaries}) >= 2
    # Every annotation key across ALL steps of the run is unique at publish.
    assert_unique_qualified_names("fx", anns)

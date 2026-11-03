"""F3 and F12 (gate walk 2026-10-29): a library step's question row and Re-run.

F3: GET /api/projects/<slug>/analyses/<id>/measurements answered "Unknown analysis" for a library step because the file
system adapter declared no `analysis_kinds`. F12: Re-run was disabled with "no mapped survey step(s)" for a file-system
library step the run route would have run, because the enablement read the survey-step map, not the registry the route
validates against.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from tests.library_step_install import install_steps  # noqa: F401 - the fixture


def test_a_file_systems_library_step_re_run_is_enabled_when_the_route_would_run_it(install_steps):
    from resource_explorer.workflows.stage_page import runnable_and_reason

    runnable, reason = runnable_and_reason("fs_permissions", "filesystem")
    assert runnable is False and reason.startswith("step not installed here: 'fs_permissions'")   # honest, before install
    install_steps("fs_permissions")
    assert runnable_and_reason("fs_permissions", "filesystem") == (True, "")
    # a step still missing keeps its refusal; a built-in analysis is unaffected
    assert runnable_and_reason("fs_activity", "filesystem")[0] is False
    assert runnable_and_reason("not_an_analysis", "filesystem")[0] is False


def test_the_measurements_route_answers_for_a_file_system_library_step(install_steps, tmp_path):
    from resource_explorer.workflows.analysis import run_filesystem_analysis
    from resource_explorer.workflows.stage_page import build_measurements
    from tests.test_files_derived_steps_host import _register, _tree, _walk

    install_steps("fs_permissions")
    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    _walk(reg, _register(reg, _tree(tmp_path)))
    assert run_filesystem_analysis("tree", "fs_permissions", registry=reg).status == "ok"
    out = build_measurements(reg, "tree", "fs_permissions", "filesystem")          # no LookupError: it is not "Unknown analysis"
    assert out["analysis_id"] == "fs_permissions" and not out["not_applicable"]
    assert out["measurements"], out
    assert all(m.get("name") for m in out["measurements"])


def test_the_measurements_route_answers_for_a_database_library_step(install_steps, tmp_path):
    from resource_explorer.workflows.stage_page import build_measurements

    install_steps("statistics_freshness")
    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    reg.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))
    reg.record_step_annotations("database", "coco", "statistics_freshness", run="r1", measured_at="2026-10-29T10:00:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "public.t",
                                              "summary": "t: 3 days since ANALYZE",
                                              "resourceProperties": {"Days Since Analyze": 3, "asOf": "2026-10-26T01:00:00"},
                                              "additionalProperties": {"resultState": "measured"}}])
    out = build_measurements(reg, "coco", "statistics_freshness", "database")
    assert out["analysis_id"] == "statistics_freshness" and out["measurements"]
    assert out["measurements"][0]["name"]

"""TR-93: a scheduled Survey Definition whose steps were cancelled. The scheduler's activity row follows the same rule as the
run's own row (workflows.survey_definition): real errors stay errors; at least one completed step plus cancellations is
still a success and says how many steps were cancelled; with NO completed step nothing was measured, so the row is
`cancelled` and never says "completed successfully". Runs the real `_run_due` against a temporary registry; the executor
is the only fake."""
from unittest.mock import patch

import pytest

from resource_explorer import scheduler
from resource_explorer.registry import Project, ProjectRegistry

REF = "GovActionProcess::RepoScoutingSurvey"
OK = {"step": "Step::a", "re_analysis_step": "a", "status": "ok"}
CAN = {"step": "Step::c", "re_analysis_step": "c", "status": "cancelled", "engine": "prefect"}


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "sched.db"))


def _run(registry, result):
    registry.add(Project(slug="myproj", display_name="My Project", github_url="https://github.com/test/myproj",
                         description=""))
    registry.save_schedule("repo", "myproj", REF, "daily", True, target_kind="survey")
    with registry._conn() as conn:
        conn.execute("UPDATE resource_schedules SET next_run = '2020-01-01T00:00:00+00:00' WHERE entity_slug = ?",
                     ("myproj",))
    with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
         patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
               return_value={"run_recorded": True, **result}):
        scheduler._run_due()
    (entry,) = registry.list_activity(entity_slug="myproj")
    return entry


def test_a_run_where_every_step_was_cancelled_is_cancelled_not_successful(registry):
    e = _run(registry, {"errors": [], "cancelled_count": 2, "steps": [dict(CAN), dict(CAN)]})
    assert e["status"] == "cancelled"
    assert "was cancelled" in e["summary"] and "2 steps cancelled, nothing measured" in e["summary"]
    assert "completed successfully" not in e["summary"]


def test_a_run_with_a_completed_step_and_a_cancelled_one_is_a_success_that_says_so(registry):
    e = _run(registry, {"errors": [], "cancelled_count": 1, "steps": [dict(OK), dict(CAN)]})
    assert e["status"] == "ok"
    assert e["summary"].endswith("completed successfully · 1 step cancelled")


def test_a_cancelled_run_with_no_readable_steps_counts_as_nothing_completed(registry):
    e = _run(registry, {"errors": [], "cancelled_count": 1})
    assert e["status"] == "cancelled" and "nothing measured" in e["summary"]


def test_errors_stay_errors_and_a_clean_run_keeps_its_old_words(registry, tmp_path):
    e = _run(registry, {"errors": ["boom"], "cancelled_count": 1, "steps": [dict(OK), dict(CAN)]})
    assert e["status"] == "error" and "completed with errors" in e["summary"]
    clean = _run(ProjectRegistry(db_path=str(tmp_path / "other.db")), {"errors": [], "steps": [dict(OK)]})
    assert clean["status"] == "ok" and clean["summary"].endswith("completed successfully")
    assert "cancelled" not in clean["summary"]

"""Request parameters reach the step that declares them (step_parameters.py), and nothing else does.

Before 2026-10-20 an engine action's `requestParameters` were stored on the run target and never read, and a
process step's were parsed onto `SurveyStep.request_parameters` and never read either. The engine-host tests
below reuse the run-credential harness (tests/test_coordinated_run_credential.py): Egeria faked at the pyegeria
boundary, the `postgres_operations` runner an autospec of the real one.
"""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.step_parameters import (
    BOOL,
    INT,
    JSON_OBJECT,
    StepParameter,
    StepParameterError,
    coerce_step_parameters,
)
from tests.test_coordinated_run_credential import (  # noqa: F401 - fixtures reused
    _empty_holder,
    _marker_row,
    _no_egeria_unless_faked,
    runner,
)
from tests.test_engine_host_case3 import INSTANCE, _process_action, _with_credential
from tests.test_engine_host_e4 import _run_queue_once, _sweep, egeria, no_connection, reg  # noqa: F401

# ── the coercion rules ────────────────────────────────────────────────────────────────────────────────────────


def test_a_declared_parameter_is_coerced_from_egerias_string():
    got = coerce_step_parameters(
        {"sampling": StepParameter(JSON_OBJECT), "on": StepParameter(BOOL), "n": StepParameter(INT)},
        {"sampling": '{"strategy": "head", "max_rows": 50}', "on": "yes", "n": " 7 "})
    assert got.accepted == {"sampling": {"strategy": "head", "max_rows": 50}, "on": True, "n": 7}
    assert got.ignored == [] and got.invalid == []


def test_an_undeclared_parameter_is_named_as_ignored_never_forwarded():
    got = coerce_step_parameters({"n": StepParameter(INT)}, {"includeSchemaNames": "sales", "n": "1"})
    assert got.accepted == {"n": 1}
    assert got.ignored == ["includeSchemaNames"]


@pytest.mark.parametrize(("kind", "value"), [(JSON_OBJECT, "[1, 2]"), (JSON_OBJECT, "{not json"),
                                             (BOOL, "maybe"), (INT, "three"), (INT, True)])
def test_a_declared_parameter_that_does_not_parse_is_refused_with_its_value(kind, value):
    got = coerce_step_parameters({"p": StepParameter(kind)}, {"p": value})
    assert got.accepted == {}
    assert len(got.invalid) == 1 and "'p'" in got.invalid[0] and "not valid" in got.invalid[0]
    with pytest.raises(StepParameterError, match="step_x"):
        got.raise_if_invalid("step_x")


def test_a_validator_refusal_is_a_refusal():
    def must_be_small(v):
        if v > 10:
            raise ValueError("too big")

    got = coerce_step_parameters({"n": StepParameter(INT, validate=must_be_small)}, {"n": "11"})
    assert got.accepted == {} and "too big" in got.invalid[0]


@pytest.mark.parametrize("name", ["db_user", "db_pwd", "scan_surveyed_at", "source_report"])
def test_a_reserved_name_is_never_accepted_from_a_request_even_if_declared(name):
    got = coerce_step_parameters({name: StepParameter("str")}, {name: "x"})
    assert got.accepted == {} and "supplied by Resource Explorer" in got.invalid[0]


def test_the_value_sampling_steps_declare_sampling_and_it_is_checked():
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_STEP_REGISTRY

    profile = DATABASE_STEP_REGISTRY["postgres_column_profile"].parameters
    assert set(profile) == {"sampling", "read_egeria_catalog"}
    assert set(DATABASE_STEP_REGISTRY["postgres_nested_columns"].parameters) == {"sampling"}
    ok = coerce_step_parameters(profile, {"sampling": '{"strategy": "head"}', "read_egeria_catalog": "false"})
    assert ok.accepted == {"sampling": {"strategy": "head"}, "read_egeria_catalog": False}
    bad = coerce_step_parameters(profile, {"sampling": '{"strategy": "everything"}'})
    assert bad.accepted == {} and "'sampling'" in bad.invalid[0]


# ── through the engine host ──────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def declares_probe(monkeypatch):
    """`postgres_operations` declares one INT parameter, `probe`, for these tests only."""
    from resource_explorer.surveyors.database import survey_definition_adapter as db

    monkeypatch.setattr(db.DATABASE_STEP_REGISTRY["postgres_operations"], "parameters",
                        {"probe": StepParameter(INT)})


def _action_with(egeria, reg, guid: str, params: dict | None) -> dict:
    _with_credential(reg)
    _marker_row(reg, INSTANCE, marker=False)
    action = _process_action(egeria, guid, "postgres_operations", instance=INSTANCE, next_guards=())
    if params is not None:
        action["requestParameters"] = params
    _sweep(reg)
    _run_queue_once(reg)
    return egeria.actions[guid]["completion"]


def test_a_declared_request_parameter_reaches_the_step_coerced(reg, egeria, no_connection, runner, declares_probe):
    done = _action_with(egeria, reg, "ea-probe", {"probe": "3", "includeSchemaNames": "sales"})
    assert runner.call_args.kwargs["probe"] == 3
    assert "includeSchemaNames" not in runner.call_args.kwargs
    # Named, not silently dropped: Egeria's own parameters ride in the same map.
    assert "not used: includeSchemaNames" in done["message"]


def test_a_request_parameter_that_does_not_parse_refuses_the_step_before_it_runs(reg, egeria, no_connection, runner,
                                                                                 declares_probe):
    done = _action_with(egeria, reg, "ea-bad", {"probe": "three"})
    # survey-invalid, plus the `Any` the engine host emits for authored next-step links (Mandy note A12).
    assert done["status"] == "INVALID" and "survey-invalid" in done["guards"]
    assert "'probe'" in done["message"] and "not valid" in done["message"]
    runner.assert_not_called()


def test_a_request_can_never_set_the_credential(reg, egeria, no_connection, runner):
    done = _action_with(egeria, reg, "ea-cred", {"db_pwd": "from-the-request"})
    assert done["status"] == "INVALID" and "supplied by Resource Explorer" in done["message"]
    runner.assert_not_called()


def test_no_request_parameters_changes_nothing(reg, egeria, no_connection, runner, declares_probe):
    done = _action_with(egeria, reg, "ea-none", None)
    assert "probe" not in runner.call_args.kwargs
    assert "not used" not in (done["message"] or "")

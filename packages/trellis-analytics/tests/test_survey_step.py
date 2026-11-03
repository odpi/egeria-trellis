"""Level 2 of a step (brief 2c'): the library's `SurveyStep` wrapper.

The user's code (level 1) is a plain `run(inputs) -> results`. The wrapper owns everything else: the declared
inputs and nothing more, never a password, at most read-only on the resource, the envelope on every result, and
the store. These tests drive it with a stand-in host; the end-to-end run through RE is in
test_database_staleness_step.py.
"""
import dataclasses
import logging

import pytest

from trellis_analytics.envelope import Envelope, ResultState
from trellis_analytics.manifest import StepManifest
from trellis_analytics.step import HostContext, StepInputs, StepRefused, SurveyStep


def _manifest(**kw) -> StepManifest:
    base = dict(name="s", function="nowhere.run", source="ods", version="9.9", resource_types=["database"])
    base.update(kw)
    return StepManifest(**base)


def _host(**kw) -> HostContext:
    base = dict(resource_type="database", slug="shop", producing_run="run-1", ods=object())
    base.update(kw)
    return HostContext(**base)


# ── credential tier: read-only on the resource and nothing more (owner ruling 2026-10-07) ──

def test_manifest_refuses_a_tier_above_read_only_with_a_sentence_naming_it():
    with pytest.raises(ValueError, match=r"'write'.*read-only on the resource"):
        _manifest(credential_tier="write")


def test_wrapper_refuses_a_higher_tier_even_from_a_manifest_built_unchecked():
    m = StepManifest.model_construct(**{**_manifest().model_dump(), "credential_tier": "owner"})
    with pytest.raises(StepRefused, match=r"'owner'.*read-only on the resource"):
        SurveyStep(m, code=lambda inputs: [])


def test_wrapper_hands_a_connection_opened_at_read_only_never_a_password():
    seen, asked = {}, []

    def connect(tier):
        asked.append(tier)
        return "a-connection"

    def code(inputs):
        seen["inputs"] = inputs
        return []

    m = _manifest(credential_tier="read_only", inputs=["ods", "connection"])
    SurveyStep(m, code=code).run(_host(connect=connect))
    assert asked == ["read_only"]
    assert seen["inputs"].connection == "a-connection"
    names = {f.name for f in dataclasses.fields(StepInputs)} | {f.name for f in dataclasses.fields(HostContext)}
    assert not [n for n in names if any(w in n for w in ("pass", "pwd", "secret", "user"))], names
    with pytest.raises(TypeError):
        HostContext(resource_type="database", slug="shop", producing_run="r", db_pwd="hunter2")


def test_a_step_that_declares_no_connection_gets_none_and_the_host_is_not_asked():
    seen = {}
    m = _manifest(inputs=["ods"])
    SurveyStep(m, code=lambda i: seen.setdefault("i", i) and []).run(
        _host(connect=lambda tier: pytest.fail("a step that declared no connection was handed one")))
    assert seen["i"].connection is None and seen["i"].ods is not None


def test_undeclared_inputs_are_not_handed_over():
    seen = {}
    m = _manifest(inputs=["resource"])
    SurveyStep(m, code=lambda i: seen.setdefault("i", i) and []).run(_host())
    assert seen["i"].ods is None and seen["i"].slug == "shop"


def test_an_unknown_input_is_refused_naming_it():
    with pytest.raises(ValueError, match="'password'"):
        _manifest(inputs=["ods", "password"])


def test_a_connection_without_read_only_tier_is_refused():
    with pytest.raises(ValueError, match="connection"):
        _manifest(inputs=["connection"], credential_tier="none")


# ── the envelope on every result, and the store ──

def test_every_result_carries_the_envelope_and_is_stored():
    stored: list = []

    def code(inputs):
        return [{"scope": "public.orders", "state": "measured", "value": {"rows_changed": 3}, "summary": "+3 rows"},
                {"scope": "public.v", "state": "not_applicable", "reason": "a view holds no rows of its own"}]

    anns = SurveyStep(_manifest(), code=code).run(_host(store=lambda a: stored.extend(a) or len(a)))
    assert stored == anns and len(anns) == 2
    for a in anns:
        props = a.additional_properties()
        env = Envelope.from_additional_properties(props)
        assert (env.producer_version, env.producing_run) == ("9.9", "run-1")
        assert env.scope == a.scope
        assert a.annotation_type == "ResourceMeasureAnnotation" and a.analysis_step == "s"
    assert anns[0].envelope.result_state is ResultState.MEASURED
    assert anns[0].resource_properties == {"rows_changed": "3"}
    assert anns[1].envelope.result_state_reason == "a view holds no rows of its own"


@pytest.mark.parametrize("result, words", [
    ({"scope": "x", "value": {}}, "no state"),
    ({"scope": "x", "state": "probably"}, "'probably'"),
    ({"scope": "x", "state": "not_established"}, "reason"),
    ({"state": "measured"}, "scope"),
])
def test_a_result_without_a_whole_envelope_is_refused_and_nothing_is_stored(result, words):
    stored: list = []
    with pytest.raises(StepRefused, match=words):
        SurveyStep(_manifest(), code=lambda i: [result]).run(_host(store=lambda a: stored.extend(a) or len(a)))
    assert stored == []


def test_unknown_question_id_or_purpose_warns_naming_it_and_the_step_still_loads(caplog):
    m = _manifest(questions=["Q-0041", "Q-9999"], purposes=["Maintain", "Wander"])
    with caplog.at_level(logging.WARNING):
        s = SurveyStep(m, code=lambda i: [])
    assert "'Q-9999'" in caplog.text and "'Wander'" in caplog.text
    assert "'Q-0041'" not in caplog.text and "'Maintain'" not in caplog.text
    assert s.manifest.questions == ["Q-0041", "Q-9999"] and s.manifest.purposes == ["Maintain", "Wander"]


# ── what an engine host dispatches on: the request type and the nothing-to-derive guard ──

def test_a_step_declares_its_request_type_or_gets_the_conventional_one():
    assert _manifest(name="database_staleness").request_type == "re-database-staleness"
    assert _manifest(request_type="re-other").request_type == "re-other"


def test_a_request_type_that_is_not_hyphenated_lower_case_is_refused():
    with pytest.raises(ValueError, match="request_type 'Re_Bad'"):
        _manifest(request_type="Re_Bad")


def test_the_nothing_to_derive_guard_is_one_of_egerias_survey_guards():
    from trellis_analytics.manifest import SURVEY_GUARDS

    assert SURVEY_GUARDS == ("survey-completed", "survey-invalid", "survey-failed")
    assert _manifest().nothing_to_derive_guard == "survey-invalid"
    assert _manifest(nothing_to_derive_guard="survey-completed").nothing_to_derive_guard == "survey-completed"
    with pytest.raises(ValueError, match="'survey-skipped' is not one of Egeria's survey guards"):
        _manifest(nothing_to_derive_guard="survey-skipped")

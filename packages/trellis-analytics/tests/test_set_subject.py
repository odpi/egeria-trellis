"""A set as the subject (brief 2026-10-30, Part C): one run for a work list's members, a result per member and one
roll-up for the set, all under one producing run; a member the step does not measure is said, never dropped."""
import pytest

from trellis_analytics.manifest import StepManifest
from trellis_analytics.step import HostContext, StepRefused, SurveyStep

SET = "wl_coco"


def _manifest(**kw) -> StepManifest:
    base = dict(name="batch_step", function="nowhere.run", source="ods", version="1", resource_types=["database"],
                subject="set")
    base.update(kw)
    return StepManifest(**base)


def _run(code, members, manifest=None):
    stored = []
    host = HostContext(resource_type="work_list", slug=SET, producing_run="run-7", ods=object(),
                       store=lambda anns: stored.extend(anns) or len(anns), members=tuple(members), set_slug=SET)
    out = SurveyStep(manifest or _manifest(), code=code).run(host)
    return out, stored


def _measure_each(inputs):
    rows = [{"scope": slug, "state": "measured", "value": {"n": 1}, "summary": f"{slug}: 1"} for _, slug in inputs.members]
    rows.append({"scope": inputs.set_slug, "state": "measured", "value": {"members": len(inputs.members)},
                 "summary": f"{len(inputs.members)} measured"})
    return rows


DBS = [("database", "a"), ("database", "b"), ("database", "c")]


def test_a_set_run_over_three_members_writes_three_plus_one_annotations_under_one_run():
    out, stored = _run(_measure_each, DBS)
    assert len(out) == 4 and stored == out
    assert sorted(a.scope for a in out) == ["a", "b", "c", SET]
    assert {a.envelope.producing_run for a in out} == {"run-7"}
    rollup = next(a for a in out if a.scope == SET)
    assert rollup.summary == "3 measured"


def test_a_member_that_is_not_of_the_steps_resource_types_is_skipped_with_a_reason_row_never_silently():
    out, _ = _run(_measure_each, [*DBS, ("filesystem", "fs1")])
    skipped = next(a for a in out if a.scope == "fs1")
    assert skipped.envelope.result_state.value == "not_applicable"
    assert "fs1 is a filesystem; step 'batch_step' measures database" in skipped.envelope.result_state_reason
    assert [s for _, s in next(a for a in out if a.scope == SET).resource_properties.items()] and \
        next(a for a in out if a.scope == SET).resource_properties["members"] == "3"       # the code saw only the databases


def test_a_set_with_no_member_the_step_measures_says_so_in_its_roll_up_and_runs_no_code():
    def never(_):
        raise AssertionError("the code must not run")
    out, _ = _run(never, [("filesystem", "fs1")])
    rollup = next(a for a in out if a.scope == SET)
    assert rollup.envelope.result_state.value == "not_established" and "nothing to measure" in rollup.envelope.result_state_reason
    assert any(a.scope == "fs1" for a in out)


def test_a_result_about_something_that_is_neither_a_member_nor_the_set_is_refused_and_nothing_is_stored():
    def stray(inputs):
        return [*_measure_each(inputs), {"scope": "zzz", "state": "measured"}]
    with pytest.raises(StepRefused, match="neither members of the set"):
        _run(stray, DBS)


@pytest.mark.parametrize("rollups", [0, 2])
def test_a_set_step_returns_exactly_one_roll_up(rollups):
    def code(inputs):
        rows = [r for r in _measure_each(inputs) if r["scope"] != inputs.set_slug]
        return rows + [{"scope": inputs.set_slug, "state": "measured"} for _ in range(rollups)]
    with pytest.raises(StepRefused, match=f"returned {rollups} roll-ups"):
        _run(code, DBS)


def test_a_set_step_cannot_declare_a_connection():
    with pytest.raises(ValueError, match="one connection cannot serve every member"):
        _manifest(inputs=["ods", "connection"], credential_tier="read_only")


def test_a_step_about_one_resource_is_unchanged_and_hands_no_members():
    m = _manifest(subject="resource")
    seen = {}

    def code(inputs):
        seen["m"] = inputs.members
        return [{"scope": "shop", "state": "measured"}]
    host = HostContext(resource_type="database", slug="shop", producing_run="r", ods=object())
    out = SurveyStep(m, code=code).run(host)
    assert seen["m"] == () and [a.scope for a in out] == ["shop"]

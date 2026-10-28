import pytest
from pyegeria.view.analytic_registry import AnalyticFunctionSpec

from trellis_analytics.discovery import discover, register
from trellis_analytics.envelope import Envelope, ResultState
from trellis_analytics.manifest import RoutineManifest, StepManifest
from trellis_analytics.manifests import CURATION_COVERAGE
from trellis_analytics.readers.ods import OdsReader, OdsUnreadable


def test_manifest_is_valid_spec_with_the_five_fields():
    assert isinstance(CURATION_COVERAGE, AnalyticFunctionSpec)
    for f in ("kind", "source", "version", "envelope", "annotation_type", "resource_types"):
        assert hasattr(CURATION_COVERAGE, f), f
    assert (CURATION_COVERAGE.kind, CURATION_COVERAGE.source) == ("routine", "ods")
    assert CURATION_COVERAGE.envelope is True and CURATION_COVERAGE.version == "0.1.0"
    # an old-style spec (none of the new fields) still validates: the base class is unchanged
    AnalyticFunctionSpec(name="old", function="x.y")


def test_step_manifest_defaults_and_read_only_tier():
    s = StepManifest(name="s", function="a.b", source="ods", version="1", resource_types=["database"])
    assert s.kind == "step" and s.annotation_type == "ResourceMeasureAnnotation"
    with pytest.raises(ValueError):
        StepManifest(name="s", function="a.b", source="ods", version="1", credential_tier="admin")


def test_discovery_through_entry_point(entry_point_dist):
    found = discover()
    assert "curation_coverage" in found
    assert found["curation_coverage"].function.endswith("curation_coverage")


def test_manifest_purposes_are_checked_at_load_and_warn_never_reject(caplog):
    """Owner ruling 2026-10-13, "warn only - suggestive everywhere": a manifest's `purpose` qualifier is checked
    against the investigation-purposes list when manifests load; an unknown value is named in a warning and the
    manifest still loads."""
    import logging

    from trellis_analytics.discovery import check_purposes

    m = StepManifest(name="staleness", function="a.b", source="ods", version="1", purposes=["Maintain", "Wander"])
    with caplog.at_level(logging.WARNING):
        warnings = check_purposes({"staleness": m}, ["Maintain", "Find"])
    assert warnings == ["manifest 'staleness': purpose 'Wander' is not in the investigation-purposes list; "
                        "kept as written"]
    assert "'Wander'" in caplog.text
    assert m.purposes == ["Maintain", "Wander"]


def test_manifest_purposes_default_to_resource_explorer_s_list():
    """With RE installed, the list is RE's (its cache of Egeria's list, else its shipped seed): Find and
    Integrate are on it (owner ruling 2026-10-13)."""
    from trellis_analytics.discovery import check_purposes

    m = StepManifest(name="s", function="a.b", source="ods", version="1", purposes=["Find", "Integrate"])
    assert check_purposes({"s": m}) == []


def test_register_adds_to_pyegeria_registry_once(entry_point_dist):
    from pyegeria.view.analytic_registry import clear_runtime_analytic_functions, get_analytic_registry
    clear_runtime_analytic_functions()
    try:
        assert register() == ["context_pack", "curation_coverage"]
        assert register() == []          # idempotent
        assert get_analytic_registry()["curation_coverage"].kind == "routine"
    finally:
        clear_runtime_analytic_functions()


def test_envelope_round_trips_through_additional_properties():
    e = Envelope(result_state=ResultState.NOTHING_FOUND, producer_version="0.1.0", scope="database:x", producing_run="r1")
    props = e.to_additional_properties()
    assert props["resultState"] == "nothing_found"
    assert Envelope.from_additional_properties(props) == e


def test_envelope_states_are_four_and_reasons_required():
    assert {s.value for s in ResultState} == {"measured", "nothing_found", "not_established", "not_applicable"}
    with pytest.raises(ValueError):
        Envelope(result_state=ResultState.NOT_ESTABLISHED)       # not established must say why
    with pytest.raises(ValueError):
        Envelope.from_additional_properties({"measuredAt": "t"})  # a result without an envelope is refused


def test_ods_reader_is_select_only_and_read_only(make_ods):
    ods = OdsReader(make_ods())
    with pytest.raises(ValueError):
        ods.query("DELETE FROM egeria_outbox")
    with pytest.raises(OdsUnreadable):
        OdsReader("sqlite:////nonexistent/dir/x.sqlite").query("SELECT 1")


def test_manifest_questions_are_checked_by_stable_id_and_warn_never_reject(caplog):
    """Owner ruling, "suggestive everywhere": a manifest names the questions it answers by catalog id; an id
    the catalog does not hold is named in a warning and the manifest still loads, ids kept as written."""
    import logging

    from trellis_analytics.discovery import check_questions

    m = StepManifest(name="staleness", function="a.b", source="ods", version="1", questions=["Q-0001", "Q-9999"])
    with caplog.at_level(logging.WARNING):
        warnings = check_questions({"staleness": m}, {"Q-0001", "Q-0002"})
    assert warnings == ["manifest 'staleness': question 'Q-9999' is not an id in the question catalog; "
                        "kept as written"]
    assert "'Q-9999'" in caplog.text
    assert m.questions == ["Q-0001", "Q-9999"]


def test_manifest_questions_default_to_resource_explorer_s_catalog_without_a_registry():
    """With RE installed the ids are the shipped catalog's, read without constructing a ProjectRegistry (which
    would connect to the shared registry and migrate it)."""
    from resource_explorer.registry import ProjectRegistry
    from trellis_analytics.discovery import check_questions, known_question_ids

    ids = known_question_ids()
    assert ids and "Q-0001" in ids
    m = StepManifest(name="s", function="a.b", source="ods", version="1", questions=["Q-0001", "Q-0002"])
    original = ProjectRegistry.__init__
    ProjectRegistry.__init__ = lambda *a, **k: (_ for _ in ()).throw(AssertionError("constructed a registry"))
    try:
        assert check_questions({"s": m}) == []
        assert check_questions({"s": StepManifest(name="t", function="a.b", source="ods", version="1",
                                                 questions=["Q-8888"])}) != []
    finally:
        ProjectRegistry.__init__ = original


def test_manifest_with_no_questions_does_not_read_the_catalog():
    from trellis_analytics.discovery import check_questions

    assert check_questions({"c": CURATION_COVERAGE}, known=None) == []
    assert CURATION_COVERAGE.questions == []

import threading
from unittest.mock import patch

import pytest

import resource_explorer.surveyors.survey_definition_reader as sdr

from resource_explorer.surveyors.survey_definition_reader import (
    SurveyDefinitionReader,
    SurveyDefinitionReaderError,
    UnsupportedSurveyDefinitionError,
)

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

# Fixtures below match the real GovernanceOfficer.get_governance_action_process_graph
# (renamed from get_governance_process_graph in an upcoming pyegeria release)
# response shape, confirmed against a live qs-view-server for both a single-step
# and a two-step chained Survey Definition (2026-07-07/08): a flat node list
# ("firstProcessStep" + "nextProcessSteps") plus a separate flat edge list
# ("processStepLinks", keyed by GUID via "previousProcessStep"/"nextProcessStep").
# Step properties live under "processStepProperties" (not "properties" — only
# the process element itself uses that).


def _step_element(guid, qualified_name, additional_properties=None, display_name=None):
    return {
        "elementHeader": {"guid": guid},
        "processStepProperties": {
            "qualifiedName": qualified_name,
            "displayName": display_name or qualified_name,
            "additionalProperties": additional_properties or {},
        },
    }


def _link(prev_guid, next_guid, guard=None, mandatory_guard=False):
    link = {
        "previousProcessStep": {"guid": prev_guid},
        "nextProcessStep": {"guid": next_guid},
        "nextProcessStepLinkGUID": "link-guid",
        "mandatoryGuard": mandatory_guard,
    }
    if guard is not None:
        link["guard"] = guard
    return link


def _graph(process_guid, qualified_name, additional_properties=None,
           first_step=None, next_steps=None, links=None):
    graph = {
        "governanceActionProcess": {
            "elementHeader": {"guid": process_guid},
            "properties": {
                "qualifiedName": qualified_name,
                "displayName": qualified_name,
                "additionalProperties": additional_properties or {},
            },
        },
    }
    if first_step is not None:
        graph["firstProcessStep"] = {"element": first_step, "linkGUID": "first-link-guid"}
    if next_steps is not None:
        graph["nextProcessSteps"] = next_steps
    if links is not None:
        graph["processStepLinks"] = links
    return graph


def _reader() -> SurveyDefinitionReader:
    # _parse_graph is side-effect-free and needs no live connection/pyegeria client.
    return SurveyDefinitionReader()


def test_parse_single_step_graph():
    step = _step_element(
        "step-1", "GovActionProcessStep::Survey::SchemaAndStats",
        additional_properties={"executes_at": "resource-explorer", "re_analysis_step": "postgres_schema_and_stats"},
    )
    graph = _graph(
        "proc-1", "GovActionProcess::Survey",
        additional_properties={"supported_technology_type": "PostgreSQL Database"},
        first_step=step,
    )

    survey_def = _reader()._parse_graph(graph)

    assert survey_def.process_guid == "proc-1"
    assert survey_def.supported_technology_type == "PostgreSQL Database"
    assert len(survey_def.steps) == 1
    assert survey_def.steps[0].guid == "step-1"
    assert survey_def.steps[0].executes_at == "resource-explorer"
    assert survey_def.steps[0].re_analysis_step == "postgres_schema_and_stats"
    assert survey_def.survey_kind is None  # not present in additional_properties above


def test_parse_survey_kind_when_present():
    step = _step_element(
        "step-1", "GovActionProcessStep::Survey::Step",
        additional_properties={"executes_at": "resource-explorer"},
    )
    graph = _graph(
        "proc-1", "GovActionProcess::Survey",
        additional_properties={"supported_technology_type": "Git Repository", "survey_kind": "discovery"},
        first_step=step,
    )

    survey_def = _reader()._parse_graph(graph)

    assert survey_def.survey_kind == "discovery"


def test_parse_linear_two_step_graph():
    step1 = _step_element(
        "step-1", "GovActionProcessStep::Survey::SchemaAndStats",
        additional_properties={"executes_at": "resource-explorer", "re_analysis_step": "postgres_schema_and_stats"},
    )
    step2 = _step_element(
        "step-2", "GovActionProcessStep::Survey::RowCountSnapshot",
        additional_properties={"executes_at": "egeria"},
    )
    graph = _graph(
        "proc-1", "GovActionProcess::Survey",
        additional_properties={"supported_technology_type": "PostgreSQL Database"},
        first_step=step1,
        next_steps=[step2],
        links=[_link("step-1", "step-2", mandatory_guard=False)],
    )

    survey_def = _reader()._parse_graph(graph)

    assert [s.guid for s in survey_def.steps] == ["step-1", "step-2"]
    assert survey_def.steps[1].executes_at == "egeria"


def test_parse_graph_with_no_first_step():
    graph = _graph("proc-empty", "GovActionProcess::Empty")
    survey_def = _reader()._parse_graph(graph)
    assert survey_def.steps == []


def test_a_branching_definition_parses_with_its_guards_intact():
    """This raised UnsupportedSurveyDefinitionError until 2026-08-26.

    Refusing was honest while nothing could run a branch, but it made a
    branching definition unreadable as well as unrunnable — not displayable,
    not diffable against its authored document, not repairable. The plan
    (survey_execution_plan) and the Prefect flow can both express and run one,
    so the reader was the last thing in the way.
    """
    step1 = _step_element("step-1", "Step::First", additional_properties={"executes_at": "resource-explorer"})
    branch_a = _step_element("a", "Step::A", additional_properties={"executes_at": "resource-explorer"})
    branch_b = _step_element("b", "Step::B", additional_properties={"executes_at": "resource-explorer"})
    graph = _graph(
        "proc-branch", "GovActionProcess::Branching",
        first_step=step1,
        next_steps=[branch_a, branch_b],
        links=[_link("step-1", "a", guard="ok"), _link("step-1", "b", guard="not-ok")],
    )

    survey_def = _reader()._parse_graph(graph)

    assert [s.qualified_name for s in survey_def.steps][0] == "Step::First"
    assert {s.qualified_name for s in survey_def.steps} == {"Step::First", "Step::A", "Step::B"}
    # The guards are what make it a branch rather than an ambiguous chain, and
    # they are what the executor routes on.
    assert {(l.guard) for l in survey_def.links} == {"ok", "not-ok"}
    assert survey_def.unreachable_step_guids == []


def test_a_cycle_is_still_rejected():
    """A branch is a shape that can be ordered; a cycle is one that cannot be,
    and any order for it would be a fiction."""
    step1 = _step_element("step-1", "Step::First", additional_properties={"executes_at": "resource-explorer"})
    step2 = _step_element("step-2", "Step::Second", additional_properties={"executes_at": "resource-explorer"})
    graph = _graph(
        "proc-cycle", "GovActionProcess::Cyclic",
        first_step=step1, next_steps=[step2],
        links=[_link("step-1", "step-2"), _link("step-2", "step-1")],
    )

    with pytest.raises(UnsupportedSurveyDefinitionError):
        _reader()._parse_graph(graph)


def test_missing_executes_at_is_rejected():
    step1 = _step_element("step-1", "Step::NoExecutesAt", additional_properties={})
    graph = _graph("proc-missing", "GovActionProcess::Missing", first_step=step1)

    with pytest.raises(SurveyDefinitionReaderError):
        _reader()._parse_graph(graph)


def test_unrecognized_executes_at_value_parses_fine():
    """The reader only rejects unsupported *shapes* (branching, missing keys) —
    an unfamiliar-but-well-formed executes_at value (e.g. a future engine like
    Airflow) is not the reader's job to reject; that's the executor's call."""
    step1 = _step_element(
        "step-1", "Step::Airflow",
        additional_properties={"executes_at": "airflow", "re_analysis_step": "some_dag"},
    )
    graph = _graph("proc-airflow", "GovActionProcess::Airflow", first_step=step1)

    survey_def = _reader()._parse_graph(graph)
    assert survey_def.steps[0].executes_at == "airflow"


def test_cycle_is_rejected():
    step1 = _step_element("step-1", "Step::One", additional_properties={"executes_at": "egeria"})
    step2 = _step_element("step-2", "Step::Two", additional_properties={"executes_at": "egeria"})
    graph = _graph(
        "proc-cycle", "GovActionProcess::Cycle",
        first_step=step1,
        next_steps=[step2],
        links=[_link("step-1", "step-2"), _link("step-2", "step-1")],  # cycle back to step1
    )

    with pytest.raises(UnsupportedSurveyDefinitionError):
        _reader()._parse_graph(graph)


class _FakeGovernanceOfficer:
    def __init__(self, results):
        self._results = results
        self.find_governance_definitions_calls = 0

    def find_governance_definitions(self, **_kwargs):
        self.find_governance_definitions_calls += 1
        return self._results


def _reader_with_fake_results(results) -> SurveyDefinitionReader:
    from resource_explorer.surveyors import survey_definition_reader as sdr

    sdr.clear_caches()  # D3's module-level cache would otherwise leak stale results across tests
    reader = _reader()
    reader._governance_officer = _FakeGovernanceOfficer(results)  # short-circuits connect()
    return reader


def _governance_process_result(qualified_name, technology_type, survey_kind=None, guid="g"):
    additional = {"supported_technology_type": technology_type}
    if survey_kind is not None:
        additional["survey_kind"] = survey_kind
    return {
        "properties": {"qualifiedName": qualified_name, "displayName": qualified_name, "additionalProperties": additional},
        "elementHeader": {"guid": guid},
    }


class TestFindCandidateProcessGuidsSurveyKindFilter:
    """Analysis-step Egeria registration plan D1 (survey_kind filtering) —
    docs/discovery-automate-project-context-plan.md Part 1."""

    def test_no_survey_kind_filter_returns_everything_matching_technology_type(self):
        results = [
            _governance_process_result("GovActionProcess::A", "Git Repository", survey_kind="discovery"),
            _governance_process_result("GovActionProcess::B", "Git Repository", survey_kind="automate_full"),
            _governance_process_result("GovActionProcess::C", "PostgreSQL Database", survey_kind="discovery"),
        ]
        reader = _reader_with_fake_results(results)
        candidates = reader.find_candidate_process_guids("Git Repository")
        assert {c["qualified_name"] for c in candidates} == {"GovActionProcess::A", "GovActionProcess::B"}

    def test_survey_kind_filter_narrows_to_exact_match(self):
        results = [
            _governance_process_result("GovActionProcess::A", "Git Repository", survey_kind="discovery"),
            _governance_process_result("GovActionProcess::B", "Git Repository", survey_kind="automate_full"),
        ]
        reader = _reader_with_fake_results(results)
        candidates = reader.find_candidate_process_guids("Git Repository", survey_kind="discovery")
        assert [c["qualified_name"] for c in candidates] == ["GovActionProcess::A"]

    def test_survey_kind_filter_excludes_definitions_with_no_survey_kind_at_all(self):
        # A Survey Definition authored before this convention existed (no
        # survey_kind key at all) must not leak into a kind-filtered list.
        results = [_governance_process_result("GovActionProcess::Legacy", "Git Repository", survey_kind=None)]
        reader = _reader_with_fake_results(results)
        assert reader.find_candidate_process_guids("Git Repository", survey_kind="discovery") == []
        # ...but shows up fine when no filter is applied (backward compatibility).
        assert len(reader.find_candidate_process_guids("Git Repository")) == 1


class TestFindCandidateProcessGuidsCaching:
    """D3 (docs/survey-question-context-plan.md) — the full search_string="*"
    scan is cached short-TTL, same as D2's scoped path."""

    def test_second_call_with_same_args_does_not_hit_egeria_again(self):
        results = [_governance_process_result("GovActionProcess::A", "Git Repository")]
        reader = _reader_with_fake_results(results)
        reader.find_candidate_process_guids("Git Repository")
        reader.find_candidate_process_guids("Git Repository")
        assert reader._governance_officer.find_governance_definitions_calls == 1

    def test_different_technology_type_is_not_cache_polluted(self):
        results = [
            _governance_process_result("GovActionProcess::A", "Git Repository"),
            _governance_process_result("GovActionProcess::B", "PostgreSQL Database"),
        ]
        reader = _reader_with_fake_results(results)
        repo_candidates = reader.find_candidate_process_guids("Git Repository")
        db_candidates = reader.find_candidate_process_guids("PostgreSQL Database")
        assert [c["qualified_name"] for c in repo_candidates] == ["GovActionProcess::A"]
        assert [c["qualified_name"] for c in db_candidates] == ["GovActionProcess::B"]
        assert reader._governance_officer.find_governance_definitions_calls == 2


class _FakeClassificationExplorer:
    """Fake for ClassificationExplorer — records call counts so tests can
    assert D3's caching actually avoids a second live call."""

    def __init__(self, guid_by_name=None, scoped_elements_by_guid=None):
        self._guid_by_name = guid_by_name or {}
        self._scoped_elements_by_guid = scoped_elements_by_guid or {}
        self.get_guid_for_name_calls = 0
        self.get_scoped_elements_calls = 0

    def create_egeria_bearer_token(self, *_a, **_kw):
        pass

    def get_guid_for_name(self, name, **_kw):
        self.get_guid_for_name_calls += 1
        return self._guid_by_name.get(name)

    def get_scoped_elements(self, scope_guid, **_kw):
        self.get_scoped_elements_calls += 1
        return self._scoped_elements_by_guid.get(scope_guid, [])


def _reader_with_fake_classification_explorer(fake) -> SurveyDefinitionReader:
    from resource_explorer.surveyors import survey_definition_reader as sdr

    sdr.clear_caches()
    reader = _reader()
    reader._classification_explorer = fake  # short-circuits _connect_classification_explorer()
    # _lookup_question_guid (unlike other ClassificationExplorer callers) goes
    # through the thread-local path, which may construct on a shared-pool
    # worker thread rather than this one (pyegeria ISSUE-96 mitigation) — stub
    # the single construction seam so the fake is used no matter which thread
    # asks for it, same as reader._classification_explorer does for the
    # main-thread-cached path.
    reader._new_classification_explorer = lambda: fake
    return reader


class TestResolveQuestionGuid:
    """D2 (docs/survey-question-context-plan.md) — Question name -> GUID
    resolution via ClassificationExplorer.get_guid_for_name, long-TTL cached."""

    def test_resolves_known_question(self):
        fake = _FakeClassificationExplorer(guid_by_name={"Is this repository actively maintained?": "q-guid-1"})
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader.resolve_question_guid("Is this repository actively maintained?") == "q-guid-1"

    def test_unknown_question_returns_none_not_raises(self):
        fake = _FakeClassificationExplorer()
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader.resolve_question_guid("Some question never authored in Egeria") is None

    def test_result_is_cached_across_calls(self):
        fake = _FakeClassificationExplorer(guid_by_name={"Q": "guid-1"})
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader.resolve_question_guid("Q") == "guid-1"
        assert reader.resolve_question_guid("Q") == "guid-1"
        assert fake.get_guid_for_name_calls == 1

    def test_lookup_error_returns_none_not_raises(self):
        class _Raising(_FakeClassificationExplorer):
            def get_guid_for_name(self, name, **_kw):
                raise RuntimeError("boom")

        reader = _reader_with_fake_classification_explorer(_Raising())
        assert reader.resolve_question_guid("Q") is None

    def test_pyegeria_not_found_sentence_is_not_treated_as_a_guid(self):
        """Regression: pyegeria signals "no match" by returning the *string*
        "No elements found", not None — a truthy value that passed straight
        through the old `or None` guard and was handed onward as if it were a
        GUID. It then reached get_scoped_elements() as a URL path segment, 404'd,
        and the caller's broad except swallowed it — silently downgrading the
        scoped fast path to the full scan on every call.

        This escaped the suite because the fake above returns None on a miss,
        i.e. it was better behaved than the real library; this test pins the
        real behavior instead."""
        fake = _FakeClassificationExplorer(guid_by_name={"Q": "No elements found"})
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader.resolve_question_guid("Q") is None

    def test_non_string_lookup_result_is_not_treated_as_a_guid(self):
        fake = _FakeClassificationExplorer(guid_by_name={"Q": {"guid": "x"}})
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader.resolve_question_guid("Q") is None


class _CountingClassificationExplorerFactory:
    """Records one fresh fake per call to ``_new_classification_explorer``,
    and which thread asked for it — for pinning per-thread client identity
    (pyegeria ISSUE-96 mitigation) without touching real pyegeria/network."""

    def __init__(self, guid_by_name=None):
        self._guid_by_name = guid_by_name or {}
        self.instances = []
        self.calling_thread_idents = []

    def __call__(self):
        client = _FakeClassificationExplorer(guid_by_name=self._guid_by_name)
        self.instances.append(client)
        self.calling_thread_idents.append(threading.get_ident())
        return client


class TestThreadLocalClassificationExplorer:
    """pyegeria ISSUE-96 mitigation: _lookup_question_guid must never share
    one ClassificationExplorer (and so one httpx.AsyncClient / event loop)
    across threads — see the mechanism note on
    SurveyDefinitionReader._thread_local_explorer (__init__) and on
    _thread_local_classification_explorer for the measured before/after
    (100% failure shared -> 0% failure per-thread, 52 names x 2 runs)."""

    def test_different_threads_get_different_clients(self):
        factory = _CountingClassificationExplorerFactory()
        reader = _reader()
        reader._new_classification_explorer = factory
        reader.principal()   # Brief I: resolved in the calling thread, as the pooled path does

        seen = {}

        def call(key):
            seen[key] = reader._thread_local_classification_explorer()

        t1 = threading.Thread(target=call, args=("t1",))
        t2 = threading.Thread(target=call, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert seen["t1"] is not seen["t2"]
        assert len(factory.instances) == 2

    def test_same_thread_reuses_its_own_client(self):
        factory = _CountingClassificationExplorerFactory()
        reader = _reader()
        reader._new_classification_explorer = factory
        reader.principal()   # Brief I: resolved in the calling thread, as the pooled path does

        seen = []

        def call_twice():
            seen.append(reader._thread_local_classification_explorer())
            seen.append(reader._thread_local_classification_explorer())

        t = threading.Thread(target=call_twice)
        t.start()
        t.join()

        assert seen[0] is seen[1]
        assert len(factory.instances) == 1  # constructed once, reused on the 2nd call

    def test_main_thread_path_still_uses_the_cached_classification_explorer(self):
        """Every OTHER ClassificationExplorer caller (e.g. the D2 scoped
        walk) must keep using the shared, main-thread-cached attribute —
        only question-GUID lookups switched to the thread-local path."""
        fake = _FakeClassificationExplorer()
        reader = _reader()
        reader._classification_explorer = fake

        assert reader._connect_classification_explorer() is fake
        # A second call returns the SAME cached instance, never re-constructs.
        reader._new_classification_explorer = lambda: (_ for _ in ()).throw(
            AssertionError("must not reconstruct — _classification_explorer was already cached")
        )
        assert reader._connect_classification_explorer() is fake

    def test_a_lookup_failure_on_one_thread_does_not_poison_another_threads_client(self):
        class _FlakyThenFine:
            """First construction raises on every call; every construction
            after that succeeds — models one thread's client going bad
            without affecting a client built fresh on another thread."""

            _constructed = 0

            def __init__(self):
                _FlakyThenFine._constructed += 1
                self._is_first = _FlakyThenFine._constructed == 1

            def create_egeria_bearer_token(self, *_a, **_kw):
                pass

            def get_guid_for_name(self, name, **_kw):
                if self._is_first:
                    raise RuntimeError("bound to a different event loop")
                return "guid-from-second-thread"

        reader = _reader()
        reader._new_classification_explorer = _FlakyThenFine
        reader.principal()   # Brief I: resolved in the calling thread, as the pooled path does

        results = {}

        def call(key):
            results[key] = reader._lookup_question_guid("Q")

        t1 = threading.Thread(target=call, args=("t1",))
        t1.start()
        t1.join()
        assert results["t1"] is None  # failure degrades to not-found, never raises

        t2 = threading.Thread(target=call, args=("t2",))
        t2.start()
        t2.join()
        assert results["t2"] == "guid-from-second-thread"  # unaffected by t1's failure


class TestFindCandidateProcessGuidsByQuestions:
    """The Egeria walk — now the FALLBACK, exercised directly.

    Since 2026-09-11 find_candidate_process_guids_by_questions answers from
    the authored documents and only walks Egeria when no documents are
    readable. These tests pin the walk's own behaviour, so they call it by
    its new name; the local path has its own class below.

    D2 — scoped candidate lookup via ClassificationExplorer.get_scoped_elements,
    replacing the search_string="*" full scan for a phase/perspective-narrowed
    query. D3 — short-TTL cached."""

    def _survey_definition_element(self, qualified_name, technology_type, survey_kind=None, guid="sd-guid"):
        additional = {"supported_technology_type": technology_type}
        if survey_kind is not None:
            additional["survey_kind"] = survey_kind
        return {
            "properties": {"qualifiedName": qualified_name, "displayName": qualified_name, "additionalProperties": additional},
            "elementHeader": {"guid": guid},
        }

    def test_no_resolvable_question_returns_empty(self):
        fake = _FakeClassificationExplorer()
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader._find_candidates_via_egeria(["Unknown question"], "Git Repository") == []
        # Never even attempts a scoped-elements call with no resolvable guid.
        assert fake.get_scoped_elements_calls == 0

    def test_returns_scoped_survey_definitions_matching_technology_type(self):
        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid"},
            scoped_elements_by_guid={
                "q1-guid": [
                    self._survey_definition_element("GovActionProcess::A", "Git Repository"),
                    self._survey_definition_element("GovActionProcess::B", "PostgreSQL Database", guid="sd-guid-2"),
                ]
            },
        )
        reader = _reader_with_fake_classification_explorer(fake)
        candidates = reader._find_candidates_via_egeria(["Q1"], "Git Repository")
        assert [c["qualified_name"] for c in candidates] == ["GovActionProcess::A"]

    def test_survey_kind_filter_applied(self):
        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid"},
            scoped_elements_by_guid={
                "q1-guid": [
                    self._survey_definition_element("GovActionProcess::A", "Git Repository", survey_kind="discovery"),
                    self._survey_definition_element("GovActionProcess::B", "Git Repository", survey_kind="automate_full", guid="g2"),
                ]
            },
        )
        reader = _reader_with_fake_classification_explorer(fake)
        candidates = reader._find_candidates_via_egeria(["Q1"], "Git Repository", survey_kind="discovery")
        assert [c["qualified_name"] for c in candidates] == ["GovActionProcess::A"]

    def test_dedupes_survey_definition_scoped_by_multiple_questions(self):
        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid", "Q2": "q2-guid"},
            scoped_elements_by_guid={
                "q1-guid": [self._survey_definition_element("GovActionProcess::A", "Git Repository")],
                "q2-guid": [self._survey_definition_element("GovActionProcess::A", "Git Repository")],
            },
        )
        reader = _reader_with_fake_classification_explorer(fake)
        candidates = reader._find_candidates_via_egeria(["Q1", "Q2"], "Git Repository")
        assert len(candidates) == 1

    def test_result_is_cached_across_calls(self):
        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid"},
            scoped_elements_by_guid={"q1-guid": [self._survey_definition_element("GovActionProcess::A", "Git Repository")]},
        )
        reader = _reader_with_fake_classification_explorer(fake)
        reader._find_candidates_via_egeria(["Q1"], "Git Repository")
        reader._find_candidates_via_egeria(["Q1"], "Git Repository")
        assert fake.get_scoped_elements_calls == 1

    def test_string_response_treated_as_no_elements_found(self):
        # pyegeria returns a bare string (e.g. "No elements found") instead
        # of a list when a query has no results — must not crash iterating it.
        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid"},
            scoped_elements_by_guid={},
        )
        fake.get_scoped_elements = lambda scope_guid, **_kw: "No elements found"
        reader = _reader_with_fake_classification_explorer(fake)
        assert reader._find_candidates_via_egeria(["Q1"], "Git Repository") == []


class _FakeClock:
    """A shared mutable clock, not a finite scripted sequence — the code
    under test (and its own upstream helpers, e.g. resolve_question_guid's
    caching) call time.monotonic() an unknown/unstable number of times, so a
    fixed-length iter([...]) is fragile: run out early and next() raises
    StopIteration, silently swallowed by find_candidate_process_guids_by_
    questions' own broad except, and the test just sees zero warnings with
    no hint why. A clock that's simply read (never exhausts) and explicitly
    advanced only where the test cares (inside the fake's get_scoped_elements)
    sidesteps that entirely."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestSlowCallInstrumentation:
    """Instrumentation on the Egeria walk, which is now the fallback path
    (`_find_candidates_via_egeria`) and is called by that name here.

    2026-09-04, after a real stuck-server incident with no way to tell
    'slow' from 'hung': a get_scoped_elements call over 5s, or the whole
    sequential loop over 10s, must log a WARNING naming which question and
    how long — the diagnostic this incident needed and didn't have. Asserts
    against the module's `log` object directly (mocked) rather than caplog,
    to sidestep this suite's own logging setup/propagation config entirely —
    the point under test is "did .warning() get called with the right
    message", not how a handler downstream renders it."""

    def test_a_single_slow_call_logs_a_warning_naming_the_question(self):
        import time as time_module

        from resource_explorer.surveyors import survey_definition_reader as sdr

        clock = _FakeClock()
        fake = _FakeClassificationExplorer(guid_by_name={"Q1": "q1-guid"})
        fake.get_scoped_elements = lambda scope_guid, **_kw: (clock.advance(6.0), [])[1]
        reader = _reader_with_fake_classification_explorer(fake)

        with patch.object(time_module, "monotonic", clock.monotonic), \
             patch.object(sdr, "log") as mock_log:
            reader._find_candidates_via_egeria(["Q1"], "Git Repository")

        assert mock_log.warning.call_count == 1
        args = mock_log.warning.call_args[0]
        assert "Q1" in args and 6.0 in args

    def test_a_fast_call_logs_nothing(self):
        from resource_explorer.surveyors import survey_definition_reader as sdr

        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid"},
            scoped_elements_by_guid={"q1-guid": []},
        )
        reader = _reader_with_fake_classification_explorer(fake)
        with patch.object(sdr, "log") as mock_log:
            reader._find_candidates_via_egeria(["Q1"], "Git Repository")
        mock_log.warning.assert_not_called()

    def test_the_total_loop_duration_is_logged_when_slow_even_if_no_single_call_is(self):
        """The interesting aggregate case a per-call check alone would miss:
        three calls at 4s each (under the 5s per-call threshold, so none of
        them individually warn) still sum to 12s, over the 10s loop
        threshold — and that total is real information a per-call-only
        check would silently lose."""
        import time as time_module

        from resource_explorer.surveyors import survey_definition_reader as sdr

        clock = _FakeClock()
        fake = _FakeClassificationExplorer(
            guid_by_name={"Q1": "q1-guid", "Q2": "q2-guid", "Q3": "q3-guid"},
        )
        fake.get_scoped_elements = lambda scope_guid, **_kw: (clock.advance(4.0), [])[1]
        reader = _reader_with_fake_classification_explorer(fake)

        with patch.object(time_module, "monotonic", clock.monotonic), \
             patch.object(sdr, "log") as mock_log:
            reader._find_candidates_via_egeria(["Q1", "Q2", "Q3"], "Git Repository")

        # No per-call warning (each call is 4s, under the 5s threshold) — only
        # the loop-total warning (12s, over the 10s threshold) fires.
        assert mock_log.warning.call_count == 1
        args = mock_log.warning.call_args[0]
        assert 3 in args and 12.0 in args


def test_missing_node_for_linked_guid_is_rejected():
    """A link pointing at a guid with no corresponding node (malformed/partial
    graph) should fail loudly, not silently stop the chain early."""
    step1 = _step_element("step-1", "Step::One", additional_properties={"executes_at": "egeria"})
    graph = _graph(
        "proc-dangling", "GovActionProcess::Dangling",
        first_step=step1,
        next_steps=[],  # step "step-2" is linked but never listed as a node
        links=[_link("step-1", "step-2")],
    )

    with pytest.raises(SurveyDefinitionReaderError):
        _reader()._parse_graph(graph)


class _FakeGovernanceOfficerGraph:
    """Fake returning a raw graph (for reconcile_step_links, which reads
    processStepLinks directly rather than going through _parse_graph)."""

    def __init__(self, links):
        self._links = links
        self.get_governance_action_process_graph_calls = 0

    def get_governance_action_process_graph(self, **_kwargs):
        self.get_governance_action_process_graph_calls += 1
        return {"elementGraph": {"processStepLinks": self._links}}


class _FakeMetadataExpert:
    """delete_related_elements() is called directly again (pyegeria's
    ISSUE-63 fixed upstream — the method now routes through
    DeleteRelationshipRequestBody, which does declare deleteMethod, instead
    of the old OpenMetadataDeleteRequestBody that silently dropped it)."""

    def __init__(self):
        self.deleted_guids = []
        self.delete_bodies = []

    def delete_related_elements(self, relationship_guid, body=None):
        self.deleted_guids.append(relationship_guid)
        self.delete_bodies.append(body)


def _unique_name_link(prev_qn, next_qn, link_guid):
    return {
        "previousProcessStep": {"uniqueName": prev_qn},
        "nextProcessStep": {"uniqueName": next_qn},
        "nextProcessStepLinkGUID": link_guid,
    }


class TestReconcileStepLinks:
    """D1 follow-up (docs/survey-question-context-plan.md) — the live
    incident where Dr.Egeria's non-idempotent Link Next Process Step
    duplicated every edge on re-run, plus one genuinely stale edge."""

    def _reader_with_fakes(self, links):
        from resource_explorer.surveyors import survey_definition_reader as sdr

        reader = _reader()
        gov = _FakeGovernanceOfficerGraph(links)
        reader._governance_officer = gov
        meta = _FakeMetadataExpert()
        reader._metadata_expert = meta
        sdr._fetch_cache.clear()
        return reader, gov, meta

    def test_clean_chain_deletes_nothing(self):
        links = [
            _unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1"),
        ]
        reader, gov, meta = self._reader_with_fakes(links)
        result = reader.reconcile_step_links("proc-guid", "X", ["a", "b"])
        assert result.kept == 1
        assert result.removed_total == 0
        assert meta.deleted_guids == []

    def test_duplicate_edge_deletes_the_extra_one(self):
        links = [
            _unique_name_link("GovActionProcessStep::RepoCoarseScout::repo_health", "GovActionProcessStep::RepoCoarseScout::repo_language", "l1"),
            _unique_name_link("GovActionProcessStep::RepoCoarseScout::repo_health", "GovActionProcessStep::RepoCoarseScout::repo_language", "l2"),
        ]
        reader, gov, meta = self._reader_with_fakes(links)
        result = reader.reconcile_step_links("proc-guid", "RepoCoarseScout", ["repo_health", "repo_language"])
        assert result.removed_duplicate == 1
        assert meta.deleted_guids == ["l2"]

    def test_dry_run_reports_but_does_not_delete(self):
        links = [
            _unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1"),
            _unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l2"),
        ]
        reader, gov, meta = self._reader_with_fakes(links)
        result = reader.reconcile_step_links("proc-guid", "X", ["a", "b"], dry_run=True)
        assert result.removed_duplicate == 1
        assert meta.deleted_guids == []

    def test_busts_fetch_cache_after_deleting(self):
        from resource_explorer.surveyors import survey_definition_reader as sdr

        links = [
            _unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1"),
            _unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l2"),
        ]
        reader, gov, meta = self._reader_with_fakes(links)
        sdr._fetch_cache["proc-guid"] = (0.0, "stale-cached-survey-def")
        reader.reconcile_step_links("proc-guid", "X", ["a", "b"])
        assert "proc-guid" not in sdr._fetch_cache

    def test_no_changes_does_not_touch_fetch_cache(self):
        from resource_explorer.surveyors import survey_definition_reader as sdr

        links = [_unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1")]
        reader, gov, meta = self._reader_with_fakes(links)
        sdr._fetch_cache["proc-guid"] = (1e18, "still-fresh")  # far-future timestamp, not stale
        reader.reconcile_step_links("proc-guid", "X", ["a", "b"])
        assert sdr._fetch_cache.get("proc-guid") == (1e18, "still-fresh")

    def test_fetch_error_returns_error_result_not_raises(self):
        reader = _reader()

        class _Raising:
            def get_governance_action_process_graph(self, **_kwargs):
                raise RuntimeError("boom")

        reader._governance_officer = _Raising()
        result = reader.reconcile_step_links("proc-guid", "X", ["a", "b"])
        assert result.error
        assert "boom" in result.error

    def test_idempotent_second_call_is_a_no_op(self):
        links = [_unique_name_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1")]
        reader, gov, meta = self._reader_with_fakes(links)
        reader.reconcile_step_links("proc-guid", "X", ["a", "b"])
        reader.reconcile_step_links("proc-guid", "X", ["a", "b"])
        assert meta.deleted_guids == []



class TestFindCandidatesFromDocuments:
    """The local path: which definitions answer these questions, read from the
    authored documents' `Link Element To Scope` blocks — the same fact the
    Egeria walk read one question at a time, 45 round trips deep.

    A fake documented_definitions() stands in for the docs directory, and the
    only Egeria call left — resolving a matched definition's GUID — is
    stubbed, so these assert the selection logic and nothing else.
    """

    @pytest.fixture(autouse=True)
    def docs(self, monkeypatch):
        from resource_explorer.surveyors.survey_definition_docs import DefinitionDoc

        def doc(name, tech, kind, scoped):
            d = DefinitionDoc()
            d.process = name
            d.display_name = f"{name} Survey"
            d.technology_type = tech
            d.survey_kind = kind
            d.scoped_by = list(scoped)
            return d

        fake = {
            "A": doc("A", "Git Repository", "scouting", ["Q1", "Q2"]),
            "B": doc("B", "Git Repository", "discovery", ["Q2"]),
            "C": doc("C", "PostgreSQL Database", "scouting", ["Q1"]),
            "U": doc("U", "Git Repository", "scouting", ["Q9"]),   # unpublished
        }
        monkeypatch.setattr(
            "resource_explorer.surveyors.survey_definition_docs.documented_definitions", lambda: fake)
        sdr.clear_caches()
        yield fake
        sdr.clear_caches()

    def _reader(self, monkeypatch, published=("A", "B", "C")):
        reader = sdr.SurveyDefinitionReader()
        monkeypatch.setattr(
            reader, "find_process_guid_by_name",
            lambda qn: f"guid-{qn.split('::')[-1]}" if qn.split("::")[-1] in published else None)
        return reader

    def test_matches_by_scoped_question_and_technology_type(self, monkeypatch):
        reader = self._reader(monkeypatch)
        out = reader.find_candidate_process_guids_by_questions(["Q1"], "Git Repository")
        assert [c["qualified_name"] for c in out] == ["GovActionProcess::A"]   # not C: wrong tech
        assert out[0]["guid"] == "guid-A"
        assert out[0]["matched_questions"] == ["Q1"]

    def test_matched_questions_accumulate_in_asked_order(self, monkeypatch):
        reader = self._reader(monkeypatch)
        out = reader.find_candidate_process_guids_by_questions(["Q2", "Q1"], "Git Repository")
        by = {c["qualified_name"]: c["matched_questions"] for c in out}
        assert by["GovActionProcess::A"] == ["Q2", "Q1"]
        assert by["GovActionProcess::B"] == ["Q2"]

    def test_survey_kind_filter(self, monkeypatch):
        reader = self._reader(monkeypatch)
        out = reader.find_candidate_process_guids_by_questions(["Q2"], "Git Repository", survey_kind="discovery")
        assert [c["qualified_name"] for c in out] == ["GovActionProcess::B"]

    def test_unpublished_definition_is_not_offered(self, monkeypatch):
        """Documented but with no GUID in Egeria: it cannot run, so it is not
        a candidate. The document cannot know this; the one Egeria call left
        is exactly for it."""
        reader = self._reader(monkeypatch)
        assert reader.find_candidate_process_guids_by_questions(["Q9"], "Git Repository") == []

    def test_no_egeria_walk_is_made(self, monkeypatch):
        """The point of the change. get_scoped_elements must not be called."""
        reader = self._reader(monkeypatch)
        called = []
        monkeypatch.setattr(reader, "_find_candidates_via_egeria",
                            lambda *a, **k: called.append(a) or [])
        reader.find_candidate_process_guids_by_questions(["Q1"], "Git Repository")
        assert called == []

    def test_falls_back_to_egeria_when_no_documents(self, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.surveyors.survey_definition_docs.documented_definitions", lambda: {})
        reader = self._reader(monkeypatch)
        monkeypatch.setattr(reader, "_find_candidates_via_egeria",
                            lambda qs, tech, survey_kind=None: [{"qualified_name": "via-egeria"}])
        out = reader.find_candidate_process_guids_by_questions(["Q1"], "Git Repository")
        assert out == [{"qualified_name": "via-egeria"}]

    def test_guid_lookup_is_cached_including_not_published(self, monkeypatch):
        reader = self._reader(monkeypatch)
        calls = []
        real = reader.find_process_guid_by_name
        monkeypatch.setattr(reader, "find_process_guid_by_name", lambda qn: calls.append(qn) or real(qn))
        reader.find_candidate_process_guids_by_questions(["Q9"], "Git Repository")
        sdr._candidates_cache.clear()          # force the selection to run again
        reader.find_candidate_process_guids_by_questions(["Q9"], "Git Repository")
        assert calls == ["GovActionProcess::U"], "the None answer must be cached too"

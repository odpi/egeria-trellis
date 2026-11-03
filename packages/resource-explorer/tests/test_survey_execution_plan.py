"""The plan is where a Survey Definition stops being a list and becomes a graph.

RE should not be a workflow engine. `survey_definition_executor` walked
`survey_def.steps` in its own `while` loop and dispatched single steps to
Prefect — RE as sequencer, Prefect as task runner, the inversion of what it
should be. The thing that forced it: `SurveyDefinition.steps` is a flat list
produced by walking a single chain, which is also why the reader raises on
branching, while the real graph sat unused beside it in `.links`.

This module builds the graph. It evaluates nothing: a guard is recorded and
carried, never decided, because deciding it here would rebuild the engine in
the file whose purpose is to stop RE from being one.
"""
from dataclasses import dataclass, field

import pytest

from resource_explorer.surveyors.survey_execution_plan import (
    CyclicPlanError,
    MissingPrerequisiteError,
    PrerequisiteTierError,
    build_plan,
    serialise,
)


@dataclass
class FakeStep:
    guid: str
    re_analysis_step: str
    qualified_name: str = ""
    executes_at: str = "resource-explorer"


@dataclass
class FakeLink:
    previous_guid: str
    next_guid: str
    guard: str = "Any"


@dataclass
class FakeDefinition:
    qualified_name: str = "GovActionProcess::T"
    steps: list = field(default_factory=list)
    links: list = field(default_factory=list)


def _linear(n: int) -> FakeDefinition:
    steps = [FakeStep(guid=f"g{i}", re_analysis_step=f"s{i}") for i in range(n)]
    links = [FakeLink(f"g{i}", f"g{i+1}") for i in range(n - 1)]
    return FakeDefinition(steps=steps, links=links)


def test_a_linear_definition_plans_in_the_order_it_was_authored():
    """The property that makes this a no-op on every definition that exists:
    ties in the topological sort break on declaration order."""
    plan = build_plan(_linear(4))
    assert [s.step_key for s in plan.steps] == ["s0", "s1", "s2", "s3"]
    assert plan.entry_points == ["s0"]
    assert plan.branches is False


def test_dependencies_come_from_links_not_from_list_order():
    """A definition whose links disagree with its list order must follow the
    links — they are the declaration; the list order is an artefact."""
    steps = [FakeStep(guid="a", re_analysis_step="a"),
             FakeStep(guid="b", re_analysis_step="b"),
             FakeStep(guid="c", re_analysis_step="c")]
    # authored c -> a -> b, listed a, b, c
    links = [FakeLink("c", "a"), FakeLink("a", "b")]
    plan = build_plan(FakeDefinition(steps=steps, links=links))
    assert [s.step_key for s in plan.steps] == ["c", "a", "b"]
    assert plan.by_key["a"].depends_on == ["c"]


def test_a_branch_is_planned_rather_than_refused():
    """The reader raises on this shape. The plan represents it."""
    steps = [FakeStep(guid=g, re_analysis_step=g) for g in ("triage", "deep", "quick")]
    links = [FakeLink("triage", "deep", "needs_deep"),
             FakeLink("triage", "quick", "good_enough")]
    plan = build_plan(FakeDefinition(steps=steps, links=links))
    assert plan.branches is True
    assert plan.by_key["deep"].guarded_by == {"triage": "needs_deep"}
    assert plan.by_key["quick"].guarded_by == {"triage": "good_enough"}
    assert plan.by_key["deep"].conditional and not plan.by_key["triage"].conditional


def test_an_unconditional_edge_is_not_recorded_as_a_guard():
    """`Any` means "always follow" — recording it as a condition would make
    every ordinary step look conditional and skippable."""
    plan = build_plan(_linear(2))
    assert plan.by_key["s1"].guarded_by == {}
    assert plan.by_key["s1"].depends_on == ["s0"]


def test_a_join_waits_for_both_sides():
    steps = [FakeStep(guid=g, re_analysis_step=g) for g in ("a", "b", "c", "join")]
    links = [FakeLink("a", "b"), FakeLink("a", "c"),
             FakeLink("b", "join"), FakeLink("c", "join")]
    plan = build_plan(FakeDefinition(steps=steps, links=links))
    assert sorted(plan.by_key["join"].depends_on) == ["b", "c"]
    order = [s.step_key for s in plan.steps]
    assert order.index("join") > max(order.index("b"), order.index("c"))


def test_a_cycle_raises_rather_than_planning_part_of_the_survey():
    """A partial plan would run some steps and report on the whole definition."""
    steps = [FakeStep(guid=g, re_analysis_step=g) for g in ("a", "b")]
    links = [FakeLink("a", "b"), FakeLink("b", "a")]
    with pytest.raises(CyclicPlanError):
        build_plan(FakeDefinition(steps=steps, links=links))


def test_a_definition_with_no_links_falls_back_to_list_order():
    """Older definitions, or ones whose links were lost. An inference, and the
    module says so in a log rather than presenting it as a declaration."""
    steps = [FakeStep(guid=f"g{i}", re_analysis_step=f"s{i}") for i in range(3)]
    plan = build_plan(FakeDefinition(steps=steps, links=[]))
    assert [s.step_key for s in plan.steps] == ["s0", "s1", "s2"]
    assert plan.by_key["s1"].depends_on == ["s0"]


def test_a_link_naming_a_step_outside_the_definition_is_ignored_not_fatal():
    steps = [FakeStep(guid="a", re_analysis_step="a")]
    plan = build_plan(FakeDefinition(steps=steps, links=[FakeLink("a", "elsewhere")]))
    assert [s.step_key for s in plan.steps] == ["a"]


def test_an_empty_definition_plans_to_nothing():
    plan = build_plan(FakeDefinition(steps=[], links=[]))
    assert plan.steps == [] and plan.entry_points == []


def test_serialise_carries_exactly_what_the_flow_needs():
    steps = [FakeStep(guid=g, re_analysis_step=g, qualified_name=f"QN::{g}")
             for g in ("triage", "deep")]
    links = [FakeLink("triage", "deep", "needs_deep")]
    rows = serialise(build_plan(FakeDefinition(steps=steps, links=links)))
    assert rows[0]["step_key"] == "triage" and rows[0]["depends_on"] == []
    assert rows[1] == {"step_key": "deep", "qualified_name": "QN::deep",
                       "executes_at": "resource-explorer",
                       "depends_on": ["triage"],
                       "guarded_by": {"triage": "needs_deep"},
                       "satisfied_by_stored": {},
                       "added_because": "", "demanded_by": ""}


# ── PRODUCES edges (design §17.1's Prefect-side gap) ────────────────────────
#
# Same technique test_prerequisite_resolver.py uses: a constructed registry
# and a monkeypatched `step_produces._registries`/`step_preconditions.
# PRECONDITIONS`, so these pin the ALGORITHM in `_add_produces_edges` rather
# than any real step's declared cost or table.


def _precondition(name, table, monkeypatch):
    from resource_explorer.surveyors import step_preconditions as sp

    monkeypatch.setitem(sp.PRECONDITIONS, name, sp.Precondition(
        sp._needs_rows(table, name), table=table))


@pytest.fixture
def produces_world(monkeypatch):
    """`consumer` needs `producer`'s table. Both api/low, so nothing here
    should ever cross a tier by default — each test that wants a tier
    mismatch changes exactly one cost."""
    from resource_explorer.surveyors import step_produces
    from resource_explorer.surveyors.repo_survey_definition_adapter import StepInfo

    registry = {
        "consumer": StepInfo("consumer", None, "", [], fetch_cost="api", compute_cost="low",
                             requires_context={"needs_thing": "consumer reads producer's rows"}),
        "producer": StepInfo("producer", None, "", [], fetch_cost="api", compute_cost="low",
                             produces=("thing_table",)),
    }
    _precondition("needs_thing", "thing_table", monkeypatch)
    monkeypatch.setattr(step_produces, "_registries", lambda: {"repo": registry})
    return registry


def test_a_producer_already_scheduled_earlier_gets_no_new_edge(produces_world):
    """The authored graph already runs `producer` before `consumer` — nothing
    for PRODUCES-folding to add."""
    steps = [FakeStep(guid="producer", re_analysis_step="producer"),
             FakeStep(guid="consumer", re_analysis_step="consumer")]
    links = [FakeLink("producer", "consumer")]
    plan = build_plan(FakeDefinition(steps=steps, links=links),
                      step_registry=produces_world)
    assert plan.by_key["consumer"].depends_on == ["producer"]
    assert [s.step_key for s in plan.steps] == ["producer", "consumer"]


def _no_op_steps_and_link():
    """Two steps with nothing to do with `producer`/`consumer`, linked to each
    other, present only so `links` is non-empty. `build_plan` infers a chain
    from list order ONLY when a definition declares NO links at all — an
    empty `links` list on a 2+-step definition would silently create its own
    producer->consumer edge via that fallback, making a test pass for the
    wrong reason. A harmless real link elsewhere keeps the "genuinely no
    authored edge between these two" case honest."""
    return ([FakeStep(guid="noop_a", re_analysis_step="noop_a"),
             FakeStep(guid="noop_b", re_analysis_step="noop_b")],
            FakeLink("noop_a", "noop_b"))


def test_a_missing_producer_edge_is_added(produces_world):
    """No authored link between the two at all — PRODUCES-folding must add
    one, so Prefect schedules `producer` first."""
    noop_steps, noop_link = _no_op_steps_and_link()
    steps = [FakeStep(guid="producer", re_analysis_step="producer"),
             FakeStep(guid="consumer", re_analysis_step="consumer"), *noop_steps]
    plan = build_plan(FakeDefinition(steps=steps, links=[noop_link]),
                      step_registry=produces_world)
    assert plan.by_key["consumer"].depends_on == ["producer"]
    assert plan.by_key["producer"].depends_on == []
    order = [s.step_key for s in plan.steps]
    assert order.index("producer") < order.index("consumer")
    # Not a guard — an unconditional dependency, same as an authored `Any` edge.
    assert plan.by_key["consumer"].guarded_by == {}


def test_a_producer_outside_the_definition_is_a_build_time_error(produces_world):
    """`producer` is never one of this definition's own steps. Prefect cannot
    schedule a task for a step it was not authored with — this cannot be
    silently skipped the way the local resolver's dead-end can, because
    nothing here re-checks stored data at runtime."""
    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    with pytest.raises(MissingPrerequisiteError, match="producer"):
        build_plan(FakeDefinition(steps=steps, links=[]),
                  step_registry=produces_world)


def test_a_cycle_between_produces_edges_raises_the_existing_cycle_error(
    produces_world, monkeypatch,
):
    """`producer` itself needs `consumer`'s table — a cycle PRODUCES-folding
    creates rather than one authored directly. Reuses `CyclicPlanError`
    rather than inventing a second cycle error, since the failure mode (an
    unorderable graph) is identical."""
    from resource_explorer.surveyors import step_preconditions as sp
    from resource_explorer.surveyors.repo_survey_definition_adapter import StepInfo

    produces_world["producer"] = StepInfo(
        "producer", None, "", [], fetch_cost="api", compute_cost="low",
        produces=("thing_table",),
        requires_context={"needs_other": "producer reads consumer's rows"})
    produces_world["consumer"] = StepInfo(
        "consumer", None, "", [], fetch_cost="api", compute_cost="low",
        produces=("other_table",),
        requires_context={"needs_thing": "consumer reads producer's rows"})
    _precondition("needs_other", "other_table", monkeypatch)

    noop_steps, noop_link = _no_op_steps_and_link()
    steps = [FakeStep(guid="producer", re_analysis_step="producer"),
             FakeStep(guid="consumer", re_analysis_step="consumer"), *noop_steps]
    with pytest.raises(CyclicPlanError):
        build_plan(FakeDefinition(steps=steps, links=[noop_link]),
                  step_registry=produces_world)


def test_a_producer_above_the_definitions_tier_is_a_build_time_error(produces_world):
    """`producer` is raised to `download`, above `consumer`'s `api` tier. On
    the local path this becomes a proposal the user confirms; a Prefect plan
    has no such surface at build time, so this is the judgement call this
    change makes: fail loudly, naming the mismatch, rather than silently
    adding a tier the caller never budgeted for."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import StepInfo

    produces_world["producer"] = StepInfo(
        "producer", None, "", [], fetch_cost="download", compute_cost="low",
        produces=("thing_table",))
    noop_steps, noop_link = _no_op_steps_and_link()
    steps = [FakeStep(guid="producer", re_analysis_step="producer"),
             FakeStep(guid="consumer", re_analysis_step="consumer"), *noop_steps]
    with pytest.raises(PrerequisiteTierError, match="download"):
        build_plan(FakeDefinition(steps=steps, links=[noop_link]),
                  step_registry=produces_world)


def test_no_step_registry_keeps_the_old_behaviour(produces_world):
    """Omitting `step_registry` (every call site before this change) must not
    start folding PRODUCES edges in — the whole point of the parameter being
    optional. Authored here in the "wrong" order from PRODUCES' point of view
    (`consumer` before `producer`) precisely so a silent reorder would be
    caught: with no registry passed, `build_plan` has no way to know
    `consumer` needs `producer`'s table at all, so the authored order must
    survive untouched."""
    steps = [FakeStep(guid="producer", re_analysis_step="producer"),
             FakeStep(guid="consumer", re_analysis_step="consumer")]
    links = [FakeLink("consumer", "producer")]
    plan = build_plan(FakeDefinition(steps=steps, links=links))
    assert plan.by_key["producer"].depends_on == ["consumer"]
    assert [s.step_key for s in plan.steps] == ["consumer", "producer"]


# ── §E: a fresh stored answer satisfies the prerequisite, even on Prefect ──
#
# BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §E: Scouting had already run 20 minutes
# earlier and left a schema inventory in the registry; the runtime resolver
# (`_any_step_needs_prerequisites`) correctly saw that and routed the run to
# Prefect, but `build_plan`'s purely structural check knew nothing about
# stored data and raised `MissingPrerequisiteError` anyway because the
# producer step (Scouting's own) isn't part of the Analysis definition. These
# pin the fix: passing `registry`/`entity` lets the same freshness check the
# resolver already trusts short-circuit the structural requirement.


def _real_registry_with_thing_table(tmp_path, *, rows=True, producer_ran_minutes_ago=None):
    """A real sqlite registry, plus a `thing_table` the fixture precondition
    counts. `producer_ran_minutes_ago` writes the `step_runs` row that proves
    the producer ran (None writes none)."""
    from datetime import datetime, timedelta, timezone

    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry(db_path=str(tmp_path / "plan.db"))
    with registry._conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS thing_table (project_slug TEXT)")
        if rows:
            conn.execute("INSERT INTO thing_table (project_slug) VALUES ('db1')")
    if producer_ran_minutes_ago is not None:
        when = datetime.now(timezone.utc) - timedelta(minutes=producer_ran_minutes_ago)
        registry.record_step_run("db1", "producer", when.isoformat())
    return registry


def test_a_fresh_stored_answer_needs_no_producer_edge_even_outside_the_definition(
    produces_world, tmp_path,
):
    """`producer` is not authored into this definition at all, but the entity
    already has rows AND a step_runs row proving `producer` ran 20 minutes
    ago. That satisfies the precondition without any edge or added step."""
    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    entity = SimpleFakeEntity(slug="db1")
    registry = _real_registry_with_thing_table(tmp_path, producer_ran_minutes_ago=20)
    plan = build_plan(FakeDefinition(steps=steps, links=[]),
                      step_registry=produces_world,
                      registry=registry, entity=entity)
    consumer = plan.by_key["consumer"]
    assert consumer.depends_on == [], "a fresh stored answer must not add an edge"
    assert [s.step_key for s in plan.steps] == ["consumer"], "nothing may be added"
    assert consumer.satisfied_by_stored.get("needs_thing"), (
        "provenance for the satisfied-by-stored precondition was not recorded"
    )


def test_a_stale_stored_answer_adds_the_producer(produces_world, tmp_path):
    """Rows exist but the producer last ran far outside the window: not
    satisfied, so the planner adds the producer (it is within budget)."""
    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    entity = SimpleFakeEntity(slug="db1")
    registry = _real_registry_with_thing_table(tmp_path, producer_ran_minutes_ago=60 * 500)
    plan = build_plan(FakeDefinition(steps=steps, links=[]),
                      step_registry=produces_world, registry=registry, entity=entity)
    added = plan.by_key["producer"]
    assert plan.by_key["consumer"].depends_on == ["producer"]
    assert added.added_because == "added producer: needed for needs_thing; no fresh result"
    assert added.demanded_by == "consumer"
    assert [s.step_key for s in plan.steps] == ["producer", "consumer"]
    assert serialise(plan)[0]["added_because"] == added.added_because


def test_no_stored_answer_at_all_adds_the_producer(produces_world, tmp_path):
    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    entity = SimpleFakeEntity(slug="db1")
    registry = _real_registry_with_thing_table(tmp_path, rows=False)
    plan = build_plan(FakeDefinition(steps=steps, links=[]),
                      step_registry=produces_world, registry=registry, entity=entity)
    assert [s.step_key for s in plan.steps] == ["producer", "consumer"]
    assert plan.by_key["producer"].executes_at == "resource-explorer"
    assert plan.by_key["consumer"].added_because == ""


def test_an_added_producer_over_the_definitions_budget_still_raises(
    produces_world, tmp_path,
):
    from resource_explorer.surveyors.repo_survey_definition_adapter import StepInfo

    produces_world["producer"] = StepInfo(
        "producer", None, "", [], fetch_cost="download", compute_cost="low",
        produces=("thing_table",))
    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    registry = _real_registry_with_thing_table(tmp_path, rows=False)
    with pytest.raises(MissingPrerequisiteError, match="beyond this definition's budget") as exc:
        build_plan(FakeDefinition(steps=steps, links=[]),
                   step_registry=produces_world, registry=registry,
                   entity=SimpleFakeEntity(slug="db1"))
    assert exc.value.producer_key == "producer"
    assert exc.value.precondition_name == "needs_thing"


def test_omitting_registry_and_entity_keeps_the_old_structural_only_behaviour(
    produces_world,
):
    """Every call site that does not (yet) pass registry/entity must see
    exactly the old error — no freshness check silently applied."""
    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    with pytest.raises(MissingPrerequisiteError, match="producer"):
        build_plan(FakeDefinition(steps=steps, links=[]),
                  step_registry=produces_world)


class SimpleFakeEntity:
    def __init__(self, slug):
        self.slug = slug


def test_serialise_carries_satisfied_by_stored():
    steps = [FakeStep(guid="a", re_analysis_step="a")]
    plan = build_plan(FakeDefinition(steps=steps, links=[]))
    plan.steps[0].satisfied_by_stored["has_schema_inventory"] = "2026-09-27T20:59:29+00:00"
    rows = serialise(plan)
    assert rows[0]["satisfied_by_stored"] == {
        "has_schema_inventory": "2026-09-27T20:59:29+00:00"}


# ── §E item 4: `requires` alongside `produces`, and the authoring-time lint ──


def test_step_info_requires_derives_from_requires_context(produces_world):
    consumer = produces_world["consumer"]
    assert consumer.requires == ("producer",), (
        "StepInfo.requires must derive the producer from requires_context, "
        "not require a second hand-typed declaration"
    )
    assert produces_world["producer"].requires == ()


def test_authoring_gaps_names_a_precondition_whose_producer_is_missing(
    produces_world,
):
    from resource_explorer.surveyors.survey_execution_plan import authoring_gaps

    steps = [FakeStep(guid="consumer", re_analysis_step="consumer")]
    gaps = authoring_gaps(FakeDefinition(steps=steps, links=[]), produces_world)
    assert gaps == [{"step_key": "consumer", "requires": "producer",
                     "in_definition": False}]


def test_authoring_gaps_is_empty_when_the_producer_is_also_authored(
    produces_world,
):
    from resource_explorer.surveyors.survey_execution_plan import authoring_gaps

    steps = [FakeStep(guid="producer", re_analysis_step="producer"),
             FakeStep(guid="consumer", re_analysis_step="consumer")]
    links = [FakeLink("producer", "consumer")]
    assert authoring_gaps(FakeDefinition(steps=steps, links=links), produces_world) == []


def test_every_live_definition_plans_to_its_existing_order():
    """The regression guarantee, against the real documents rather than
    fixtures: if the plan reordered anything, this change would alter what
    runs today."""
    from resource_explorer.surveyors.survey_definition_docs import documented_definitions

    for name, doc in documented_definitions().items():
        steps = [FakeStep(guid=k, re_analysis_step=k) for k in doc.steps]
        links = [FakeLink(p, n, g) for p, n, g in doc.links]
        plan = build_plan(FakeDefinition(qualified_name=name, steps=steps, links=links))
        assert [s.step_key for s in plan.steps] == doc.steps, name
        assert plan.branches is False, name


# ── The dataops_testgen scenario, with the REAL step registry (2026-10-14) ──
#
# Scouting ran `repo_manifest_parse` at 21:18:31 and `project_dependencies`
# held 126 versioned rows by 21:18:52; at 21:31:52 the Assessment survey still
# refused with "no fresh stored result ... found", because the freshness query
# asked `project_dependencies` for a `surveyed_at` column it does not have.


def _repo_registry(tmp_path):
    from resource_explorer.registry import Project, ProjectRegistry

    registry = ProjectRegistry(db_path=str(tmp_path / "tg.db"))
    registry.add(Project(slug="tg", display_name="tg",
                         github_url="https://github.com/x/tg"))
    return registry


def _store_versioned_dependencies(registry, n=3):
    with registry._conn() as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO project_dependencies (project_slug, dep_name, "
                "dep_version, indexed_at) VALUES ('tg', ?, '1.0', '2026-10-14T21:18:40')",
                (f"dep{i}",))


def _assessment(*extra):
    keys = ("repo_cve_scan", *extra)
    return FakeDefinition(
        qualified_name="GovActionProcess::Assessment",
        steps=[FakeStep(guid=k, re_analysis_step=k, qualified_name=k) for k in keys],
        links=[FakeLink(a, b) for a, b in zip(keys, keys[1:])])


def _plan_for(registry, definition):
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    return build_plan(definition, step_registry=STEP_REGISTRY, registry=registry,
                      entity=SimpleFakeEntity(slug="tg"))


def test_testgen_manifest_parse_ten_minutes_ago_satisfies_the_assessment(tmp_path):
    from datetime import datetime, timedelta, timezone

    registry = _repo_registry(tmp_path)
    _store_versioned_dependencies(registry)
    registry.record_step_run(
        "tg", "repo_manifest_parse",
        (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
    plan = _plan_for(registry, _assessment("repo_arch_detect"))
    assert [s.step_key for s in plan.steps] == ["repo_cve_scan", "repo_arch_detect"]
    assert plan.by_key["repo_cve_scan"].satisfied_by_stored.get("has_versioned_dependencies")
    assert not any(s.added_because for s in plan.steps)


def test_testgen_with_no_rows_adds_manifest_parse(tmp_path):
    registry = _repo_registry(tmp_path)
    plan = _plan_for(registry, _assessment("repo_arch_detect"))
    keys = [s.step_key for s in plan.steps]
    assert keys.index("repo_manifest_parse") < keys.index("repo_cve_scan")
    added = plan.by_key["repo_manifest_parse"]
    assert added.added_because == (
        "added repo_manifest_parse: needed for has_versioned_dependencies; "
        "no fresh result")
    assert added.demanded_by == "repo_cve_scan"


def test_testgen_over_budget_manifest_parse_still_raises(tmp_path):
    """A definition authored at the api tier has not budgeted a download."""
    registry = _repo_registry(tmp_path)
    with pytest.raises(MissingPrerequisiteError) as exc:
        _plan_for(registry, _assessment())
    assert exc.value.producer_key == "repo_manifest_parse"
    assert "budget" in str(exc.value)


# ── repo_api_structure declares has_code_symbols (2026-10-15) ───────────────

def _definition(*keys):
    return FakeDefinition(
        qualified_name="GovActionProcess::T",
        steps=[FakeStep(guid=k, re_analysis_step=k, qualified_name=k) for k in keys],
        links=[FakeLink(a, b) for a, b in zip(keys, keys[1:])])


def _store_code_symbols(registry, extracted_minutes_ago=10):
    from datetime import datetime, timedelta, timezone

    with registry._conn() as conn:
        conn.execute(
            "INSERT INTO project_code_symbols (project_slug, file_path, language, kind, name, "
            "qualified_name) VALUES ('tg', 'a.py', 'python', 'function', 'f', 'a.f')")
    registry.record_step_run(
        "tg", "repo_symbol_extraction",
        (datetime.now(timezone.utc) - timedelta(minutes=extracted_minutes_ago)).isoformat())


def test_api_structure_declares_the_symbols_it_reads_and_stays_at_its_own_tier():
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    info = STEP_REGISTRY["repo_api_structure"]
    assert "has_code_symbols" in info.requires_context
    assert info.requires == ("repo_symbol_extraction",)
    assert (info.fetch_cost, info.compute_cost) == ("none", "low")      # its own declared tier, unchanged


def test_a_definition_with_both_steps_in_order_plans_unchanged(tmp_path):
    registry = _repo_registry(tmp_path)
    definition = _definition("repo_file_inventory", "repo_symbol_extraction", "repo_api_structure")
    for kwargs in ({}, {"registry": registry, "entity": SimpleFakeEntity(slug="tg")}):
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        plan = build_plan(definition, step_registry=STEP_REGISTRY, **kwargs)
        assert [s.step_key for s in plan.steps] == [
            "repo_file_inventory", "repo_symbol_extraction", "repo_api_structure"]
        assert not any(s.added_because for s in plan.steps)
        assert plan.by_key["repo_api_structure"].depends_on == ["repo_symbol_extraction"] or \
            "repo_symbol_extraction" in plan.by_key["repo_api_structure"].depends_on


def test_no_fresh_symbols_adds_extraction_for_a_run_that_already_downloads(tmp_path):
    """A full-scope definition: another of its steps already acquires the zipball at download/medium, so
    the definition has budgeted the download, a medium-compute ceiling covers extraction, and the plan adds it ahead of api_structure."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    assert STEP_REGISTRY["repo_arch_detect"].fetch_cost == "download"
    registry = _repo_registry(tmp_path)
    plan = build_plan(_definition("repo_arch_detect", "repo_api_structure"), step_registry=STEP_REGISTRY,
                      max_compute_cost="medium",     # the full-scope run's ceiling; extraction is medium compute
                      registry=registry, entity=SimpleFakeEntity(slug="tg"))
    keys = [s.step_key for s in plan.steps]
    assert set(keys) == {"repo_arch_detect", "repo_symbol_extraction", "repo_api_structure"}
    assert keys.index("repo_symbol_extraction") < keys.index("repo_api_structure")
    added = plan.by_key["repo_symbol_extraction"]
    assert added.demanded_by == "repo_api_structure" and added.added_because


def test_only_api_structure_never_adds_a_download_even_under_a_download_ceiling(tmp_path):
    """The lone / scoped shape: nothing in the definition acquires a zipball, so the planner refuses
    (the executor then proposes the producing definition) instead of adding a download."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    registry = _repo_registry(tmp_path)
    for ceilings in ({}, {"max_fetch_cost": "download", "max_compute_cost": "medium"}):
        with pytest.raises(MissingPrerequisiteError) as exc:
            build_plan(_definition("repo_api_structure"), step_registry=STEP_REGISTRY,
                       registry=registry, entity=SimpleFakeEntity(slug="tg"), **ceilings)
        assert exc.value.producer_key == "repo_symbol_extraction" and "download" in str(exc.value)


def test_fresh_symbols_mean_extraction_is_not_added(tmp_path):
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    registry = _repo_registry(tmp_path)
    _store_code_symbols(registry)
    plan = build_plan(_definition("repo_arch_detect", "repo_api_structure"), step_registry=STEP_REGISTRY,
                      registry=registry, entity=SimpleFakeEntity(slug="tg"))
    assert [s.step_key for s in plan.steps] == ["repo_arch_detect", "repo_api_structure"]
    assert plan.by_key["repo_api_structure"].satisfied_by_stored.get("has_code_symbols")

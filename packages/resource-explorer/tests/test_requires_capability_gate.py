"""`requires_capability` as one axis beside cost tier, in the same gate.

`REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` §7.1, replying to
`ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` (#251):

    Build it as one axis beside cost tier in the same gate, not as a separate
    flow: a step declares `fetch_cost`, `compute_cost` and
    `requires_capability`, and the launcher shows one combined reason.

What could go wrong, and what each group below pins shut:

  * the axis turns into a SECOND gate — two proposals, two prompts, a reader
    left to reconcile "this costs a download" with "this cannot see 23 of
    your tables". `test_both_axes...` is the one that fails if that happens;
  * it blocks on an absence — a database nobody ever probed reads as a
    database whose credential can do nothing, which is exactly the
    absence-as-answer failure the credential work exists to close, pointing
    the other way;
  * the four values get treated as a ladder, so a `pg_monitor` role with no
    grants "satisfies read";
  * accepting "run it anyway" re-resolves, hits the same shortfall and skips
    the step the user just approved — an accept button that does nothing;
  * it leaks onto repo/filesystem steps, which have no credential model.

The declaration tests at the bottom are deliberately about the AUDIT rather
than about taste: `DATABASE-STEP-CAPABILITY-AUDIT.md` traced every step to
its SQL, and a change to one of these values should have to argue with that
document rather than slip through.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from resource_explorer.surveyors import credential_capability as cc
from resource_explorer.surveyors import prerequisite_resolver as pr
from resource_explorer.surveyors import step_preconditions as sp
from resource_explorer.surveyors import step_produces
from resource_explorer.surveyors.repo_survey_definition_adapter import (
    CAPABILITY_VALUES,
    StepInfo,
)


_ENTITY = SimpleNamespace(slug="db1", display_name="DB One")


class _Reg:
    """A registry stand-in that can answer row counts, step_runs and the one
    survey blob the capability probe is read back from."""

    def __init__(self, counts=None, probe=None, surveys=None):
        self._counts = counts or {}
        self._surveys = surveys if surveys is not None else (
            [{"survey_data": '{"credential_capability": %s}' % _json(probe)}]
            if probe is not None else [])

    def _conn(self):
        counts = self._counts

        class _Conn:
            def execute(self, sql, params):
                import re
                table = re.search(r"FROM (\w+)", sql).group(1)
                return SimpleNamespace(fetchone=lambda: {"n": counts.get(table, 0)})

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Conn()

    def get_database_surveys(self, slug):
        return list(self._surveys)

    def query_step_runs(self, **_):
        return []

    def median_step_wall_ms(self, step_key):
        return None


def _json(obj):
    import json
    return json.dumps(obj)


#: A credential that can see everything: all 26 tables, pg_monitor, INSERT.
FULL = {"connected_as": "surveyor", "schema_total": 8, "schema_visible": 8,
        "table_total": 26, "table_select": 26, "stats_role": True,
        "write_capable": True, "by_schema": {}}

#: The credential from the incident that started this work — `egeria_user` on
#: `coco_pharma`: SELECT on 3 of 26 tables, no pg_monitor, no INSERT.
NARROW = {"connected_as": "egeria_user", "schema_total": 8, "schema_visible": 6,
          "table_total": 26, "table_select": 3, "stats_role": False,
          "write_capable": False, "by_schema": {}}


def _precondition(name, table, monkeypatch):
    monkeypatch.setitem(sp.PRECONDITIONS, name, sp.Precondition(
        sp._needs_rows(table, name), table=table))


@pytest.fixture
def world(monkeypatch):
    """`asker` needs `filler`'s table. Both inside each other's cost tier, so
    the DEFAULT outcome is a silent auto-run and every test that wants a
    proposal has to change exactly one thing — capability, cost, or both."""
    registry = {
        "asker": StepInfo("asker", None, "", [], fetch_cost="api",
                          compute_cost="medium",
                          requires_context={"needs_filled": "asker reads filler's rows"}),
        "filler": StepInfo("filler", None, "", [], fetch_cost="api",
                           compute_cost="low", produces=("filled_table",)),
    }
    _precondition("needs_filled", "filled_table", monkeypatch)
    monkeypatch.setattr(step_produces, "_registries", lambda: {"database": registry})
    return registry


# ── the four predicates, and the ladder that is not one ─────────────────────

def test_catalog_is_satisfied_by_any_credential_that_produced_a_probe():
    """`catalog` is the tier that cannot fail for a connected role — the
    system catalogs are readable regardless of grants, which is the very
    property that gives the probe its denominator. A stored probe IS the
    evidence, because producing one requires catalog reads."""
    assert cc.assess("catalog", NARROW).satisfied is True
    assert cc.assess("catalog", NARROW).known is True


def test_stats_does_not_imply_read_and_read_does_not_imply_stats():
    """The four values are predicates, not rungs. A monitoring role with no
    table grants satisfies `stats` and fails `read`; `egeria_user` is the
    reverse. An ordinal comparison anywhere in the gate gets one of these
    two backwards, silently."""
    monitor_only = dict(NARROW, stats_role=True, table_select=0)
    assert cc.assess("stats", monitor_only).satisfied is True
    assert cc.assess("read", monitor_only).satisfied is False

    reader_only = dict(NARROW, stats_role=False, table_select=26)
    assert cc.assess("read", reader_only).satisfied is True
    assert cc.assess("stats", reader_only).satisfied is False


def test_read_carries_the_fraction_the_probe_already_computed():
    """The gate reports the fraction rather than a bare no — §3: "a step
    requires `read`; the probe says `read` holds on 3 of 26 tables; the gate
    reports the fraction"."""
    a = cc.assess("read", NARROW)
    assert (a.have, a.of) == (3, 26)
    assert "3 of 26" in a.detail
    assert "egeria_user" in a.detail


def test_write_is_probed_and_never_a_ladder_position():
    assert cc.assess("write", NARROW).satisfied is False
    assert cc.assess("write", FULL).satisfied is True
    assert "checked without writing anything" in cc.assess("write", NARROW).detail


def test_a_database_with_no_tables_is_not_a_read_shortfall():
    """0 of 0 has no denominator to state and no grant to ask for. Reporting
    it as a shortfall would raise a proposal about nothing."""
    empty = dict(NARROW, table_total=0, table_select=0)
    assert cc.assess("read", empty).satisfied is True


def test_an_unrecognised_requirement_asks_rather_than_disappearing():
    """A typo in a declaration should surface as a question, not be treated
    as satisfied — the same direction `_exceeds` takes for an unrecognised
    cost tier."""
    a = cc.assess("reed", NARROW)
    assert a.blocks is True
    assert "unrecognised" in a.detail


# ── absence is not a shortfall ──────────────────────────────────────────────

def test_a_resource_that_was_never_probed_does_not_block(world):
    """The load-bearing direction. Gating on an unmeasured probe turns "we
    have not looked" into "you may not ask", for every database registered
    before the probe existed."""
    world["asker"].requires_capability = "read"
    assert cc.assess("read", None).known is False
    assert cc.assess("read", None).blocks is False

    res = pr.resolve(_Reg({"filled_table": 3}), _ENTITY, "asker", world)
    assert res.status == pr.SATISFIED, (
        "an unprobed credential was treated as an incapable one")


def test_undeclared_and_unmeasured_are_distinguishable_not_folded():
    """Two different facts — "this step makes no claim" and "nobody has
    looked yet" — must not render alike, even though neither blocks."""
    undeclared = cc.assess("", NARROW)
    unmeasured = cc.assess("read", None)
    assert undeclared.requirement == "" and unmeasured.requirement == "read"
    assert undeclared.known is False and unmeasured.known is False
    assert (undeclared.requirement, undeclared.known) != (
        unmeasured.requirement, unmeasured.known)


# ── the gate: satisfied, unsatisfied, and both axes at once ─────────────────

def test_a_step_whose_capability_is_satisfied_runs_with_no_proposal(world):
    """The control. `asker` needs `read`, the credential has SELECT on all 26
    tables, its precondition is filled — nothing to ask about at all."""
    world["asker"].requires_capability = "read"
    res = pr.resolve(_Reg({"filled_table": 3}, probe=FULL), _ENTITY, "asker", world)
    assert res.status == pr.SATISFIED
    assert res.proposal is None
    assert res.may_run is True


def test_a_step_whose_capability_is_not_satisfied_proposes_partially(world):
    """Capability alone, no cost crossing and no chain: the proposal names no
    steps to run first, says what the credential can actually reach, and
    offers the demanding step itself as the thing to run anyway."""
    world["asker"].requires_capability = "read"
    res = pr.resolve(_Reg({"filled_table": 3}, probe=NARROW), _ENTITY, "asker", world)

    assert res.status == pr.PROPOSAL
    # …and it does NOT withhold the answer — see the advisory tests below for
    # why this axis warns rather than blocks. The interactive launcher still
    # stops to ask, because it calls `/plan` before dispatching.
    assert res.may_run is True
    assert res.proposal.advisory is True
    assert res.proposal.steps == [], (
        "a capability shortfall is not fixed by running a producer first — "
        "naming one would quote a cost for work that changes nothing")
    assert res.proposal.run_partially == "asker"
    assert res.proposal.capability["have"] == 3
    assert res.proposal.capability["of"] == 26

    sentence = res.proposal.sentence()
    assert "can run, but not completely" in sentence
    assert "3 of 26 relation(s)" in sentence
    assert "egeria_user" in sentence
    assert sentence.endswith("."), (
        "sentence() ends with a full stop, not the decision -- see question()")
    assert "estimated 0s" not in sentence, (
        "a capability-only proposal has no chain, so quoting an estimate "
        "states a cost for work that does not exist")
    # The actionable question moved out of sentence() into its own method
    # (REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §1 defect 3).
    assert res.proposal.question() == "Run it within this credential's scope?"


def test_both_axes_insufficient_produce_ONE_combined_message(world):
    """§7.1's "the launcher shows one combined reason", pinned.

    `filler` is raised to `download`, which `asker`'s `api` tier does not
    cover — a cost crossing. `asker` also needs `read`, which the credential
    does not have — a capability shortfall. Two axes, ONE proposal, ONE
    sentence carrying both clauses.
    """
    world["filler"] = StepInfo("filler", None, "", [], fetch_cost="download",
                               compute_cost="low", produces=("filled_table",))
    world["asker"].requires_capability = "read"

    res = pr.resolve(_Reg(probe=NARROW), _ENTITY, "asker", world)

    assert res.status == pr.PROPOSAL
    proposals = [res.proposal]
    assert len([p for p in proposals if p is not None]) == 1, (
        "two gates produced two asks; §7.1 says one")

    kinds = {r.kind for r in res.proposal.reasons}
    assert kinds == {"tier", "capability"}, kinds
    assert res.proposal.combines_cost_and_capability is True

    sentence = res.proposal.sentence()
    # One sentence, both clauses. Not two sentences, and not one that drops
    # the other axis — a reader told only about the download would accept a
    # run whose answer is bounded by a credential nobody mentioned. Ends with
    # a full stop; the single actionable question is question()'s job.
    assert sentence.endswith("."), sentence
    assert sentence.count(".") == 1, (
        "one sentence, not two run together -- exactly one full stop", sentence)
    assert "download" in sentence, "the cost axis vanished from the combined ask"
    assert "3 of 26 relation(s)" in sentence, "the capability axis vanished"
    # The combined template never otherwise names the demanding step, so
    # `asker`'s own capability reason keeps its step-name prefix here (unlike
    # the capability-only case above) -- REPLY-COPY-REVIEW-CREDENTIAL-AND-
    # FIT-LANGUAGE.md §1 defect 2 only calls out the capability-ONLY
    # template's redundancy, not this one.
    assert "`asker` needs" in sentence
    # And it still offers the capability-shaped accept, because the chain
    # running does not widen the grant.
    assert res.proposal.run_partially == "asker"
    assert res.proposal.steps == ["filler"]
    assert res.proposal.question() == "Run it within this credential's scope?"


def test_a_producer_short_on_capability_is_named_too(world):
    """Auto-running a producer whose credential cannot see what it reads
    would fill the demanding step's input with a bounded answer and then
    present the result as if the input were whole."""
    world["filler"].requires_capability = "stats"
    res = pr.resolve(_Reg(probe=NARROW), _ENTITY, "asker", world)
    assert res.status == pr.PROPOSAL
    assert any(r.kind == "capability" and r.step_key == "filler"
               for r in res.proposal.reasons)
    assert res.proposal.steps == ["filler"]
    # The DEMANDING step declared nothing, so there is nothing to "run
    # partially" — running the chain is the whole of this ask.
    assert res.proposal.run_partially == ""


def test_consent_suppresses_only_the_capability_axis(world):
    """"Run it anyway" must actually run it. Without this the accept path
    re-resolves, raises the same shortfall and skips the step the user just
    approved — an accept button that does nothing.

    And it must suppress ONLY that axis: a cost crossing in the same proposal
    is consented by naming the steps, not by this flag.
    """
    world["asker"].requires_capability = "read"
    reg = _Reg({"filled_table": 3}, probe=NARROW)

    assert pr.resolve(reg, _ENTITY, "asker", world).status == pr.PROPOSAL
    consented = pr.resolve(reg, _ENTITY, "asker", world, capability_consented=True)
    assert consented.status == pr.SATISFIED
    assert consented.may_run is True

    # Cost still gates, consented or not.
    world["filler"] = StepInfo("filler", None, "", [], fetch_cost="download",
                               compute_cost="low", produces=("filled_table",))
    still = pr.resolve(_Reg(probe=NARROW), _ENTITY, "asker", world,
                       capability_consented=True)
    assert still.status == pr.PROPOSAL
    assert {r.kind for r in still.proposal.reasons} == {"tier"}


def test_consent_does_not_erase_the_measured_shortfall():
    """Consent is permission to proceed, never permission to stop mentioning
    it — the run's envelope still has to say the answer was measured within
    the credential's scope."""
    info = StepInfo("s", None, "", [], requires_capability="read")
    reason, assessment = pr._capability_reason("s", info, NARROW, consented=True)
    assert reason is None
    assert assessment.satisfied is False and assessment.have == 3


# ── the RFA path ────────────────────────────────────────────────────────────

def test_the_launcher_rfa_names_the_step_the_probes_rfa_cannot():
    """The launcher's RFA and the probe's are not duplicates. The probe's is
    resource-shaped and standing ("SELECT on 3 of 26 tables"); this one is
    question-shaped — somebody tried to run THIS step and that is what the
    missing grant cost."""
    a = cc.assess("read", NARROW)
    rfa = cc.capability_rfa("coco_pharma", "Coco Pharma",
                            "postgres_column_profile", a)
    assert rfa["entity_slug"] == "coco_pharma"
    assert "postgres_column_profile" in rfa["summary"]
    assert rfa["analysis_name"] == "postgres_column_profile"
    assert "3 of 26" in rfa["detail"]
    assert "register a connection" in rfa["detail"]


def test_no_rfa_is_offered_when_there_is_no_measured_shortfall():
    """Three different reasons not to raise one, none of which is "the
    credential is fine": undeclared, unprobed, and genuinely satisfied. The
    route distinguishes them; `blocks` is what it asks."""
    assert cc.assess("", NARROW).blocks is False      # undeclared
    assert cc.assess("read", None).blocks is False    # unprobed
    assert cc.assess("read", FULL).blocks is False    # satisfied


# ── scope: database-first, and the repo path untouched ──────────────────────

def test_repo_and_filesystem_steps_declare_nothing_and_read_no_probe():
    """Introduced database-first, exactly as `produces`/`requires_context`
    were (the #241 precedent). A repo resolve must not even look for a probe:
    `_Reg` here has no `get_database_surveys` at all, so a lookup would
    raise rather than quietly return nothing."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    declared = {k: v.requires_capability for k, v in STEP_REGISTRY.items()
                if getattr(v, "requires_capability", "")}
    assert declared == {}, (
        f"repo steps declared a capability requirement: {declared}. There is "
        "no credential model for repositories yet, so the gate has nothing to "
        "check it against and would be stating an unverifiable requirement.")

    class _NoDbRegistry:
        def query_step_runs(self, **_):
            return []

        def median_step_wall_ms(self, k):
            return None

    assert pr._declares_capability(STEP_REGISTRY, STEP_REGISTRY.keys()) is False
    res = pr.resolve(_NoDbRegistry(), _ENTITY, "repo_health", STEP_REGISTRY)
    assert res.status in (pr.SATISFIED, pr.AUTO_RUN, pr.PROPOSAL, pr.UNSATISFIABLE)


# ── the declarations themselves, against the audit ──────────────────────────

def test_every_database_step_declares_a_value_from_the_vocabulary():
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )

    for key, info in DATABASE_STEP_REGISTRY.items():
        value = info.requires_capability
        assert value == "" or value in CAPABILITY_VALUES, (
            f"{key} declares requires_capability={value!r}, which is not one of "
            f"{sorted(CAPABILITY_VALUES)} — the four values of the reply's §3")


def test_the_database_declarations_match_the_audit():
    """`DATABASE-STEP-CAPABILITY-AUDIT.md` is the source of truth for these,
    not this session's reading of the SQL. Changing one should mean arguing
    with that document — which is what this assertion makes you do."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )

    # User steps (extensibility brief 2c) are not the audit's: each declares its
    # tier in its own manifest, and the generated catalog entry carries it.
    user_steps = {e.provided_by.get("step") or e.id: e.provided_by.get("credential_tier", "")
                  for e in _load()["database"] if e.provided_by}
    for step, tier in user_steps.items():
        assert DATABASE_STEP_REGISTRY[step].requires_capability == ("" if tier in ("", "none") else "read"), step
    assert {k: v.requires_capability for k, v in DATABASE_STEP_REGISTRY.items()
            if k not in user_steps} == {
        # audit §1 — CORRECTED 2026-09-24/25: `pg_stat_user_tables`/`pg_stat_
        # user_indexes` were live-verified NOT gated by `pg_monitor` (see the
        # audit's "Correction" section). The strongest tier this step's own
        # output still needs is `read` (`information_schema.*` enumeration,
        # `pg_stats` column profile — both genuinely privilege-filtered).
        "postgres_schema_and_stats": "read",
        # audit §2 — CORRECTED 2026-09-24/25: the four-way bundle is now
        # three-and-one, not two-and-two. privilege_audit,
        # db_external_dependencies AND db_activity_signals are catalog
        # (db_activity_signals' pg_stat_user_tables read is unfiltered, same
        # correction as above); only db_resilience is stats, and only
        # because it reads pg_stat_replication (genuinely pg_monitor-gated).
        "postgres_operations": "stats",
        # audit §4 — opens no connection at all; not even `catalog` applies.
        "db_derived": "",
        # audit §5 — "the floor, not a choice": a literal SELECT on real
        # tables.
        "postgres_column_profile": "read",
        # audit §6 — same sampling machinery, same floor.
        "postgres_nested_columns": "read",
        # audit §7 — read by declared purpose (information_schema.views).
        # Its code path reaches stats-tier views, which the audit flags as a
        # step bug to fix, not a requirement to encode.
        "sql_analysis": "read",
        # audit §3 — catalog, live-verified against coco_pharma.
        "credential_capability": "catalog",
    }


def test_the_probe_step_itself_can_never_be_gated_out(world):
    """The step every capability answer comes from must not need a capability
    nobody has. If `credential_capability` declared anything stronger than
    `catalog`, a credential too narrow to run it would be gated out of the
    one probe that could have said so — a deadlock with no error."""
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )

    info = DATABASE_STEP_REGISTRY["credential_capability"]
    assert info.requires_capability == "catalog"
    # Against the narrowest real credential on record, it still runs.
    assert cc.assess(info.requires_capability, NARROW).blocks is False


def test_db_derived_answers_for_a_database_whose_credentials_are_gone():
    """The audit's §4 point, as behaviour rather than as a comment: the
    zero-fetch step must resolve with no probe and no credential at all."""
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )

    info = DATABASE_STEP_REGISTRY["db_derived"]
    assert info.requires_capability == ""
    # Not merely unsatisfiable-and-ignored: it makes no claim at all, so even
    # the most incapable probe on record produces no reason for it.
    reason, _ = pr._capability_reason("db_derived", info, NARROW)
    assert reason is None


# ── read-back: the probe, not a second opinion ──────────────────────────────

def test_the_probe_is_read_from_any_stored_survey_most_recent_first():
    """Matches `routes/databases.py::_to_summary` exactly: a plain
    schema-only run AFTER the probe ran must not hide a still-current
    capability reading. One behaviour, two readers."""
    reg = _Reg(surveys=[
        {"survey_data": '{"schema_info": {}}'},           # newest, no probe
        {"survey_data": _json({"credential_capability": NARROW})},
    ])
    assert cc.stored_probe(reg, _ENTITY) == NARROW


def test_an_unreadable_or_absent_survey_blob_reads_as_not_known():
    """Never as a capability of zero. On our own failure to establish
    something, do not block."""
    assert cc.stored_probe(_Reg(surveys=[{"survey_data": "not json"}]), _ENTITY) is None
    assert cc.stored_probe(_Reg(surveys=[]), _ENTITY) is None
    assert cc.stored_probe(_Reg(), SimpleNamespace(slug="")) is None

    class _Raises:
        def get_database_surveys(self, slug):
            raise RuntimeError("registry down")

    assert cc.stored_probe(_Raises(), _ENTITY) is None


# ── advisory: the axis warns, it does not withhold the answer ───────────────

def test_a_capability_only_proposal_still_runs(world):
    """The judgement call that keeps this axis from being a regression.

    Blocking here would leave a database whose credential lacks `pg_monitor`
    with NO survey at all — strictly worse than the bounded answer it is
    warning about, and silent on every path with nobody there to ask (the
    scheduler, the CLI, a Survey Definition run).
    """
    world["asker"].requires_capability = "read"
    res = pr.resolve(_Reg({"filled_table": 3}, probe=NARROW), _ENTITY, "asker", world)
    assert res.status == pr.PROPOSAL
    assert res.proposal.advisory is True
    assert res.may_run is True, (
        "a capability shortfall withheld the answer entirely; it is supposed "
        "to bound it and say so")


def test_a_cost_proposal_is_never_advisory(world):
    """The asymmetry is deliberate and must not leak: a cost proposal asks to
    SPEND something, and proceeding unasked is the harm."""
    world["filler"] = StepInfo("filler", None, "", [], fetch_cost="download",
                               compute_cost="low", produces=("filled_table",))
    res = pr.resolve(_Reg(), _ENTITY, "asker", world)
    assert res.status == pr.PROPOSAL
    assert res.proposal.advisory is False
    assert res.may_run is False


def test_a_combined_proposal_blocks_on_its_cost_half(world):
    """Adding the capability axis must not weaken the cost gate. One
    capability reason alongside a tier reason does not make the whole thing
    advisory."""
    world["filler"] = StepInfo("filler", None, "", [], fetch_cost="download",
                               compute_cost="low", produces=("filled_table",))
    world["asker"].requires_capability = "read"
    res = pr.resolve(_Reg(probe=NARROW), _ENTITY, "asker", world)
    assert {r.kind for r in res.proposal.reasons} == {"tier", "capability"}
    assert res.proposal.advisory is False
    assert res.may_run is False


def test_an_advisory_run_is_labelled_rather_than_silent():
    """"Say so" is the other half of "run partially". The executor attaches
    the proposal to the step's own report entry — a step that ran bounded
    must not come back as a plain "ok"."""
    import inspect

    from resource_explorer.surveyors import survey_definition_executor as sde

    src = inspect.getsource(sde.SurveyDefinitionExecutor._execute)
    assert "proposal.advisory" in src
    assert 'ok_entry["capability"]' in src

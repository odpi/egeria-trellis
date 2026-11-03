"""Whether a step can say anything at all, checked before it is dispatched.

`StepInfo` already declares `requires_resources` (a zipball, a clone) and
`requires_views` — both about *runtime inputs*. Neither expresses "this step
reads rows another step writes, and there are none", which is a different and
commoner condition. `cve_scan` is the worked example: on a repo with no parsed
dependencies it runs, reads `project_dependencies`, finds nothing, and correctly
declines rather than claiming "no CVEs" — right behaviour, but it was dispatched,
timed, and counted as an input that produced no answer.

**A skip is a result, not an omission.** Every skip here carries a reason, via
`result_status.skipped()`, which takes one as a required argument. A step that
simply vanishes from a report is indistinguishable from one that ran and found
nothing — the failure this codebase keeps removing — so the skip is emitted as a
real annotation rather than by not appearing.

**This is the precondition half, not the guard half.** Egeria's own model answers
*which links fire* — each `NextGovernanceActionProcessStep` carries a `guard`,
and `EngineActionHandler.initiateNextEngineActions` follows the links whose guard
appears in the previous step's `outputGuards` (`validNextAction = (guard ==
null)`). That governs traversal of an authored graph. This governs whether a step
in a flat orchestrator run has anything to work with. They compose: a guard says
a branch was not taken, a precondition says a step had no input, and both must
leave a trace rather than a silence.

**Preconditions are named, not inlined.** A name can be declared on a step, read
in a report, and tested; a lambda on a step cannot. The names are deliberately
about *stored data*, not about steps, so the check does not encode an execution
order it cannot enforce — `test_step_execution_order.py` already pins
producers-before-consumers positionally, and a second implicit ordering beside it
is how the two drift.

**The producer is derived, not typed here (2026-09-23, design §17.1).** Each
entry used to carry a step-key string beside its check, "for the reason text
only". §17.1 needs the same fact for a second purpose — the resolver has to
run the producer, not merely name it — and two purposes reading one hand-typed
string in a module that otherwise refuses to name steps is exactly the drift
this file's own docstring warns about. So an entry now declares the **table**
it counts, and `step_produces.producer_of()` answers which step fills it, from
that step's own `StepInfo.produces`. The vocabulary of this module is
unchanged: it still names stored data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from resource_explorer.surveyors import result_status

log = logging.getLogger(__name__)


def _row_count(registry, table: str, slug: str, where: str = "",
               slug_column: str = "project_slug") -> int:
    """-1 when the table cannot be read at all, which is not the same as empty."""
    try:
        with registry._conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {slug_column} = ?"
                + (f" AND {where}" if where else ""),
                (slug,),
            ).fetchone()
        return int(row["n"] or 0)
    except Exception as exc:
        log.warning("precondition: cannot count %s for %s: %s", table, slug, exc)
        return -1


#: How long a producer's stored result stands in for a fresh run of it, when
#: the resolver is asked whether a precondition is satisfied — brief
#: BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §E, "the resolver checks the registry
#: for a prior result ... within a freshness window (see design §5.1a for the
#: window definition)". §5.1a itself (multi-resource-questions-design.md)
#: defines freshness for *catalog statistics* (pg_stats: fresh/stale/
#: never_collected/not_visible, judged by modification count and age), not a
#: single numeric window for "does a structural inventory still count" — this
#: is a different question (a table/column *inventory* row, not a stats
#: estimate) that the design does not give a number for. 24 hours is this
#: change's own judgement call, chosen so a same-day Scouting → Analysis
#: sequence (the brief's own worked example: 20 minutes apart) is always
#: satisfied, while a schema inventory from last week is treated as stale
#: enough to be worth refreshing rather than silently reused forever — the
#: `_row_count`-only check this replaces had no upper bound at all. Recorded
#: here, explicitly, rather than left as an unstated constant, so a later
#: reader can find and revisit it with one grep.
DEFAULT_FRESHNESS_WINDOW_HOURS = 24


def _latest_surveyed_at(registry, table: str, slug: str,
                        slug_column: str = "project_slug",
                        ts_column: str = "surveyed_at") -> str | None:
    """The newest `ts_column` for `table`/`slug`, or None if there is none or
    the table cannot be read. Only tables that really carry such a column may
    be asked (`Precondition.ts_column` says which); an unreadable one is
    logged at WARNING, because a swallowed failure here made four of five
    preconditions unsatisfiable by stored data (2026-10-14).
    """
    try:
        with registry._conn() as conn:
            row = conn.execute(
                f"SELECT MAX({ts_column}) AS ts FROM {table} WHERE {slug_column} = ?",
                (slug,),
            ).fetchone()
        ts = row["ts"] if row else None
        return str(ts) if ts else None
    except Exception as exc:
        log.warning("precondition: cannot read %s from %s for %s: %s",
                    ts_column, table, slug, exc)
        return None


def _latest_producer_run(registry, step_key: str, slug: str) -> str | None:
    """When `step_key` last ran successfully on `slug`, from `step_runs`.

    The one provenance source every precondition shares: whichever table a
    producer fills, it leaves a `step_runs` row (`step_cost_observer.record`,
    written by the local loop, the Prefect task and the auto-run alike). A row
    whose `dispatch_failed` is set is a dispatch that did not run, so it does
    not count. None when there is no such row or `step_runs` cannot be read
    (the latter logged at WARNING).
    """
    if not step_key:
        return None
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT MAX(surveyed_at) AS ts FROM step_runs "
                "WHERE slug = ? AND step_key = ? "
                "AND (dispatch_failed IS NULL OR dispatch_failed = '')",
                (slug, step_key),
            ).fetchone()
        ts = row["ts"] if row else None
        return str(ts) if ts else None
    except Exception as exc:
        log.warning("precondition: cannot read step_runs for %s/%s: %s",
                    slug, step_key, exc)
        return None


def _parse_ts(value: str):
    from datetime import datetime, timezone

    for candidate in (value, value.replace(" ", "T")):
        try:
            ts = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def _hours_since(surveyed_at: str) -> float | None:
    """Hours between `surveyed_at` (an ISO-ish timestamp as stored by this
    registry) and now, or None if it cannot be parsed — treated as "can't
    tell freshness" by the caller, not as "definitely stale"."""
    from datetime import datetime, timezone

    for candidate in (surveyed_at, surveyed_at.replace(" ", "T")):
        try:
            ts = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    log.debug("precondition: could not parse surveyed_at %r", surveyed_at)
    return None


def _needs_rows(table: str, what: str, where: str = "",
                slug_column: str = "project_slug") -> Callable:
    def check(registry, project) -> tuple[bool, str]:
        n = _row_count(registry, table, project.slug, where, slug_column)
        if n < 0:
            # Cannot tell. Run the step: refusing on an unreadable check would
            # turn our own failure into a claim about the repository, and the
            # step's own absence handling is better than a guess here.
            return True, f"could not read {table}; running the step rather than assuming"
        if n:
            return True, f"{n} row(s) in {table}"
        return False, f"no {what} recorded for this resource"
    return check


@dataclass(frozen=True)
class Precondition:
    """One named condition on stored data.

    `table` is the load-bearing field: it is what the check counts AND what
    `step_produces.producer_of()` inverts to a step key. The producer is
    therefore never written down twice.

    `slug_column` is carried separately from `check` (whose closure already
    knows it) so `freshness()` below can run the same "which table, keyed by
    what" lookup `_needs_rows` uses, without re-deriving it or asking the
    check to expose its own closure.

    `caveat` carries the part of a remedy that is NOT "run this step" — the
    Gradle/BOM case below is the whole reason the field exists: running
    `repo_manifest_parse` is necessary and, on that repository, not
    sufficient.
    """
    check: Callable
    table: str
    slug_column: str = "project_slug"
    caveat: str = ""
    #: The same filter `check` applies, so `freshness()` counts the rows the
    #: check counts (has_versioned_dependencies is not has_dependencies).
    where: str = ""
    #: A timestamp column on `table` itself, when it really has one
    #: (`database_tables.surveyed_at`). Optional corroboration only: the
    #: producer's `step_runs` row is the source every precondition has.
    ts_column: str = ""

    def produced_by(self) -> str:
        """The step that fills `self.table`, from its own `produces`."""
        from resource_explorer.surveyors import step_produces

        return step_produces.producer_of(self.table)

    def remedy(self) -> str:
        """What a reader should do next, as one sentence. Says so honestly
        when nothing declares a producer — naming a step that does not exist
        would be worse than admitting the gap."""
        step = self.produced_by()
        if not step:
            return (
                f"Nothing in this build declares that it writes {self.table} — "
                "no step can be named to satisfy this."
            )
        return f"Run {step}{f' ({self.caveat})' if self.caveat else ''} first."

    def freshness(self, registry, project,
                 window_hours: float = DEFAULT_FRESHNESS_WINDOW_HOURS
                 ) -> tuple[bool, str | None]:
        """(fresh, surveyed_at).

        Fresh means BOTH: the rows exist (the same count `check` makes), AND
        the producer step ran successfully on this resource inside the
        window. The run time comes from the producer's `step_runs` row, which
        every precondition has; a table that also carries its own timestamp
        (`ts_column`) may supply a newer one.

        `surveyed_at` is the newest run time found (None when there is none),
        returned whether or not it falls inside the window, as provenance.
        An unreadable count or an unparseable stamp is NOT fresh: the
        conservative direction, which asks the producer to run rather than
        trusting what could not be read. Unreadable is logged at WARNING.
        """
        candidates = [_latest_producer_run(registry, self.produced_by(),
                                           project.slug)]
        if self.ts_column:
            candidates.append(_latest_surveyed_at(
                registry, self.table, project.slug, self.slug_column,
                self.ts_column))
        stamps = [c for c in candidates if c]
        if not stamps:
            return False, None
        parsed = [(_parse_ts(c), c) for c in stamps]
        usable = [(t, c) for t, c in parsed if t is not None]
        if not usable:
            log.warning("precondition: could not parse %r as a time", stamps)
            return False, stamps[0]
        newest_ts, newest = max(usable, key=lambda tc: tc[0])
        n = _row_count(registry, self.table, project.slug, self.where,
                       self.slug_column)
        if n <= 0:
            # The producer ran but nothing is stored (or it cannot be read):
            # a run is not an answer when its rows are not there.
            return False, newest
        age_hours = _hours_since(newest)
        if age_hours is None:
            return False, newest
        return age_hours <= window_hours, newest


#: name → the condition. No producer string: see this module's docstring and
#: `Precondition.produced_by`.
PRECONDITIONS: dict[str, Precondition] = {
    "has_dependencies": Precondition(
        _needs_rows("project_dependencies", "dependencies"),
        table="project_dependencies"),
    # NOT the same condition, and the difference is the whole point. Measured
    # 2026-09-01: `egeria_git` has 216 dependency rows and **0 with a version** —
    # Gradle declares versions through a BOM or version catalog, so the parser
    # recovers the coordinate and not the version. `has_dependencies` passes
    # there and `cve_scan` still cannot query OSV, which needs a pinned version.
    # A guard that fires on the wrong property is worse than no guard: it reads
    # as coverage. Reported by a concurrent session and verified here directly.
    "has_versioned_dependencies": Precondition(
        _needs_rows("project_dependencies", "dependencies with a resolved version",
                    where="dep_version IS NOT NULL AND dep_version != ''"),
        table="project_dependencies",
        where="dep_version IS NOT NULL AND dep_version != ''",
        caveat="and version resolution — see Backlog, Gradle/BOM"),
    "has_file_inventory": Precondition(
        _needs_rows("project_file_inventory", "file inventory"),
        table="project_file_inventory"),
    "has_code_symbols": Precondition(
        _needs_rows("project_code_symbols", "code symbols"),
        table="project_code_symbols"),
    # ── databases (2026-09-23, design §17.1's own worked chain) ───────────
    #
    # `postgres_column_profile` reads table/column rows to know what to
    # sample; with none stored it would sample nothing and report a confident
    # empty profile. The slug column differs from the repo tables' — the
    # database detail tables are keyed by `database_slug` (registry.py's
    # `_DB_FS_DETAIL_TABLE_DDL`), which is why `_needs_rows` takes one.
    "has_schema_inventory": Precondition(
        _needs_rows("database_tables", "schema inventory",
                    slug_column="database_slug"),
        table="database_tables",
        slug_column="database_slug",
        ts_column="surveyed_at"),
    # `propose_quality_checks` applies the selection rules to a stored value
    # profile (DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §5); with none it
    # proposes nothing, which must not read as "no column qualified".
    "has_value_profile": Precondition(
        _needs_rows("database_value_profiles", "value profile",
                    slug_column="database_slug"),
        table="database_value_profiles",
        slug_column="database_slug",
        ts_column="surveyed_at"),
}


# ─────────────────────────────────────────────────────────────────────────
# Human-input preconditions (ENRICHMENT-E1-CONTEXT-TAB, 2026-09-29 — the
# project owner's amendment to the designer's reply §0.2: analyses whose
# prerequisite is a HUMAN INPUT, not a survey read, do run at the Enrichment
# stage). This module's own docstring says its vocabulary "is deliberately
# about stored data" — that holds here unchanged: a declared documentation
# source, a declared lens and a confirmed glossary term are all stored
# facts, just ones a PERSON writes rather than a survey. Kept as a separate
# dict rather than folded into `PRECONDITIONS` because the two answer
# different questions for a caller — `PRECONDITIONS` says "may a SURVEY STEP
# run" (and its `Precondition.remedy()`/`produced_by()` assume a step
# produces the missing table); this dict says "is a human input PRESENT",
# with no step to name as a remedy — the remedy is a person, told in
# `_needs_human_input`'s own reason text.
#
# Every check below reads real stored data where a real table exists, and
# is deliberately CONSERVATIVE where one does not: an unreadable or missing
# table (`confirmed_glossary_terms` does not exist — no glossary-confirmation
# mechanism is built at all; documentation sources read #348's `doc_sources`)
# reads as NOT SATISFIED, never as "cannot tell, so allow it" the way
# `_needs_rows` treats an unreadable table for a SURVEY step. Reversed on
# purpose from `_needs_rows`: there, "cannot tell" defers to running the
# step, which will itself report an honest absence; here, "cannot tell"
# must not be indistinguishable from "a person declared this and we lost
# it" — the direction that matters for a human input is never to claim one
# exists when it might not.
def _needs_human_input(kind: str) -> Callable:
    def check(registry, project) -> tuple[bool, str]:
        try:
            if kind == "lens":
                # No stored, investigation-level DataLens exists yet (§16.5
                # points 3-5 of the design are explicitly out of scope —
                # see db_derived.py's own header comment on preliminary_fit).
                # The one real, already-stored signal is preliminary_fit's
                # OWN last-read `lens_declared` flag — a resource-scoped
                # proxy for what should be investigation-scoped. Documented
                # limitation, not silently assumed: see ENRICHMENT-E1-CONTEXT-TAB-IMPLEMENTED.md.
                from resource_explorer.facts import FactLayer

                fl = FactLayer(registry, resource_type="database")
                fact = fl.fact(project.slug, "preliminary_fit")
                value = getattr(fact, "value", None) or {}
                if isinstance(value, dict) and value.get("lens_declared"):
                    return True, "a lens is declared (from preliminary_fit's own last read)"
                return False, "declare a lens on the investigation"
            if kind == "documentation_source":
                # The table #348 actually created (`doc_sources`), read by
                # slug alone -- no entity_type filter -- exactly as Context's
                # block reads it (Context lists by the resource's own slug),
                # so the unlock and the Context tab cannot disagree.
                n = _row_count(registry, "doc_sources", project.slug,
                                slug_column="entity_slug")
                if n > 0:
                    return True, f"{n} documentation source(s) declared"
                return False, "no documentation source declared yet"
            if kind == "ingested_documentation":
                n = _row_count(registry, "doc_sources", project.slug,
                                where="ingested_at IS NOT NULL AND ingested_at != ''",
                                slug_column="entity_slug")
                if n > 0:
                    return True, f"{n} documentation source(s) ingested"
                return False, "no documentation has been ingested yet"
            if kind == "confirmed_glossary_term":
                n = _row_count(registry, "confirmed_glossary_terms", project.slug,
                                slug_column="database_slug")
                if n > 0:
                    return True, f"{n} glossary term(s) confirmed"
                return False, "no glossary term confirmed yet"
        except Exception as exc:
            log.debug("human-input precondition %r could not be checked: %s", kind, exc)
        return False, f"could not establish whether a {kind.replace('_', ' ')} is present yet"
    return check


#: name (the catalog's `requires_input` vocabulary) → checker. Not
#: `Precondition` instances — no survey step produces any of these, so
#: `produced_by()`/`remedy()`'s "run this step" shape does not apply; the
#: reason string returned by the check IS the remedy.
HUMAN_INPUT_CHECKS: dict[str, Callable] = {
    "lens": _needs_human_input("lens"),
    "documentation_source": _needs_human_input("documentation_source"),
    "ingested_documentation": _needs_human_input("ingested_documentation"),
    "confirmed_glossary_term": _needs_human_input("confirmed_glossary_term"),
}


def human_input_state(registry, project, requires_input: str) -> tuple[bool, str]:
    """(present, reason) for a catalog entry's `requires_input` value.

    An unknown `requires_input` string reads as NOT satisfied rather than
    raising or silently passing — same conservative direction as every check
    above, for a caller (the Enrichment "Survey & analyses" map) that must
    never render "unlocked" for a kind this module does not recognize."""
    checker = HUMAN_INPUT_CHECKS.get(requires_input)
    if checker is None:
        return False, f"unrecognized human-input kind {requires_input!r}"
    return checker(registry, project)


def fresh_hit(registry, project, name: str,
             window_hours: float = DEFAULT_FRESHNESS_WINDOW_HOURS
             ) -> tuple[bool, str | None]:
    """(within_window, surveyed_at) for a named precondition, or (False, None)
    for a name this module does not know.

    Shared by `prerequisite_resolver` (the runtime, per-entity check) and
    `survey_execution_plan._add_produces_edges` (the Prefect plan builder,
    §E) so "does this precondition already have a fresh stored answer" has
    exactly one implementation — design §19.5's principle ("a fresh answer
    satisfies the prerequisite") applied consistently rather than
    re-derived once per engine.
    """
    entry = PRECONDITIONS.get(name)
    if entry is None:
        return False, None
    return entry.freshness(registry, project, window_hours)


def evaluate(registry, project, requires_context: dict[str, str]) -> tuple[bool, str, str]:
    """(may_run, precondition_name, reason).

    `requires_context` maps a precondition name to the step's own explanation of
    why it needs it. The returned reason combines that with what was actually
    measured, so the skip says both what was missing and why this step cares.

    An unknown precondition name RUNS the step and logs loudly. Silently skipping
    on a typo would be the worst outcome available: a step that never runs, for a
    reason nobody can look up, indistinguishable from one with nothing to say.
    """
    for name, why in (requires_context or {}).items():
        entry = PRECONDITIONS.get(name)
        if entry is None:
            log.error(
                "step declares unknown precondition %r — running it anyway. "
                "Known: %s", name, ", ".join(sorted(PRECONDITIONS)))
            continue
        met, detail = entry.check(registry, project)
        if not met:
            return False, name, f"{why} — {detail}. {entry.remedy()}"
    return True, "", ""


def unmet(registry, project, requires_context: dict[str, str]) -> list[tuple[str, str]]:
    """Every unmet precondition, as (name, reason) — not just the first.

    `evaluate()` stops at the first failure, which is right for "may this step
    run" and wrong for §17.1's resolver: a step missing two inputs must produce
    ONE proposal naming both, not one proposal and then, after that is
    accepted, a second. Kept as a separate function rather than changing
    `evaluate`'s shape, because every existing caller wants the cheap
    short-circuit.
    """
    out: list[tuple[str, str]] = []
    for name, why in (requires_context or {}).items():
        entry = PRECONDITIONS.get(name)
        if entry is None:
            log.error(
                "step declares unknown precondition %r — running it anyway. "
                "Known: %s", name, ", ".join(sorted(PRECONDITIONS)))
            continue
        met, detail = entry.check(registry, project)
        if not met:
            out.append((name, f"{why} — {detail}. {entry.remedy()}"))
    return out


def skip_status(precondition: str, reason: str) -> dict:
    """The status row for a skipped step. `skipped()` requires the reason."""
    return result_status.skipped(reason, gate=precondition)

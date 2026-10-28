"""What a step actually cost, against what it declared.

Three cost mis-declarations turned up in one week, and no test could have
caught any of them because every one was a *declaration* that disagreed with
*behaviour*:

  * `repo_classification` declared `fetch_cost="none"` and called the GitHub
    API — ~10 minutes per repo against a tier whose defining property is zero
    fetch (CLAUDE.md rule 17). It was found by noticing a corpus run had done
    3 repos in 10 minutes, not by any check.
  * `repo_manifest_parse` declared `compute_cost="medium"` and measures like
    `repo_file_inventory`, which is `low` — so `max_compute_cost="low"` runs
    excluded it from the very tier it was added to serve.
  * A timing was reported for `resolve_doc_locations()` when the thing that
    runs is `build_report()` — out by 8x, in the flattering direction.

The common cause is that a cost tier is a guess made when a step is authored
and nothing measures it afterwards. `tests/test_analysis_catalog_reader.py`'s
zero-fetch guard checks `requires_resources`, not `fetch_cost`, and a step can
satisfy it while opening sockets — a guard that can be satisfied by declaring
one thing and doing another is not a guard.

**This reports; it never corrects.** Auto-updating a declaration from an
observation would have silently absorbed the `repo_classification` bug: a step
that suddenly starts fetching is sometimes a regression worth seeing, not a
label worth rewriting. Observed and declared are recorded side by side and the
disagreement is surfaced; deciding which is wrong is a person's job, because
sometimes it is the code.

**A first observation is loud on purpose.** For a step nobody has ever
measured, "no disagreement recorded" and "never checked" are the same silence —
the shape of most of what went wrong this week.

Socket counting is deliberately crude: `socket.socket.connect` is wrapped for
the duration of a step. Under concurrent surveys in one process the count can
attribute another thread's connection to this step, so a NON-ZERO count on a
step declared zero-fetch is a prompt to look, not a proof. A zero count is the
trustworthy direction, and that asymmetry is stated rather than hidden.

**And there is a second asymmetry, found while verifying §17.2 live against a
real database (2026-09-23): the zero is NOT trustworthy for a step that
connects through libpq.** `psycopg2` opens its socket inside the C client, so
`socket.socket.connect` is never called and `connects` comes back 0 for a
`postgres_schema_and_stats` run that demonstrably opened a connection. The
`fetch_cost='none'` disagreement check therefore cannot fire for ANY database
step, which is worth knowing before reading a clean board as evidence. The
database family's real fetch signal is `api_calls`/`bytes_fetched` over HTTP
(Egeria) plus, when it is built, §17.2's `source_queries` — the optional,
sampled axis this phase deliberately does not build.

**Seconds are one axis of a vector (2026-09-23, design §17.2).** Wall time is
what the user waits and is not what the funnel's argument is about: a step
that waits on the network is cheap in CPU and slow in wall time, and for
repositories the scarce resource is the GitHub rate budget rather than either.
`observe()` now also captures CPU time, bytes fetched, API and Egeria calls,
cache hits, LLM tokens and the step's own yield, and `record()` writes the
whole vector as one `step_runs` row instead of two per-step metrics scattered
across project rows. The old `project_analysis_metrics` write is KEPT — the
`funnel-cost-measured.md` measurement and every existing reader query it, and
a migration that moved the numbers on the same day the vector arrived would
have made a gap in the history look like a change in the costs.
"""
from __future__ import annotations

import logging
import resource
import socket
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

KIND = "step_cost"

#: Wall-clock ceilings for each declared compute tier, in seconds. Derived from
#: measurements rather than chosen: repo_file_inventory 0.2-0.3s and
#: repo_manifest_parse 0.0-0.6s are both `low`; repo_classification's ~25s
#: median and the arch coupling passes are the medium/high end. Generous on
#: purpose — this exists to catch order-of-magnitude errors, not to police
#: seconds, and a noisy check gets switched off.
_COMPUTE_CEILING = {"low": 5.0, "medium": 60.0, "high": float("inf")}

_counter_lock = threading.Lock()
_connect_count = 0
_patched = False
_real_connect = socket.socket.connect


def _counting_connect(self, address):  # noqa: ANN001
    global _connect_count
    with _counter_lock:
        _connect_count += 1
    return _real_connect(self, address)


@dataclass
class Observation:
    step_key: str
    elapsed: float
    connects: int
    declared_fetch: str
    declared_compute: str
    #: What the step actually produced — WITHOUT this, a duration is
    #: uninterpretable. repo_data_profiling measured 0.0s median across 21
    #: repos, and a step that correctly found nothing and a step that never
    #: reached its input produce exactly that same number. Duration alone
    #: cannot separate "fast because there was nothing to do" from "fast
    #: because it did nothing", which is the absence-looks-like-zero shape
    #: relocated into the measuring instrument.
    annotations: int = -1          # -1 = not captured
    #: The step's own StepOutcome labels, when it emits them (step_outcome.py).
    #: `no_signal` means it looked and there was nothing; `unverified` means it
    #: could not look. That is exactly the distinction a duration cannot make.
    outcomes: tuple[str, ...] = ()
    #: Empty when observation and declaration agree.
    disagreement: str = ""

    # ── the rest of §17.2's vector ───────────────────────────────────────
    #: Process CPU time consumed inside the step, in ms — *compute*, which is
    #: what tier placement actually claims. -1 = not captured.
    cpu_ms: float = -1.0
    #: Bytes that arrived over the network into RE during the step.
    bytes_fetched: int = 0
    #: True when every counted request could be sized. False makes
    #: `bytes_fetched` a floor, and a reader must not add it up as a total.
    bytes_complete: bool = True
    api_calls: int = 0
    api_calls_by_host: dict = field(default_factory=dict)
    egeria_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    #: `SourceCache` hits/misses within the step, and the three-state label
    #: `cold`/`warm`/`not-consulted` — so a warm run is not mistaken for a
    #: cheap step (22.6s cold vs 1.3s warm is one step's real range), and a
    #: database survey that consults no cache at all is not filed as "warm".
    cache_hits: int = 0
    cache_misses: int = 0
    acquisition: str = "not-consulted"
    #: How many catalog questions this step's analyses answer — yield's
    #: denominator. -1 = could not be determined (the catalog did not load),
    #: which is not 0.
    questions_answered: int = -1
    #: local / prefect / egeria / remote, and whose engine's numbers these are.
    executor: str = "local"
    source: str = "local"
    #: Prefect flow-run id or Egeria engine-action GUID, for linking out.
    executor_ref: str = ""
    #: §17.1's attribution: "" for a directly-requested step, the requesting
    #: step's key for an auto-run prerequisite.
    demanded_by: str = ""
    #: Prefect dispatch honesty (2026-09-28). Mirrors `executor_ref` for a
    #: genuine Prefect dispatch (kept as its own field, not folded into
    #: `executor_ref`, per the step_runs schema migration's own reasoning —
    #: see registry.py::record_step_run). Empty unless `executor=='prefect'`
    #: and the dispatch actually happened.
    flow_run_id: str = ""
    #: Non-empty only when a Prefect dispatch was attempted and fell back to
    #: local execution — the exception text `run_prefect_step` caught. Empty
    #: for a step that was never routed to Prefect at all, which is a
    #: different, unremarkable case this field must not conflate with a
    #: failure.
    dispatch_failed: str = ""
    #: R21: the OpenLineage run id and how delivery went
    #: (`openlineage.RunState.summary()`), so a run's event can be found from
    #: its `step_runs` row and a lost event is visible, not inferred. Empty
    #: only for an Observation built by hand.
    openlineage: dict = field(default_factory=dict)

    def vector(self) -> dict:
        """The cost vector as it is stored, one key per §17.2 axis.

        `annotations`/`questions_answered` carry -1 for "not captured", never
        0 — a step that produced nothing and a step whose yield was never
        looked at must not read alike, which is the whole reason this module
        records yield beside duration at all.
        """
        return {
            "wall_ms": round(self.elapsed * 1000.0, 1),
            "cpu_ms": round(self.cpu_ms, 1),
            "bytes_fetched": self.bytes_fetched,
            "bytes_complete": self.bytes_complete,
            "api_calls": self.api_calls,
            "api_calls_by_host": dict(self.api_calls_by_host),
            "egeria_calls": self.egeria_calls,
            "llm_tokens_in": self.llm_tokens_in,
            "llm_tokens_out": self.llm_tokens_out,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "acquisition": self.acquisition,
            "connects": self.connects,
            "annotations": self.annotations,
            "questions_answered": self.questions_answered,
            "outcomes": list(self.outcomes),
            "interpretable": self.interpretable,
            **({"openlineage": dict(self.openlineage)} if self.openlineage else {}),
        }

    def declared_vector(self) -> dict:
        return {"fetch_cost": self.declared_fetch, "compute_cost": self.declared_compute}

    @property
    def interpretable(self) -> bool:
        """Can this timing be reasoned about at all?

        A fast run says nothing about a cost tier unless we know the step had
        real work to do. `unverified` is the giveaway that it did not.
        """
        if "unverified" in self.outcomes:
            return False
        return self.annotations != 0

    @property
    def agrees(self) -> bool:
        return not self.disagreement


def _cpu_seconds() -> float:
    """Process CPU time (user + system), self and children.

    Children are included because two of the most expensive things a step
    does — `git` and the zipball extraction — run as subprocesses, and a
    measure of "compute" that left them out would report the heaviest steps
    in this package as costing nothing.

    Process-wide, so a concurrent survey in another thread inflates it, the
    same crudeness (and the same direction) the socket counter already
    states about itself: a high number is a prompt to look, a low one is
    trustworthy.
    """
    try:
        me = resource.getrusage(resource.RUSAGE_SELF)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime
    except Exception:  # pragma: no cover - platform guard
        return float("nan")


@contextmanager
def observe(step_key: str, declared_fetch: str, declared_compute: str,
            *, executor: str = "local", source: str = "local",
            executor_ref: str = "", demanded_by: str = "",
            entity_type: str = "", slug: str = "", resource=None,
            job_family: str = "analysis"):
    """Measure a step's whole cost vector, then compare it with what it declared.

    Yields a one-element list that receives the Observation on exit, so a
    caller can read the result without the context manager having to return
    two things.

    The three ContextVar scopes (`acquisition`, `llm_usage`, `external_calls`)
    nest, so wrapping a prerequisite step inside a demanding one attributes
    the cost to BOTH — which is §17.1 condition 3's requirement, not an
    accident: "why did Scouting take three minutes" is answered by the
    demanding step carrying its prerequisite's cost, while `demanded_by` on
    the producer's own row keeps the two separable.
    """
    global _connect_count, _patched
    from resource_explorer.observability import acquisition, external_calls, llm_usage
    from resource_explorer.observability import openlineage

    # R21: every step passes through here, so this is the one place a run
    # event is emitted -- START now, COMPLETE/FAIL in the finally below.
    ol_state, ol_token = openlineage.begin(job_family, step_key, entity_type, slug, resource)
    raised: BaseException | None = None

    out: list[Observation] = []
    with _counter_lock:
        start_count = _connect_count
        first = not _patched
        if first:
            socket.socket.connect = _counting_connect  # type: ignore[method-assign]
            _patched = True
    t0 = time.perf_counter()
    cpu0 = _cpu_seconds()
    with ExitStack() as stack:
        calls = stack.enter_context(external_calls.call_scope())
        acquired = stack.enter_context(acquisition.acquisition_scope())
        tokens = stack.enter_context(llm_usage.usage_scope())
        try:
            yield out
        except BaseException as exc:
            raised = exc
            raise
        finally:
            elapsed = time.perf_counter() - t0
            cpu = _cpu_seconds() - cpu0
            with _counter_lock:
                connects = _connect_count - start_count
            obs = Observation(step_key, elapsed, connects, declared_fetch, declared_compute)
            obs.cpu_ms = -1.0 if cpu != cpu else round(cpu * 1000.0, 1)
            obs.bytes_fetched = calls.bytes_fetched
            obs.bytes_complete = calls.bytes_complete
            obs.api_calls = calls.api_calls
            obs.api_calls_by_host = dict(calls.by_host)
            obs.egeria_calls = calls.egeria_calls
            obs.cache_hits = acquired.hits
            obs.cache_misses = acquired.misses
            obs.acquisition = acquired.state
            obs.llm_tokens_in = int(getattr(tokens, "prompt_tokens", 0) or 0)
            obs.llm_tokens_out = int(getattr(tokens, "completion_tokens", 0) or 0)
            obs.executor = executor
            obs.source = source
            obs.executor_ref = executor_ref
            obs.demanded_by = demanded_by
            obs.disagreement = _disagreement(obs)
            openlineage.finish(ol_state, ol_token, raised=raised)
            obs.openlineage = ol_state.summary()
            out.append(obs)
            if obs.disagreement:
                log.warning("step cost: %s — %s", step_key, obs.disagreement)


def _disagreement(obs: Observation) -> str:
    """The disagreement between what was declared and what happened, if any.

    Only unambiguous cases. A step declared zero-fetch that opened a connection
    is not arguable; a step slower than its tier's generous ceiling is worth a
    look. Being FASTER than declared is reported too — repo_manifest_parse's
    over-declaration excluded it from the cheap tier it was built for, so
    over-declaring is not the harmless direction it sounds like.
    """
    bits = []
    if obs.declared_fetch == "none" and obs.connects > 0:
        bits.append(
            f"declares fetch_cost='none' but opened {obs.connects} connection(s) — "
            "zero-fetch is the defining property of the Discovery tier, and a "
            "max_fetch_cost='none' run currently believes this step is free"
        )
    # The compute ceiling only applies to a step that made NO connections.
    #
    # Caught by running this against repo_classification: it took 20.1s and got
    # flagged for declaring compute_cost='low', when ~all of that was waiting on
    # five GitHub round trips. Wall clock is a fair proxy for compute only when
    # nothing was fetched; for a fetching step it measures the network and says
    # nothing about the declaration. Emitting that disagreement would have been
    # a false alarm on an honest declaration — and a check that cries wolf is a
    # check that gets switched off, which this module's own docstring warns
    # about. Separating fetch time from compute time properly needs
    # instrumentation inside each step; until then this abstains rather than
    # guesses.
    # An uninterpretable timing is not evidence about a declaration. A step
    # that reported `unverified`, or produced nothing at all, was not exercised
    # — flagging its speed would be reading a number taken while the thing
    # being measured was not happening.
    if not obs.interpretable:
        return "; ".join(bits)
    ceiling = _COMPUTE_CEILING.get(obs.declared_compute, float("inf"))
    if obs.connects == 0 and obs.elapsed > ceiling:
        bits.append(
            f"declares compute_cost='{obs.declared_compute}' (ceiling {ceiling:.0f}s) "
            f"but took {obs.elapsed:.1f}s with no connections, so that is compute"
        )
    elif obs.declared_compute in ("medium", "high") and obs.elapsed < 1.0 and obs.connects == 0:
        bits.append(
            f"declares compute_cost='{obs.declared_compute}' but took {obs.elapsed:.2f}s with "
            "no connections — over-declaring excludes a step from the cheap tiers it "
            "may have been built to serve"
        )
    return "; ".join(bits)


#: record() outcomes. A caller can branch on these; a log line alone cannot be
#: branched on, and "the metric was never written" would otherwise look exactly
#: like "the step was never slow" — which is the failure this whole module
#: exists to catch, so the observer swallowing its own write silently would be
#: the joke writing itself.
RECORDED = "recorded"
FIRST = "first"
NOT_RECORDED = "not_recorded"


def _step_ever_measured(registry, step_key: str) -> bool:
    """Has this step been measured for ANY resource before now?

    Reads the metrics table directly rather than via query_metrics_history,
    which is per-slug by design — the question here is about the step.
    """
    metric = f"{step_key}_elapsed"
    with registry._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM project_analysis_metrics "
            "WHERE kind = ? AND metric_name = ? LIMIT 1",
            (KIND, metric),
        ).fetchone()
    return row is not None


def completed(annotations, raised: bool) -> bool:
    """True when a step ran to the end: it did not raise and reported no `step_error` (BaseSurveyor._warn,
    which most surveyors use instead of raising). Only a completed step's step_runs row carries the commit
    it read (`survey_commit_sha`): a failed step read nothing a reader should cite."""
    return not raised and not any(getattr(a, "check_name", "") == "step_error" for a in annotations or ())


def describe_work(annotations) -> tuple[int, tuple[str, ...]]:
    """What a step produced, as the observer needs it: how many annotations,
    and any StepOutcome labels they carry.

    Read from the annotations the orchestrator already holds, so no step has to
    cooperate for its timing to become interpretable — a step that has not
    adopted the outcome vocabulary still contributes its count.
    """
    outcomes = []
    for ann in annotations or ():
        props = getattr(ann, "json_properties", None) or {}
        outcome = props.get("outcome")
        if outcome:
            outcomes.append(outcome)
    return len(annotations or ()), tuple(sorted(set(outcomes)))


def count_questions_answered(step_key: str, entity_type: str = "repo") -> int:
    """How many catalog questions this step's analyses answer — §17.2's yield
    denominator, "questions from the catalog's `analysis_ids` inverse".

    Returns -1 when the catalogs cannot be read. That is not 0: a step whose
    questions could not be counted and a step that answers none of them are
    different facts, and cost-per-question divides by this.
    """
    try:
        from resource_explorer.surveyors import question_catalog_reader as qcr

        analyses = _analyses_for_step(step_key, entity_type)
        if analyses is None:
            return -1
        if not qcr.is_authored(entity_type):
            # No questions authored for this resource type at all. Distinct
            # from "authored, and none of them need this step" — the first is
            # a gap in the catalog, the second is a fact about the step.
            return -1
        total = 0
        for entry in qcr.get_questions(entity_type) or ():
            # `get_questions` returns plain dicts (its own `QuestionList`
            # carries `QuestionCatalogEntry` dataclasses only internally);
            # both shapes are read here rather than assuming one, because
            # guessing wrong here reports every step as answering nothing —
            # a yield of 0, which is precisely the number this axis exists to
            # make meaningful.
            answering = entry.get("answering") if isinstance(entry, dict) \
                else getattr(entry, "answering", None)
            ids = set((answering.get("analysis_ids") if isinstance(answering, dict)
                       else getattr(answering, "analysis_ids", ())) or ())
            if ids & analyses:
                total += 1
        return total
    except Exception as exc:
        log.debug("could not count questions answered by %s: %s", step_key, exc)
        return -1


def _analyses_for_step(step_key: str, entity_type: str) -> set[str] | None:
    """The analysis ids this step is the source of, or None when the map for
    this resource type is not declared."""
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    try:
        adapter = get_adapter(entity_type)
    except Exception:
        return None
    provider = getattr(adapter, "analysis_source_steps", None)
    if provider is None:
        return None
    mapping = provider() or {}
    return {aid for aid, keys in mapping.items() if step_key in (keys or ())}


def record(registry, slug: str, obs: Observation, surveyed_at: str | None = None,
           entity_type: str = "repo", survey_commit_sha: str | None = None) -> str:
    """Persist one observation. Returns FIRST when this is the first ever for
    this step, RECORDED normally, NOT_RECORDED when persistence failed.

    For a step nobody has measured, "no disagreement" and "never checked" are
    the same silence — and that silence is the shape of most of what this
    module exists to catch. Which is exactly why the failure path returns a
    distinguishable value rather than only logging: an observer whose own
    writes fail quietly stops being evidence and starts being decoration.
    """
    # The vector first (§17.2). It comes BEFORE the legacy per-project metric
    # write below because that write is repo-only by construction:
    # `project_analysis_metrics` carries a foreign key to `projects(slug)`, so
    # `upsert_metric` refuses a database or filesystem slug outright. Written
    # second, its refusal returned NOT_RECORDED and the vector — the
    # entity-type-agnostic thing this whole section exists to store — was
    # never attempted, which is how the first live database run produced no
    # row at all.
    vector_recorded = True
    try:
        if obs.questions_answered < 0:
            obs.questions_answered = count_questions_answered(obs.step_key, entity_type)
        registry.record_step_run(
            slug, obs.step_key, surveyed_at or "",
            entity_type=entity_type,
            source=obs.source, executor=obs.executor,
            executor_ref=obs.executor_ref, demanded_by=obs.demanded_by,
            metrics=obs.vector(), declared=obs.declared_vector(),
            disagreement=obs.disagreement,
            flow_run_id=obs.flow_run_id, dispatch_failed=obs.dispatch_failed,
            survey_commit_sha=survey_commit_sha,
        )
    except Exception as exc:
        vector_recorded = False
        log.warning("could not record the step_runs row for %s: %s", obs.step_key, exc)

    if entity_type != "repo":
        # No legacy metrics for a non-repo resource — there never were any,
        # and `project_analysis_metrics` cannot hold them. The FIRST-ever
        # check below reads that same table, so it cannot answer for a
        # database step either; RECORDED is the honest return, not FIRST.
        return RECORDED if vector_recorded else NOT_RECORDED

    first = False
    history_read = True
    try:
        # Across ALL resources, not just this one. Scoped per-slug it fired for
        # every repo in a corpus run — 189 "FIRST" lines in 21 repos, on track
        # for ~540 — and a check that shouts on every row is one that gets
        # switched off, which this module's own docstring warns about. "Nobody
        # has ever measured this step" is a fact about the step, not about a
        # repo, so it should be said once.
        first = not _step_ever_measured(registry, obs.step_key)
    except Exception as exc:
        # first stays False, so a genuinely-first observation would be reported
        # as routine — the caller needs to know the difference.
        history_read = False
        log.warning("could not read step-cost history for %s: %s", obs.step_key, exc)
    try:
        registry.upsert_metric(
            slug, KIND,
            {f"{obs.step_key}_elapsed": round(obs.elapsed, 3),
             f"{obs.step_key}_connects": float(obs.connects)},
            detail={"step": obs.step_key,
                    "declared_fetch": obs.declared_fetch,
                    "declared_compute": obs.declared_compute,
                    "observed_connects": obs.connects,
                    "observed_elapsed": round(obs.elapsed, 3),
                    "observed_annotations": obs.annotations,
                    "observed_outcomes": list(obs.outcomes),
                    "interpretable": obs.interpretable,
                    "disagreement": obs.disagreement,
                    "first_observation": first},
            surveyed_at=surveyed_at,
        )
    except Exception as exc:
        # Never fail a survey over its own instrumentation — but say so in the
        # return value, not only in a log line nobody is reading.
        log.warning("could not record step cost for %s: %s", obs.step_key, exc)
        return NOT_RECORDED
    # The vector is written at the top of this function, before the legacy
    # metric above — see the comment there for why the order matters.
    if not vector_recorded:
        return NOT_RECORDED
    if not history_read:
        return NOT_RECORDED
    if first:
        log.warning(
            "step cost: FIRST measurement of %s — %.1fs, %d connection(s), "
            "declared fetch=%s compute=%s%s",
            obs.step_key, obs.elapsed, obs.connects,
            obs.declared_fetch, obs.declared_compute,
            f" — {obs.disagreement}" if obs.disagreement else " (agrees)",
        )
    return FIRST if first else RECORDED

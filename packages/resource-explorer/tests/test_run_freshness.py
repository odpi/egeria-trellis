"""The freshness gate on user-initiated analysis runs.

Measured 2026-09-10 over the 1,207 successful runs in `activity_log`: 20.7%
happened within five minutes of an identical prior run of the same analysis on
the same repo, and ~95% of those produced no different findings — a fifth of all
runs buying nothing, at up to 110s each.

Two decisions from the project owner shape what is tested here: **skip by
default** (not warn-and-run), and **leave the scheduler alone** — gating nightly
sweeps changes what "nightly" means, which is a different decision from sparing
someone a redundant click.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from resource_explorer.registry import Project
from resource_explorer.workflows.analysis import (
    _humanise_age, assess_freshness)


@pytest.fixture
def slug(request):
    import re as _re
    return "fr_" + _re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:48]


@pytest.fixture
def reg(pg_registry, slug):
    pg_registry.add(Project(slug=slug, display_name=slug,
                            github_url=f"https://github.com/x/{slug}"))
    return pg_registry


def _log_run(reg, slug, analysis_id, *, minutes_ago=0, status="ok"):
    """One activity_log analysis_run row, back-dated."""
    from resource_explorer.activity_logger import log_analysis_run
    entry_id = log_analysis_run(reg, "repo", slug, slug, status,
                                f"ran {analysis_id}", analysis_id, published=None)
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with reg._conn() as c:
        c.execute("UPDATE activity_log SET ts = %s WHERE id = %s", (ts, entry_id))
    return entry_id


class TestTheThreeStates:
    def test_never_run_is_not_fresh(self, reg, slug):
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert not f.fresh and f.state == "never-run"
        assert "no recorded run" in f.reason("security_scan")

    def test_a_recent_run_is_fresh(self, reg, slug):
        _log_run(reg, slug, "security_scan", minutes_ago=5)
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert f.fresh and f.state == "fresh"
        assert "has data from" in f.reason("security_scan")
        assert "force=true" not in f.reason("security_scan")
        assert "not running it again" not in f.reason("security_scan")

    def test_an_old_run_is_stale(self, reg, slug):
        _log_run(reg, slug, "security_scan", minutes_ago=180)
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert not f.fresh and f.state == "stale"

    def test_the_threshold_is_the_boundary(self, reg, slug):
        _log_run(reg, slug, "security_scan", minutes_ago=30)
        assert assess_freshness(reg, "repo", slug, "security_scan",
                                max_age_seconds=3600).fresh
        assert not assess_freshness(reg, "repo", slug, "security_scan",
                                    max_age_seconds=600).fresh


class TestWhatMustNeverCountAsFresh:
    def test_a_failed_run_is_not_freshness(self, reg, slug):
        """Its data is the OLD data. Refusing to re-run after a failure is the
        one behaviour nobody would want."""
        _log_run(reg, slug, "security_scan", minutes_ago=1, status="error")
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert not f.fresh, "an errored run is being treated as fresh data"

    def test_a_recent_failure_re_runs_even_with_an_older_success(self, reg, slug):
        """The behaviour, stated deliberately rather than discovered.

        `get_analysis_last_run` keeps only the MOST RECENT run per analysis
        ("newest-first, so the first win stands"), so a failure hides the
        older success entirely and freshness cannot see it. Two readings are
        defensible — the 30-minute-old data IS still there — and this picks
        RE-RUN, because the action a person takes after a failure is to press
        Run, and a gate that answered "no need, it succeeded half an hour ago"
        would be refusing the one request that is obviously legitimate.

        Written the other way round first, asserting the older success should
        keep the gate closed; the failure of that test is what surfaced the
        choice.
        """
        _log_run(reg, slug, "security_scan", minutes_ago=30)
        _log_run(reg, slug, "security_scan", minutes_ago=1, status="error")
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert not f.fresh, (
            "a run whose latest attempt FAILED is being skipped as fresh — "
            "pressing Run after a failure must run")

    def test_a_future_timestamp_is_stale_not_infinitely_fresh(self, reg, slug):
        """Clock skew, or a future-dated row. Treating it as fresh would wedge
        the analysis into never running again, with no way to notice."""
        _log_run(reg, slug, "security_scan", minutes_ago=-600)
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert not f.fresh and f.state == "stale"

    def test_an_unparseable_timestamp_does_not_crash_or_grant_freshness(self, reg, slug):
        entry = _log_run(reg, slug, "security_scan", minutes_ago=5)
        with reg._conn() as c:
            c.execute("UPDATE activity_log SET ts = %s WHERE id = %s",
                      ("not-a-timestamp", entry))
        f = assess_freshness(reg, "repo", slug, "security_scan")
        assert not f.fresh and f.state == "never-run"


class TestDerivedAnalysesInheritFreshness:
    def test_the_diagram_is_fresh_because_the_recovery_ran(self, reg, slug):
        """`architecture_diagram` owns no steps — it derives from
        `architecture_recovery`'s. A recent run of the recovery makes the
        diagram's data current even though the diagram has never been run."""
        _log_run(reg, slug, "architecture_recovery", minutes_ago=3)
        f = assess_freshness(reg, "repo", slug, "architecture_diagram")
        assert f.fresh, "a derived analysis is not seeing its source's run"
        assert f.via == "architecture_recovery"
        assert "via 'architecture_recovery'" in f.reason("architecture_diagram")

    def test_a_stale_source_leaves_the_derived_stale(self, reg, slug):
        _log_run(reg, slug, "architecture_recovery", minutes_ago=300)
        assert not assess_freshness(reg, "repo", slug, "architecture_diagram").fresh

    def test_the_most_recent_of_the_two_wins(self, reg, slug):
        _log_run(reg, slug, "architecture_recovery", minutes_ago=300)
        _log_run(reg, slug, "architecture_diagram", minutes_ago=2)
        f = assess_freshness(reg, "repo", slug, "architecture_diagram")
        assert f.fresh and f.via == "architecture_diagram"

    def test_an_ordinary_analysis_does_not_inherit_from_anything(self, reg, slug):
        _log_run(reg, slug, "architecture_recovery", minutes_ago=1)
        assert not assess_freshness(reg, "repo", slug, "security_scan").fresh


class TestAgeIsReadable:
    @pytest.mark.parametrize("seconds,expected", [
        (5, "less than a minute ago"), (60, "1 minute ago"),
        (3599, "59 minutes ago"), (3600, "1 hour ago"),
        (86399, "23 hours ago"), (86400, "1 day ago"), (259200, "3 days ago"),
    ])
    def test_units_change_at_the_right_boundaries(self, seconds, expected):
        """The first version printed minutes at every scale and produced
        '1827 minutes ago', which a reader has to do arithmetic on."""
        assert _humanise_age(seconds) == expected


class TestTheGateIsWiredAndScopedCorrectly:
    @staticmethod
    def _route_uses(monkeypatch, reg):
        """Point the route's own `ProjectRegistry()` at the test schema.

        The route constructs its registry itself, and `pg_registry` is a
        throwaway schema reached through a `database_url` override — so without
        this the route looks in the real database and 404s on a project the
        fixture created. Patched at `resource_explorer.registry` because the
        route imports the name inside the function, so the lookup happens at
        call time.
        """
        import resource_explorer.registry as reg_mod
        monkeypatch.setattr(reg_mod, "ProjectRegistry", lambda *a, **k: reg)

    def test_the_run_route_declines_a_fresh_analysis(self, reg, slug, monkeypatch):
        """Behavioural, not structural. The first version of this asserted that
        `_assess_freshness` and `"skipped"` appeared in the route's SOURCE —
        which stayed true when the gate was disabled with `if False:`, so it
        passed against a completely ungated route. Found by sabotage."""
        import asyncio
        from resource_explorer.web.routes import projects

        self._route_uses(monkeypatch, reg)
        _log_run(reg, slug, "security_scan", minutes_ago=2)
        out = asyncio.run(projects.run_single_analysis(slug, "security_scan"))
        assert out["status"] == "skipped", (
            f"a run 2 minutes old was not declined: {out}")
        assert out["reason"] == "already-fresh"
        assert out["activity_id"] is None and out["run_id"] is None, (
            "a declined run still handed back ids, so the caller will poll for "
            "a run that was never started")
        assert "security_scan" in out["detail"]

    def test_force_runs_anyway(self, reg, slug, monkeypatch):
        """The override has to actually reach the queue, or the gate is a wall."""
        import asyncio
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.web.routes import projects

        enqueued = []
        monkeypatch.setattr(ProjectRegistry, "enqueue_run",
                            lambda self, kind, target, **kw: enqueued.append((kind, target))
                            or "run-forced")
        self._route_uses(monkeypatch, reg)
        _log_run(reg, slug, "security_scan", minutes_ago=2)
        out = asyncio.run(projects.run_single_analysis(slug, "security_scan", force=True))
        assert out["status"] == "started", f"force=true was still declined: {out}"
        assert enqueued, "force=true returned started but enqueued nothing"

    def test_a_stale_analysis_is_not_declined(self, reg, slug, monkeypatch):
        """The gate must not simply refuse everything."""
        import asyncio
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.web.routes import projects

        monkeypatch.setattr(ProjectRegistry, "enqueue_run",
                            lambda self, kind, target, **kw: "run-stale")
        self._route_uses(monkeypatch, reg)
        _log_run(reg, slug, "security_scan", minutes_ago=600)
        out = asyncio.run(projects.run_single_analysis(slug, "security_scan"))
        assert out["status"] == "started", f"a 10-hour-old run was declined: {out}"

    def test_the_scheduler_is_not_gated(self):
        """A project-owner decision, 2026-09-10. Gating nightly sweeps changes
        what 'nightly' means."""
        import inspect
        from resource_explorer import scheduler
        assert "assess_freshness" not in inspect.getsource(scheduler), (
            "the scheduler now consults freshness — scheduled sweeps were "
            "deliberately left ungated")


class TestTheEnqueueResponseCarriesThePrice:
    """The run dialog's saved-time sentence (index.html) needs the price on
    the SAME response that started the run — only the freshness-skip path
    carried it before, so a plain first run (never skipped) had no estimate
    to render against once it completed as `published: "queued"`."""

    def test_the_started_response_carries_the_rerun_fields(self, reg, slug, monkeypatch):
        import asyncio
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.web.routes import projects

        monkeypatch.setattr(ProjectRegistry, "enqueue_run",
                            lambda self, kind, target, **kw: "run-x")
        TestTheGateIsWiredAndScopedCorrectly._route_uses(monkeypatch, reg)
        # never-run — assess_freshness reports "never-run", so this reaches
        # the enqueue path rather than the skip path.
        out = asyncio.run(projects.run_single_analysis(slug, "security_scan"))
        assert out["status"] == "started", f"unexpectedly declined: {out}"
        for key in ("rerun_cost_seconds", "rerun_steps_seconds", "rerun_publish_seconds",
                    "rerun_cost_basis", "rerun_cost_runs", "rerun_split_runs"):
            assert key in out, (
                f"the enqueue response is missing {key!r} — the run dialog's "
                f"saved-time sentence has nothing to render")
        assert out["rerun_cost_basis"] in ("measured", "declared", "unknown"), out

    def test_a_raising_estimate_still_enqueues_and_says_so(self, reg, slug, monkeypatch):
        """`estimate_run_cost` has no broad except by design — it should fail
        loudly if the queue or catalog can't be read. But the run is already
        enqueued by the time the estimate runs, so a broken estimate must not
        take the run down with it, and must not be swallowed as a bare log
        line either (tests/test_no_silent_success.py's ratchet)."""
        import asyncio
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.web.routes import projects

        monkeypatch.setattr(ProjectRegistry, "enqueue_run",
                            lambda self, kind, target, **kw: "run-y")

        def _boom(*a, **k):
            raise RuntimeError("boom: catalog unreadable")

        monkeypatch.setattr(projects, "_estimate_run_cost", _boom)
        TestTheGateIsWiredAndScopedCorrectly._route_uses(monkeypatch, reg)
        out = asyncio.run(projects.run_single_analysis(slug, "security_scan"))
        assert out["status"] == "started", (
            "a broken cost estimate must not prevent the run itself from "
            f"being enqueued: {out}")
        assert out["rerun_cost_basis"] == "unavailable"
        assert out.get("rerun_cost_error") and "boom" in out["rerun_cost_error"], (
            "the estimate failure must be recorded observably in the response, "
            "not just logged — that's the silent-success ratchet"
        )


class TestTheFrontendRunCallersHandleASkip:
    """Split out of TestTheGateIsWiredAndScopedCorrectly (unchanged content)
    to make room for TestTheEnqueueResponseCarriesThePrice between the
    route-level tests above and the frontend-derivation tests below."""


def _succeeded_run(reg, analysis_id, seconds, slug="any"):
    """One succeeded `runs` row whose wall time is `seconds`."""
    from datetime import datetime, timedelta, timezone

    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    run_id = reg.enqueue_run("analysis_run", {"slug": slug, "analysis_id": analysis_id})
    with reg._conn() as c:
        c.execute("UPDATE runs SET state='succeeded', started_at=%s, finished_at=%s WHERE id=%s",
                  (t0.isoformat(), (t0 + timedelta(seconds=seconds)).isoformat(), run_id))
    return run_id


def _split_activity(reg, analysis_id, steps_seconds, publish_seconds, slug="any"):
    """One terminal `analysis_run` activity_log row carrying the per-phase
    split in `detail`, the shape `execute_and_record_analysis` writes."""
    import json as _json

    from resource_explorer.activity_logger import log_analysis_run

    entry_id = log_analysis_run(reg, "repo", slug, slug, "ok",
                                f"ran {analysis_id}", analysis_id, published=None)
    detail = _json.dumps({
        "analysis_id": analysis_id, "published": None,
        "steps_seconds": steps_seconds, "publish_seconds": publish_seconds,
        "publish_mode": "not-attempted" if publish_seconds is None else "inline",
    })
    with reg._conn() as c:
        c.execute("UPDATE activity_log SET detail = %s WHERE id = %s", (detail, entry_id))
    return entry_id


class TestTheSkipNamesThePrice:
    """Designer ruling (2026-09-13): "a gate that only says 'too fresh' reads
    as an obstacle; one that names the price reads as the system being careful
    with your money." And the price must say how it was arrived at — a declared
    catalog word dressed as a measurement is the failure the whole measurement
    spec was written to prevent."""

    def test_measured_is_the_median_of_succeeded_runs_never_the_mean(self, pg_registry):
        from resource_explorer.workflows.analysis import estimate_run_cost

        # The fixture schema is shared across this module, so each test uses
        # its own analysis id rather than inheriting a neighbour's rows.
        for s in (10, 12, 14, 600):           # one 10-minute outlier
            _succeeded_run(pg_registry, "zz_median_probe", s)
        cost = estimate_run_cost(pg_registry, "zz_median_probe")
        assert cost.basis == "measured" and cost.runs == 4
        assert cost.seconds == 13.0, f"expected the median 13.0, got {cost.seconds} (mean would be 159)"
        assert cost.sentence() == "A re-run costs about 13s (median of 4 runs)."

    def test_failed_and_unfinished_runs_do_not_count(self, pg_registry):
        from resource_explorer.workflows.analysis import estimate_run_cost

        from datetime import datetime, timezone

        _succeeded_run(pg_registry, "zz_unfinished_probe", 20)
        # A failed run with timestamps, and a running one with no finished_at.
        # Neither is left `queued`: the pg schema is shared across this module
        # and a claimable row here was picked up by test_run_queue's
        # concurrent-claimers test, which then saw two winners.
        now = datetime.now(timezone.utc).isoformat()
        failed = pg_registry.enqueue_run("analysis_run", {"slug": "x", "analysis_id": "zz_unfinished_probe"})
        running = pg_registry.enqueue_run("analysis_run", {"slug": "x", "analysis_id": "zz_unfinished_probe"})
        with pg_registry._conn() as c:
            c.execute("UPDATE runs SET state='failed', started_at=%s, finished_at=%s WHERE id=%s",
                      (now, now, failed))
            c.execute("UPDATE runs SET state='running', started_at=%s WHERE id=%s", (now, running))
        cost = estimate_run_cost(pg_registry, "zz_unfinished_probe")
        assert cost.runs == 1 and cost.seconds == 20.0

    def test_declared_is_labelled_declared(self, pg_registry):
        """`dependency_support` is in the catalog and has never run here."""
        from resource_explorer.workflows.analysis import estimate_run_cost

        cost = estimate_run_cost(pg_registry, "dependency_support")
        assert cost.basis == "declared" and cost.seconds is None and cost.runs == 0
        assert cost.declared == "fast"
        assert "declared" in cost.sentence() and "not yet measured" in cost.sentence()
        assert "costs about" not in cost.sentence(), "a declared word must not read as a measurement"

    def test_unknown_says_so_rather_than_guessing(self, pg_registry):
        from resource_explorer.workflows.analysis import estimate_run_cost

        cost = estimate_run_cost(pg_registry, "no_such_analysis_zzz")
        assert cost.basis == "unknown" and cost.seconds is None
        assert "not known" in cost.sentence()

    def test_a_derived_analysis_costs_what_its_source_costs(self, pg_registry):
        """`architecture_diagram` runs the recovery's steps; with no runs of
        its own, its price is the recovery's — and `via` says so."""
        from resource_explorer.workflows.analysis import estimate_run_cost

        _succeeded_run(pg_registry, "architecture_recovery", 49)
        cost = estimate_run_cost(pg_registry, "architecture_diagram")
        assert cost.basis == "measured" and cost.seconds == 49.0
        assert cost.via == "architecture_recovery"

    def test_the_skip_payload_carries_the_price_and_its_basis(self, reg, slug, monkeypatch):
        import asyncio
        from resource_explorer.web.routes import projects

        TestTheGateIsWiredAndScopedCorrectly._route_uses(monkeypatch, reg)
        _log_run(reg, slug, "ci_quality", minutes_ago=2)
        _succeeded_run(reg, "ci_quality", 33, slug=slug)
        out = asyncio.run(projects.run_single_analysis(slug, "ci_quality"))
        assert out["status"] == "skipped"
        assert out["rerun_cost_basis"] == "measured" and out["rerun_cost_seconds"] == 33.0
        assert out["rerun_cost_runs"] == 1 and out["rerun_cost_via"] == "ci_quality"
        assert "A re-run costs about 33s" in out["detail"], (
            "the price has to be in the sentence the toast shows, not only in a field")

    def test_the_split_leads_the_sentence_when_it_exists(self, pg_registry):
        """Designer's ruling, 2026-09-13: the wall clock is always split, never
        one figure — and the sentence has to LEAD with it."""
        from resource_explorer.workflows.analysis import estimate_run_cost

        _succeeded_run(pg_registry, "zz_split_probe", 90)
        _succeeded_run(pg_registry, "zz_split_probe", 94)
        _split_activity(pg_registry, "zz_split_probe", 0.06, 92.3)
        _split_activity(pg_registry, "zz_split_probe", 0.08, 88.1)
        cost = estimate_run_cost(pg_registry, "zz_split_probe")
        assert cost.basis == "measured"
        assert cost.steps_seconds == 0.07 and cost.publish_seconds == pytest.approx(90.2)
        assert cost.split_runs == 2
        sentence = cost.sentence()
        assert sentence.startswith("A re-run takes about 0.1s to run and about"), sentence
        assert "to publish" in sentence and "in all" in sentence
        # The halves and the total must come from the same rows. Here the
        # `runs`-table median is 92s and the split sums to 90.27s: a sentence
        # that said "1m 32s in all" beside "1m 30s to publish" would carry two
        # numbers that disagree — the list-answer defect, in a toast.
        # 0.07 + 90.2: publish is >90% of the total, so the total names its
        # dominant half rather than restating "1m 30s" beside "1m 30s".
        assert "1m 30s in all, nearly all of it publishing" in sentence, sentence
        assert "about 1m 30s in all" not in sentence, sentence
        assert "1m 32s" not in sentence, sentence

    def test_a_balanced_split_keeps_the_plain_total(self, pg_registry):
        """When neither half dominates, "about X in all" is the honest total and
        naming a dominant half would be false."""
        from resource_explorer.workflows.analysis import estimate_run_cost

        _succeeded_run(pg_registry, "zz_balanced_probe", 100)
        _split_activity(pg_registry, "zz_balanced_probe", 40.0, 60.0)
        sentence = estimate_run_cost(pg_registry, "zz_balanced_probe").sentence()
        assert "about 1m 40s in all (median of 1 run)." in sentence, sentence
        assert "nearly all of it" not in sentence, sentence

    def test_no_split_rows_leaves_the_sentence_byte_identical(self, pg_registry):
        """Old runs (predating the split instrumentation) must fall back to
        exactly today's sentence — not a mangled half-split one."""
        from resource_explorer.workflows.analysis import estimate_run_cost

        _succeeded_run(pg_registry, "zz_nosplit_probe", 13)
        cost = estimate_run_cost(pg_registry, "zz_nosplit_probe")
        assert cost.steps_seconds is None and cost.publish_seconds is None
        assert cost.sentence() == "A re-run costs about 13s (median of 1 run)."

    def test_the_skip_payload_carries_the_split_fields(self, reg, slug, monkeypatch):
        import asyncio
        from resource_explorer.web.routes import projects

        TestTheGateIsWiredAndScopedCorrectly._route_uses(monkeypatch, reg)
        _log_run(reg, slug, "sla_content", minutes_ago=2)
        _succeeded_run(reg, "sla_content", 92, slug=slug)
        _split_activity(reg, "sla_content", 0.06, 92.3, slug=slug)
        out = asyncio.run(projects.run_single_analysis(slug, "sla_content"))
        assert out["status"] == "skipped"
        assert out["rerun_steps_seconds"] == 0.06
        assert out["rerun_publish_seconds"] == 92.3
        assert out["rerun_split_runs"] == 1
        assert "to run and about" in out["detail"] and "to publish" in out["detail"]

    def test_a_not_attempted_publish_does_not_poison_the_publish_median(self, pg_registry):
        """`publish_seconds=None` means "not attempted" (no assigned project, or
        no annotations) — not a zero-second publish. It must not enter the
        median as if it were a fast one, and it must not vanish from
        `split_runs` either: the row is still a real, instrumented run."""
        from resource_explorer.workflows.analysis import estimate_run_cost

        _succeeded_run(pg_registry, "zz_notattempted_probe", 10)
        _succeeded_run(pg_registry, "zz_notattempted_probe", 12)
        _succeeded_run(pg_registry, "zz_notattempted_probe", 14)
        _split_activity(pg_registry, "zz_notattempted_probe", 0.1, 40.0)
        _split_activity(pg_registry, "zz_notattempted_probe", 0.1, 60.0)
        _split_activity(pg_registry, "zz_notattempted_probe", 0.1, None)  # not-attempted
        cost = estimate_run_cost(pg_registry, "zz_notattempted_probe")
        assert cost.split_runs == 3, "the not-attempted row is a real instrumented run"
        assert cost.publish_seconds == 50.0, (
            f"the None publish row poisoned the median: got {cost.publish_seconds}")
        assert cost.steps_seconds == 0.1

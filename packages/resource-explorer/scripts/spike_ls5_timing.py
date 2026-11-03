"""LS5 spike: native analyses against their Analytic Library twins, on one database in the live registry.

    python packages/resource-explorer/scripts/spike_ls5_timing.py SLUG [--runs 3] [--json OUT]

Runs on main without the twins. The twins (`re_statistics_freshness`, `re_value_profile`) and the `RE_SPIKE_LIBRARY_TWINS`
flag live only on the closed branch `re/spike-analyses-as-library-steps`; where a twin is not installed this times the native
side alone and says "twin not installed" in place of every twin figure (never a blank or a zero). With the twins installed
(`uv pip install -e` the two example packages, on that branch) it also runs them, which writes only what a library step run
writes (its step results, an ordinary step run). Run it on a quiet machine (load average under 15, no gate running): every
figure here is a wall-clock time.

Measures (the brief's table): per-run wall time native vs twin for both analyses; SQL statements each issues; result
equivalence (the numbers); the Value profile screen with the flag off and on (the publish table is not timed here).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from types import SimpleNamespace


def median_ms(fn, runs: int) -> tuple[float, list[float]]:
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        times.append(round((time.perf_counter() - t) * 1000, 1))
    return statistics.median(times), times


NOT_COUNTED = "not counted: the registry's raw connections bypass the engine hook"


class Counter:
    """SELECT statements the registry's own engine issues while it is on. Reads through the registry's raw connections
    (`_conn`) never reach this hook, so a count of 0 means "not counted", and `shown` says so rather than printing 0."""

    def __init__(self, registry):
        from sqlalchemy import event

        self.n, self.sqls = 0, []
        self._engine, self._event = registry.engine, event
        event.listen(registry.engine, "before_cursor_execute", self._hit)

    def _hit(self, conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            self.n += 1
            self.sqls.append(statement[:90])

    @property
    def shown(self):
        return self.n or NOT_COUNTED

    def stop(self):
        self._event.remove(self._engine, "before_cursor_execute", self._hit)


def twin_reads(fn) -> int:
    """Statements the step's own read-only connection ran during `fn` (the typed reads)."""
    from resource_explorer.surveyors import survey_step_host as h

    seen = [0]
    real = h._execute_read_only

    def counting(conn, is_pg, sql, params):
        seen[0] += 1
        return real(conn, is_pg, sql, params)

    h._execute_read_only = counting
    try:
        fn()
    finally:
        h._execute_read_only = real
    return seen[0]


NOT_INSTALLED = "twin not installed"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--json")
    args = ap.parse_args()
    from resource_explorer import value_profile_screen
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import survey_step_host as host
    from resource_explorer.surveyors.database import db_derived
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        _db_derived_field_reader, _value_profile_results)

    try:
        from resource_explorer import spike_twins
    except ImportError:
        spike_twins = None
    reg, slug, runs = ProjectRegistry(), args.slug, args.runs
    entity = SimpleNamespace(slug=slug)
    if spike_twins:
        os.environ.pop(spike_twins.FLAG, None)
    steps = host.discover_steps("database")
    has = {n: spike_twins is not None and n in steps for n in ("re_statistics_freshness", "re_value_profile")}
    if any(has.values()):
        host.refresh("database")
    out: dict = {"slug": slug, "runs": runs, "load_average": os.getloadavg()[0]}

    def twin(name):
        return host.SurveyStepHost(steps[name], "database", {"package": f"trellis-step-{name.replace('_', '-')}"})

    # ── statistics freshness ───────────────────────────────────────────────────────────────────────────────────
    reader = _db_derived_field_reader("db_statistics_freshness")
    isolated = []
    real = db_derived.derive_statistics_freshness

    def timed(inputs):
        t = time.perf_counter()
        try:
            return real(inputs)
        finally:
            isolated.append(round((time.perf_counter() - t) * 1000, 1))

    db_derived.derive_statistics_freshness = timed
    try:
        native_ms, native_all = median_ms(lambda: reader(reg, slug), runs)
    finally:
        db_derived.derive_statistics_freshness = real
    c = Counter(reg)
    native_value = reader(reg, slug)
    c.stop()
    fresh = {"native_reader_ms": native_ms, "native_reader_all": native_all,
             "native_function_only_ms": statistics.median(isolated) if isolated else None,
             "native_selects_for_whole_derived_pass": c.shown,
             "native_counts": native_value.get("counts") or {}, "tables": native_value.get("table_count")}
    if has["re_statistics_freshness"]:
        twin_ms, twin_all = median_ms(lambda: twin("re_statistics_freshness")(entity, reg), runs)
        last = {}
        reads = twin_reads(lambda: last.update(twin("re_statistics_freshness")(entity, reg)))
        summary = next((a for a in last["step_annotations"] if a["scope"] == slug), {})
        fresh.update({"twin_run_ms": twin_ms, "twin_run_all": twin_all, "twin_timing": last.get("timing"),
                      "twin_typed_reads": reads, "twin_summary": summary.get("resourceProperties"),
                      "twin_results": last.get("annotation_count")})
    else:
        fresh.update({k: NOT_INSTALLED for k in ("twin_run_ms", "twin_timing", "twin_typed_reads", "twin_summary")})
    out["statistics_freshness"] = fresh

    # ── value profile ──────────────────────────────────────────────────────────────────────────────────────────
    native_ms, native_all = median_ms(lambda: _value_profile_results(reg, slug), runs)
    c = Counter(reg)
    native_vp = _value_profile_results(reg, slug)
    c.stop()
    vp = {"native_reader_ms": native_ms, "native_reader_all": native_all, "native_selects": c.shown,
          "native": {k: native_vp.get(k) for k in ("column_count", "measured_count", "state_counts")}}
    if has["re_value_profile"]:
        twin_ms, twin_all = median_ms(lambda: twin("re_value_profile")(entity, reg), runs)
        last = {}
        reads = twin_reads(lambda: last.update(twin("re_value_profile")(entity, reg)))
        readback_ms, readback_all = median_ms(lambda: spike_twins.value_profile_from_twin(reg, slug), runs)
        twin_vp = spike_twins.value_profile_from_twin(reg, slug)
        vp.update({"twin_run_ms": twin_ms, "twin_run_all": twin_all, "twin_timing": last.get("timing"),
                   "twin_readback_ms": readback_ms, "twin_typed_reads": reads,
                   "twin": {k: twin_vp.get(k) for k in ("column_count", "measured_count", "state_counts")},
                   "tables_stored": last.get("annotation_count", 0) - 1})
    else:
        vp.update({k: NOT_INSTALLED for k in ("twin_run_ms", "twin_timing", "twin_readback_ms", "twin_typed_reads",
                                              "twin")})
    out["value_profile"] = vp

    # ── the Value profile screen, and the flag (twins only) ────────────────────────────────────────────────────
    t = time.perf_counter()
    summary_screen = value_profile_screen.build_summary(reg, slug)
    screens: dict = {"off": {"value_profile_header": summary_screen.get("header"),
                             "value_profile_tables": len(summary_screen.get("tables") or []),
                             "value_profile_ms": round((time.perf_counter() - t) * 1000, 1)}}
    if spike_twins and has["re_value_profile"]:
        os.environ[spike_twins.FLAG] = "1"
        try:
            t = time.perf_counter()
            on = value_profile_screen.build_summary(reg, slug)
            screens["on"] = {"value_profile_header": on.get("header"), "value_profile_tables": len(on.get("tables") or []),
                             "value_profile_ms": round((time.perf_counter() - t) * 1000, 1)}
        finally:
            os.environ.pop(spike_twins.FLAG, None)
    else:
        screens["on"] = NOT_INSTALLED
    out["screens"] = screens
    text = json.dumps(out, indent=2, default=str)
    print(text)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)


if __name__ == "__main__":
    main()

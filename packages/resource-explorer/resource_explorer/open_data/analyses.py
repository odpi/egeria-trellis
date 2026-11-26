"""Per-dataset open data analyses on the queue path (TR-54 item B, step B1).

An open data analysis (od_licence_usable, od_cadence_kept, od_profile ...) is declared in configdata/analysis_catalog.yaml
(`open_data_analyses`) and runs ONE DATASET at a time, as an `open_data_analysis_run` row on the run queue (never inline in a
request): one stored result per (dataset slug, analysis_id), an Activity entry, no publish. This module holds the runner
registry that B2-B4 fill, the refusals in words, and the price line. It has no analysis logic of its own.

A runner is `run(ctx: RunContext) -> OpenDataResult` and states how many portal calls it makes (`calls(dataset)`), so the
price is shown before the press. A declared analysis with no runner is "declared but has no reader yet": never run, never
"ok", never priced as 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

#: What a stored result may say. `not_measured` is never 0, "none" or "usable".
RESULT_STATES = ("measured", "not_measured", "not_permitted", "not_applicable", "nothing_found")


class RunnerFailed(RuntimeError):
    """The run could not do its work (a portal did not answer). It ends the run's activity in error with these words, stores NO
    result, so the dataset keeps the values and the earlier result it had, and the words say so. Never read as "found nothing"."""


@dataclass
class OpenDataResult:
    state: str                                   # one of RESULT_STATES
    summary: str                                 # one line, for the Activity entry
    measures: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    calls_made: int | None = None                # portal calls actually made (None: not counted)


@dataclass
class RunContext:
    registry: Any
    store: Any
    dataset: dict
    requested_by: str = ""
    transport_factory: Callable[[], Any] | None = None

    def transport(self):
        """The portal transport, built only when a runner asks for it (a runner that calls no portal never builds one)."""
        if self.transport_factory is not None:
            return self.transport_factory()
        from resource_explorer.open_data.transport import HttpTransport
        return HttpTransport()


@dataclass(frozen=True)
class Runner:
    analysis_id: str
    run: Callable[[RunContext], OpenDataResult]
    calls: Callable[[dict], int]                 # portal calls one run on this dataset makes


RUNNERS: dict[str, Runner] = {}


def register(analysis_id: str, run: Callable[[RunContext], OpenDataResult],
             calls: Callable[[dict], int] = lambda dataset: 0) -> None:
    RUNNERS[analysis_id] = Runner(analysis_id, run, calls)


#: Analyses RE ships a runner for: id -> the module that holds its `RUNNER`. Loaded when first asked for, so a worker that never
#: runs one never imports it. A runner registered with `register` (a test's stand-in) wins.
BUILT_IN = {"od_licence_usable": "resource_explorer.open_data.licence_usable",
            "od_cadence_kept": "resource_explorer.open_data.cadence_kept",
            # TR-54 B4: a work-list press (scout, profile, refresh) is one queued run per dataset of these; od_profile is the profile run
            # reached from one dataset (the same runner under its own id)
            "external_data_scout": "resource_explorer.open_data.queued:SCOUT",
            "external_data_refresh": "resource_explorer.open_data.queued:REFRESH",
            "external_data_profile": "resource_explorer.open_data.queued:PROFILE",
            "od_profile": "resource_explorer.open_data.queued:PROFILE"}


def runner_for(analysis_id: str) -> Runner | None:
    if analysis_id in RUNNERS:
        return RUNNERS[analysis_id]
    module = BUILT_IN.get(analysis_id)
    if module is None:
        return None
    import importlib

    module, _, attribute = module.partition(":")
    return getattr(importlib.import_module(module), attribute or "RUNNER")


def _declared() -> dict[str, Any]:
    from resource_explorer.surveyors import analysis_catalog_reader

    return {e.id: e for e in analysis_catalog_reader._load()["open_data"]}


def refusal(analysis_id: str) -> str | None:
    """Why this analysis cannot run on a dataset, in words; None when it can."""
    declared = _declared()
    if analysis_id not in declared:
        return (f"'{analysis_id}' is not an open data analysis: the ones declared for a dataset are "
                f"{', '.join(sorted(declared))}.")
    if runner_for(analysis_id) is not None:
        return None
    if getattr(declared[analysis_id], "availability", "") == "inline":
        return (f"'{analysis_id}' runs from a work list press today, one call per dataset in the list; it is not a per-dataset "
                f"run yet.")
    return f"'{analysis_id}' is declared but has no reader yet, so it cannot be run."


def price(analysis_id: str, dataset: dict) -> dict:
    """The price line shown BEFORE the press: the portal calls one run makes on this dataset. Not priced (never 0) when the
    analysis has no reader yet."""
    runner = runner_for(analysis_id)
    if runner is None:
        return {"analysis_id": analysis_id, "priced": False, "calls": None,
                "line": f"not priced · {analysis_id} has no reader yet"}
    n = int(runner.calls(dataset))
    line = (f"1 dataset × {n} call{'s' if n != 1 else ''} = {n} call{'s' if n != 1 else ''}" if n
            else "1 dataset · no portal call")
    return {"analysis_id": analysis_id, "priced": True, "calls": n, "line": line}


def annotation_record(analysis_id: str, dataset: dict, result: OpenDataResult, measured_at: str) -> dict:
    """The one stored result of a run, in `step_annotations`' shape (a StepAnnotation record)."""
    if result.state not in RESULT_STATES:
        raise ValueError(f"{analysis_id}: result state {result.state!r} is not one of {', '.join(RESULT_STATES)}")
    return {
        "annotation_type": analysis_id, "scope": "dataset", "summary": result.summary, "explanation": result.explanation,
        "resourceProperties": {"dataset": dataset["slug"], "title": dataset.get("title")},
        "additionalProperties": {"resultState": result.state, "measures": dict(result.measures),
                                 **({"calls_made": result.calls_made} if result.calls_made is not None else {}),
                                 "measuredAt": measured_at},
    }

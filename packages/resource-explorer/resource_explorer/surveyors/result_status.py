"""What a results card should SAY when it has no rows to show.

Every render mode in index.html falls back to one string:

    "No results yet — click Run to scan."

It is shown for three different situations, and it asserts the one that is
often false. Measured across the corpus 2026-08-24, the persisted outcomes say:

  * data_profile            36 no_signal   — scanned, provably no data files
  * manifest_parse_deps      9 no_signal, 4 unverified
  * manifest_parse_ci       11 unverified  — no GitHub Actions workflows to read
  * website_ingestion        3 no_signal

So 36 cards invite a user to "click Run to scan" a repo that has already been
scanned and has nothing to profile, and 15 more invite a re-run that will
produce the same non-answer, because the missing prerequisite is a different
step. Telling someone to repeat an action that cannot help is worse than saying
nothing: it spends their time to reach the same screen.

`step_outcome.py` already draws the distinction the run needs; this draws the
one the *reader* needs, which is not quite the same. A run cares whether its
zero is provable. A reader cares what to do next — and "nothing to do, this is
the answer" and "do something else first" are the two ends of that.

    recovered / partial / regression -> measured          the card has content
    no_signal                        -> nothing_found     a real, final answer
    unverified                       -> not_established   a prerequisite is missing
    (no persisted run at all)        -> never_run         the original message, now
                                                          only shown when it is true
"""
from __future__ import annotations

import json
from typing import Any

MEASURED = "measured"
NOTHING_FOUND = "nothing_found"
NOT_ESTABLISHED = "not_established"
NEVER_RUN = "never_run"
#: The analysis ran (or reads live-ingested data), but no `results_reader` is
#: registered for it at all -- distinct from `NOTHING_FOUND` (a reader ran and
#: genuinely measured a zero). Conflating the two was found live 2026-09-25
#: (REVIEW-SURVEY-PANE-285.md): `facts.py::_read_results` returned `{}`
#: whether a reader existed and found nothing, or no reader existed to call,
#: so "is this database alive" and "primary or replica" rendered "ran and
#: found nothing -- a measured zero" for db_activity_signals/db_resilience,
#: which is a real gap in the answering machinery, not a real zero. Excluded
#: from `Fact.is_known` on purpose: withholding the checkmark is the point --
#: a reader that does not exist has not measured anything, known or not.
NO_READER = "no_reader"
#: A third completeness state alongside `measured`/`not_established`
#: (REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §4, replying to
#: ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md #251): the surveyor DID
#: run and DID produce a real count, but the credential it connected as
#: cannot see the whole resource — Postgres's `information_schema` is
#: privilege-filtered while `pg_namespace`/`pg_class` are not, so a
#: `credential_capability` probe can state the exact fraction (e.g. "3 of 26
#: tables, 6 of 8 schemas"). Rendering that count as a bare `measured` value
#: ("3 tables") is the confident-wrong-answer shape find-absence-as-answer
#: exists to catch — this state carries the fraction so the caller (facts.py)
#: can say so instead of presenting a partial view as the whole one.
#:
#: Deliberately its own state rather than `PARTIAL` (facts.py): `PARTIAL`
#: means "this run covered only part of what the analysis owns" — a gap in
#: WHAT WAS ASKED. This means "what was asked was fully attempted, but the
#: credential's own visibility bounds what could be seen" — a gap in WHAT
#: COULD BE SEEN. Collapsing the two would lose the fraction, which is the
#: whole point (the "blind spot has a known denominator" per the reply's §0).
MEASURED_WITHIN_CREDENTIAL_SCOPE = "measured_within_credential_scope"
#: A gate decided this step would have been the wrong question for this
#: resource, so it never ran. Added 2026-08-24 for repo_classification's
#: run/skip gate (docs/architecture-recovery-design.md §5.5b).
#:
#: Deliberately NOT a sixth label in step_outcome.py, and that is the whole
#: point: those five describe what a run ACHIEVED, and a skip is the absence of
#: a run. `unverified` would be actively wrong for it — that means "we tried and
#: could not tell", where a skip means "we chose not to try, and that choice was
#: correct". A funnel's skip is its biggest win; rendering it as a degraded
#: state would punish the thing working as designed.
#:
#: It belongs here instead because this is the reader-facing layer, and a reader
#: does need to be told — otherwise a skipped step is indistinguishable from a
#: step that failed to produce anything.
SKIPPED_BY_DESIGN = "skipped_by_design"
#: The analysis found the right material and structured it wrongly. Added
#: 2026-08-24 with the architecture-recovery session, from a real case: on
#: Kubernetes, component detection scored 0 of 6 while covering 2,002 of 2,132
#: files — every component present, all six collapsed under one overclaiming
#: node.
#:
#: **Still has no emitter, and 2026-08-26 measurement says that is correct
#: rather than an omission.** Now that projection has a real hierarchy to work
#: with (README finding 117), the shape is detectable without ground truth — a
#: few roots holding everything — and across all 28 repos with eight or more
#: components it occurs **zero times**. The one repo that concentrates
#: (`haystack_opea`, 10 components under 2 roots) is eight third-party
#: dependencies grouped correctly, and the three flat ones (`docling_java`,
#: `sqlglot`, `ryoma`) are genuinely flat sets of top-level modules.
#:
#: The originating case needed GROUND TRUTH to recognise: "0 of 6" is a score
#: against a known answer, and the product path has no known answer. So the
#: honest position is that this state belongs to the spike's scorer, and an
#: emitter in the product would be inventing a detector for a condition nobody
#: has observed here. Kept rather than deleted because the renderer branches on
#: it and deleting a state other code reads is a change to make deliberately,
#: not in passing — flagged to the presentation session as theirs to decide.
#:
#: This is NOT a weaker `measured` and NOT a stronger `nothing_found`. It is a
#: different KIND of answer: a merge failure, not a detection failure. Rendered
#: as the same empty state as "found nothing" it cost that session an afternoon
#: chasing the wrong bug, which is the entire argument for the state existing.
#:
#: It deliberately carries no number of its own. A coverage figure belongs in
#: the reason text as evidence, never as a field — a percentage on a state is a
#: partial-credit score in everything but name, and
#: docs/architecture-recovery-design.md §5.5a(c) rules those out because they
#: punish deliberate choices. See tests/test_expectations.py, which asserts no
#: field name contains score/grade/rating/maturity/percentage/count.
MISGROUPED = "misgrouped"

_OUTCOME_TO_STATE = {
    "recovered": MEASURED,
    "partial": MEASURED,
    "regression": MEASURED,
    "no_signal": NOTHING_FOUND,
    "unverified": NOT_ESTABLISHED,
}

#: cause -> what the reader should do about it. Only causes that have an
#: actionable answer appear here; anything else falls back to the generic text,
#: which is honest rather than inventing a remedy.
_CAUSE_HINTS = {
    "empty_file_inventory": "Run Coarse Profile first — it builds the file inventory this reads.",
    "empty_code_symbols": "Run Code Symbol Extraction first — no symbols have been extracted yet.",
    "no_dependency_manifest": "This repo ships no recognised dependency manifest.",
    "manifest_present_no_deps_parsed":
        "A manifest is present but could not be parsed — see docs/Backlog.md "
        "(DependencyParser covers Python/Node/Go/Maven, not Gradle or Cargo).",
    "manifests_present_no_deps_extracted":
        "A manifest is present but no dependencies were extracted. Run Coarse Profile.",
    "no_github_actions_workflows_found":
        "No .github/workflows content to read. CI running elsewhere (Travis, Jenkins, "
        "CircleCI) is not visible to this check.",
    "no_homepage": "Run Homepage Discovery first — this needs the project's site URL.",
    "self_published": "The project publishes its own site; nothing to ingest separately.",
    "code_host": "The declared homepage points back at the code host, not a docs site.",
    "no_data_files": "Scanned — this repo contains no CSV/Parquet/XLSX data files.",
    "no_hygiene_files": "Scanned — none of the recognised hygiene files are present.",
    "no_files_in_scope": "The scope locator matched none of this resource's files.",
    "no_symbols_in_scope": "The scope locator matched none of the extracted symbols.",
}


def _as_dict(detail: Any) -> dict:
    if isinstance(detail, str):
        try:
            return json.loads(detail)
        except Exception:
            return {}
    return detail if isinstance(detail, dict) else {}


def skipped(reason: str, *, gate: str = "") -> dict:
    """The status for a step a gate deliberately declined to run.

    Takes the reason as a required argument: a skip with no stated reason is
    indistinguishable from a failure on the screen, which is exactly what this
    state exists to prevent.
    """
    return {
        "state": SKIPPED_BY_DESIGN,
        "outcome": "",
        "cause": gate,
        "hint": reason,
        "known_positive": False,
    }


def dependency_not_satisfied(reason: str, *, depends_on: str = "") -> dict:
    """A step ran, and could not establish a result because the step it consumes
    has not produced one.

    Deliberately **`not_established`, not a seventh state.** A summarising
    microflow's input being absent is a real, nameable situation, and the
    temptation is to add `dependency_missing` alongside the six. It would be
    wrong: the six describe what a reader is looking at, and what the reader is
    looking at here is genuinely "we tried and could not tell". Which *upstream*
    step was missing is a cause, and `cause` is the field for it.

    `reason` is required for the same purpose as in `skipped()` and
    `misgrouped()`: unexplained, this is indistinguishable from a summary of
    nothing — and a summary of nothing is worse than most absences, because it
    reads as a confident answer.
    """
    return {
        "state": NOT_ESTABLISHED,
        "outcome": "",
        "cause": depends_on,
        "hint": reason,
        "known_positive": False,
    }


def misgrouped(reason: str) -> dict:
    """The right material, the wrong structure.

    Takes the reason as a required argument for the same purpose `skipped()`
    does: unexplained, this renders indistinguishably from "found nothing",
    which is the confusion it exists to end. Put the evidence in the prose —
    "2,002 of 2,132 files matched, but all six components landed under a single
    node" — rather than reaching for a coverage field.
    """
    return {
        "state": MISGROUPED,
        "outcome": "",
        "cause": "",
        "hint": reason,
        "known_positive": True,
    }


def status_from_detail(detail: Any) -> dict | None:
    """The status envelope for one persisted row's detail blob, or None when it
    carries no outcome (a kind that has not adopted the vocabulary yet — those
    keep today's behaviour rather than being given a guessed status)."""
    d = _as_dict(detail)
    outcome = d.get("outcome")
    if not outcome:
        return None
    cause = d.get("outcome_cause") or ""
    return {
        "state": _OUTCOME_TO_STATE.get(outcome, NOT_ESTABLISHED),
        "outcome": outcome,
        "cause": cause,
        "hint": _CAUSE_HINTS.get(cause, ""),
        "known_positive": bool(d.get("outcome_known_positive")),
    }


def attach(result: dict, detail: Any) -> dict:
    """Add `_status` to a results dict, in place, when a status is derivable.

    Additive on purpose: every existing top-level key keeps its meaning and its
    consumers, so a render mode that ignores `_status` behaves exactly as before.
    """
    status = status_from_detail(detail)
    if status:
        result["_status"] = status
    return result

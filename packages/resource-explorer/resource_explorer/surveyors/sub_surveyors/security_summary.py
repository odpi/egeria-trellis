"""Sub-surveyor: the security topic summary — a REDUCER, not a measurement.

This step measures nothing about the repository. It reads what the other
security steps already wrote and says one thing about the topic, which is the
shape `docs/survey-composition-and-topic-summary-design.md` calls a reducer.
`foss_scorecard` was already one; this is the first declared as such.

**Three properties it exists to preserve, none of which a summary gets for free.**

*Which moment is being summarised.* Findings are read at `MAX(surveyed_at)` per
kind, independently, so this composes the latest sighting of each input and those
can be days apart. It is a **state** summary, not a summary of one run — so it
carries `oldest_input_at`, the age of the stalest thing it relied on, rather than
only the time it was computed. A confident sentence stamped today, resting on a
month-old CVE scan, is exactly the staleness `cii_badge` refuses to launder for
badge levels.

*Absence is not a finding.* An input that never ran and an input that ran and
found nothing are different facts, and averaging them into a score is how a
survey of four missing steps reports a clean bill of health. Coverage is reported
alongside the verdict, and a verdict is refused outright below a floor.

*Precedence is declared, not accidental.* Two inputs answer the security-policy
question at different strengths: `security_scan` says the file exists,
`repo_conventions` says it exists **and** contains a disclosure process. The
second subsumes the first. The stronger wins and the weaker is retained as
corroboration rather than dropped — and it is written down here rather than
falling out of the order a loop happens to iterate in, which is what
`foss_scorecard` does today.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "SecuritySummary"

#: The kinds this reduces over. Named explicitly rather than derived from
#: `family="security"`: a family is a UI grouping and can gain a member for
#: presentational reasons, which would silently change what a published summary
#: means.
#: NOTE the first entry. The *analysis id* is `security_scan`; the **findings
#: kind it writes** is `security_hygiene` — they diverged when SecuritySurveyor
#: was renamed SecurityHygieneSurveyor and the id was deliberately left alone
#: because it keys schedules and stored data. Using the analysis id here read as
#: "security_scan never ran" for every repo on the first run of this reducer:
#: a typo in this tuple is indistinguishable from an un-run input, which is the
#: precise failure this step exists to prevent, reproduced inside it.
#: `test_security_summary.py` pins every entry against the kinds actually
#: written, so the next divergence fails a test instead of quietly widening a
#: gap in the summary.
INPUT_KINDS = (
    "security_hygiene",
    "security_features",
    "ci_quality",
    "license_classification",
    "repo_conventions",
    "foss_scorecard",
    "cii_badge",
    "cve_scan",
)

#: Below this many inputs present, no verdict is issued at all. Three of eight is
#: not a security picture, and a summary that grades it anyway is worse than
#: silence because it looks like an answer.
MIN_INPUTS_FOR_VERDICT = 4

#: (weaker, stronger) pairs answering the same question at different strengths.
#: The stronger wins; the weaker is kept as corroboration.
PRECEDENCE = (
    # Findings KINDS, not analysis ids — see INPUT_KINDS above. This pair had
    # the same `security_scan` mistake and it failed differently: rather than a
    # visible "never ran", it silently produced zero supersessions, so the
    # summary looked like it had found no overlapping checks when it had simply
    # been looking for a kind nobody writes. A wrong name here is invisible.
    (("security_hygiene", "security_policy"),
     ("repo_conventions", "security_policy_content")),
)

#: Labels that mean "this input reports a problem". Deliberately explicit: a
#: label this does not know is counted as neither good nor bad, not as good.
_BAD = {"gap", "fail", "missing", "absent", "stale", "not_registered"}
_GOOD = {"pass", "present", "ok", "current", "complete", "yes"}


def _age_days(iso: str) -> int | None:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days


def gather(registry, slug: str) -> dict:
    """{kind: [findings]} for inputs that ran, and the list that did not.

    An empty list from `query_findings` means "this kind has no stored findings",
    which is *never ran* — not "ran and found nothing clean". The two are kept
    apart from here down.
    """
    present, missing = {}, []
    for kind in INPUT_KINDS:
        rows = registry.query_findings(slug, kind) or []
        if rows:
            present[kind] = rows
        else:
            missing.append(kind)
    return {"present": present, "missing": missing}


def apply_precedence(present: dict) -> list[dict]:
    """Supersessions actually in effect for this resource.

    Only reported when BOTH sides ran: a stronger check that never ran does not
    supersede anything, and saying it did would credit the summary with evidence
    it does not have.
    """
    out = []
    for (weak_kind, weak_check), (strong_kind, strong_check) in PRECEDENCE:
        if weak_kind not in present or strong_kind not in present:
            continue
        weak = next((r for r in present[weak_kind] if r["check_name"] == weak_check), None)
        strong = next((r for r in present[strong_kind] if r["check_name"] == strong_check), None)
        if not weak or not strong:
            continue
        out.append({
            "question": strong_check,
            "superseded": f"{weak_kind}:{weak_check}",
            "by": f"{strong_kind}:{strong_check}",
            "weaker_label": weak["label"],
            "stronger_label": strong["label"],
            "agree": (weak["label"] in _GOOD) == (strong["label"] in _GOOD),
        })
    return out


def _counts(present: dict, superseded_keys: set) -> dict:
    good = bad = unknown = 0
    for kind, rows in present.items():
        for r in rows:
            if f"{kind}:{r['check_name']}" in superseded_keys:
                continue
            label = (r.get("label") or "").lower()
            if label in _BAD:
                bad += 1
            elif label in _GOOD:
                good += 1
            else:
                unknown += 1
    return {"good": good, "bad": bad, "unknown": unknown}


def summarise(gathered: dict) -> dict:
    """The topic summary, or an explicit refusal to give one."""
    present, missing = gathered["present"], gathered["missing"]
    covered = len(present)
    total = len(INPUT_KINDS)

    stamps = [r["surveyed_at"] for rows in present.values() for r in rows if r.get("surveyed_at")]
    oldest = min(stamps) if stamps else ""
    oldest_age = _age_days(oldest) if oldest else None

    supersessions = apply_precedence(present)
    superseded_keys = {s["superseded"] for s in supersessions}
    counts = _counts(present, superseded_keys)

    base = {
        "covered": covered, "total": total, "missing": missing,
        "counts": counts, "supersessions": supersessions,
        "oldest_input_at": oldest, "oldest_input_age_days": oldest_age,
        "is_state_summary": True,
    }

    if covered < MIN_INPUTS_FOR_VERDICT:
        return {
            **base,
            "label": "",
            "known": False,
            "summary": (
                f"No security summary: only {covered} of {total} inputs have run "
                f"({', '.join(missing)} have not). Below {MIN_INPUTS_FOR_VERDICT} "
                "this would be a verdict on absence rather than on the repository."
            ),
        }

    label = "concerns" if counts["bad"] else ("clean" if counts["good"] else "inconclusive")
    age_note = (f" Oldest contributing result is {oldest_age} day(s) old."
                if oldest_age is not None else
                " The age of the contributing results could not be read.")
    gap_note = (f" {len(missing)} input(s) never ran: {', '.join(missing)}."
                if missing else " Every input has run.")
    sup_note = ""
    if supersessions:
        s = supersessions[0]
        sup_note = (f" Where two checks answer the same question, the stronger is "
                    f"used: {s['by']} over {s['superseded']}"
                    f"{'' if s['agree'] else ' — and they disagree'}.")

    return {
        **base,
        "label": label,
        "known": True,
        "summary": (
            f"Security: {label} — {counts['bad']} finding(s) of concern, "
            f"{counts['good']} clear, {counts['unknown']} not established, "
            f"across {covered} of {total} inputs.{gap_note}{age_note}{sup_note}"
        ),
    }


def findings_for(summary: dict) -> list[dict]:
    """Rows for the findings table. Three, kept apart on purpose."""
    return [
        {"check_name": "security_posture", "label": summary["label"],
         "summary": summary["summary"],
         "confidence": 100 if summary["known"] else 0,
         "detail": {"known": summary["known"], "counts": summary["counts"],
                    "is_state_summary": True}},
        {"check_name": "input_coverage",
         "label": "complete" if not summary["missing"] else "partial",
         "summary": (f"{summary['covered']} of {summary['total']} security inputs have run."
                     + (f" Never ran: {', '.join(summary['missing'])}." if summary["missing"] else "")),
         "confidence": 100,
         "detail": {"known": True, "covered": summary["covered"],
                    "total": summary["total"], "missing": summary["missing"]}},
        {"check_name": "summary_freshness",
         "label": ("current" if (summary["oldest_input_age_days"] or 0) <= 30
                   else "stale") if summary["oldest_input_age_days"] is not None else "",
         "summary": (
             f"The oldest result this summary rests on is "
             f"{summary['oldest_input_age_days']} day(s) old. This summarises the "
             "latest sighting of each input, not one survey run, so its own "
             "timestamp is newer than some of its evidence."
             if summary["oldest_input_age_days"] is not None
             else "The age of the contributing results could not be read."),
         "confidence": 100 if summary["oldest_input_age_days"] is not None else 0,
         "detail": {"known": summary["oldest_input_age_days"] is not None,
                    "oldest_input_at": summary["oldest_input_at"],
                    "age_days": summary["oldest_input_age_days"]}},
    ]


class SecuritySummarySurveyor(BaseSurveyor):
    """Reduces the security family's stored findings to one topic summary."""

    def __init__(self, project: Project, registry: ProjectRegistry,
                 surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            summary = summarise(gather(self.registry, slug))
            findings = findings_for(summary)
            # supersedes_previous=True: security family (docs/Backlog.md),
            # this one call is the whole run's answer for
            # (slug, "security_summary", "").
            self.registry.upsert_finding(slug, "security_summary", findings,
                                         surveyed_at=self._surveyed_at,
                                         supersedes_previous=True)
            out.append(ClassificationAnnotation(
                summary=summary["summary"],
                analysis_step=STEP,
                check_name="security_posture",
                candidate_classifications=[summary["label"]] if summary["label"] else [],
                confidence=100 if summary["known"] else 0,
                json_properties={
                    **{f["check_name"]: f["label"] for f in findings},
                    # A reducer over other steps' output, so its zero is always
                    # about what those steps left rather than about the repo:
                    # `known` is False when the inputs were not there, and no
                    # known-positive can exist for a summary of nothing.
                    # `missing` is the step's own record of a partial reduction
                    # and it already labels itself "partial" in findings_for().
                    **(StepOutcome("unverified",
                                   cause="no security inputs available to summarise")
                       if not summary["known"] else
                       StepOutcome("partial",
                                   cause="some contributing checks were missing",
                                   detail={"missing": summary["missing"]})
                       if summary.get("missing") else
                       StepOutcome("recovered")).as_row(),
                },
            ))
        except Exception as exc:
            log.exception("SecuritySummarySurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out

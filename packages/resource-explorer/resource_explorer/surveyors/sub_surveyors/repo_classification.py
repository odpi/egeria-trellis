"""Sub-surveyor: what the repo *represents*, and where its expected artifacts are.

Design reference: `docs/architecture-recovery-design.md` §5.5b. This wires the
three library modules built for it into a runnable survey step:

    github/repo_role.py      -> what kind of thing is this repo (7 roles, ranked)
    github/doc_locations.py  -> where does each artifact actually live
    github/expectations.py   -> what should a project of this kind have, + the gate

**Why this is Discovery-tier and not Assessment.** It answers "is the expensive
work worth doing at all", which is the funnel's own question (CLAUDE.md rule 17).
Its most valuable output is a *negative*: on a tutorial or samples repo with no
structural evidence, architecture recovery should not run at all. §5.5b is
explicit that this is a **gate, not a weighting** — the cost saved is the whole
tier, not a filter applied after the expensive work already happened.

**The gate keys on containment, not on the primary role.** `odpi/egeria-workspaces`
ranks `tutorial` primary — its README says "designed for learning", and it holds
37 notebooks — *and* it is architecture-recovery target T1, which scored 18/27.
A gate reading the primary role would skip the repo we have recovered most
successfully. See `expectations.recovery_gate`.

**No score is emitted, deliberately.** §5.5a(c)/§5.5b: a checklist becomes a
maturity score, and a maturity score punishes deliberate choices — a small stable
library documents lightly on purpose. This step reports located artifacts, absent
ones, and *confirmations* (absences that support the role, e.g. a library having
no deployment artifacts, which `trellis.md` records verbatim). It emits no count,
percentage or grade.

**Resolved: a skip is not a sixth outcome.** This module originally recorded the
gate as a bare finding because none of `step_outcome.py`'s five labels fits "we
deliberately did not run because it would have been the wrong question" —
`no_signal` requires `known_positive=True` and nothing ran, and `unverified`
means *could not* run when in fact we could and chose not to.

The owner of that module resolved it one layer up rather than by adding a label,
and the reasoning is worth keeping: the five describe what a run **achieved**,
and a skip is the absence of a run, so adding it would have weakened the other
five. `surveyors/result_status.py` carries a reader-facing vocabulary instead —
a run asks *"is my zero provable?"*, a reader asks *"what should I do about
it?"*, and those group differently. `rs.skipped(reason, gate=...)` takes the
reason as a **required** argument, because a skip with no stated reason renders
identically to a failure. We pass `recovery_gate`'s own reason string through
verbatim: the whole value of the design is that the card can say *why* this
repo was skipped, and a generic "gate declined" would waste it.
"""
from __future__ import annotations

import logging
from datetime import datetime

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation

log = logging.getLogger(__name__)

STEP = "RepoClassification"


def _owner_repo(url: str) -> str:
    """`owner/name` from a GitHub URL. Mirrors `GitHubClient._url_to_slug` —
    duplicated rather than imported to keep this surveyor free of a client
    construction it does not otherwise need."""
    url = (url or "").rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com/" in url:
        return url.split("github.com/")[-1]
    return url


class RepoClassificationSurveyor(BaseSurveyor):
    """Classifies the repo's role(s), resolves the artifacts that role implies,
    and records whether architecture recovery should run."""

    def __init__(self, project: Project, registry: ProjectRegistry,
                 surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        from resource_explorer.github import expectations as ex

        slug = self.project.slug
        owner_repo = _owner_repo(getattr(self.project, "github_url", "") or "")
        if "/" not in owner_repo:
            log.warning("%s: no usable GitHub URL — skipping classification", slug)
            return []

        try:
            report = ex.build_report(owner_repo)
        except Exception as exc:
            # Observable, not swallowed. `doc_locations`/`repo_role` degrade
            # internally rather than raise, so reaching here means something
            # genuinely unexpected — and a log line alone would make that
            # indistinguishable from "this repo has no role", which is a
            # legitimate result. A peer session lost three inserts to exactly
            # this pattern (a broad try/except around a registry write turning
            # an FK violation into a warning nobody read), so the failure is
            # persisted as a finding the caller and the UI can both see.
            log.exception("%s: repo classification failed", slug)
            self._record_error(slug, f"{type(exc).__name__}: {exc}")
            return []

        if not report.primary_role:
            log.info("%s: no role determined (%s)", slug, "; ".join(report.notes[:2]))
            return []

        roles = report.roles
        summary = (f"{report.primary_role}"
                   + (f" (also {', '.join(roles[1:])})" if len(roles) > 1 else "")
                   + f" — architecture recovery: {report.gate}")

        from resource_explorer.surveyors import result_status as rs

        gate_status = (rs.skipped(report.gate_reason, gate="architecture_recovery")
                       if report.gate == "skip" else None)

        annotation = ClassificationAnnotation(
            summary=summary,
            analysis_step=STEP,
            check_name="repo_role",
            candidate_classifications=roles,
            # 60 rather than a computed number: the roles carry their own
            # evidence, and §5.5b forbids turning that evidence into a grade.
            # This is "derived, corroborated by evidence the reader can see",
            # which is what §3.3b's Derived level means.
            confidence=60,
            json_properties={
                "primary_role": report.primary_role,
                "roles": roles,
                "recovery_gate": report.gate,
                "recovery_gate_reason": report.gate_reason,
                **({"result_status": gate_status} if gate_status else {}),
                # The two vocabularies side by side, which this module's own
                # docstring is the place that settled: `result_status` above is
                # about the GATE — what a reader should do about a skipped
                # architecture recovery — while this is about THIS run, which
                # either classified the repo or could not.
                #
                # A classification with no roles is never a provable zero: the
                # role set is derived from located artifacts, so finding none
                # means we saw nothing to classify, not that the repo is
                # role-less. There is no known-positive available.
                **(StepOutcome("recovered",
                               detail={"roles": len(roles),
                                       "located": len(report.found)})
                   if roles else
                   StepOutcome("unverified",
                               cause="no artifacts located to classify from")).as_row(),
                "located": [{"kind": i.kind, "outcome": i.outcome,
                             "evidence": i.evidence, "date": i.date} for i in report.found],
                "absent_but_expected": [{"kind": i.kind} for i in report.missing],
                "absent_as_expected": [{"kind": i.kind} for i in report.confirmations],
                "present_though_unexpected": [{"kind": i.kind, "outcome": i.outcome}
                                              for i in report.unexpected],
                "notes": report.notes,
            },
        )

        findings = [{
            "check_name": "repo_role",
            # Not report.primary_role: `summary` already leads with it
            # ("documentation (also tutorial...) — architecture recovery:
            # run"), so that was rendering as "**documentation** —
            # documentation (also tutorial...)" — the same word twice, the
            # first copy adding nothing. A fixed, generic label describes
            # what this finding IS rather than repeating the value it holds.
            "label": "Role",
            "summary": summary,
            "confidence": 60,
            "detail": {"roles": roles, "notes": report.notes},
        }, {
            # The gate is recorded as its own finding precisely because no
            # step_outcome label expresses it (see the module docstring).
            "check_name": "architecture_recovery_gate",
            "label": report.gate,
            "summary": report.gate_reason,
            "confidence": 60,
            "detail": {"primary_role": report.primary_role, "roles": roles,
                       # Reader-facing state, carried on the finding so the card
                       # can render a skip as the funnel working rather than as
                       # a degraded result. Only set when the gate declined —
                       # a run needs no special state.
                       **({"result_status": gate_status} if gate_status else {})},
        }]
        # `label: item.kind` on these four loops, not the outcome/fixed word
        # ("not-found"/"confirms"/item.outcome) they used to carry: with
        # several artifacts checked per repo, a bold header repeating the
        # same near-constant word ("in-repo", "in-repo", "in-repo", ...)
        # tells a reader nothing they could not already see, while `kind`
        # (readme/api-reference/deployment/...) — the one field that
        # actually differs per bullet — sat buried mid-sentence instead.
        # `summary` drops its own leading "{kind}: " for the same reason:
        # once kind is the label, repeating it there was the same word
        # twice again, one level down (confirmed live 2026-09-07: "**in-
        # repo** — readme: in-repo — README.md" on a documentation repo,
        # every one of three artifact bullets bold-headed "in-repo").
        for item in report.missing:
            findings.append({
                "check_name": f"expected_{item.kind}",
                "label": item.kind,
                "summary": f"expected for a {report.primary_role}, not located "
                           f"in-repo, in a sibling repo, or on the doc site",
                "confidence": 60,
                "detail": {"kind": item.kind, "expected": True},
            })
        for item in report.found:
            findings.append({
                "check_name": f"expected_{item.kind}",
                "label": item.kind,
                "summary": f"{item.outcome} — {item.evidence}",
                "confidence": 60,
                "detail": {"kind": item.kind, "evidence": item.evidence, "date": item.date},
            })
        # confirmations/unexpected were computed and sent to Egeria's
        # additionalProperties (above) but never turned into findings here —
        # a silent-field-allowlist bug one layer upstream of the usual shape:
        # not a reader dropping a persisted field, but this loop never
        # persisting them at all. Named directly in this module's own
        # docstring as one of only three things it reports ("located
        # artifacts, absent ones, and *confirmations*"), so a curator reading
        # local findings could see two of the three. `unexpected` never had a
        # comparable "this is one of the things we report" sentence, but it
        # is symmetric with `confirmations` in the same ExpectationReport and
        # equally silent otherwise.
        for item in report.confirmations:
            findings.append({
                "check_name": f"confirmed_{item.kind}",
                "label": item.kind,
                "summary": f"absent, as expected for a {report.primary_role} "
                           f"— supports the role rather than counting against it",
                "confidence": 60,
                "detail": {"kind": item.kind},
            })
        for item in report.unexpected:
            findings.append({
                "check_name": f"unexpected_{item.kind}",
                "label": item.kind,
                "summary": f"{item.outcome} — present though not expected "
                           f"for a {report.primary_role}",
                "confidence": 60,
                "detail": {"kind": item.kind, "outcome": item.outcome},
            })

        # The row "what kind of thing is this repository" gains its dataset sentence from what ingestion stored
        # (dataset_descriptor.py). Reading it must never cost the classification itself, so a failure is recorded as
        # a finding of its own (label "error"): visible on the page, distinguishable from "no dataset signal".
        try:
            from resource_explorer.surveyors.sub_surveyors.dataset_descriptor import kind_finding

            extra = kind_finding(self.registry, slug, report.dataset_evidence)
        except Exception as exc:
            log.warning("%s: could not read the dataset descriptors for the classification row", slug, exc_info=True)
            extra = {"check_name": "dataset_kind", "label": "error", "confidence": 100,
                     "summary": f"the dataset descriptors could not be read: {type(exc).__name__}: {exc}"[:240],
                     "detail": {"state": "error"}}
        if extra:
            findings.append(extra)

        # Deliberately NOT wrapped in try/except: a write failure here is the
        # exact class of bug the FK work found — an unregistered slug, or a
        # schema mismatch — and absorbing it would hide the thing worth seeing.
        # `upsert_finding` now raises a clear ValueError for an unregistered
        # slug, and that should reach the caller.
        self.registry.upsert_finding(slug, "repo_classification", findings,
                                     surveyed_at=self._surveyed_at)
        return [annotation]

    def _record_error(self, slug: str, detail: str) -> None:
        """Persist an unexpected classification failure as a visible finding."""
        try:
            self.registry.upsert_finding(
                slug, "repo_classification",
                [{"check_name": "classification_error", "label": "error",
                  "summary": f"repo classification did not complete: {detail}",
                  "confidence": 100, "detail": {"error": detail}}],
                surveyed_at=self._surveyed_at,
            )
        except Exception:
            log.exception("%s: could not persist classification error", slug)

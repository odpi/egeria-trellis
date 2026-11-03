"""Sub-surveyor: CLA/DCO provenance — stated vs. enforced
(`docs/gap-analyses-design.md` §3).

Answers `docs/dr-egeria/resource_questions.csv:31` verbatim. Two checks,
deliberately: `cla_dco_stated` (does CONTRIBUTING content actually mention
DCO/CLA — not just "a file exists", the exact shallowness
`SecurityHygieneSurveyor` has today for its three checks) and
`cla_dco_enforced` (is there a config/workflow/branch-protection signal
that actually gates PRs on it — a stated-but-unenforced policy is worse
than either extreme, per design §3, because it creates a false sense of
coverage).

**Analysis id vs finding kind** (design §0). Proposed `analysis_catalog`
id: `contribution_provenance`. Finding **kind**:
`"contribution_provenance_findings"`.

**Task scope boundary**: not registered. Proposed `StepInfo`:

    step_key            = "repo_contribution_provenance"
    surveyor_cls         = ContributionProvenanceSurveyor
    annotation_types      = ["ClassificationAnnotation",
                             "RequestForActionAnnotation"]
    accepts_surveyed_at   = True
    requires_resources    = {"zipball_root": "local_path"}
    requires_views        = {"zipball_root": VIEW_SOURCE}
    requires_context       = {"has_file_inventory": "checks for
                              CONTRIBUTING.md and enforcement config paths
                              via the file inventory before reading their
                              content"}
    fetch_cost            = "download"
    compute_cost          = "low"   # VERIFY — reads a handful of specific
                                     # files, not a full-tree walk; design
                                     # §3's own prior, not measured here.

Proposed `ANALYSIS_KINDS` entry: id `contribution_provenance`,
`step_keys=["repo_contribution_provenance"]`, `family` not `"security"` —
design frames this as a legal/IP-provenance question; flagged for whoever
registers this, same as `telemetry_scan`.

**Measured before building, per design's "Honest limits" §**: grepped
`resource_explorer/github/stats_fetcher.py` and
`security_features.py` for `branch_protection`/`required_status_checks` —
**absent from both**. Branch-protection required-status-check data is
NOT captured anywhere in this codebase today, confirmed rather than
assumed. So the "enforcement config found via GitHub's own branch
protection" half of `cla_dco_enforced` cannot be answered at all by this
surveyor as built — it always reports `UNVERIFIED`
(`cause="branch_protection_not_fetched"`) for that specific sub-question,
never `NO_SIGNAL`, per design §3's own worked example ("we didn't check
GitHub's branch protection" and "GitHub's branch protection doesn't
require it" are different claims"). Fetching that data is new plumbing
(a new GitHub API call, or extending `stats_fetcher.py`) — out of scope
here, flagged for the registration pass per the design's own "Honest
limits" section.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import RECOVERED, StepOutcome, UNVERIFIED, no_signal
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import (
    Annotation,
    ClassificationAnnotation,
    RequestForActionAnnotation,
)

log = logging.getLogger(__name__)

STEP = "ContributionProvenance"
FINDING_KIND = "contribution_provenance_findings"

_CONTRIBUTING_CANDIDATES = ("CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING")
_CONTRIBUTING_PREFIXES = (".github/",)

_DCO_CLA_KEYWORD_PATTERN = re.compile(
    r"\b(DCO|Developer Certificate of Origin|CLA|Contributor License Agreement)\b",
    re.IGNORECASE,
)

#: Enforcement-config candidates a filename/path check CAN answer without
#: branch-protection API data — a DCO GitHub Action, a CLA-bot config.
_ENFORCEMENT_WORKFLOW_HINT = re.compile(r"dco|cla", re.IGNORECASE)
_ENFORCEMENT_BOT_CONFIGS = (
    ".github/cla.yml", ".clabot", ".github/CLAassistant.yml", ".cla-assistant.yml",
)


def _matches_contributing(rel: str) -> bool:
    lower = rel.lower()
    base = lower.rsplit("/", 1)[-1]
    if base in {c.lower() for c in _CONTRIBUTING_CANDIDATES}:
        return True
    return any(lower == (pfx + base) for pfx in _CONTRIBUTING_PREFIXES
               if base in {c.lower() for c in _CONTRIBUTING_CANDIDATES})


def _find_contributing(inventory: list[str]) -> str:
    for p in inventory:
        if _matches_contributing(p):
            return p
    return ""


def _find_enforcement_workflow(inventory: list[str]) -> str:
    for p in inventory:
        lower = p.lower()
        if lower.startswith(".github/workflows/") and _ENFORCEMENT_WORKFLOW_HINT.search(lower):
            return p
    return ""


def _find_bot_config(inventory: list[str]) -> str:
    lowered = {p.lower(): p for p in inventory}
    for candidate in _ENFORCEMENT_BOT_CONFIGS:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return ""


class ContributionProvenanceSurveyor(BaseSurveyor):
    def __init__(
        self, project: Project, registry: ProjectRegistry, local_path: str,
        surveyed_at: str | None = None,
    ) -> None:
        super().__init__(project, registry)
        self._local_path = local_path
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        findings: list[dict] = []
        try:
            inventory = [
                p.replace("\\", "/")
                for p in self.registry.get_file_inventory(self.project.slug)
            ]
            if not inventory:
                self._unverified(results, findings, "empty_file_inventory",
                                  "CLA/DCO check not run — the file inventory is empty. "
                                  "Run repo_file_inventory first.")
                self._persist(findings)
                return results

            local_root = Path(self._local_path)
            contributing_path = _find_contributing(inventory)
            workflow_path = _find_enforcement_workflow(inventory)
            bot_config_path = _find_bot_config(inventory)

            stated_outcome, stated_detail, stated_text = self._check_stated(
                local_root, contributing_path)
            findings.append({
                "check_name": "cla_dco_stated", "label": stated_outcome.outcome,
                "confidence": 100 if stated_outcome.is_conclusive else 0,
                "summary": stated_text, "detail": {**stated_outcome.as_row(), **stated_detail},
            })
            results.append(
                ClassificationAnnotation(
                    check_name="cla_dco_stated",
                    summary=stated_text, analysis_step=STEP,
                    candidate_classifications=[stated_outcome.outcome],
                    confidence=100 if stated_outcome.is_conclusive else 0,
                    explanation=stated_text, json_properties=stated_outcome.as_row(),
                )
            )

            enforced_label, enforced_detail, enforced_text = self._check_enforced(
                workflow_path, bot_config_path)
            findings.append({
                "check_name": "cla_dco_enforced", "label": enforced_label,
                "confidence": 100 if enforced_label in {"pass", "gap"} else 0,
                "summary": enforced_text, "detail": enforced_detail,
            })
            results.append(
                ClassificationAnnotation(
                    check_name="cla_dco_enforced",
                    summary=enforced_text, analysis_step=STEP,
                    candidate_classifications=[enforced_label] if enforced_label else [],
                    confidence=100 if enforced_label in {"pass", "gap"} else 0,
                    explanation=enforced_text, json_properties=enforced_detail,
                )
            )

            # Design §3: "no contribution doc plus no enforcement config
            # found is still a real, conclusive 'this repo has no CLA/DCO
            # story', but say so as one combined finding rather than two
            # that could be read as contradicting each other if only one is
            # skimmed." Both underlying facts here ARE filename-checkable
            # (no CONTRIBUTING doc; no workflow/bot config) — this combined
            # finding does NOT depend on the unresolvable branch-protection
            # question, so it is safe to state conclusively.
            if stated_outcome.outcome == "no_signal" and not workflow_path and not bot_config_path:
                combined_text = (
                    "No CONTRIBUTING/DCO/CLA document and no enforcement config "
                    "(workflow or bot config) found — this repo has no stated CLA/DCO "
                    "story. (Whether GitHub branch protection independently requires a "
                    "check is still unknown — see cla_dco_enforced.)"
                )
                findings.append({
                    "check_name": "cla_dco_provenance", "label": "no_signal",
                    "summary": combined_text,
                    "detail": {"outcome": "no_signal", "outcome_cause": "no_cla_dco_story",
                               "outcome_known_positive": True},
                })
                results.append(
                    ClassificationAnnotation(
                        check_name="cla_dco_provenance",
                        summary=combined_text, analysis_step=STEP,
                        candidate_classifications=["no_signal"], confidence=100,
                        explanation=combined_text,
                        json_properties={"outcome": "no_signal",
                                        "outcome_cause": "no_cla_dco_story",
                                        "outcome_known_positive": True},
                    )
                )

            # The asymmetric-risk finding, per design §3: "stated but not
            # enforced" is a false sense of coverage. Deliberately gated on
            # "gap" specifically, per design, not "unknown" — an unverified
            # enforcement question must not be read as a confirmed absence,
            # so this RFA does not fire for the "unknown" case above. As a
            # direct, honest consequence: since `_check_enforced` above
            # never returns "gap" (it cannot, without branch-protection
            # data — see that method's own comment), this RFA is currently
            # UNREACHABLE. Kept exactly as design specifies rather than
            # loosened to fire on "unknown" too, because loosening it would
            # be claiming confirmed non-enforcement RE cannot actually
            # confirm — flagged in the task report as a design/data gap,
            # not silently worked around here.
            has_stated = stated_outcome.outcome == RECOVERED
            if has_stated and enforced_label == "gap":
                results.append(
                    RequestForActionAnnotation(
                        check_name="cla_dco_enforcement_gap",
                        summary="DCO/CLA policy is stated but no enforcement was found",
                        analysis_step=STEP,
                        action_requested=(
                            "Add an enforcement mechanism (a DCO GitHub Action, a CLA-bot "
                            "config, or a required branch-protection status check) so the "
                            "stated policy is actually gated, not merely documented."
                        ),
                        action_target_name=contributing_path,
                        explanation=(
                            "CONTRIBUTING content mentions DCO/CLA, and no workflow, bot "
                            "config, or (checkable) enforcement signal was found. A "
                            "stated-but-unenforced policy creates a false sense of coverage."
                        ),
                        confidence=60,
                        json_properties={"stated": True, "enforced": False},
                    )
                )

            # _gap_analysis_results (the shared reader all four GAP analyses
            # go through) reads status from a "scan_summary" row specifically
            # — its own docstring says "each of the four writes" one. This
            # module's success path never did: only its _unverified early
            # return (empty file inventory) wrote scan_summary, so a normal
            # completed run left attach_status with nothing to read, silently
            # dropping the status envelope every other GAP analysis carries.
            # RECOVERED here mirrors secret_scan.py's own template for "the
            # scan ran to completion" — real findings above already carry the
            # substantive verdict; this row exists so the reader can tell
            # "ran, produced these findings" apart from "did not run".
            scan_summary_text = (
                f"CLA/DCO provenance check completed — stated: {stated_outcome.outcome}, "
                f"enforced: {enforced_label}."
            )
            findings.append({
                "check_name": "scan_summary", "label": RECOVERED,
                "confidence": 100, "summary": scan_summary_text,
                "detail": StepOutcome(RECOVERED, known_positive=True).as_row(),
            })
            results.append(
                ClassificationAnnotation(
                    check_name="scan_summary",
                    summary=scan_summary_text, analysis_step=STEP,
                    candidate_classifications=[RECOVERED], confidence=100,
                    explanation=scan_summary_text,
                    json_properties=StepOutcome(RECOVERED, known_positive=True).as_row(),
                )
            )

            self._persist(findings)

        except Exception as exc:
            log.exception("ContributionProvenanceSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    def _check_stated(self, local_root: Path, contributing_path: str):
        if not contributing_path:
            # No CONTRIBUTING doc at all. Design §3: NO_SIGNAL for the
            # *stated* check only — the inventory proves there is no such
            # file to have missed.
            outcome = no_signal("no_contributing_doc_found", known_positive=True)
            return outcome, {"contributing_path": ""}, (
                "No CONTRIBUTING.md (or variant) found in the file inventory."
            )
        try:
            text = (local_root / contributing_path).read_text(
                encoding="utf-8", errors="ignore")
        except OSError as exc:
            outcome = StepOutcome(UNVERIFIED, cause="contributing_doc_unreadable",
                                  detail={"error": str(exc)})
            return outcome, {"contributing_path": contributing_path}, (
                f"{contributing_path} is present but could not be read: {exc}"
            )
        if _DCO_CLA_KEYWORD_PATTERN.search(text):
            outcome = StepOutcome(RECOVERED, known_positive=True,
                                  detail={"contributing_path": contributing_path})
            return outcome, {"contributing_path": contributing_path}, (
                f"{contributing_path} mentions DCO/CLA."
            )
        outcome = no_signal("no_dco_cla_keyword_in_contributing", known_positive=True)
        return outcome, {"contributing_path": contributing_path}, (
            f"{contributing_path} is present and was read; no DCO/CLA keyword found in it."
        )

    def _check_enforced(self, workflow_path: str, bot_config_path: str):
        # Design §3's enforced-label vocabulary: `pass` is reserved for a
        # verifiably-active enforcement path (branch-protection required
        # checks) — which this surveyor cannot check at all (see module
        # docstring's "Measured before building" note). A config file's
        # mere presence is `partial`, never `pass`, because presence alone
        # cannot distinguish an active bot from one uninstalled from the org.
        if workflow_path or bot_config_path:
            found = workflow_path or bot_config_path
            return "partial", {
                "workflow_path": workflow_path, "bot_config_path": bot_config_path,
                "outcome": "partial",
                "outcome_cause": "config_present_not_corroborated",
            }, (
                f"Enforcement config found ({found}), but its active status could not be "
                "corroborated (no branch-protection data fetched — see module docstring). "
                "Reported as 'partial', not 'enforced'."
            )
        # No config found by filename. Whether GitHub's branch protection
        # requires a DCO/CLA check independently of any repo-local config is
        # a real, different, unanswered question — this codebase does not
        # fetch that data (grepped stats_fetcher.py/security_features.py:
        # absent). UNVERIFIED, not a claim that enforcement is absent —
        # label "unknown" (design §3's vocabulary), not "gap": "gap" would
        # claim enforcement is conclusively absent, which the missing
        # branch-protection data does not let this surveyor claim. This
        # means, honestly stated: `cla_dco_enforced` never actually reaches
        # "gap" as currently built, and the "stated but gap" RFA described
        # below is consequently unreachable until branch-protection
        # fetching exists — see this module's docstring and the task
        # report's "where the design turned out wrong" section.
        return "unknown", {
            "workflow_path": "", "bot_config_path": "",
            "outcome": "unverified",
            "outcome_cause": "branch_protection_not_fetched",
        }, (
            "No DCO/CLA workflow or bot config found by filename. Whether GitHub branch "
            "protection independently requires a DCO/CLA status check is unknown — that "
            "data is not fetched anywhere in this codebase today (see module docstring)."
        )

    def _unverified(self, results, findings, cause: str, reason: str) -> None:
        outcome = StepOutcome(UNVERIFIED, cause=cause)
        results.append(
            ClassificationAnnotation(
                check_name="scan_summary",
                summary=reason, analysis_step=STEP,
                candidate_classifications=[], confidence=0,
                explanation=reason, json_properties=outcome.as_row(),
            )
        )
        findings.append({
            "check_name": "scan_summary", "label": outcome.outcome,
            "confidence": 0,
            "summary": reason, "detail": outcome.as_row(),
        })

    def _persist(self, findings: list[dict]) -> None:
        try:
            self.registry.upsert_finding(
                self.project.slug, FINDING_KIND,
                [
                    {"check_name": f["check_name"], "label": f["label"],
                     "summary": f["summary"],
                     "confidence": f.get("confidence", 100),
                     "detail": f.get("detail")}
                    for f in findings
                ],
                surveyed_at=self._surveyed_at,
                # This one call is the whole run's answer for
                # (slug, FINDING_KIND, ""): a newer run retires the older
                # run's rows, as secret_scan does. Without it every earlier
                # run kept counting as current
                # (FINDINGS-SUPERSESSION-IMPLEMENTED.md).
                supersedes_previous=True,
            )
        except Exception as exc:
            log.warning("Could not persist contribution provenance findings for %s: %s",
                        self.project.slug, exc)

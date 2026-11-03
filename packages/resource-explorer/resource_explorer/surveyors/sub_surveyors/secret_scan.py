"""Sub-surveyor: secret handling — HEAD-snapshot content scan against a
vendored ruleset (`docs/gap-analyses-design.md` §1).

**Analysis id vs finding kind, stated explicitly per design §0's rule.**
The proposed `analysis_catalog` id (not registered by this module — see
below) is `secret_scan`. The finding **kind** this surveyor writes to
`project_analysis_findings`, via `upsert_finding`, is
`"secret_scan_findings"` — deliberately not a bare repeat of the id, so a
future reducer pattern-matching the "obvious" name cannot repeat the
`security_scan`/`security_hygiene` divergence (design §0).

**What this does NOT do (task scope boundary):** this module does not
touch `repo_survey_definition_adapter.py`'s `STEP_REGISTRY`/
`ANALYSIS_KINDS`, and is not wired into `SurveyOrchestrator` — it is built
and unit-tested standing alone, invoked directly. See this module's own
docstring for the exact `StepInfo` shape the eventual registration needs.

**Proposed StepInfo, for whoever does that registration pass** (not built
here):

    step_key            = "repo_secret_scan"
    surveyor_cls         = SecretScanSurveyor
    annotation_types      = ["ClassificationAnnotation",
                             "RequestForActionAnnotation"]
    accepts_surveyed_at   = True
    requires_resources    = {"zipball_root": "local_path"}
    requires_views        = {"zipball_root": VIEW_SOURCE}
    requires_context       = {"has_file_inventory": "secret_scan walks
                              project_file_inventory paths to avoid a second
                              independent directory walk and to reuse the
                              same first-party/vendor exclusion the
                              inventory already classifies"}
    fetch_cost            = "download"
    compute_cost          = "medium"   # VERIFY against step_cost_observer
                                        # p90 after ~10 real runs, per
                                        # design §1 — not measured here.

Proposed `ANALYSIS_KINDS` entry: id `secret_scan`, `step_keys=
["repo_secret_scan"]`, `family="security"`.

**Scope: HEAD only, deliberately** — design §1: a secret removed in a
later commit but still reachable via `git log -p` is a real, distinct,
more expensive finding (`VIEW_HISTORY`/`git_clone_root`, a full log walk)
that this analysis does not attempt. `repo_secret_scan_history` is a
legitimate future sibling, not a merged responsibility here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import PARTIAL, RECOVERED, StepOutcome, UNVERIFIED, no_signal
from resource_explorer.surveyors import result_status
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.sub_surveyors import secret_ruleset as ruleset_mod
from resource_explorer.surveyors.sub_surveyors.provider_meta import check_staleness
from resource_explorer.surveyors.sub_surveyors.secret_ruleset import RulesetUnavailable
from resource_explorer.surveyors.survey_report import (
    Annotation,
    ClassificationAnnotation,
    RequestForActionAnnotation,
)

log = logging.getLogger(__name__)

STEP = "SecretScan"

#: The finding kind — see module docstring's analysis-id-vs-kind note.
FINDING_KIND = "secret_scan_findings"


def _partial_cause(report) -> str:
    """Cause token for a PARTIAL scan, derived from the report's counts."""
    if report.total_budget_hit:
        return "total_time_budget_hit"
    if report.timed_out:
        return "file_time_budget_hit"
    return "files_over_size_cap"


def _skip_sentence(report) -> str:
    """Counts and the top offenders, paths only (never matched text)."""
    parts = []
    if report.skipped_size:
        parts.append(f"{len(report.skipped_size)} file(s) over the "
                     f"{report.bounds.max_file_bytes} byte size cap were skipped")
    if report.skipped_binary:
        parts.append(f"{len(report.skipped_binary)} binary file(s) were skipped")
    if report.timed_out:
        parts.append(f"{len(report.timed_out)} file(s) exhausted the "
                     f"{report.bounds.file_budget_seconds:g}s per-file time budget and "
                     "were only partly scanned")
    if report.not_reached:
        parts.append(f"{len(report.not_reached)} file(s) were never opened because the "
                     f"{report.bounds.total_budget_seconds:g}s total time budget ran out")
    elif report.total_budget_hit:
        parts.append(f"the {report.bounds.total_budget_seconds:g}s total time budget ran out")
    worst = sorted(report.skipped_size + [r[:2] for r in report.timed_out]
                   + report.not_reached, key=lambda r: -r[1])[:3]
    tail = ("; largest: " + ", ".join(f"{p} ({n} bytes)" for p, n in worst)) if worst else ""
    return "; ".join(parts) + tail + "."


class SecretScanSurveyor(BaseSurveyor):
    """Scans `project_file_inventory` paths, read from a freshly extracted
    zipball (`local_path`), against the vendored gitleaks ruleset
    (`secret_ruleset.py`). `local_path` is injected the same way
    `ManifestParseSurveyor` documents — D6's `requires_resources`
    mechanism, `{"zipball_root": "local_path"}`."""

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

    # ── main entry point ────────────────────────────────────────────────

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        findings: list[dict] = []
        try:
            # Ruleset-availability guard — NOT a `requires_context`/
            # `PRECONDITIONS` check (design §1: "this step's own dependency
            # is absent" is a different kind of absence than "another
            # step's output is absent"). Handled here, directly, the same
            # shape `step_preconditions.skip_status()` produces at the
            # orchestrator layer, so a reader sees one consistent
            # SKIPPED_BY_DESIGN vocabulary regardless of which layer
            # detected the skip.
            try:
                rules = ruleset_mod.load_ruleset()
            except RulesetUnavailable as exc:
                self._emit_skip(results, findings, "ruleset_unavailable", str(exc))
                self._persist(findings)
                return results

            # File-inventory precondition, defensive re-check. The eventual
            # `StepInfo.requires_context={"has_file_inventory": ...}`
            # entry means SurveyOrchestrator would normally never dispatch
            # this surveyor's run() at all on an empty inventory — but this
            # module is unit-tested standing alone (task scope boundary,
            # this module's own docstring), so it must not silently trust
            # an unmet precondition it wasn't actually gated on. Mirrors
            # SecurityHygieneSurveyor's own defensive re-check for the same
            # reason.
            inventory = [
                p.replace("\\", "/")
                for p in self.registry.get_file_inventory(self.project.slug)
            ]
            if not inventory:
                self._emit_unverified(
                    results, findings, "empty_file_inventory",
                    "Secret scan not run — the file inventory is empty. Run "
                    "repo_file_inventory (or a Profile refresh) first; an absent "
                    "finding and an unread repository are not the same claim.",
                )
                self._persist(findings)
                return results

            # Known-positive: the ruleset's own fixture, not a scanned-file
            # count (design §1's explicit preference — a scanned-file count
            # proves files were read, not that the matching logic works on
            # this run).
            self_test = ruleset_mod.run_self_test(rules)
            if not self_test.passed:
                self._emit_self_test_failure(results, findings, self_test)
                self._persist(findings)
                return results

            matches, report = rules.scan_paths(Path(self._local_path), inventory)
            files_scanned, files_excluded = report.files_scanned, report.files_excluded

            provider_row = rules.provider_info().as_row()
            self._emit_ruleset_freshness(results, findings, provider_row)

            # Bounds: facts the scanner recorded, never the code path taken. Keys
            # are added ONLY when something was skipped, so a scan that skipped
            # nothing persists exactly what it always did.
            skipped = (report.detail()
                       if (report.partial or report.skipped_binary) else {})
            if report.partial:
                outcome = StepOutcome(
                    PARTIAL, cause=_partial_cause(report), known_positive=True,
                    detail={
                        "matched": len(matches), "files_scanned": files_scanned,
                        "files_excluded": files_excluded,
                        "fixture_self_test": "passed", **skipped, **provider_row,
                    },
                )
                summary_text = (
                    f"PARTIAL scan — {_skip_sentence(report)} "
                    f"{len(matches)} secret-shaped match(es) found in what WAS read "
                    f"against {provider_row['provider_name']} "
                    f"{provider_row['version_or_as_of'][:12]} ({files_scanned} file(s) "
                    "fully scanned). This is NOT a clean result and NOT a claim of no "
                    "secrets: the skipped files were not checked. Limits are set by "
                    "RE_SECRET_SCAN_MAX_FILE_BYTES / _FILE_BUDGET_SECONDS / "
                    "_TOTAL_BUDGET_SECONDS."
                )
            elif matches:
                outcome = StepOutcome(
                    RECOVERED, known_positive=True,
                    detail={
                        "matched": len(matches), "files_scanned": files_scanned,
                        "files_excluded": files_excluded,
                        "fixture_self_test": "passed", **skipped, **provider_row,
                    },
                )
                summary_text = (
                    f"{len(matches)} secret-shaped match(es) found against "
                    f"{provider_row['provider_name']} {provider_row['version_or_as_of'][:12]}, "
                    f"over {files_scanned} scanned file(s) (self-test passing)."
                )
            else:
                outcome = no_signal(
                    "no_secret_pattern_matches", known_positive=True,
                    detail={
                        "files_scanned": files_scanned, "files_excluded": files_excluded,
                        "fixture_self_test": "passed", **skipped, **provider_row,
                    },
                )
                summary_text = (
                    f"No matches against {provider_row['provider_name']} "
                    f"{provider_row['version_or_as_of'][:12]}'s rules, in the current HEAD "
                    f"snapshot of tracked files ({files_scanned} scanned, "
                    f"{files_excluded} excluded"
                    + (f", {len(report.skipped_binary)} binary file(s) skipped"
                       if report.skipped_binary else "")
                    + "), self-test passing. This is NOT a claim "
                    "that the repository has no secrets — only that none matched this "
                    "ruleset's rules in this scan."
                )

            results.append(
                ClassificationAnnotation(
                    check_name="scan_summary",
                    summary=summary_text,
                    analysis_step=STEP,
                    candidate_classifications=[outcome.outcome],
                    confidence=100 if outcome.is_conclusive else 0,
                    explanation=summary_text,
                    json_properties=outcome.as_row(),
                )
            )
            findings.append({
                "check_name": "scan_summary", "label": outcome.outcome,
                "confidence": 100 if outcome.is_conclusive else 0,
                "summary": summary_text,
                "detail": {**outcome.as_row(), **provider_row, **skipped,
                           "files_scanned": files_scanned, "files_excluded": files_excluded,
                           "fixture_self_test": "passed"},
            })

            # Evidence — one row/annotation PER match, separately
            # addressable now (task scope boundary: no AnnotationExtension
            # linking built here; each stands alone until that plumbing
            # lands per docs/annotation-linking-plan.md).
            for m in matches:
                match_detail = {
                    "path": m.path, "line": m.line, "rule_id": m.rule_id,
                    "excerpt": m.excerpt, **provider_row,
                }
                findings.append({
                    "check_name": "secret_pattern", "label": m.rule_id,
                    "confidence": 70,
                    "summary": f"{m.description} at {m.path}:{m.line}",
                    "detail": match_detail,
                })
                results.append(
                    RequestForActionAnnotation(
                        check_name="secret_pattern",
                        item_key=f"{m.path}:{m.line}:{m.offset}:{m.rule_id}",
                        summary=f"Possible secret: {m.description}",
                        analysis_step=STEP,
                        action_requested=(
                            "Rotate/remove the credential and add this path to "
                            ".gitignore if it should never have been tracked."
                        ),
                        action_target_name=f"{m.path}:{m.line}",
                        explanation=(
                            f"Rule {m.rule_id!r} ({provider_row['provider_name']} "
                            f"{provider_row['version_or_as_of'][:12]}) matched at "
                            f"{m.path}:{m.line}. Excerpt (masked): {m.excerpt}"
                        ),
                        confidence=70,
                        json_properties=match_detail,
                    )
                )

            self._persist(findings)

        except Exception as exc:
            log.exception("SecretScanSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    # ── outcome-emission helpers ────────────────────────────────────────

    def _emit_skip(self, results, findings, gate: str, reason: str) -> None:
        status = result_status.skipped(reason, gate=gate)
        results.append(
            ClassificationAnnotation(
                check_name="scan_summary",
                summary=f"Secret scan skipped: {reason}",
                analysis_step=STEP,
                candidate_classifications=[result_status.SKIPPED_BY_DESIGN],
                confidence=0,
                explanation=reason,
                json_properties=status,
            )
        )
        findings.append({
            "check_name": "ruleset_available", "label": result_status.SKIPPED_BY_DESIGN,
            "confidence": 0,
            "summary": reason, "detail": status,
        })

    def _emit_unverified(self, results, findings, cause: str, reason: str) -> None:
        outcome = StepOutcome(UNVERIFIED, cause=cause)
        results.append(
            ClassificationAnnotation(
                check_name="scan_summary",
                summary=reason, analysis_step=STEP,
                candidate_classifications=[],
                confidence=0, explanation=reason,
                json_properties=outcome.as_row(),
            )
        )
        findings.append({
            "check_name": "scan_summary", "label": outcome.outcome,
            "confidence": 0,
            "summary": reason, "detail": outcome.as_row(),
        })

    def _emit_self_test_failure(self, results, findings, self_test) -> None:
        outcome = StepOutcome(
            UNVERIFIED, cause="ruleset_self_test_failed",
            detail={
                "expected_rule_ids": sorted(self_test.expected_rule_ids),
                "matched_rule_ids": sorted(self_test.matched_rule_ids),
                "missing_rule_ids": sorted(self_test.missing_rule_ids),
            },
        )
        reason = (
            "The vendored ruleset's own known-positive fixture did not match as "
            f"expected — missing rule id(s): {sorted(self_test.missing_rule_ids)}. "
            "The method could not prove itself working this run, so whatever it "
            "would have reported (including zero matches) cannot be trusted as a "
            "clean result."
        )
        results.append(
            ClassificationAnnotation(
                check_name="ruleset_self_test",
                summary="Secret scan self-test failed — result not trustworthy",
                analysis_step=STEP,
                candidate_classifications=[],
                confidence=0, explanation=reason,
                json_properties=outcome.as_row(),
            )
        )
        findings.append({
            "check_name": "scan_summary", "label": outcome.outcome,
            "confidence": 0,
            "summary": reason, "detail": outcome.as_row(),
        })

    def _emit_ruleset_freshness(self, results, findings, provider_row: dict) -> None:
        staleness = check_staleness(
            ruleset_mod.RULESET_AS_OF_DATE,
            threshold_days=ruleset_mod.RULESET_STALENESS_THRESHOLD_DAYS,
        )
        known = staleness.label != ""
        summary_text = (
            f"Ruleset {provider_row['provider_name']} rules last changed upstream "
            f"{staleness.age_days} day(s) ago ({staleness.as_of_date})."
            if known else
            f"Could not determine the vendored ruleset's own age "
            f"(as_of_date={staleness.as_of_date!r})."
        )
        results.append(
            ClassificationAnnotation(
                check_name="ruleset_freshness",
                summary=summary_text, analysis_step=STEP,
                candidate_classifications=[staleness.label] if staleness.label else [],
                confidence=100 if known else 0,
                explanation=summary_text,
                json_properties={
                    "known": known, "as_of_date": staleness.as_of_date,
                    "age_days": staleness.age_days,
                    "threshold_days": staleness.threshold_days,
                },
            )
        )
        findings.append({
            "check_name": "ruleset_freshness", "label": staleness.label,
            "confidence": 100 if known else 0,
            "summary": summary_text,
            "detail": {
                "known": known, "as_of_date": staleness.as_of_date,
                "age_days": staleness.age_days,
                "threshold_days": staleness.threshold_days, **provider_row,
            },
        })

    def _persist(self, findings: list[dict]) -> None:
        """Same rationale as SecurityHygieneSurveyor._persist: extracted so
        every early-return path (skip, unverified, self-test failure)
        writes through the same code path as a completed scan — otherwise
        "we could not check" would be the one outcome that left no row."""
        try:
            # supersedes_previous=True: security family (docs/Backlog.md),
            # this one call is the whole run's answer for
            # (slug, FINDING_KIND, "").
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
                supersedes_previous=True,
            )
        except Exception as exc:
            log.warning("Could not persist secret scan findings for %s: %s",
                        self.project.slug, exc)

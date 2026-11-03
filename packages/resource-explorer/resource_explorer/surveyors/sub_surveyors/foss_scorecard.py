"""Sub-surveyor: an OpenSSF-Scorecard-shaped assessment, over data already held.

Answers "how does it measure up on the FOSS scorecard?" without collecting
anything new. Every check below reads `project_stats`, the file inventory, or
findings another analysis already wrote — the same read-only-at-survey-time
relationship CiQualitySurveyor has with its own findings.

The four supply-chain checks (2026-08-26) work the same way: SupplyChainParser
parses .github/workflows at ingestion time, where the zipball is already on
disk, and this reads what it wrote. Until it has run they report `unknown`
with what would make them evaluable — never `fail`, because "we have not
parsed the workflows" and "the workflows are unsafe" are opposite claims and
only one of them is an accusation.

**The one place this deliberately departs from OpenSSF Scorecard.** Scorecard
awards a low score to a check it could not evaluate, so "this project has no
branch protection" and "we could not see whether it has branch protection"
both land as a bad number, and the aggregate silently mixes them. That is the
failure this codebase keeps removing, so here an unevaluable check reports
`not_established` and is EXCLUDED from the score, and the coverage is reported
alongside it: a 9.1 scored over 6 of 13 checks is a different claim from a 9.1
scored over 13, and the reader gets to see which they have.

Consequently the score is not comparable with a published OpenSSF score. It is
not trying to be — it is trying to be true about what was measured.

Checks are declared in CHECKS, one entry each, so adding one is a function and
a row rather than an edit in several places.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from dataclasses import dataclass
from typing import Callable

from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, QualityScoreAnnotation

log = logging.getLogger(__name__)

STEP = "FossScorecard"

#: What a check concluded. `unknown` is the important one — it means the check
#: could not be evaluated from what we hold, and it never contributes to the
#: score in either direction.
PASS = "pass"
FAIL = "fail"
PARTIAL = "partial"
UNKNOWN = "unknown"

#: Points a state contributes, on OpenSSF's 0–10 per-check scale. `unknown` has
#: no entry at all: it is not a zero, it is an absence, and giving it a number
#: here is exactly how the two get confused.
_POINTS = {PASS: 10.0, PARTIAL: 5.0, FAIL: 0.0}


@dataclass
class Check:
    id: str
    title: str
    #: (stats, findings_by_kind, file_paths) -> (state, detail_str)
    evaluate: Callable
    #: What would make this evaluable, when it is not. Shown to the reader, so
    #: "unknown" is actionable rather than merely honest.
    needs: str = ""


def _stat(stats: dict, key, default=None):
    v = stats.get(key)
    return default if v in (None, "") else v


def _c_maintained(stats, findings, paths) -> tuple:
    if _stat(stats, "archived"):
        return FAIL, "Repository is archived."
    c90 = _stat(stats, "commits_90d", 0) or 0
    c30 = _stat(stats, "commits_30d", 0) or 0
    if c90 >= 30:
        return PASS, f"{c90} commit(s) in the last 90 days."
    if c90 > 0 or c30 > 0:
        return PARTIAL, f"Only {c90} commit(s) in 90 days — active but slow."
    return FAIL, "No commits recorded in the last 90 days."


def _c_license(stats, findings, paths) -> tuple:
    spdx = (_stat(stats, "license_spdx_id", "") or "").strip()
    name = (_stat(stats, "license", "") or "").strip()
    if spdx and spdx.upper() != "NOASSERTION":
        return PASS, f"{spdx}"
    if name:
        # GitHub found a LICENSE file it could not identify. Not nothing, but
        # not a usable license declaration either.
        return PARTIAL, f"A license file exists but was not identified ({name})."
    return FAIL, "No license detected."


def _c_ci_tests(stats, findings, paths) -> tuple:
    rows = findings.get("ci_quality") or []
    if not rows:
        return UNKNOWN, "The CI quality analysis has not run for this resource."
    by_name = {r.get("check_name"): (r.get("label") or "").lower() for r in rows}
    runs_tests = by_name.get("ci_runs_tests", "")
    if runs_tests in ("yes", "true", "pass", "present"):
        return PASS, "CI runs tests."
    if by_name:
        return FAIL, f"CI present but no test execution detected ({', '.join(sorted(by_name))})."
    return UNKNOWN, "No CI findings recorded."


def _c_security_policy(stats, findings, paths) -> tuple:
    # See the kind list in run(): `security_hygiene` is the finding kind,
    # `security_scan` was the analysis id and matched nothing. The failure was
    # not a wrong score — this function correctly returned UNKNOWN with a
    # reason. The reason was FALSE: it said "neither security analysis has run"
    # about repos where security_hygiene had run and written findings. A fact
    # about our own lookup, rendered as a fact about the project.
    for kind in ("security_hygiene", "security_features"):
        for r in findings.get(kind) or []:
            if "polic" in (r.get("check_name") or "").lower():
                label = (r.get("label") or "").lower()
                good = label in ("pass", "present", "yes", "true")
                return (PASS if good else FAIL), r.get("summary") or label
    return UNKNOWN, "Neither security analysis has run for this resource."


def _c_releases(stats, findings, paths) -> tuple:
    count = _stat(stats, "releases_count", 0) or 0
    if count <= 0:
        return FAIL, "No releases published."
    interval = _stat(stats, "avg_release_interval_days")
    detail = f"{count} release(s)"
    if interval:
        detail += f", roughly every {interval} day(s)"
    return PASS, detail + "."


def _c_contributors(stats, findings, paths) -> tuple:
    n = _stat(stats, "contributors_count", 0) or 0
    if n >= 10:
        return PASS, f"{n} contributors."
    if n >= 3:
        return PARTIAL, f"{n} contributors — a small group."
    if n > 0:
        # Bus factor. Reported as a real finding rather than an unknown: we
        # measured it, and the number is the concern.
        return FAIL, f"Only {n} contributor(s) — single-maintainer risk."
    return UNKNOWN, "Contributor count was not collected."


def _c_code_review(stats, findings, paths) -> tuple:
    return UNKNOWN, "Requires pull-request review history."


def _c_branch_protection(stats, findings, paths) -> tuple:
    return UNKNOWN, "Requires the branch-protection API, which needs admin scope."


def _c_signed_releases(stats, findings, paths) -> tuple:
    return UNKNOWN, "Requires release asset signatures."


def _c_vulnerabilities(stats, findings, paths) -> tuple:
    rows = findings.get("cve_scan") or []
    if not rows:
        return UNKNOWN, "No vulnerability scan has run — see the CVE analysis."
    open_cves = [r for r in rows if (r.get("label") or "").lower() not in ("clean", "none")]
    if open_cves:
        return FAIL, f"{len(open_cves)} dependency advisory match(es)."
    return PASS, "No advisories matched the recorded dependencies."


def _c_sast(stats, findings, paths) -> tuple:
    raw = _stat(stats, "security_and_analysis_json", "") or ""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        parsed = {}
    if not parsed:
        return UNKNOWN, "GitHub's security-and-analysis settings were empty or not collected."
    enabled = [k for k, v in parsed.items()
               if isinstance(v, dict) and v.get("status") == "enabled"]
    if enabled:
        return PASS, f"Enabled: {', '.join(sorted(enabled))}."
    return FAIL, "No GitHub security analysis features are enabled."


# ── supply chain: read from what SupplyChainParser wrote at ingestion ────────
#: Its labels are already this vocabulary, so mapping is identity for the three
#: real states and explicit for its fourth, which is an absence, not a failure.
_SC_STATE = {"pass": PASS, "partial": PARTIAL, "fail": FAIL,
             "not_established": UNKNOWN}


def _supply_chain(findings, check_name: str, absent: str) -> tuple:
    """One supply-chain row, or a stated reason it could not be read.

    Two distinct absences, kept apart: the analysis never ran, and it ran and
    found no workflows to look at. Only the second is a fact about the repo.
    """
    rows = findings.get("supply_chain") or []
    if not rows:
        return UNKNOWN, absent
    for r in rows:
        if r.get("check_name") == check_name:
            return _SC_STATE.get((r.get("label") or "").lower(), UNKNOWN), \
                r.get("summary") or ""
    return UNKNOWN, "The supply-chain analysis ran but did not report this check."


_SC_ABSENT = ("No workflow supply-chain findings are recorded — refresh the "
              "profile to parse .github/workflows.")


def _c_token_permissions(stats, findings, paths) -> tuple:
    return _supply_chain(findings, "supply_chain_token_permissions", _SC_ABSENT)


def _c_pinned_dependencies(stats, findings, paths) -> tuple:
    return _supply_chain(findings, "supply_chain_pinned_dependencies", _SC_ABSENT)


def _c_dangerous_workflow(stats, findings, paths) -> tuple:
    return _supply_chain(findings, "supply_chain_dangerous_workflow", _SC_ABSENT)


# ── path-derived checks ─────────────────────────────────────────────────────
#: An SBOM committed to the repository. Generating one in CI and attaching it
#: to a release is the other common practice and leaves no file behind, so a
#: miss here is reported as a miss for THIS form, not as "has no SBOM".
_SBOM_RE = re.compile(
    r"(^|/)(sbom[^/]*|.*\.spdx(\.json|\.ya?ml)?|.*bom\.json|"
    r".*cyclonedx[^/]*\.(json|xml))$", re.I)

#: Dependency-update automation, by its configuration file.
_UPDATE_BOT = {
    ".github/dependabot.yml": "Dependabot", ".github/dependabot.yaml": "Dependabot",
    "renovate.json": "Renovate", "renovate.json5": "Renovate",
    ".renovaterc": "Renovate", ".renovaterc.json": "Renovate",
    ".github/renovate.json": "Renovate", ".github/renovate.json5": "Renovate",
}


def _c_sbom(stats, findings, paths) -> tuple:
    if paths is None:
        return UNKNOWN, "No file inventory is recorded for this resource."
    hits = sorted(p for p in paths if _SBOM_RE.search(p or ""))
    if hits:
        return PASS, f"{len(hits)} SBOM file(s) committed: {', '.join(hits[:3])}."
    return FAIL, ("No SBOM file is committed. One generated in CI and attached to a "
                  "release would not appear here.")


def _c_dependency_update_tool(stats, findings, paths) -> tuple:
    if paths is None:
        return UNKNOWN, "No file inventory is recorded for this resource."
    lower = {(p or "").lower(): p for p in paths}
    found = sorted({name for cfg, name in _UPDATE_BOT.items() if cfg in lower})
    if found:
        return PASS, f"Configured: {', '.join(found)}."
    return FAIL, "No Dependabot or Renovate configuration is committed."


CHECKS: list = [
    Check("maintained", "Maintained", _c_maintained),
    Check("license", "License", _c_license),
    Check("ci_tests", "CI-Tests", _c_ci_tests,
          needs="Run the CI quality analysis (repo_ci_quality)."),
    Check("security_policy", "Security-Policy", _c_security_policy,
          needs="Run the security hygiene analysis (repo_security)."),
    Check("releases", "Packaging", _c_releases),
    Check("contributors", "Contributors", _c_contributors),
    Check("sast", "SAST", _c_sast),
    Check("vulnerabilities", "Vulnerabilities", _c_vulnerabilities,
          needs="Run a dependency advisory scan against the recorded dependencies."),
    Check("code_review", "Code-Review", _c_code_review,
          needs="Pull-request review history, which is not collected."),
    Check("branch_protection", "Branch-Protection", _c_branch_protection,
          needs="The branch-protection API, which needs admin scope on the repo."),
    Check("signed_releases", "Signed-Releases", _c_signed_releases,
          needs="Release asset signatures, which are not collected."),
    Check("dependency_update", "Dependency-Update-Tool", _c_dependency_update_tool,
          needs="A file inventory, which the profile refresh records."),
    Check("token_permissions", "Token-Permissions", _c_token_permissions,
          needs="Refresh the profile so .github/workflows is parsed."),
    Check("pinned_dependencies", "Pinned-Dependencies", _c_pinned_dependencies,
          needs="Refresh the profile so .github/workflows is parsed."),
    Check("dangerous_workflow", "Dangerous-Workflow", _c_dangerous_workflow,
          needs="Refresh the profile so .github/workflows is parsed."),
    Check("sbom", "SBOM", _c_sbom,
          needs="A file inventory, which the profile refresh records."),
]


def score(results: list) -> dict:
    """Aggregate, over EVALUATED checks only.

    Unknown checks are excluded rather than zeroed, and `coverage` says how
    many were evaluated — a 9.1 over 6 of 12 checks is a different claim from
    a 9.1 over 12, and collapsing them is what makes a published scorecard
    number hard to trust.
    """
    # `label` carries the state -- upsert_finding's contract is
    # check_name/label/summary, so the state rides in `label` and not in a
    # field of its own.
    scored = [r for r in results if r.get("label") in _POINTS]
    total = sum(_POINTS[r["label"]] for r in scored)
    return {
        "score": round(total / len(scored), 1) if scored else None,
        "checks_evaluated": len(scored),
        "checks_total": len(results),
        "checks_unknown": len(results) - len(scored),
        "comparable_to_openssf": False,
    }


class FossScorecardSurveyor(BaseSurveyor):
    """OpenSSF-Scorecard-shaped checks over already-collected data."""

    def __init__(self, project, registry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        # One timestamp per orchestrator run, so this batch groups with the
        # rest of its survey rather than landing milliseconds apart.
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            slug = self.project.slug
            stats = self.registry.get_latest_project_stats(slug) or {}
            findings = {
                kind: (self.registry.query_findings(slug, kind) or [])
                # `security_hygiene`, NOT `security_scan`. `security_scan` is the
                # analysis ID in analysis_catalog.yaml; the finding KIND that
                # SecurityHygieneSurveyor writes is `security_hygiene`. Reading
                # the id here matched nothing, forever.
                #
                # Measured 2026-09-01: kind `security_scan` has 0 rows across 0
                # repos, while `security_hygiene` has 252 `security_policy`
                # findings. Every foss_scorecard security-policy verdict on
                # record — 155 of them — was `unknown`.
                #
                # security_summary hit this exact mistake and fixed it; its own
                # comment says "this pair had the same `security_scan` mistake".
                # The fix did not travel to this file.
                for kind in ("ci_quality", "security_hygiene", "security_features",
                             "cve_scan", "supply_chain")
            }
            # None, not [], when nothing is recorded: "no inventory" and "an
            # inventory containing no SBOM" are opposite answers, and a bare
            # empty list would make the first read as the second.
            paths = None
            # Through the registry, not a second copy of its filter: a
            # reader that spells COALESCE(vendored, 0) = 0 itself is one
            # more place to forget when the rule changes (#39 was about
            # exactly that shape).
            rows = self.registry.get_file_inventory(slug)
            if rows:
                paths = list(rows)

            results = []
            for check in CHECKS:
                try:
                    state, detail = check.evaluate(stats, findings, paths)
                except Exception as exc:   # one bad check must not lose the rest
                    log.debug("scorecard check %s failed: %s", check.id, exc)
                    state, detail = UNKNOWN, f"Check errored ({type(exc).__name__})."
                results.append({
                    "check_name": check.id, "label": state,
                    "summary": detail,
                    "confidence": 100 if state != UNKNOWN else 0,
                    "detail": {"title": check.title, "state": state,
                               "needs": check.needs if state == UNKNOWN else ""},
                })

            agg = score(results)
            self.registry.upsert_finding(slug, "foss_scorecard", results,
                                         surveyed_at=self._surveyed_at)
            if agg["score"] is not None:
                self.registry.upsert_metric(
                    slug, "foss_scorecard",
                    {"score": agg["score"],
                     "checks_evaluated": float(agg["checks_evaluated"]),
                     "checks_unknown": float(agg["checks_unknown"])},
                    detail=agg, surveyed_at=self._surveyed_at,
                )

            summary = (
                f"FOSS scorecard {agg['score']}/10 over "
                f"{agg['checks_evaluated']} of {agg['checks_total']} checks"
                if agg["score"] is not None else
                "No scorecard check could be evaluated from the data held"
            )
            out.append(QualityScoreAnnotation(
                summary=summary + (
                    f"; {agg['checks_unknown']} not evaluable"
                    if agg["checks_unknown"] else ""
                ),
                analysis_step=STEP,
                check_name="foss_scorecard",
                # Only the real score. `checks_evaluated`/`checks_unknown` are
                # counts, not quality dimensions, and putting them here would
                # make them read as scores in every consumer of this dict.
                quality_scores=({"foss_scorecard": float(agg["score"])}
                                if agg["score"] is not None else {}),
                confidence=80,
                json_properties={
                    **agg,
                    "checks": {r["check_name"]: r["label"] for r in results},
                    # `checks_evaluated` is the known-positive the aggregate
                    # already computes: a scorecard with a score evaluated real
                    # checks; one with none evaluated looked at nothing, and
                    # "No scorecard check could be evaluated" is a statement
                    # about our inputs, not about the project's practices.
                    **(StepOutcome("recovered",
                                   detail={"checks_evaluated": agg["checks_evaluated"]})
                       if agg["score"] is not None else
                       no_signal("no scorecard check could be evaluated from the data held",
                                 known_positive=bool(agg["checks_evaluated"]),
                                 checks_total=agg["checks_total"])).as_row(),
                },
            ))
        except Exception as exc:
            log.exception("FossScorecardSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out

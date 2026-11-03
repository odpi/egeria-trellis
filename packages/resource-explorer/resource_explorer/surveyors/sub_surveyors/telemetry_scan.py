"""Sub-surveyor: telemetry / phone-home detection
(`docs/gap-analyses-design.md` §2).

Answers `docs/dr-egeria/resource_questions.csv:32` verbatim: "Does the
software contain telemetry, phone-home mechanisms, or external metrics
tracking?"

**Analysis id vs finding kind** (design §0). Proposed `analysis_catalog`
id: `telemetry_scan`. Finding **kind** written via `upsert_finding`:
`"telemetry_scan_findings"` — not a bare repeat of the id, same rule
`secret_scan.py` follows for the same reason.

**Task scope boundary**: not registered in `STEP_REGISTRY`/
`ANALYSIS_KINDS`. Proposed `StepInfo` for whoever does that pass:

    step_key            = "repo_telemetry_scan"
    surveyor_cls         = TelemetryScanSurveyor
    annotation_types      = ["ClassificationAnnotation",
                             "RequestForActionAnnotation"]
    accepts_surveyed_at   = True
    requires_resources    = {"zipball_root": "local_path"}
    requires_views        = {"zipball_root": VIEW_SOURCE}
    requires_context       = {"has_file_inventory": "phone_home checks for a
                              disclosure document (README/PRIVACY/DISCLOSURE)
                              via the file inventory before scanning source
                              for the calls it would be disclosing"}
    fetch_cost            = "download"
    compute_cost          = "medium"   # VERIFY — design §2 flags this as
                                        # possibly closer to "low" if scope
                                        # is source-extensions-only; not
                                        # measured here.

Proposed `ANALYSIS_KINDS` entry: id `telemetry_scan`,
`step_keys=["repo_telemetry_scan"]`, `family` — not `"security"` (design's
`RepoComplianceSurvey` section frames this as a privacy question, not a
security one); no existing family fits cleanly, flagged for whoever
registers this.

**What this must NOT claim** (design §2, restated at the call site below
rather than only here): a static scan cannot see dynamically constructed
URLs, config/env-resolved calls, or a dependency's own phone-home
behaviour. It classifies known telemetry-SDK/analytics-vendor shapes, not
"any network call" — a false positive here (flagging an API client as
spyware) is reputationally costly in a different direction than a false
negative, so only curated-pattern matches drive the RequestForAction.
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

STEP = "TelemetryScan"
FINDING_KIND = "telemetry_scan_findings"

#: Source-file extensions this scan considers "scannable" — deliberately
#: narrower than the whole tree (design §2: "if restricted to source-file
#: extensions only ... it likely measures closer to repo_manifest_parse's
#: 'low' than to a full-tree walk"). Also the basis for
#: `source_files_considered`, this analysis's own known-positive (stronger
#: than `has_file_inventory` alone — see design §2's worked example of a
#: pure-data/pure-docs repo).
_SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rb", ".php",
    ".swift", ".cs", ".cpp", ".c", ".m", ".scala", ".rs",
})

_VENDOR_PATTERN = re.compile("|".join([
    r"segment\.(?:io|com)", r"api\.mixpanel\.com", r"analytics\.track\s*\(",
    r"amplitude\.com/2/httpapi", r"[a-z_]*sentry_?dsn", r"\.ingest\.sentry\.io",
    r"google-analytics\.com", r"googletagmanager\.com", r"gtag\s*\(\s*['\"]config",
    r"posthog\.(?:com|track)", r"heap\.io", r"fullstory\.com", r"hotjar\.com",
    r"datadoghq\.com/api", r"newrelic\.com", r"metrics\.send\s*\(",
    r"track_event\s*\(",
]), re.IGNORECASE)

_DISCLOSURE_PATHS = frozenset({
    "PRIVACY.md", "PRIVACY.rst", ".github/PRIVACY.md",
    "DISCLOSURE.md", ".github/DISCLOSURE.md",
})
_DISCLOSURE_KEYWORDS = ("telemetry", "phone-home", "phone home", "opt-out", "opt out",
                        "analytics", "data collection")


def _is_source_path(rel: str) -> bool:
    lower = rel.lower()
    if any(seg in lower for seg in (
            "/vendor/", "/node_modules/", "/dist/", "/build/", "/.git/",
            "/test/", "/tests/", "/testdata/", "/__pycache__/", "/venv/")):
        return False
    return Path(rel).suffix.lower() in _SOURCE_EXTENSIONS


class TelemetryScanSurveyor(BaseSurveyor):
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
                                  "Telemetry scan not run — the file inventory is "
                                  "empty. Run repo_file_inventory first.")
                self._persist(findings)
                return results

            source_paths = [p for p in inventory if _is_source_path(p)]
            if not source_paths:
                self._unverified(
                    results, findings, "no_source_files_in_inventory",
                    "No source files (by recognised extension) found in the file "
                    "inventory. This may be a config/data-only repo, not a "
                    "telemetry-free one — those are different claims.",
                )
                self._persist(findings)
                return results

            local_root = Path(self._local_path)
            matches: list[dict] = []
            files_scanned = 0
            for rel in source_paths:
                full = local_root / rel
                try:
                    if not full.is_file():
                        continue
                    text = full.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    log.debug("telemetry_scan: could not read %s: %s", rel, exc)
                    continue
                files_scanned += 1
                for m in _VENDOR_PATTERN.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    matches.append({
                        "path": rel, "line": line, "sdk_or_pattern": m.group(0),
                    })

            disclosure_present, disclosure_path = self._find_disclosure(inventory, local_root)
            findings.append({
                "check_name": "disclosure_document",
                "label": "pass" if disclosure_present else "gap",
                "summary": (f"Disclosure document found: {disclosure_path}"
                            if disclosure_present else
                            "No README/PRIVACY/DISCLOSURE content plausibly discloses "
                            "network behaviour."),
                "detail": {"path": disclosure_path} if disclosure_present else {},
            })

            if matches:
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"matched": len(matches),
                                              "source_files_considered": files_scanned})
                summary_text = (
                    f"{len(matches)} telemetry-SDK-shaped call site(s)/import(s) found "
                    f"in {files_scanned} scanned source file(s)."
                )
            else:
                outcome = no_signal("no_telemetry_shaped_matches", known_positive=True,
                                    source_files_considered=files_scanned)
                summary_text = (
                    f"No telemetry-SDK-shaped call sites or literal outbound-URL "
                    f"constants found in scanned source ({files_scanned} file(s)), over "
                    "the curated pattern set checked. This is not a claim of 'no "
                    "phone-home' — a static scan cannot see dynamically constructed "
                    "URLs, env-resolved calls, or a dependency's own behaviour."
                )

            results.append(
                ClassificationAnnotation(
                    check_name="telemetry_scan",
                    summary=summary_text, analysis_step=STEP,
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
                "detail": {**outcome.as_row(), "source_files_considered": files_scanned,
                           "disclosure_document_present": disclosure_present},
            })

            for m in matches:
                findings.append({
                    "check_name": "telemetry_call_site", "label": m["sdk_or_pattern"],
                    "summary": f"Telemetry-shaped pattern at {m['path']}:{m['line']}",
                    "detail": m,
                })

            # RFA only when calls found AND no disclosure — design §2: "the
            # RFA is 'document what this already does,' not 'remove network
            # calls'."
            if matches and not disclosure_present:
                results.append(
                    RequestForActionAnnotation(
                        check_name="disclosure_document",
                        summary="Telemetry-shaped call sites found with no disclosure document",
                        analysis_step=STEP,
                        action_requested=(
                            "Document the network behaviour already present (a PRIVACY.md "
                            "or a README section) — or confirm it is intentional API/"
                            "package-manager traffic, not telemetry."
                        ),
                        action_target_name=matches[0]["path"],
                        explanation=(
                            f"{len(matches)} match(es) against a curated telemetry-SDK/"
                            "analytics-vendor pattern set, and no PRIVACY/DISCLOSURE "
                            "document or README section was found."
                        ),
                        confidence=60,
                        json_properties={"matched": len(matches)},
                    )
                )
            # matches found and disclosed: no RFA, per design — the summary
            # finding above already records the match count.

            self._persist(findings)

        except Exception as exc:
            log.exception("TelemetryScanSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    def _find_disclosure(self, inventory: list[str], local_root: Path) -> tuple[bool, str]:
        by_name = {p.rsplit("/", 1)[-1]: p for p in inventory}
        for candidate in _DISCLOSURE_PATHS:
            name = candidate.rsplit("/", 1)[-1]
            if name in by_name:
                return True, by_name[name]
        # README with a plausible disclosure keyword — presence-tier only,
        # per design §2 ("a RAG-tier follow-on could grade the disclosure's
        # quality, out of scope here").
        readme_path = by_name.get("README.md") or by_name.get("README.rst")
        if readme_path:
            try:
                text = (local_root / readme_path).read_text(
                    encoding="utf-8", errors="ignore").lower()
                if any(kw in text for kw in _DISCLOSURE_KEYWORDS):
                    return True, readme_path
            except OSError:
                pass
        return False, ""

    def _unverified(self, results, findings, cause: str, reason: str) -> None:
        outcome = StepOutcome(UNVERIFIED, cause=cause)
        results.append(
            ClassificationAnnotation(
                check_name="telemetry_scan",
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
            log.warning("Could not persist telemetry scan findings for %s: %s",
                        self.project.slug, exc)

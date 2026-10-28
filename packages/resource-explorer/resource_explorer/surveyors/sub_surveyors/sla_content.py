"""Sub-surveyor: published support/SLA content
(`docs/gap-analyses-design.md` §4).

Answers the real content-reading question `question_catalog.yaml:394`
already flags as a gap ("repository_health's trend chart ... is not an
SLA"). Whether the project publishes any support/service-level commitment
as *stated content* — never whether one is honoured; RE has no way to
verify an uptime figure against reality, only whether one is published.

**Analysis id vs finding kind** (design §0). Proposed `analysis_catalog`
id: `sla_content`. Finding **kind**: `"sla_content_findings"`.

**Task scope boundary**: not registered. Proposed `StepInfo`:

    step_key            = "repo_sla_content"
    surveyor_cls         = SlaContentSurveyor
    annotation_types      = ["ClassificationAnnotation"]   # NO RFA, ever,
                                                            # from absence
                                                            # alone — see
                                                            # design §4.
    accepts_surveyed_at   = True
    requires_resources    = {"zipball_root": "local_path"}
    requires_views        = {"zipball_root": VIEW_SOURCE}
    requires_context       = {"has_file_inventory": "checks candidate
                              SLA/support document paths via the file
                              inventory before reading content"}
    fetch_cost            = "download"
    compute_cost          = "low"   # VERIFY — narrowest scope of the four,
                                     # design §4's own strongest prior; not
                                     # measured here.

Proposed `ANALYSIS_KINDS` entry: id `sla_content`,
`step_keys=["repo_sla_content"]`, `family` — not `"security"`; a
support-commitment question. Flagged for whoever registers this.

**The one thing this analysis is built around, restated at the call
site**: absence is the overwhelmingly common, entirely unremarkable case
for most OSS repos. `label` is `present`/`absent`, never `pass`/`gap` —
`pass`/`gap` frames absence as a defect, and it is not one here. No
`RequestForActionAnnotation` is ever emitted for absence, from this
analysis alone.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import RECOVERED, StepOutcome, UNVERIFIED, no_signal
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation

log = logging.getLogger(__name__)

STEP = "SlaContent"
FINDING_KIND = "sla_content_findings"

#: Candidate paths — generous on purpose (design §4: "must not search only
#: SLA.md-shaped filenames and call that exhaustive").
_CANDIDATE_FILENAMES = (
    "SLA.md", "SLA.rst", ".github/SLA.md",
    "SUPPORT.md", "SUPPORT.rst", ".github/SUPPORT.md",
    "README.md", "README.rst",
)
_CANDIDATE_DIR_KEYWORDS = ("sla", "support", "availability")

_SLA_KEYWORD_PATTERN = re.compile(
    r"\b(uptime|SLA|service level|response time|business hours|"
    r"99\.\d%|P1|P2|P3|support tier|availability commitment)\b",
    re.IGNORECASE,
)


def _candidate_paths(inventory: list[str]) -> list[str]:
    by_name = {p.rsplit("/", 1)[-1]: p for p in inventory}
    found = []
    for candidate in _CANDIDATE_FILENAMES:
        name = candidate.rsplit("/", 1)[-1]
        if name in by_name and by_name[name] not in found:
            found.append(by_name[name])
    for p in inventory:
        lower = p.lower()
        if any(f"/docs/{kw}" in lower or lower.startswith(f"docs/{kw}") for kw in _CANDIDATE_DIR_KEYWORDS):
            if p not in found:
                found.append(p)
    return found


class SlaContentSurveyor(BaseSurveyor):
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
                                  "SLA content check not run — the file inventory is "
                                  "empty. Run repo_file_inventory first.")
                self._persist(findings)
                return results

            candidates = _candidate_paths(inventory)
            local_root = Path(self._local_path)
            sla_hits: list[dict] = []
            unreadable: list[str] = []

            for rel in candidates:
                try:
                    text = (local_root / rel).read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    unreadable.append(rel)
                    log.debug("sla_content: could not read %s: %s", rel, exc)
                    continue
                for m in _SLA_KEYWORD_PATTERN.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    sla_hits.append({"path": rel, "line": line, "keyword": m.group(0)})

            if not candidates:
                # No candidate paths at all — the searched-and-absent list
                # IS the known-positive (design §4): without listing what
                # was checked, "no SLA content" and "we didn't know where
                # to look" are indistinguishable.
                outcome = no_signal("no_candidate_paths_present", known_positive=True,
                                    candidate_paths_checked=[])
                summary_text = (
                    "No candidate SLA/support paths (SLA.md, SUPPORT.md, README, a "
                    "docs/sla|support|availability subtree) present in the file "
                    "inventory. Absence is the common, unremarkable case for most "
                    "repositories."
                )
            elif unreadable and not sla_hits:
                outcome = StepOutcome(UNVERIFIED, cause="candidate_path_unreadable",
                                      detail={"unreadable": unreadable})
                summary_text = (
                    f"{len(unreadable)} candidate path(s) present but unreadable: "
                    f"{', '.join(unreadable)}."
                )
            elif sla_hits:
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"matched": len(sla_hits),
                                              "candidate_paths_checked": candidates})
                summary_text = (
                    f"SLA-shaped content found in {len({h['path'] for h in sla_hits})} "
                    f"of {len(candidates)} candidate path(s) checked "
                    f"({', '.join(candidates)}). This states the content was published — "
                    "NOT that any stated commitment is true or honoured; RE cannot "
                    "verify that."
                )
            else:
                outcome = no_signal("no_sla_shaped_keywords_found", known_positive=True,
                                    candidate_paths_checked=candidates)
                summary_text = (
                    f"{len(candidates)} candidate path(s) checked "
                    f"({', '.join(candidates)}) — none contain SLA-shaped content "
                    "(uptime/response-time/support-tier language). A neutral fact, "
                    "not a gap: most repositories never intend to publish an SLA."
                )

            results.append(
                ClassificationAnnotation(
                    check_name="sla_content",
                    summary=summary_text, analysis_step=STEP,
                    candidate_classifications=[outcome.outcome],
                    confidence=100 if outcome.is_conclusive else 0,
                    explanation=summary_text, json_properties=outcome.as_row(),
                )
            )
            findings.append({
                "check_name": "sla_content",
                "confidence": 100 if outcome.is_conclusive else 0,
                "label": "present" if sla_hits else "absent",
                "summary": summary_text,
                "detail": {**outcome.as_row(), "candidate_paths_checked": candidates},
            })
            # _gap_analysis_results (the shared reader all four GAP analyses
            # go through) reads status from a "scan_summary" row — its own
            # docstring says "each of the four writes" one. Only _unverified
            # above did; every normal-run branch above wrote "sla_content"
            # instead, so a completed run left attach_status with nothing to
            # read. `outcome` here is already the same object the real
            # finding above used, so this row costs nothing new to compute.
            findings.append({
                "check_name": "scan_summary", "label": outcome.outcome,
                "confidence": 100 if outcome.is_conclusive else 0,
                "summary": summary_text, "detail": outcome.as_row(),
            })

            # Evidence rows — only when content was actually found. For the
            # common "absent" case there is deliberately little to link
            # (design §4): the summary alone already carries the full,
            # honest claim.
            for h in sla_hits:
                findings.append({
                    "check_name": "sla_content_evidence", "label": h["keyword"],
                    "summary": f"SLA-shaped keyword {h['keyword']!r} at {h['path']}:{h['line']}",
                    "detail": h,
                })

            self._persist(findings)

        except Exception as exc:
            log.exception("SlaContentSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    def _unverified(self, results, findings, cause: str, reason: str) -> None:
        outcome = StepOutcome(UNVERIFIED, cause=cause)
        results.append(
            ClassificationAnnotation(
                check_name="sla_content",
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
            log.warning("Could not persist SLA content findings for %s: %s",
                        self.project.slug, exc)

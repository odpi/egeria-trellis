"""Sub-surveyor: what a refresh would actually need to do — a PLANNER.

The mirror of `security_summary`. That one sits at the END of a survey and reads
what the other steps wrote; this sits at the FRONT and reads stored *state* to
say what the rest of the run needs. Same mechanism, opposite direction — see
`docs/survey-composition-and-topic-summary-design.md` §2b.

**It is advisory in this version, and that is a real limitation rather than a
simplification.** `survey_definition_executor` runs the whole step graph; it only
skips steps tagged for another engine, and Egeria's `Guard` on a step link is not
honoured as a conditional. So this step cannot stop `repo_rag_ingestion` from
running. What it does is make the decision *visible and recorded* — today so a
reader can tell "refreshed nothing because nothing changed" from "refreshed
nothing because it failed", and later as the thing a guard would read.

**Two traps this is written around, both already visible in the codebase.**

*The gate cannot be one decision for the whole survey.* `IncrementalIndexer.refresh`
is the only refresh path that checks the SHA today — and on its no-change path it
*still* profiles data files if that was never run. "Unchanged" and "complete" are
different questions: a survey-wide "nothing changed, skip everything" would skip
work that was never done in the first place. So each target is judged separately,
and `never_run` is a distinct answer from `current`.

*Cheap acquisition is not cheap compute.* `SourceCache` is keyed on (repo, SHA), so
re-running an ungated step costs ~1.3s warm rather than ~23s cold. That hides the
absence of a gate rather than substituting for one — the parse, profile and embed
work repeats in full either way.

**An unreachable head SHA is `unknown`, never `current`.** Reporting "nothing
changed" because GitHub could not be asked would be a claim about the repository
derived from our own connectivity — the failure `cii_badge` refuses for badges.
"""
from __future__ import annotations

import logging

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.survey_time import now_utc_iso
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation

log = logging.getLogger(__name__)

STEP = "RefreshPlan"

#: What a refresh survey covers, and the stored evidence that shows each has run.
#: `table` is counted to answer "has this ever produced anything"; the SHA gate
#: answers "is what it produced still current".
TARGETS = (
    {"step": "repo_file_inventory", "table": "project_file_inventory",
     "what": "file inventory and data profiles"},
    {"step": "repo_manifest_parse", "table": "project_dependencies",
     "what": "dependency, CI and convention data"},
    {"step": "repo_rag_ingestion", "table": None,
     "what": "pgvector embeddings for chat and RAG"},
    {"step": "repo_website_ingestion", "table": None,
     "what": "the project's documentation site"},
)


def head_sha(project, client=None) -> tuple[str, str]:
    """(sha, error). Never raises, and never guesses.

    One GitHub API call — the same one `IncrementalIndexer.refresh` makes. On
    failure the caller reports `unknown`, because "could not ask" and "nothing
    changed" are different facts and only one of them is about the repository.
    """
    try:
        from resource_explorer.github.client import GitHubClient
        c = client or GitHubClient()
        repo = c.get_repo(project.github_url)
        return (c.get_latest_commit_sha(repo) or ""), ""
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc)[:90]}"


def _rows(registry, table: str, slug: str) -> int:
    try:
        with registry._conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE project_slug = ?", (slug,)
            ).fetchone()
        return int(row["n"] or 0)
    except Exception as exc:  # pragma: no cover - a missing table is a real answer
        log.debug("refresh_plan: could not count %s for %s: %s", table, slug, exc)
        return -1


def plan(registry, project, sha: str, sha_error: str) -> list[dict]:
    """One finding per target, plus the overall verdict.

    Four states per target, kept apart on purpose:
      never_run  — nothing stored, so a refresh is needed regardless of the SHA
      stale      — stored, and the commit has moved
      current    — stored, and the commit has not moved
      unknown    — stored, and we could not find out whether it moved
    """
    last = (getattr(project, "last_commit_sha", "") or "").strip()
    if sha_error:
        moved = None
    elif not last:
        moved = True          # never indexed against any commit
    else:
        moved = (sha != last)

    findings, needed = [], 0
    for t in TARGETS:
        rows = _rows(registry, t["table"], project.slug) if t["table"] else None

        if rows == 0:
            label, why = "never_run", f"no {t['what']} stored — a refresh is needed whatever the commit says"
        elif moved is None:
            label, why = "unknown", (
                f"{t['what']} is stored, but the current head commit could not be read "
                f"({sha_error}) — this is a fact about us, not about the repository")
        elif moved:
            label, why = "stale", (
                f"the head commit has moved since this was last indexed "
                f"({last[:8] or 'never'} → {sha[:8]}), so {t['what']} is out of date")
        else:
            label, why = "current", f"{t['what']} was indexed at the current head ({sha[:8]})"

        if label in ("never_run", "stale"):
            needed += 1
        findings.append({
            "check_name": t["step"], "label": label, "summary": why,
            "confidence": 0 if label == "unknown" else 100,
            "detail": {"known": label != "unknown", "step": t["step"],
                       "rows": rows, "last_indexed_sha": last, "head_sha": sha},
        })

    findings.append({
        "check_name": "refresh_needed",
        "label": "unknown" if moved is None else ("yes" if needed else "no"),
        "summary": (
            f"Could not determine whether a refresh is needed: {sha_error}."
            if moved is None else
            f"{needed} of {len(TARGETS)} refresh targets need work."
            if needed else
            f"Nothing to refresh — all {len(TARGETS)} targets are current at {sha[:8]}."
        ) + (
            "  This plan is ADVISORY: the survey executor runs every step in the graph "
            "regardless, so a target reported 'current' was still re-run."
        ),
        "confidence": 0 if moved is None else 100,
        "detail": {"known": moved is None or True, "needed": needed,
                   "total": len(TARGETS), "advisory_only": True,
                   "head_sha": sha, "last_indexed_sha": last},
    })
    return findings


class RefreshPlanSurveyor(BaseSurveyor):
    """Decides — and records — what a refresh run actually needs to do."""

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
            sha, err = head_sha(self.project)
            findings = plan(self.registry, self.project, sha, err)
            self.registry.upsert_finding(self.project.slug, "refresh_plan", findings,
                                         surveyed_at=self._surveyed_at)
            overall = next(f for f in findings if f["check_name"] == "refresh_needed")
            out.append(ClassificationAnnotation(
                summary=overall["summary"],
                analysis_step=STEP,
                check_name="refresh_plan",
                candidate_classifications=[f["label"] for f in findings if f["label"]],
                confidence=overall["confidence"],
                json_properties={f["check_name"]: f["label"] for f in findings},
            ))
        except Exception as exc:
            log.exception("RefreshPlanSurveyor failed for %s", self.project.slug)
            self._warn(out, str(exc))
        return out

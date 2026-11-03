"""Sub-surveyor: Homepage and documentation site -> ClassificationAnnotations.

Scouting wants two cheap answers about a repo's web presence, and they are not
the same answer:

* the **homepage** -- the project's own site, or its org's;
* the **documentation** -- where the docs live, and whether that place answers.

GitHub's `homepage` field is very often the vendor's marketing site
(DataKitchen/dataops-testgen declares https://datakitchen.io) while the packaging
manifest says in so many words `Documentation = "https://docs.datakitchen.io/
testgen/what-is-testgen/"`. The earlier tiered finder stopped at the first hit
and never read that line. Now every source is collected first and the choice made
afterwards (resource_explorer.ingestion.docs_discovery), with the winning source
and the other candidates kept on the record.

Sources, all read from SMALL files only:

  * GitHub's own homepage field (project_stats, already fetched)
  * pyproject.toml [project.urls] / [tool.poetry], package.json, setup.cfg,
    Cargo.toml, pom.xml -- fetched one file each through the GitHub contents API
    (one listing call for the repo root, then only the files that exist)
  * the README's most-linked docs host, skipping badges, the repo's own forge
    and attribution hosts

No zipball and no clone: Scouting is the cheap, API-only tier. When a caller
already holds the repo contents it passes `local_path` and nothing is fetched.

An entry labelled Documentation/Docs wins for documentation. Placeholders
(example.com/.org/.net, localhost) are rejected wherever they appear. Each chosen
URL is labelled `project` or `org_or_vendor` -- recorded, never used to drop it.

For the chosen docs URL ONE bounded request is made (HEAD, GET only if HEAD is
refused): reachable / unreachable / not_checked, status, final URL after
redirects, Last-Modified. "Not checked" is its own answer and is never reported
as "unreachable".

Persistence: projects.homepage_url and projects.docs_url (no DDL), plus a
`docs_discovery` finding holding the sources, other candidates and reachability.
The homepage is published to Egeria by EgeriaPublisher as an ExternalReference
linked to the repo's own Asset; the docs URL is NOT published by this step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from resource_explorer.ingestion import docs_discovery as dd
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation

log = logging.getLogger(__name__)

STEP = "Homepage"
FINDING_KIND = "docs_discovery"

# Kept under their old names: tests and docs reference them.
_CODE_HOSTS = dd.CODE_HOSTS
_VENDOR_HOSTS = dd.VENDOR_HOSTS
_BADGE_HOSTS = dd.BADGE_HOSTS
_host = dd.host_of


def _is_plausible_site(url: str, repo_host: str = "") -> bool:
    """Reject the things that are technically links but never the project site."""
    return not dd.reject_reason(url, repo_host)


class HomepageSurveyor(BaseSurveyor):
    """Finds the homepage and the documentation site. See the module docstring."""

    def __init__(
        self, project: Project, registry: ProjectRegistry, local_path: str | None = None,
        surveyed_at: str | None = None, client=None, probe=None,
    ) -> None:
        super().__init__(project, registry)
        self._local_path = local_path
        self._surveyed_at = surveyed_at
        self._client = client       # GitHubClient; built lazily so tests never need a token
        self._probe = probe         # HttpProbe; ditto

    @property
    def step_name(self) -> str:
        return STEP

    # -- reading ------------------------------------------------------------
    def _github_homepage(self) -> str:
        try:
            stats = self.registry.get_latest_project_stats(self.project.slug) or {}
        except Exception:
            return ""
        return (stats.get("homepage") or "").strip()

    def _read_local(self, root: Path) -> tuple[dict[str, str], str]:
        names = [p.name for p in root.iterdir() if p.is_file()] if root.is_dir() else []
        files = {}
        for n in dd.wanted_file_names(names):
            try:
                files[n] = (root / n).read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                log.debug("homepage: could not read %s: %s", n, exc)
        return files, ""

    def _read_remote(self) -> tuple[dict[str, str], str]:
        """(files, error). Contents API only: one listing call, then each wanted file."""
        if not self.project.github_url:
            return {}, "no repo URL"
        try:
            client = self._client
            if client is None:
                from resource_explorer.github.client import GitHubClient
                client = self._client = GitHubClient()
            repo = client.get_repo(self.project.github_url)
            names = client.list_root_file_names(repo)
        except Exception as exc:
            return {}, f"{type(exc).__name__}: {str(exc)[:100]}"
        files: dict[str, str] = {}
        for n in dd.wanted_file_names(names):
            try:
                text = client.get_file_content(repo, n)
            except Exception as exc:
                log.debug("homepage: could not fetch %s: %s", n, exc)
                continue
            if text:
                files[n] = text
        return files, ""

    def _check_docs(self, url: str) -> dict:
        probe = self._probe
        if probe is None:
            from resource_explorer.ingestion.docs_probe import HttpProbe
            probe = self._probe = HttpProbe()
        try:
            return probe.probe(url).as_dict()
        except Exception as exc:
            return {"state": "unreachable", "status": None, "final_url": "", "last_modified": "",
                    "reason": f"{type(exc).__name__}: {str(exc)[:80]}"}

    # -- run ----------------------------------------------------------------
    def run(self) -> list[Annotation]:
        slug = self.project.slug
        gh_home = self._github_homepage()
        if self._local_path:
            files, read_error = self._read_local(Path(self._local_path))
        else:
            files, read_error = self._read_remote()
        # Without a repo URL nothing was ever readable; with one, an API failure
        # means the manifests/README were NOT looked at, which is not "none found".
        sources_read = not read_error

        found = dd.decide(self.project.github_url or "", gh_home, files)
        homepage, hp_source, hp_conf = None, "", 0
        if found.homepage:
            homepage = found.homepage.url
            hp_source, hp_conf = found.homepage.source_key, found.homepage.confidence
        fell_back_to_repo = False
        if not homepage and self.project.github_url:
            homepage = self.project.github_url.removesuffix(".git")
            hp_source, hp_conf, fell_back_to_repo = "repo_url", 30, True

        docs = found.docs
        reach = None
        if docs:
            reach = self._check_docs(docs.url)

        # -- persist (no DDL) ---------------------------------------------------
        # A failed write is recorded on the result (persist_errors), not only logged.
        persist_errors: list[str] = []

        def _persist(what: str, fn, *args, **kw) -> None:
            try:
                fn(*args, **kw)
            except Exception as exc:
                persist_errors.append(f"{what}: {type(exc).__name__}: {str(exc)[:80]}")

        if homepage:
            _persist("homepage_url", self.registry.update_project_homepage, slug, homepage)
        if docs or sources_read:
            # A found URL is written; "read everything, found none" clears a stale one.
            # An unreadable repo (API failure) leaves the previous answer alone.
            _persist("docs_url", self.registry.update_project_docs_url, slug, docs.url if docs else "")

        state = ("found" if docs else "none_found" if sources_read else "unknown")
        detail = {
            "docs_state": state,
            "docs_url": docs.url if docs else "",
            "docs_source": docs.source if docs else "",
            "docs_scope": found.docs_scope,
            "homepage_url": homepage or "",
            "homepage_source": hp_source,
            "homepage_scope": "" if fell_back_to_repo else found.homepage_scope,
            "reachability": reach or {"state": "not_checked", "reason": "no documentation URL to check"},
            "candidates": found.others_seen,
            "files_read": sorted(files),
            "read_error": read_error,
        }
        _persist("docs_discovery finding", self.registry.upsert_finding,
                 slug, FINDING_KIND,
                 [{"check_name": "documentation", "label": state,
                   "summary": docs.url if docs else "", "confidence": docs.confidence if docs else 0,
                   "detail": detail}],
                 surveyed_at=self._surveyed_at or datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 supersedes_previous=True)

        out: list[Annotation] = [
            self._homepage_annotation(homepage, hp_source, hp_conf, fell_back_to_repo, found, sources_read),
            self._docs_annotation(docs, found, reach, state, read_error),
        ]
        if persist_errors:
            log.warning("homepage/docs for %s: could not persist %s", slug, persist_errors)
            for a in out:
                a.additional_properties = {**(a.additional_properties or {}),
                                           "persist_errors": "; ".join(persist_errors)}
        return out

    # -- annotations ---------------------------------------------------------
    def _homepage_annotation(self, homepage, source, confidence, fell_back_to_repo, found,
                             sources_read) -> ClassificationAnnotation:
        if not homepage:
            return ClassificationAnnotation(
                check_name="homepage",
                summary="No external site found",
                analysis_step=STEP,
                confidence=100,
                explanation=(
                    "GitHub declares no usable homepage, nothing was found in the packaging "
                    "manifests or the README, and the project has no repo URL to fall back "
                    "on. A real answer, not a failure."
                ),
                candidate_classifications=[],
                json_properties=no_signal(
                    "no homepage in any declared source, and no repo URL to fall back on",
                    known_positive=True).as_row(),
            )
        label = "Project page (repo)" if fell_back_to_repo else "External site"
        return ClassificationAnnotation(
            check_name="homepage",
            summary=f"{label}: {homepage}",
            analysis_step=STEP,
            confidence=confidence,
            json_properties=(
                StepOutcome("partial", cause="fell back to the repo URL", detail={"source": source})
                if fell_back_to_repo else
                StepOutcome("recovered", detail={"source": source})
            ).as_row(),
            explanation=(
                f"Derived from {found.homepage.source if found.homepage and not fell_back_to_repo else source}. "
                "Every source is collected first (GitHub's homepage field, packaging manifests, "
                "the README); placeholders such as example.com are rejected; the repo URL itself "
                "is the last resort. Confidence reflects the source."
            ),
            candidate_classifications=[homepage],
            additional_properties={
                "homepage_url": homepage,
                "derivation_source": source,
                "is_external_site": str(not fell_back_to_repo).lower(),
                "site_scope": "" if fell_back_to_repo else (found.homepage_scope or "unknown"),
            },
        )

    def _docs_annotation(self, docs, found, reach, state, read_error) -> ClassificationAnnotation:
        others = [c for c in found.others_seen if not docs or c["url"] != docs.url]
        if docs:
            reach = reach or {}
            rstate = reach.get("state", "not_checked")
            word = {"reachable": "reachable", "unreachable": "unreachable",
                    "not_checked": "not checked"}.get(rstate, rstate)
            return ClassificationAnnotation(
                check_name="documentation_site",
                summary=f"Documentation: {docs.url} ({word})",
                analysis_step=STEP,
                confidence=docs.confidence,
                json_properties=StepOutcome(
                    "recovered" if rstate == "reachable" else "partial",
                    cause="" if rstate == "reachable" else f"docs site {word}",
                    detail={"source": docs.source_key}).as_row(),
                explanation=(
                    f"Documentation URL from {docs.source}. An entry labelled Documentation or "
                    "Docs in a manifest wins; otherwise a docs-shaped URL, then the README's "
                    "most-linked docs host. Reachability is one bounded request, not a crawl."
                ),
                candidate_classifications=[docs.url],
                additional_properties={
                    "docs_url": docs.url,
                    "derivation_source": docs.source_key,
                    "source_detail": docs.source,
                    "site_scope": found.docs_scope or "unknown",
                    "reachability": rstate,
                    "http_status": "" if reach.get("status") is None else str(reach.get("status")),
                    "final_url": reach.get("final_url", ""),
                    "last_modified": reach.get("last_modified", ""),
                    "other_candidates": ", ".join(c["url"] for c in others)[:500],
                },
            )
        if state == "unknown":
            return ClassificationAnnotation(
                check_name="documentation_site",
                summary="Documentation not looked for",
                analysis_step=STEP,
                confidence=0,
                json_properties=StepOutcome(
                    "unverified", cause="repo files could not be read",
                    detail={"error": read_error}).as_row(),
                explanation=f"The manifests and README could not be fetched ({read_error}); "
                            "this says nothing about whether documentation exists.",
                candidate_classifications=[],
            )
        return ClassificationAnnotation(
            check_name="documentation_site",
            summary="No documentation site found",
            analysis_step=STEP,
            confidence=100,
            json_properties=no_signal(
                "no documentation URL in GitHub's field, the manifests or the README",
                known_positive=True).as_row(),
            explanation="GitHub's homepage field, the packaging manifests and the README were "
                        "read; none names a documentation site. A real answer, not a failure.",
            candidate_classifications=[],
            additional_properties={"other_candidates": ", ".join(c["url"] for c in others)[:500]},
        )

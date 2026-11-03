"""Catalog-only registration for repos discovered via GitHubClient.search_repos().

Deliberately NOT OnboardingWizard (cli/wizard.py) — that always triggers a full
IngestionPipeline run (pgvector embedding, can take minutes per repo) with no
way to opt out. Bulk-importing a whole GitHub org needs a lighter path: get
each repo into the registry (survey/curate-ready immediately), skip RAG
ingestion, and let it be ingested later (per-repo, via the existing
add/refresh flows) if and when someone actually wants code search on it.

_run_import_batch() mirrors scheduler.py's _run_due()/_scheduler_loop() shape —
the only existing precedent in this codebase for work that runs independently
of the HTTP request that kicked it off. Each repo is isolated in its own
try/except so one bad repo can't take down the rest of the batch, matching
the same isolation _run_due() already gives each due schedule.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from dataclasses import dataclass, field

from resource_explorer.activity_logger import log_scout
from resource_explorer.registry import Project, ProjectRegistry

log = logging.getLogger(__name__)


def _url_to_slug(url: str) -> str:
    """Same derivation as cli/wizard.py's OnboardingWizard._url_to_slug —
    repo name (last URL path segment), not owner/repo. Kept in sync
    deliberately: both are the "no explicit slug_override" case of
    registering a repo, and must land on the same slug for the same repo
    however it was registered."""
    url = url.rstrip("/")
    slug = url.split("/")[-1]
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())


def _slugify_source_label(label: str) -> str:
    """A source_label can be a plain org name (already slug-shaped) or a
    free-text search description ("search: language:python stars:>=500") —
    sanitize either into something safe to use as an activity-log
    entity_slug ("Discover repos to scout" plan, D7)."""
    return re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")[:80] or "import_batch"


class OrgImporterError(RuntimeError):
    """Raised for import-setup errors (e.g. re-registering an existing repo)."""


class AlreadyRegisteredError(OrgImporterError):
    """The repo is already in the catalog. Not a failure: the batch skips it."""


class OrgImporter:
    """Registers a single discovered repo into the catalog, no ingestion."""

    def __init__(self, registry: ProjectRegistry | None = None) -> None:
        self.registry = registry or ProjectRegistry()

    def import_repo(
        self,
        github_url: str,
        display_name: str,
        description: str = "",
        group_slug: str = "",
    ) -> Project:
        slug = _url_to_slug(github_url)
        if self.registry.get_by_github_url(github_url) or self.registry.exists(slug):
            raise AlreadyRegisteredError(f"'{github_url}' is already registered.")

        project = Project(
            slug=slug,
            display_name=display_name,
            github_url=github_url,
            description=description,
            group_slug=group_slug or "",
        )
        self.registry.add(project)

        # No GitHub stats fetch here: it took ~2 minutes per repo and registration
        # must be near-instant. The first scouting survey's git_statistics step
        # fetches them; until then the UI says "not yet surveyed", never zero.
        return self.registry.get(slug)


# Repos with an import in flight in THIS process, keyed by normalised URL. A
# second press on the same repos is refused visibly (the response's `in_flight`)
# rather than racing the first batch into "already registered" failures.
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def claim_in_flight(urls: list[str]) -> tuple[list[str], list[str]]:
    """Reserve `urls` for a batch. Returns (claimed, refused); refused are in another batch."""
    claimed, refused = [], []
    with _IN_FLIGHT_LOCK:
        for u in urls:
            k = _norm_url(u)
            if k in _IN_FLIGHT:
                refused.append(u)
            else:
                _IN_FLIGHT.add(k)
                claimed.append(u)
    return claimed, refused


def release_in_flight(urls: list[str]) -> None:
    with _IN_FLIGHT_LOCK:
        for u in urls:
            _IN_FLIGHT.discard(_norm_url(u))


@dataclass
class ImportBatch:
    """Ids of the activity rows written when the press happens."""
    batch_id: str
    source_label: str
    repo_ids: dict[str, str] = field(default_factory=dict)  # github_url -> activity id

    def items(self) -> list[dict]:
        return [{"github_url": u, "entry_id": i} for u, i in self.repo_ids.items()]


def _safe(registry, **kwargs) -> None:
    """Activity logging must never end the batch (see _import_batch_body)."""
    try:
        log_scout(registry, **kwargs)
    except Exception:
        log.exception("Import: could not write activity entry for %s", kwargs.get("entity_slug", "?"))


def begin_import_batch(registry, source_label: str, repos: list[dict]) -> ImportBatch:
    """Write the batch row and one row per repo, all 'running', BEFORE any work.

    Called from the route, so the rows exist before the HTTP response and the
    Activity window can show the batch in progress from the moment of the press."""
    import json

    from resource_explorer.run_reconciler import process_identity

    # The thread that does the work lives in this process, so a restart or crash
    # leaves these rows for run_reconciler.reconcile() to resolve by pid liveness.
    owner = json.dumps({"_runner": process_identity()})
    batch = ImportBatch(batch_id=str(uuid.uuid4()), source_label=source_label)
    for r in repos:
        batch.repo_ids[r["github_url"]] = str(uuid.uuid4())
    for r in repos:
        url = r["github_url"]
        name = r.get("display_name") or url
        _safe(registry, entry_id=batch.repo_ids[url], entity_type="repo", entity_slug=_url_to_slug(url),
              entity_name=name, entity_location=url, status="running", detail=owner,
              summary=f"Importing '{name}' from {source_label}")
    _safe(registry, entry_id=batch.batch_id, entity_type="repo",
          entity_slug=_slugify_source_label(source_label), entity_name=source_label,
          entity_location=source_label, status="running", detail=owner, items=batch.items(),
          summary=f"Import batch running ({source_label}): {len(repos)} repo(s)")
    return batch


def _run_import_batch(
    source_label: str,
    repos: list[dict],
    group_slug: str,
    batch: "ImportBatch | None" = None,
) -> None:
    """repos: list of {"github_url", "display_name", "description"} dicts —
    already filtered to not-yet-registered repos by the caller (the route).
    Runs in a daemon thread; nothing here returns to an HTTP response.

    source_label describes what produced this batch — a GitHub org name
    (e.g. "cncf") or a search description (e.g. "search: language:python
    stars:>=500") — threaded into the activity-log summary text only; unlike
    an org name it isn't necessarily a valid URL path, so it's never used to
    build entity_location for the batch-summary entry below."""
    try:
        _import_batch_body(source_label, repos, group_slug, batch)
    except BaseException:
        # This function is a daemon thread's entry point: an exception here goes
        # nowhere at all. Nothing should reach this after the guards below, so
        # arriving here means a new failure mode, and it must not be silent.
        log.exception(
            "Import: batch '%s' terminated unexpectedly after starting %d repo(s); "
            "some may not have been registered", source_label, len(repos))
        raise
    finally:
        release_in_flight([r["github_url"] for r in repos])


def _import_batch_body(source_label: str, repos: list[dict], group_slug: str,
                       batch: "ImportBatch | None" = None) -> None:
    registry = ProjectRegistry()
    importer = OrgImporter(registry)
    if batch is None:
        batch = begin_import_batch(registry, source_label, repos)

    def _safe_log(**kwargs) -> None:
        """Activity logging must never end the batch.

        It did, on 2026-08-21: a registry write inside the success path raised
        ("server closed the connection unexpectedly"), the except handler called
        log_scout again, that raised too, and the second exception escaped the
        loop and killed this daemon thread. One repo of many was registered, no
        batch summary was written, and nothing anywhere said so — the user saw
        the first repo appear and the rest silently not.

        The audit trail is important, but it is not more important than the work
        it describes. A failure to record must not become a failure to do.
        """
        try:
            log_scout(registry, **kwargs)
        except Exception:
            log.exception("Import: could not write activity entry for %s",
                          kwargs.get("entity_slug", "?"))

    succeeded = 0
    skipped = 0
    failed = 0
    for repo in repos:
        github_url = repo["github_url"]
        display_name = repo.get("display_name") or github_url
        row = dict(entry_id=batch.repo_ids.get(github_url, ""), entity_type="repo",
                   entity_slug=_url_to_slug(github_url), entity_name=display_name,
                   entity_location=github_url)

        # Only the import itself is inside this try. Logging used to be too,
        # which meant a logging failure was counted and reported as an import
        # failure for a repo that had in fact been registered.
        try:
            importer.import_repo(
                github_url, display_name, repo.get("description", ""), group_slug,
            )
        except AlreadyRegisteredError as exc:
            # Not a failure: it is in the catalog, which is what was asked for.
            skipped += 1
            _safe_log(**row, status="ok",
                      summary=f"Skipped '{display_name}': already registered", detail=str(exc),
                      items=[{"outcome": "already_registered"}])
            continue
        except Exception as exc:
            failed += 1
            log.exception("Import: failed to register %s", github_url)
            _safe_log(**row, status="error",
                      summary=f"Failed to import '{display_name}' from {source_label}",
                      detail=str(exc), items=[{"outcome": "failed"}])
            continue

        succeeded += 1
        _safe_log(**row, status="ok", summary=f"Imported '{display_name}' from {source_label}",
                  items=[{"outcome": "imported"}])

    _safe_log(
        entry_id=batch.batch_id, entity_type="repo", entity_slug=_slugify_source_label(source_label),
        entity_name=source_label, entity_location=source_label,
        status="error" if failed else "ok", items=batch.items(),
        summary=(f"Import batch complete ({source_label}): {succeeded} imported, "
                 f"{skipped} already registered, {failed} failed, out of {len(repos)} requested"),
    )

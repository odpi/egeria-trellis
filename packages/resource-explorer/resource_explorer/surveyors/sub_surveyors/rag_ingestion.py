"""Sub-surveyor: RAG ingestion → ResourceMeasureAnnotation.

Refreshes the project's pgvector collections via `IncrementalIndexer.refresh()`
and reports what is actually indexed afterwards.

Why this is a survey step. `project_code_symbols`, `project_file_inventory` and
`project_stats` each had the same defect — a table many survey steps *read* that
no survey step *wrote*, populated only by RAG ingestion or a registration-time
side effect. Each was fixed by turning the implicit prerequisite into a declared,
ordered, re-runnable step (repo_symbol_extraction, repo_file_inventory,
repo_git_statistics). This is the fourth and largest instance: ingestion
populates the pgvector collections Chat, the query router and every RAG-backed
answer depend on, and it ran at registration, on webhook, from the scheduler and
from a bespoke on-demand route — never as part of a survey, never with a
survey's freshness signal, never with results.

Analysis is where a repo's *queryable representation* gets built. Every other
stage produces annotations about a repo; this one produces the thing
Understanding and Chat interrogate.

Wraps `IncrementalIndexer.refresh()`, not `IngestionPipeline.run()` (plan D1).
The incremental path is the right semantics for a repeatable step: a no-op when
`last_commit_sha` is unchanged, so it stays cheap in a survey that runs often.
Full ingestion stays at registration — a survey step must never be the thing
that decides to embed a repo from scratch for the first time.

No `requires_resources={"zipball_root": ...}` (plan D2), unlike
repo_file_inventory/repo_symbol_extraction. IncrementalIndexer downloads its own
zipball *conditionally*, only when there are changed files
(`ingestion/incremental.py`), so declaring the shared resource would force a
download on every run including the common no-op case: **the resource-sharing
win does not apply to a step whose common case is fetching nothing at all.**

Ordered LAST in STEP_REGISTRY (plan D3), unlike the other three microflow steps
which are ordered first because their consumers read what they write. Nothing
downstream reads pgvector — it is consumed by Chat and the query router, not by
other survey steps — and this is by far the most expensive operation in the set.
STEP_REGISTRY order is also "Full Survey (all steps)" order (the "*" sentinel in
repo_survey_types.csv), so an expensive optional step placed anywhere but last
would delay the cheap signals a survey exists to produce.

Reports live pgvector counts rather than what this run happened to do, so the
results answer the question a user actually has — "is my chat index current, and
how big is it?" — and stay correct when the step no-ops.
"""
from __future__ import annotations

import logging
from datetime import datetime

from resource_explorer.index_state import has_own_index, shared_collections
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome
from resource_explorer.surveyors.survey_report import Annotation, ResourceMeasureAnnotation

log = logging.getLogger(__name__)

STEP = "RagIngestion"


class RagIngestionSurveyor(BaseSurveyor):
    """Refreshes the project's pgvector collections incrementally, then reports
    the live per-collection chunk counts."""

    def __init__(
        self, project: Project, registry: ProjectRegistry,
        surveyed_at: str | None = None,
    ) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        slug = self.project.slug
        ingested, error = False, ""
        try:
            from resource_explorer.ingestion.incremental import IncrementalIndexer
            from resource_explorer.query_cache import QueryCache

            IncrementalIndexer().refresh(self.project)
            # The on-demand route does this too, and skipping it would leave
            # chat answering from cache built against the pre-refresh index.
            QueryCache().invalidate_project(slug)
            ingested = True
        except Exception as exc:
            # Best-effort, exactly as repo_git_statistics is: a GitHub or
            # embedding hiccup must not fail the survey. The counts below still
            # report whatever is in pgvector from the previous ingestion.
            error = str(exc)
            log.warning("RagIngestionSurveyor: ingestion failed for %s: %s", slug, exc)

        counts: dict[str, int] = {}
        collections = list(self.project.collections or [])
        try:
            from resource_explorer.vector_store_pg import MultiCollectionStore

            store = MultiCollectionStore()
            for collection in collections:
                try:
                    counts[collection] = store.count(collection)
                except Exception as exc:
                    log.warning(
                        "RagIngestionSurveyor: count failed for %s: %s", collection, exc
                    )
        except Exception as exc:
            log.warning("RagIngestionSurveyor: vector store unavailable for %s: %s", slug, exc)

        own_index = has_own_index(slug, collections)
        shared = shared_collections(slug, collections)
        # A shared collection (web_docs_* and the like) holds another source's chunks. Adding its
        # count to "this project's chunks" is the bug this branch exists to stop.
        shared_counts = {c: counts[c] for c in shared if c in counts}
        counts = {c: n for c, n in counts.items() if c not in shared_counts}
        total_chunks = sum(counts.values())
        properties = {
            **counts,
            "total_chunks": total_chunks,
            "collections": len(collections) - len(shared),
            "last_commit_sha": self.project.last_commit_sha or "",
            "ingested": ingested,
        }

        try:
            self.registry.upsert_metric(
                slug, "rag_ingestion",
                {"total_chunks": total_chunks, "collections": len(collections) - len(shared)},
                detail={"by_collection": counts, "shared_collections": shared_counts},
                surveyed_at=self._surveyed_at,
            )
        except Exception as exc:
            log.warning("Could not persist RAG ingestion snapshot for %s: %s", slug, exc)

        if not own_index:
            # This step refreshes collections the project already has; it cannot make the first
            # index. A listed collection that is not `{slug}_{type}` (the shared site docs) is not
            # the repository's own content, so "never indexed" is the honest state, not a count.
            shared_text = (
                f" Chat can search the shared collection(s) {', '.join(shared)} (the site "
                "documentation), not this repository's own content."
                if shared else ""
            )
            return [
                ResourceMeasureAnnotation(
                    check_name="rag_ingestion",
                    summary=("Not indexed for chat (no collections registered)" if not shared
                             else "Not indexed for chat (only shared collections registered)"),
                    analysis_step=STEP,
                    # Nothing was verified about the repository's content: unverified, never 100.
                    confidence=0,
                    explanation=(
                        f"{slug} has no pgvector collection of its own, so Chat and the query "
                        f"router have nothing to search in the repository itself.{shared_text} "
                        "This step refreshes existing collections only; the first index is made "
                        "by registering the repository with ingestion (the CLI wizard) or after "
                        "a repair."
                    ),
                    resource_properties=properties,
                    json_properties=StepOutcome(
                        "unverified",
                        cause="no collection of its own registered; never indexed for chat",
                        detail={"last_indexed_at": self.project.last_indexed_at or "",
                                "shared_collections": shared}).as_row(),
                )
            ]

        if not ingested and not counts:
            return [
                ResourceMeasureAnnotation(
                    check_name="rag_ingestion",
                    summary="Nothing indexed for chat",
                    analysis_step=STEP,
                    confidence=0,
                    explanation=(
                        f"Could not refresh or read the pgvector collections for {slug}."
                        + (f" Ingestion error: {error}" if error else "")
                    ),
                    resource_properties=properties,
                    json_properties=StepOutcome(
                        "unverified",
                        cause="pgvector collections could not be refreshed or read",
                        detail={"error": error or ""}).as_row(),
                )
            ]

        return [
            ResourceMeasureAnnotation(
                check_name="rag_ingestion",
                summary=(
                    f"{total_chunks} chunk(s) across {len(collections) - len(shared)} collection(s)"
                    + ("" if ingested else " (from the previous ingestion — refresh failed)")
                ),
                analysis_step=STEP,
                confidence=100 if ingested else 50,
                explanation=(
                    "Refreshed the project's pgvector collections incrementally — a no-op "
                    "when the repository's latest commit matches the last indexed one, a "
                    "re-embed of only the affected collections otherwise. The counts are "
                    "read live from the vector store, so they describe what Chat and the "
                    "query router can actually retrieve rather than what this run did."
                    + (f" This run's refresh failed ({error}); the counts above are from "
                       "the previous ingestion." if not ingested else "")
                ),
                resource_properties=properties,
                # step_outcome.py's docstring opens with this step's own bug:
                # "repo_website_ingestion reported success having embedded one
                # chunk, then 0 chunks from 1 page". A refresh that succeeded
                # and indexed nothing is exactly that shape, so zero chunks is
                # never `recovered` however cleanly the run completed — and
                # serving the previous ingestion's counts after a failed
                # refresh is `partial`: a real answer about an older moment.
                json_properties=(
                    StepOutcome("recovered", detail={"chunks": total_chunks})
                    if ingested and total_chunks else
                    StepOutcome("partial", cause="refresh failed; counts from the previous "
                                                 "ingestion",
                                detail={"error": error or ""})
                    if not ingested else
                    StepOutcome("unverified",
                                cause="refresh succeeded but indexed no chunks")
                ).as_row(),
            )
        ]

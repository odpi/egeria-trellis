"""Bulk actions over many resources at once — republish, and deletion.

An Egeria reset makes every cataloged resource stale simultaneously, so the
per-resource resolve that `Admin ▸ Egeria Links` offers means twenty clicks for
one event. These are the same operations applied to a list.

**Every operation takes an explicit list of slugs.** None of them accepts "do it
to everything stale", even though that is what the caller usually wants and could
easily be derived here. The reason is that a sweep result and a work list are
different things: `egeria-recheck` reports what it found *at that moment*, and a
bulk delete keyed to "whatever is stale now" would act on a list nobody read.
The UI shows rows, the user selects, and the selection is what travels — so what
is acted on is always what was seen.

**Every operation supports `dry_run`, and it is the first thing each caller
should do.** A dry run performs every read and no write, and returns the same
shape, so what you inspect is what will happen.

**Per-item isolation everywhere.** One resource failing must never stop the
other nineteen — the lesson from the import batch that stopped at its first repo
and reported nothing (see `github/org_importer.py`). Each item's outcome is
recorded and the run continues.

## On deleting from Egeria

`delete_in_egeria` is deliberately the narrowest of the three:

* It deletes **only** the element whose GUID RE itself recorded. It never looks
  anything up by name, so it cannot delete something RE did not create a link to.
* It refuses when there is no cached GUID rather than searching for a match.
* It does not cascade. SurveyReports and annotations beneath an asset are left
  to Egeria's own delete semantics rather than RE walking the graph.

That narrowness is the point. Egeria is the catalog of record, and RE otherwise
only ever adds to it; a delete path is the one place RE can destroy governance
history, including reports published by runs nobody in the current session made.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

ACTIONS = ("republish", "resurvey", "discard")
NO_LONGER_STALE = "no longer stale - nothing to resolve"
NOT_PERMITTED_WORDS = "not permitted"


def _refused(registry, entity_type: str, slug: str, write: str, element_guid: str | None = None) -> str:
    """Brief Z2: the one curation decision (`workflows.curate.resource_access`) for one item: "" when the caller
    may make this write, else the item's own result words "not permitted · <reason>". An unexpected failure is
    a refusal with its reason, never a pass."""
    from resource_explorer.workflows.curate import resource_access

    try:
        d = resource_access(registry, entity_type, slug, write, element_guid=element_guid)
    except Exception as exc:  # noqa: BLE001
        return f"{NOT_PERMITTED_WORDS} · could not check access ({type(exc).__name__}: {exc})"[:300]
    return "" if d.allowed else f"{NOT_PERMITTED_WORDS} · {d.reason}"


@dataclass
class BulkResult:
    """What a bulk run did, item by item.

    `details` carries one entry per requested slug with its own outcome, so a
    partial run is legible: "17 succeeded, 3 failed, and here is which" rather
    than a single status for the batch.
    """
    action: str
    dry_run: bool = False
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def record(self, entity_type: str, slug: str, result: str, message: str = "") -> None:
        counter = {"ok": "succeeded", "failed": "failed", "skipped": "skipped"}[result]
        setattr(self, counter, getattr(self, counter) + 1)
        self.details.append({"entity_type": entity_type, "slug": slug,
                             "result": result, "message": message})

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "dry_run": self.dry_run,
                "succeeded": self.succeeded, "failed": self.failed,
                "skipped": self.skipped, "details": self.details}


def _each(targets: list[dict], progress: Callable | None, total: int):
    """Iterate targets, reporting progress. Shared so every operation reports the
    same way and a caller can wire one progress callback for all three."""
    for i, t in enumerate(targets, start=1):
        if progress is not None:
            progress(i, total, t.get("entity_type", "repo"), t.get("slug", ""))
        yield t


def resolve_all(registry, targets: list[dict], action: str, *,
                dry_run: bool = False, progress: Callable | None = None) -> BulkResult:
    """Apply one resolution to many diverged resources.

    `targets` is a list of {"entity_type", "slug"}. Republish is the usual choice
    after a reset: it re-publishes what RE already holds rather than re-scanning,
    and creates fresh Egeria elements from data that did not go anywhere.

    Reuses the same per-resource resolution the single-resource route calls, so
    bulk and single cannot drift apart in what they actually do.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {', '.join(ACTIONS)}")

    result = BulkResult(action=action, dry_run=dry_run)
    total = len(targets)
    from datetime import UTC, datetime

    from resource_explorer.private_publish import PrivateAwaitsOwner, record_waiting

    run_key = f"bulk {action} · {datetime.now(UTC).isoformat()}"     # one waiting row per (slug, this run)
    for t in _each(targets, progress, total):
        entity_type, slug = t.get("entity_type", "repo"), t.get("slug", "")
        if not slug:
            result.record(entity_type, slug, "skipped", "no slug given")
            continue
        # Only a link still recorded as stale may be resolved. A resource republished since the list was read has no
        # stale record left, and resolving it would clear a GOOD link and publish a second element to Egeria.
        link = registry.get_egeria_linkage(entity_type, slug)
        if not link or link.get("status") != "stale":      # an 'uncatalogued' row is not a stale link
            result.record(entity_type, slug, "skipped", NO_LONGER_STALE)
            continue
        # Brief Z2: the same check the single-resource resolve makes (the stale element is checked as not in
        # Egeria). An item the caller may not change is skipped with its own words; the rest proceed.
        why = _refused(registry, entity_type, slug,
                       "publish" if entity_type == "repo" and action != "discard" else "decision", element_guid="")
        if why:
            result.record(entity_type, slug, "skipped", why)
            continue
        if dry_run:
            result.record(entity_type, slug, "ok", f"would {action}")
            continue
        try:
            _resolve_one(registry, entity_type, slug, action)
            result.record(entity_type, slug, "ok", action)
        except PrivateAwaitsOwner as exc:
            # Waiting, not failed (2026-10-13 review): only the private investigation's owner can publish it.
            record_waiting(registry, exc, run_key=run_key, source="bulk")
            result.record(entity_type, slug, "skipped", str(exc)[:300])
        except Exception as exc:
            # Isolated deliberately: nineteen resources must not be abandoned
            # because the twentieth failed.
            log.warning("bulk %s failed for %s/%s: %s", action, entity_type, slug, exc)
            result.record(entity_type, slug, "failed", str(exc)[:300])
    return result


def _resolve_one(registry, entity_type: str, slug: str, action: str) -> None:
    """One resource's resolution — the same steps the single-resource route runs.

    Clearing the unusable GUID is common to all three actions; they differ only
    in what runs afterwards.
    """
    if entity_type == "repo":
        registry.clear_egeria_registration(slug)
    elif entity_type == "database":
        registry.set_database_egeria_guid(slug, "")
    else:
        registry.set_filesystem_egeria_guid(slug, "")
    registry.clear_egeria_linkage_status(entity_type, slug)

    if action == "discard" or entity_type != "repo":
        # Databases and filesystems get the link cleared; re-publishing them from
        # cached local data needs per-type orchestration that does not exist yet
        # (see docs/Backlog.md).
        return

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    orch = SurveyOrchestrator(registry)
    # republish re-derives from what RE already holds — max_fetch_cost="none"
    # keeps it to stored data, which is what makes it the fast, no-re-scan option.
    survey = (orch.run(slug) if action == "resurvey"
              else orch.run(slug, max_fetch_cost="none"))
    # An explicit (re)publish waits for Egeria, as before: RUNS_PUBLISH_INLINE governs a run's auto-publish only.
    EgeriaPublisher(registry=registry).publish(survey, defer_drain=False)


def delete_local(registry, targets: list[dict], *, dry_run: bool = False,
                 progress: Callable | None = None) -> BulkResult:
    """Remove resources from RE entirely: registry row and pgvector collections.

    Destructive and not undoable from here. Dropping the collections is the
    expensive part to reverse — re-registering means re-ingesting, which is the
    costly half of onboarding a repo, not the registration.

    Egeria is left completely alone. A resource deleted locally keeps whatever
    elements it has in the catalog; use `delete_in_egeria` as well if that is
    what you want, deliberately and separately.
    """
    result = BulkResult(action="delete_local", dry_run=dry_run)
    total = len(targets)
    for t in _each(targets, progress, total):
        entity_type, slug = t.get("entity_type", "repo"), t.get("slug", "")
        try:
            if entity_type == "repo":
                project = registry.get(slug)
                if not project:
                    result.record(entity_type, slug, "skipped", "not registered")
                    continue
                collections = list(project.collections or [])
                if dry_run:
                    result.record(entity_type, slug, "ok",
                                  f"would drop {len(collections)} collection(s) and the registry row")
                    continue
                _drop_collections(collections)
                registry.remove(slug)
                result.record(entity_type, slug, "ok",
                              f"removed; dropped {len(collections)} collection(s)")
            else:
                if dry_run:
                    result.record(entity_type, slug, "ok", "would remove the registry row")
                    continue
                remover = (registry.remove_database if entity_type == "database"
                           else registry.remove_filesystem)
                remover(slug)
                result.record(entity_type, slug, "ok", "removed")
        except Exception as exc:
            log.warning("bulk delete_local failed for %s/%s: %s", entity_type, slug, exc)
            result.record(entity_type, slug, "failed", str(exc)[:300])
    return result


def _drop_collections(collections: list[str]) -> None:
    """Drop pgvector collections, tolerating ones that are already gone.

    A collection missing is not a failure of the delete — the intended end state
    is that it does not exist.
    """
    if not collections:
        return
    from resource_explorer.vector_store_pg import MultiCollectionStore

    store = MultiCollectionStore()
    for c in collections:
        try:
            store.drop_collection(c)
        except Exception as exc:
            log.warning("could not drop collection %s: %s", c, exc)


def delete_in_egeria(registry, targets: list[dict], *, dry_run: bool = False,
                     progress: Callable | None = None) -> BulkResult:
    """Delete the Egeria asset RE recorded for each resource.

    Only ever the GUID RE itself stored — see this module's docstring for why
    that narrowness matters. A resource with no cached GUID is skipped, never
    searched for by name.

    This does not touch RE's own data. The local registry row, survey results and
    collections all survive; only the catalog element goes.
    """

    result = BulkResult(action="delete_in_egeria", dry_run=dry_run)

    # Brief Z2: the access check stands IN FRONT of the archive-cascade block (unchanged below): an item the caller may
    # not delete is said as "not permitted · <reason>" before the block would be. Dry run too.
    permitted = []
    for t in targets:
        why = _refused(registry, t.get("entity_type", "repo"), t.get("slug", ""), "delete")
        if why:
            result.record(t.get("entity_type", "repo"), t.get("slug", ""), "skipped", why)
        else:
            permitted.append(t)
    targets = permitted
    total = len(targets)

    # Archive-cascade block (ISSUE-117): a delete in Egeria is an archive/delete write, and while the block is ON none may be sent. Refused
    # before any client is built, in dry run too, so a preview never promises what apply would refuse.
    from resource_explorer.catalogue_gateway import ISSUE_117_WORDS, issue_117_blocked

    # The block is lifted for ONE database slug at most (RE_ISSUE_117_BLOCK_OFF names it): each target is judged by
    # its own slug, the blocked ones fail with the block's words, the rest go on.
    still_open = []
    for t in targets:
        if issue_117_blocked(t.get("slug", "")):
            result.record(t.get("entity_type", "repo"), t.get("slug", ""), "failed", ISSUE_117_WORDS)
        else:
            still_open.append(t)
    targets = still_open
    total = len(targets)
    if not targets:
        return result

    asset_maker = None
    if not dry_run:
        from pyegeria import AssetMaker

        from resource_explorer.egeria_clients import Caller, egeria_client

        # The signed-in person, never the service account (Brief I): Egeria authorizes the
        # delete per person, and a refusal is reported as Egeria's, not retried as anyone else.
        asset_maker = egeria_client(Caller(), purpose="bulk delete in Egeria").of(AssetMaker)

    for t in _each(targets, progress, total):
        entity_type, slug = t.get("entity_type", "repo"), t.get("slug", "")
        guid = _cached_guid(registry, entity_type, slug)
        if not guid:
            # Refused rather than resolved by name: without a GUID RE recorded,
            # there is nothing here that RE can claim to have created.
            result.record(entity_type, slug, "skipped",
                          "no cached Egeria GUID — nothing RE recorded to delete")
            continue
        if dry_run:
            result.record(entity_type, slug, "ok", f"would delete Egeria asset {guid}")
            continue
        try:
            asset_maker.delete_asset(guid)
            # The local GUID goes too: leaving it would point at something that
            # no longer exists, which is precisely the divergence this codebase
            # spent the day learning to detect.
            _clear_guid(registry, entity_type, slug)
            registry.clear_egeria_linkage_status(entity_type, slug)
            result.record(entity_type, slug, "ok", f"deleted {guid}")
        except Exception as exc:
            log.warning("bulk delete_in_egeria failed for %s/%s (%s): %s",
                        entity_type, slug, guid, exc)
            from resource_explorer.egeria_outbox import _refusal_sentence, is_security_refusal

            # Egeria refused THIS person: said as a refusal, with Egeria's sentence, and never
            # retried as the service account (Brief I).
            message = (f"refused by Egeria: {_refusal_sentence(exc)}" if is_security_refusal(exc)
                       else str(exc)[:300])
            result.record(entity_type, slug, "failed", message)
    return result


def _cached_guid(registry, entity_type: str, slug: str) -> str:
    getter = {"repo": registry.get, "database": registry.get_database,
              "filesystem": registry.get_filesystem}.get(entity_type)
    if getter is None:
        return ""
    entity = getter(slug)
    return getattr(entity, "egeria_asset_guid", "") or "" if entity else ""


def _clear_guid(registry, entity_type: str, slug: str) -> None:
    if entity_type == "repo":
        registry.clear_egeria_registration(slug)
    elif entity_type == "database":
        registry.set_database_egeria_guid(slug, "")
    else:
        registry.set_filesystem_egeria_guid(slug, "")

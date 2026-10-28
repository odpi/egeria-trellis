"""Repair operations for RE's repo inventory — Admin-surface actions that go
beyond add/remove: rename a repo's slug, correct its github_url, turn on a
collection type drift detection flagged, and fix up an investigation
membership that points at the wrong repo.

None of these existed before this module. Each one is a multi-step
orchestration across the registry (SQLite/Postgres), pgvector, and (for
rename) GitHub — see docs/repair-operations-design.md for what each has to
guarantee atomically and what it refuses rather than risk.

Vocabulary note (see the brief this module was built from): "project" in
this codebase's identifiers (`ProjectRegistry`, `project_slug`, the
`projects` table) means what current vocabulary calls a **repo**. This
module is new code, so its own names, docstrings, and CLI/API surface all
say "repo" — it does not rename the underlying registry symbols, which is a
separate, larger decision this module deliberately does not make.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from resource_explorer.registry import ProjectRegistry


class RepairError(ValueError):
    """A repair operation refused to run. Always carries a human-readable
    reason — these surface directly in the CLI and the Admin UI, so the
    message is the explanation, not a code."""


@dataclass
class RenameResult:
    old_slug: str
    new_slug: str
    renamed_collections: list[str] = field(default_factory=list)
    unchanged_shared_collections: list[str] = field(default_factory=list)
    tables_touched: dict[str, int] = field(default_factory=dict)


def _owned_collection_rename_map(old_slug: str, new_slug: str,
                                 collections: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split a repo's collections into ones this rename must rename
    (`{old_slug}_{collection_type}` — this repo's own table) and ones it
    must leave alone (a shared collection like `web_docs_egeria_project_org`
    that several repos list and none of them owns — see
    collection_drift.enabled_types()'s docstring for the same shape
    distinction, made for the same reason: matching on prefix/suffix rather
    than exact equality would catch a shared collection that merely
    contains this slug as a substring and rename a table other repos still
    point at by its old name).

    Returns (old_name -> new_name for owned collections, list of shared
    collections left unchanged).
    """
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES

    owned: dict[str, str] = {}
    shared: list[str] = []
    for name in collections:
        matched = False
        for ctype_name in COLLECTION_TYPES:
            if name == f"{old_slug}_{ctype_name}":
                owned[name] = f"{new_slug}_{ctype_name}"
                matched = True
                break
        if not matched:
            shared.append(name)
    return owned, shared


def rename_repo(slug: str, new_slug: str, *, registry: ProjectRegistry | None = None) -> RenameResult:
    """Rename a repo's slug everywhere it appears: the registry (20 tables
    with a project_slug column, 12 with entity_slug, sub_resources,
    repo_dispositions, and sibling repos' parent_slug — see
    ProjectRegistry.rename_project_slug's own comment for the full
    enumeration) and its owned pgvector collection tables.

    Order matters and is chosen so a failure never leaves an orphan:
      1. Compute the rename plan (owned collections vs. shared ones left
         alone) — read-only, can't fail destructively.
      2. Rename each owned pgvector table. If any rename fails partway,
         the ones already renamed are rolled back (renamed back to their
         old name) before raising — so a failed rename_repo() leaves the
         registry and pgvector in the state they started in, not a mix.
      3. Only once every pgvector rename has succeeded, rename the SQL
         side in one transaction (ProjectRegistry.rename_project_slug).

    Investigation/working-set membership is entity_slug-keyed with
    entity_type='repo', already covered by step 3 — no separate step
    needed here (unlike the drop/repoint repair, which touches membership
    without renaming anything).

    Refuses (RepairError) before doing anything if: the repo isn't
    registered, new_slug collides with an existing repo, or new_slug
    normalizes to the same value as the current slug.
    """
    from resource_explorer.vector_store_pg import MultiCollectionStore

    reg = registry or ProjectRegistry()
    old = reg._normalize_slug(slug)
    new = reg._normalize_slug(new_slug)

    project = reg.get(old)
    if not project:
        raise RepairError(f"repo '{slug}' is not registered")
    if old == new:
        raise RepairError(f"'{new_slug}' normalizes to the same slug as '{old}' — nothing to rename")
    if reg.exists(new):
        raise RepairError(f"a repo with slug '{new}' already exists — pick a different name")

    store = MultiCollectionStore()
    rename_plan, shared = _owned_collection_rename_map(old, new, project.collections)

    renamed: list[tuple[str, str]] = []  # (old, new) actually renamed, for rollback
    try:
        for old_name, new_name in rename_plan.items():
            store.rename_collection(old_name, new_name)
            renamed.append((old_name, new_name))
    except Exception as exc:
        # Best-effort rollback of the renames that DID succeed, so a
        # mid-way pgvector failure doesn't leave some tables under the new
        # name while the registry still says old_slug. If the rollback
        # itself fails there is nothing safe left to do automatically —
        # this re-raises with both errors named rather than swallowing
        # either, per the no-silent-success rule (this function returns by
        # raising, never by returning a result that hides what happened).
        rollback_errors = []
        for done_old, done_new in reversed(renamed):
            try:
                store.rename_collection(done_new, done_old)
            except Exception as rollback_exc:
                rollback_errors.append(f"{done_new}->{done_old}: {rollback_exc}")
        detail = f"; rollback also failed for: {rollback_errors}" if rollback_errors else " (rolled back cleanly)"
        raise RepairError(
            f"renaming pgvector collection failed ({exc}); registry was not touched{detail}"
        ) from exc

    new_collections = [rename_plan.get(name, name) for name in project.collections]
    import json
    touched = reg.rename_project_slug(old, new, new_collections_json=json.dumps(new_collections))

    return RenameResult(
        old_slug=old, new_slug=new,
        renamed_collections=[f"{o} -> {n}" for o, n in rename_plan.items()],
        unchanged_shared_collections=shared,
        tables_touched=touched,
    )


def change_github_url(slug: str, new_url: str, *, confirm: bool = False,
                      registry: ProjectRegistry | None = None) -> dict:
    """Point a repo at a different github_url — refuses unless the caller
    explicitly confirms, because the repo's existing pgvector collections
    hold content embedded from the OLD url, and once the url changes that
    content is no longer a description of what the repo points at. Silently
    leaving it in place would mean every future query answers from the
    wrong repository without anything saying so.

    confirm=True drops every existing collection for this repo and resets
    it to the same "not yet indexed" state a freshly-added repo starts in
    (ProjectRegistry.invalidate_indexing) — the caller (CLI/Admin UI) is
    expected to prompt for this explicitly, mirroring `remove`'s existing
    confirmation pattern, and to tell the user a re-index is now needed.

    This does NOT trigger a re-index itself: `resource-explorer refresh
    <slug>` (or an `add`-style ingestion pass) is a separate, potentially
    slow, network-bound operation the caller should run as its own step —
    exactly like a freshly-registered repo, which is what this leaves
    behind.
    """
    from resource_explorer.vector_store_pg import MultiCollectionStore

    reg = registry or ProjectRegistry()
    project = reg.get(slug)
    if not project:
        raise RepairError(f"repo '{slug}' is not registered")
    if project.github_url == new_url:
        raise RepairError(f"'{slug}' already points at {new_url} — nothing to change")
    if not confirm:
        raise RepairError(
            f"changing the github_url invalidates {len(project.collections)} existing "
            f"collection(s) indexed from {project.github_url!r} — they describe the old "
            f"repo, not {new_url!r}. Pass confirm=True (CLI: --yes) to drop them and mark "
            f"'{slug}' as needing a re-index."
        )

    store = MultiCollectionStore()
    dropped = []
    for collection in project.collections:
        store.drop_collection(collection)
        dropped.append(collection)

    reg.update_github_url(project.slug, new_url)
    reg.invalidate_indexing(project.slug)
    return {"slug": project.slug, "old_url": project.github_url, "new_url": new_url,
            "dropped_collections": dropped}


def enable_collection(slug: str, collection_type: str, *,
                      registry: ProjectRegistry | None = None) -> dict:
    """Turn on a collection type collection_drift.detect_drift() flagged as
    eligible-but-not-enabled. A single-collection ingest, not a full
    re-index: IngestionPipeline._ingest_collection() already supports a
    `local_root=None` "incremental single-collection" mode (used previously
    only by the incremental-reindex path) that downloads the repo zipball
    itself and ingests just this one collection type — this reuses that
    rather than re-running the whole onboarding ingest.

    Known limitation, inherited from that fallback path rather than
    introduced here: it always downloads the FULL repo, ignoring
    subproject_path/extra_docs_paths. For a plain (non-monorepo) repo this
    is exactly right; for one registered with --subpath it will scan more
    of the tree than the repo's other collections do. Flagged rather than
    silently accepted — see docs/repair-operations-design.md.
    """
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES
    from resource_explorer.github.client import GitHubClient
    from resource_explorer.ingestion.pipeline import IngestionPipeline
    from resource_explorer.vector_store_pg import MultiCollectionStore

    reg = registry or ProjectRegistry()
    project = reg.get(slug)
    if not project:
        raise RepairError(f"repo '{slug}' is not registered")

    ctype = COLLECTION_TYPES.get(collection_type)
    if not ctype:
        raise RepairError(f"unknown collection type '{collection_type}'")

    collection_name = f"{project.slug}_{ctype.name}"
    if collection_name in project.collections:
        raise RepairError(f"'{collection_name}' is already enabled for '{slug}'")

    client = GitHubClient()
    repo = client.get_repo(project.github_url)
    pipeline = IngestionPipeline()
    count = pipeline._ingest_collection(repo, project.slug, collection_name, ctype)

    if count == 0:
        # detect_drift() said this collection type should have matching
        # content; a real ingest pass finding none means the file-inventory
        # signal and the actual parse/chunk/filter pipeline disagree (e.g.
        # every candidate file was empty after DataPrep filtering). Report
        # rather than silently leaving collections unchanged — this is the
        # ratchet test's "return something that says it failed" shape, just
        # expressed as a refusal instead of a broad except.
        raise RepairError(
            f"ingest produced 0 chunks for '{collection_name}' — drift detection's file "
            f"count doesn't match what the real parser extracted; nothing was enabled"
        )

    collections = [*project.collections, collection_name]
    reg.update_indexed_at(project.slug, collections)
    return {"slug": project.slug, "collection": collection_name, "chunks_inserted": count}


def list_repo_investigation_memberships(slug: str, *,
                                        registry: ProjectRegistry | None = None) -> list[dict]:
    """Which investigations' Folios this repo is a member of — the
    entity-centric view admin needs to repoint or drop a membership.
    investigations.py's own /members endpoints are all investigation-
    centric; this is new, not a duplicate."""
    reg = registry or ProjectRegistry()
    if not reg.get(slug):
        raise RepairError(f"repo '{slug}' is not registered")
    return reg.find_entity_investigations("repo", slug)


def drop_investigation_member(slug: str, investigation_slug: str, *,
                              registry: ProjectRegistry | None = None) -> list[dict]:
    """Remove this repo from one investigation's Folio. Thin wrapper over
    the existing primitives (registry.remove_working_set_member) — the gap
    this module closes is discoverability (list_repo_investigation_
    memberships above) and having an admin-level entry point at all, not a
    new storage operation."""
    reg = registry or ProjectRegistry()
    if not reg.get(slug):
        raise RepairError(f"repo '{slug}' is not registered")
    inv = reg.get_investigation(investigation_slug)
    if not inv:
        raise RepairError(f"investigation '{investigation_slug}' not found")
    ws_slug = reg.investigation_working_set_slug(investigation_slug)
    if not ws_slug:
        raise RepairError(f"investigation '{investigation_slug}' has no Folio yet — nothing to drop")
    return reg.remove_working_set_member(ws_slug, "repo", slug)


def repoint_investigation_member(slug: str, from_investigation: str, to_investigation: str, *,
                                 membership_rationale: str = "",
                                 registry: ProjectRegistry | None = None) -> dict:
    """Move a repo's membership from one investigation's Folio to another —
    e.g. it was scoped into the wrong investigation during Scouting and
    belongs in a different one. Drop-then-add rather than a single UPDATE:
    membership rows carry no identity of their own to move (PK is
    (working_set_slug, entity_type, entity_slug)), so "repoint" IS
    "remove from A, add to B" — there is no third operation this could be
    instead of that pair.

    Refuses if either investigation is missing, or if the repo is not
    actually a member of from_investigation (repointing a membership that
    doesn't exist would silently create one under a "repoint" label instead
    of surfacing that the caller's premise was wrong).
    """
    reg = registry or ProjectRegistry()
    if not reg.get(slug):
        raise RepairError(f"repo '{slug}' is not registered")
    if not reg.get_investigation(from_investigation):
        raise RepairError(f"investigation '{from_investigation}' not found")
    if not reg.get_investigation(to_investigation):
        raise RepairError(f"investigation '{to_investigation}' not found")
    if from_investigation == to_investigation:
        raise RepairError("from and to investigations are the same — nothing to repoint")

    current = {m["investigation_slug"] for m in reg.find_entity_investigations("repo", slug)}
    if from_investigation not in current:
        raise RepairError(f"'{slug}' is not currently a member of '{from_investigation}'")

    from_ws = reg.investigation_working_set_slug(from_investigation)
    reg.remove_working_set_member(from_ws, "repo", slug)
    to_ws = reg.get_or_create_working_set(to_investigation)["slug"]
    members = reg.add_working_set_member(to_ws, "repo", slug, membership_rationale=membership_rationale)
    return {"slug": slug, "from_investigation": from_investigation, "to_investigation": to_investigation,
            "to_folio_members": members}


# ── false-zero database survey rows (hotfix 2026-09-30) ───────────────────────
#
# A publish step used to write a database_surveys row (source
# "egeria-published") even when it had nothing to publish, so an empty
# schema_info was stored as if it were a measurement and became the newest
# "survey". This repair MARKS such rows invalid (never deletes them) so every
# reader excludes them. It is keyed on the damage itself — source is a publish
# source AND schema_info is empty — for ANY slug, not on a slug or timestamp.

def _publish_activity_id(reg: ProjectRegistry, slug: str, surveyed_at: str) -> str:
    """Best-effort: the survey activity_log row written within seconds of the
    false row (the step-run record for these rows does not exist)."""
    from datetime import datetime, timedelta

    try:
        t0 = datetime.fromisoformat(surveyed_at)
    except ValueError:
        return ""
    lo, hi = t0.isoformat(), (t0 + timedelta(seconds=10)).isoformat()
    with reg._conn() as conn:
        row = conn.execute(
            "SELECT id FROM activity_log WHERE entity_slug = ? AND operation = 'survey' "
            "AND intent = 'discovery' AND ts >= ? AND ts <= ? ORDER BY ts LIMIT 1",
            (slug, lo, hi),
        ).fetchone()
    return row[0] if row else ""


def plan_false_zero_survey_repair(registry: ProjectRegistry | None = None) -> list[dict]:
    """Read-only: every unmarked row showing the false-zero damage, any slug.

    Each item: {slug, surveyed_at, source, reason}. Writes nothing.
    """
    from resource_explorer.registry import FALSE_ZERO_REASON

    reg = registry or ProjectRegistry()
    plan = []
    for row in reg.find_false_zero_database_surveys():
        act = _publish_activity_id(reg, row["database_slug"], row["surveyed_at"])
        reason = FALSE_ZERO_REASON + (f" (activity {act})" if act else "")
        plan.append({"slug": row["database_slug"], "surveyed_at": row["surveyed_at"],
                     "source": row["source"], "reason": reason})
    return plan


def apply_false_zero_survey_repair(plan: list[dict],
                                   registry: ProjectRegistry | None = None) -> int:
    """Mark each planned row invalid. Returns rows marked. Never deletes."""
    reg = registry or ProjectRegistry()
    return sum(
        reg.mark_database_survey_invalid(p["slug"], p["surveyed_at"], p["source"], p["reason"])
        for p in plan
    )


def format_false_zero_plan(plan: list[dict], *, apply: bool) -> str:
    """The text the CLI prints (also what tests pin)."""
    verb = "MARKED INVALID" if apply else "WOULD MARK INVALID (dry run, nothing written)"
    lines = [f"{verb}: {len(plan)} row(s)"]
    for p in plan:
        lines.append(f"  {p['slug']}  {p['surveyed_at']}  source={p['source']}  reason={p['reason']}")
    slugs = sorted({p["slug"] for p in plan})
    lines.append(f"slugs: {', '.join(slugs) if slugs else '(none)'}")
    return "\n".join(lines)

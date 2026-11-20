#!/usr/bin/env python
"""Find (and optionally clear) cached Egeria GUIDs that no longer resolve.

Why this exists: RE caches `projects.egeria_asset_guid` so repeat publishes reuse
the same asset. When Egeria's metadata store is reseeded — a redeploy, a wipe,
a fresh quickstart — every cached GUID is left pointing at nothing, while RE
still renders those repos as ☁ Published. "Published to a catalog that no longer
holds it" and "published" look identical, which is the failure this codebase
keeps finding in other guises.

They self-heal on the NEXT publish (`egeria_publisher._find_or_create_asset`
verifies before reuse and clears a stale entry), but until something republishes,
the badge lies and any operation that needs the asset — adding it to an
investigation's working set, for one — fails with a bare NotFound.

Read-only by default. Pass --apply to clear. Clearing also removes that project's
`project_egeria_surveys` rows and its `project_published_annotation_types` rows —
local claims about surveys and published annotations Egeria no longer holds; both
counts are reported so the deletion is never silent.

Three kinds of cached GUID are checked, not one. The asset GUIDs came first; an
investigation's Egeria Project and each disposition WorkingSet's Collection were
added later and were left dangling by every sweep before this one — an
investigation would keep rendering as "linked" to a Project that no longer
exists, and promoting it again would fail on a Project GUID nothing resolves.

    uv run python scripts/sweep_stale_egeria_guids.py
    uv run python scripts/sweep_stale_egeria_guids.py --apply
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="clear the stale entries (default: report only)")
    args = ap.parse_args()

    from resource_explorer.config import get_config
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT slug, egeria_asset_guid FROM projects "
            "WHERE egeria_asset_guid IS NOT NULL AND egeria_asset_guid <> '' "
            "ORDER BY slug"
        ).fetchall()
    cached = [(r["slug"], r["egeria_asset_guid"]) for r in rows]
    if not cached:
        print("No cached Egeria asset GUIDs.")
        # Investigations and working sets dangle independently, and orphaned
        # publish claims outlive the GUID this sweep keys on. Returning here --
        # the third early return in this script to make that mistake -- is how
        # a previous pass left 97 publish claims and 7 GUIDs unreachable.
        _report_orphan_publish_claims(registry, apply=args.apply)
        maker = _connect(get_config)
        if maker is not None:
            _sweep_investigations(registry, maker, apply=args.apply)
            _sweep_outbox(registry, apply=args.apply)
        return 0
    print(f"{len(cached)} project(s) carry a cached Egeria asset GUID.\n")

    # Never guess. An unreachable Egeria must not be read as "everything is
    # stale" — that would clear every valid GUID in the catalog.
    maker = _connect(get_config)
    if maker is None:
        return 2

    # Verified by GUID, type-agnostically (TR-89): a repository is a SourceControlLibrary, not an Asset, so
    # `find_assets` / `get_asset_by_guid` cannot see it and would call every repository stale. The same read
    # egeria_resync's scan uses (ClassificationExplorer.get_element_by_guid). A read that fails is never "stale".
    classifier = _connect_classifier(get_config)
    if classifier is None:
        return 2

    stale, live, unknown = _check_cached(classifier, cached)

    print(f"  resolves in Egeria : {len(live)}")
    print(f"  STALE (dangling)   : {len(stale)}")
    print(f"  could not determine: {len(unknown)}\n")
    for slug, guid in stale:
        print(f"    stale  {slug:32} {guid}")
    for slug, guid, why in unknown:
        print(f"    ?      {slug:32} {guid}  ({why})")

    if not stale:
        print("\nNo stale asset GUIDs.")
        # Still sweep the other two kinds: they dangle independently, and an
        # early return here is why they went unchecked for as long as they did.
        _report_orphan_publish_claims(registry, apply=args.apply)
        _sweep_investigations(registry, maker, apply=args.apply)
        _sweep_outbox(registry, apply=args.apply)
        return 0
    if not args.apply:
        print(f"\nDry run. Re-run with --apply to clear {len(stale)} stale entr(ies).")
        print("Projects that could not be determined are never cleared.")
        # Pre-existing gap, found 2026-08-31 by auditing every return path in
        # this function rather than the one that happened to run: this branch —
        # a dry run that DID find stale GUIDs — never reported orphaned publish
        # claims. It is the same early-return omission the comment above warns
        # about, one path along, and it means the most informative dry run was
        # the least complete one.
        _report_orphan_publish_claims(registry, apply=False)
        _sweep_investigations(registry, maker, apply=False)
        _sweep_outbox(registry, apply=False)
        return 0

    total_surveys = total_published = 0
    for slug, _ in stale:
        result = registry.clear_egeria_registration(slug)
        total_surveys += result["surveys_deleted"]
        total_published += result.get("published_types_deleted", 0)
        print(f"    cleared {slug} (surveys removed: {result['surveys_deleted']}, "
              f"publish claims removed: {result.get('published_types_deleted', 0)})")
    print(f"\nCleared {len(stale)} stale GUID(s), {total_surveys} local survey record(s) "
          f"and {total_published} published-annotation claim(s).")
    print("Those repos now read as un-published, which is the truth. Re-publish to recreate them.")
    _report_orphan_publish_claims(registry, apply=args.apply)
    _sweep_investigations(registry, maker, apply=args.apply)
    _sweep_outbox(registry, apply=args.apply)
    return 0


def _check_cached(classifier, cached):
    """(stale, live, unknown) for `cached` [(slug, guid)], each read by GUID type-agnostically (TR-89).

    A read that fails is `unknown`, never `stale`: only Egeria's own NotFound or "No elements found" is stale."""
    stale, live, unknown = [], [], []
    for slug, guid in cached:
        try:
            found = classifier.get_element_by_guid(guid, graph_query_depth=0)
            if found is None:
                # No answer is not "not there" (pyegeria ISSUE-134).
                unknown.append((slug, guid, "Egeria gave no answer"))
            elif isinstance(found, str):
                if "No elements" in found or not found.strip():
                    stale.append((slug, guid))
                else:
                    live.append((slug, guid))
            elif found:
                live.append((slug, guid))
            else:
                stale.append((slug, guid))
        except Exception as exc:
            name = type(exc).__name__
            if "NotFound" in name or "404" in str(exc):
                stale.append((slug, guid))
            else:
                unknown.append((slug, guid, f"{type(exc).__name__}: {str(exc)[:60]}"))
    return stale, live, unknown


def _connect(get_config):
    """AssetMaker, or None with the reason printed. Never raises."""
    try:
        from pyegeria import AssetMaker
        cfg = get_config().egeria
        maker = AssetMaker(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
        maker.create_egeria_bearer_token()
        return maker
    except Exception as exc:
        print(f"Could not reach Egeria ({type(exc).__name__}: {exc}).")
        print("Refusing to sweep: unreachable is not the same as stale.")
        return None


def _connect_classifier(get_config):
    """ClassificationExplorer (the type-agnostic by-GUID read), or None with the reason printed. Never raises."""
    try:
        from pyegeria import ClassificationExplorer
        cfg = get_config().egeria
        explorer = ClassificationExplorer(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
        explorer.create_egeria_bearer_token()
        return explorer
    except Exception as exc:
        print(f"Could not reach Egeria ({type(exc).__name__}: {exc}).")
        print("Refusing to sweep: unreachable is not the same as stale.")
        return None


def _report_orphan_publish_claims(registry, *, apply: bool) -> None:
    """Publish claims for projects that no longer hold an asset GUID.

    project_published_annotation_types is what the Analyses cards' Published
    badge is derived from. Clearing was keyed on the project HAVING a stale
    GUID, so any row an earlier pass missed became unreachable the moment that
    GUID was cleared -- the badge keeps claiming a publish while nothing points
    at a catalog entry, and no sweep can ever see it again. Measured after the
    2026-08-26 redeploy: 97 rows across the same 23 projects, stranded exactly
    this way by a sweep run from an older checkout.
    """
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT p.slug AS slug, count(*) AS n "
            "FROM project_published_annotation_types t "
            "JOIN projects p ON p.slug = t.project_slug "
            "WHERE coalesce(p.egeria_asset_guid, '') = '' "
            "GROUP BY p.slug ORDER BY n DESC"
        ).fetchall()
    if not rows:
        print("\nNo orphaned publish claims.")
        return
    total = sum(r["n"] for r in rows)
    print(f"\n{total} publish claim(s) across {len(rows)} project(s) have no asset GUID behind them.")
    print("Those repos render as Published while nothing points at a catalog entry.")
    for r in rows[:10]:
        print(f"    orphan  {r['slug']:32} {r['n']}")
    if len(rows) > 10:
        print(f"    ... and {len(rows) - 10} more")
    if not apply:
        print("    Dry run — re-run with --apply to clear these too.")
        return
    with registry._conn() as conn:
        deleted = conn.execute(
            "DELETE FROM project_published_annotation_types WHERE project_slug IN ("
            "SELECT slug FROM projects WHERE coalesce(egeria_asset_guid, '') = '')"
        ).rowcount
    print(f"    Cleared {deleted} orphaned publish claim(s). Those repos now read as "
          "un-published, which is the truth.")


def _sweep_outbox(registry, *, apply: bool) -> None:
    """Completed outbox rows for resources that no longer hold an asset GUID.

    A FOURTH kind of cached Egeria GUID, and it did not exist when §6 of
    docs/egeria-reset-recovery.md named three. `egeria_outbox.egeria_guid` is
    written on success and becomes the row's primary identity: `apply_element`
    short-circuits on it and returns without writing, and a row marked `done`
    is never claimed again. So after a reset these are the most confidently
    wrong rows in the registry — they assert a completed publish, carry a GUID
    nothing resolves, and the retry machinery deliberately will not touch them.

    **The outbox is not a recovery mechanism for a wipe.** Re-running the
    surveys is. This only stops the queue claiming otherwise.

    KEYED ON THE ASSET, NOT ON A PER-ROW LOOKUP, for two reasons. Correctness:
    an annotation hangs off a SurveyReport which hangs off the asset, so if the
    asset is gone every row published beneath it is gone too — the same
    inference `_report_orphan_publish_claims` already makes one table along.
    And honesty: the first version of this passed the connection factory to
    `_resolves` as though it were a callable, so every lookup failed, all 92
    rows came back "could not be determined", and the sweep abstained on
    everything while looking like it had checked. That is precisely the failure
    `_resolves`'s own docstring records from an earlier round, reproduced by
    someone who had just read it.

    Cleared rather than re-queued, deliberately: re-queueing would replay a
    stored payload against a parent report and asset that are also gone,
    creating the annotation under a dead parentGUID — a second wrong state
    instead of an absent one.
    """
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT o.entity_slug AS slug, count(*) AS n "
                "FROM egeria_outbox o "
                "LEFT JOIN projects p ON p.slug = o.entity_slug "
                "WHERE o.status = 'done' AND coalesce(o.egeria_guid, '') <> '' "
                "  AND coalesce(p.egeria_asset_guid, '') = '' "
                "GROUP BY o.entity_slug ORDER BY n DESC"
            ).fetchall()
    except Exception as exc:
        print(f"\nCould not read egeria_outbox ({exc}) — skipped.")
        return
    if not rows:
        print("\nNo completed outbox rows are stranded without an asset.")
        return
    total = sum(r["n"] for r in rows)
    print(f"\n{total} completed outbox row(s) across {len(rows)} resource(s) claim a publish "
          f"under an asset that is gone.")
    print("The retry machinery will never revisit them: a done row with a GUID is terminal.")
    for r in rows[:10]:
        print(f"    stranded  {r['slug']:32} {r['n']} row(s)")
    if len(rows) > 10:
        print(f"    ... and {len(rows) - 10} more resource(s)")
    if not apply:
        print("    Dry run — re-run with --apply to clear these too.")
        return
    with registry._conn() as conn:
        deleted = conn.execute(
            "DELETE FROM egeria_outbox WHERE id IN ("
            "  SELECT o.id FROM egeria_outbox o"
            "  LEFT JOIN projects p ON p.slug = o.entity_slug"
            "  WHERE o.status = 'done' AND coalesce(o.egeria_guid, '') <> ''"
            "    AND coalesce(p.egeria_asset_guid, '') = '')"
        ).rowcount
    print(f"    Cleared {deleted} stranded outbox row(s). Re-run the surveys to republish — "
          "the outbox does not restore them.")


def _resolves(lookup, guid: str) -> bool | None:
    """True / False / None for "could not determine".

    `lookup` is a callable taking a GUID. It is passed in rather than picked
    here because Projects and Collections need their OWN clients --
    ProjectManager.get_project_by_guid and
    CollectionManager.get_collection_by_guid. An earlier version of this
    function called AssetMaker.get_element_by_guid, which does not exist: every
    lookup raised AttributeError, every result became "could not determine",
    and the caller -- which collected only definite-False -- printed "all
    resolve. Nothing to clear." A sweep that reports confidence it does not
    have is worse than the stale GUIDs it was meant to find.

    Never collapse None into False: an unreachable Egeria must not read as
    everything being stale.
    """
    try:
        found = lookup(guid)
    except Exception as exc:
        name = type(exc).__name__
        if "NotFound" in name or "404" in str(exc):
            return False
        # AttributeError, auth failures, transport errors -- all genuinely
        # unknown, and all reported rather than silently dropped.
        return None
    if isinstance(found, str):
        return "No elements" not in found and bool(found.strip())
    return bool(found)


def _sweep_investigations(registry, maker, *, apply: bool) -> None:
    """Investigation -> Egeria Project, and each WorkingSet -> Collection.

    Reported and cleared separately from the asset sweep because they fail
    differently: a dangling asset GUID makes a badge lie, while a dangling
    Project GUID makes promote() fail outright against a Project nothing
    resolves.
    """
    with registry._conn() as conn:
        invs = conn.execute(
            "SELECT slug, egeria_project_guid FROM investigations "
            "WHERE egeria_project_guid IS NOT NULL AND egeria_project_guid <> ''"
        ).fetchall()
        sets = conn.execute(
            "SELECT slug, egeria_collection_guid FROM working_sets "
            "WHERE egeria_collection_guid IS NOT NULL AND egeria_collection_guid <> ''"
        ).fetchall()
    with registry._conn() as conn:
        ctxs = conn.execute(
            "SELECT entity_type, entity_slug, egeria_project_guid "
            "FROM entity_egeria_project_context "
            "WHERE coalesce(egeria_project_guid, '') <> ''"
        ).fetchall()

    # Every kind is counted in this guard. Returning on a subset is the mistake
    # this script has now made four times -- each new kind of cached GUID was
    # added behind a check for the previous ones, and so went unswept until
    # something broke that pointed at it.
    if not invs and not sets and not ctxs:
        print("\nNo investigation, working-set or resource-context GUIDs cached. "
              "Nothing further to sweep.")
        return

    print(f"\n{len(invs)} investigation(s), {len(sets)} working set(s) and {len(ctxs)} "
          "resource context row(s) carry a cached GUID.")
    try:
        from resource_explorer.config import get_config
        from pyegeria import CollectionManager, ProjectManager

        cfg = get_config().egeria
        pm = ProjectManager(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
        cm = CollectionManager(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
        pm.create_egeria_bearer_token()
        cm.create_egeria_bearer_token()
    except Exception as exc:
        print(f"    Could not open Project/Collection clients ({type(exc).__name__}: {exc}).")
        print("    Skipping — unreachable is not the same as stale.")
        return

    stale_inv, stale_set, stale_ctx, unknown = [], [], [], []
    for r in invs:
        verdict = _resolves(pm.get_project_by_guid, r["egeria_project_guid"])
        if verdict is False:
            stale_inv.append((r["slug"], r["egeria_project_guid"]))
        elif verdict is None:
            unknown.append(("investigation", r["slug"], r["egeria_project_guid"]))
    for r in sets:
        verdict = _resolves(cm.get_collection_by_guid, r["egeria_collection_guid"])
        if verdict is False:
            stale_set.append((r["slug"], r["egeria_collection_guid"]))
        elif verdict is None:
            unknown.append(("working set", r["slug"], r["egeria_collection_guid"]))

    # A resource's own Egeria Project context — the FOURTH place a Project GUID
    # is cached, and the one that decides whether publishing is allowed and what
    # it attaches to. Missed by every earlier sweep: after the 2026-08-26
    # redeploy the investigation GUIDs were cleared while 21 context rows still
    # pointed at those same dead Projects, so publishing would have attached to
    # a Project that does not exist.
    seen_projects: dict[str, bool | None] = {}
    for r in ctxs:
        guid = r["egeria_project_guid"]
        if guid not in seen_projects:
            seen_projects[guid] = _resolves(pm.get_project_by_guid, guid)
        verdict = seen_projects[guid]
        if verdict is False:
            stale_ctx.append((f"{r['entity_type']}:{r['entity_slug']}", guid))
        elif verdict is None:
            unknown.append(("project context", f"{r['entity_type']}:{r['entity_slug']}", guid))

    for slug, guid in stale_inv:
        print(f"    stale investigation  {slug:28} {guid}")
    for slug, guid in stale_set:
        print(f"    stale working set    {slug:28} {guid}")
    if stale_ctx:
        print(f"    {len(stale_ctx)} resource context row(s) point at a Project that is gone:")
        for slug, guid in stale_ctx[:6]:
            print(f"        {slug:32} {guid}")
        if len(stale_ctx) > 6:
            print(f"        ... and {len(stale_ctx) - 6} more")
    # Reported, never dropped: silently swallowing these is what made an
    # entirely broken lookup print "all resolve".
    for kind, slug, guid in unknown:
        print(f"    ? {kind:18} {slug:28} {guid}  (could not determine — not cleared)")

    if not stale_inv and not stale_set and not stale_ctx:
        print(f"    Nothing stale ({len(unknown)} undetermined)." if unknown
              else "    All resolve. Nothing to clear.")
        return
    if not apply:
        print("    Dry run — re-run with --apply to clear these too.")
        return
    with registry._conn() as conn:
        for slug, _ in stale_inv:
            conn.execute(
                "UPDATE investigations SET egeria_project_guid = '', "
                "egeria_project_status = 'local' WHERE slug = ?", (slug,))
        for slug, _ in stale_set:
            conn.execute(
                "UPDATE working_sets SET egeria_collection_guid = '' WHERE slug = ?", (slug,))
        for ref, _ in stale_ctx:
            etype, _, eslug = ref.partition(":")
            # Status goes back to 'unset', not 'personal' or 'declined': the
            # decision that WAS made pointed at a Project that no longer exists,
            # so it has to be asked again rather than silently reinterpreted as
            # some other answer the user never gave.
            conn.execute(
                "UPDATE entity_egeria_project_context SET egeria_project_guid = '', "
                "egeria_project_qualified_name = '', status = 'unset' "
                "WHERE entity_type = ? AND entity_slug = ?", (etype, eslug))
    print(f"    Cleared {len(stale_inv)} investigation, {len(stale_set)} working-set and "
          f"{len(stale_ctx)} resource-context GUID(s).")
    print("    Those investigations read as local-only again — promote to rebuild them.")
    if stale_ctx:
        print("    Those resources will ask again which Egeria Project they belong to, "
              "which is the honest state: the one they had is gone.")


if __name__ == "__main__":
    sys.exit(main())

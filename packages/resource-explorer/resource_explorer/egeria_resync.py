"""Find and repair drift between Resource Explorer and Egeria.

RE keeps its own registry, so an Egeria reset loses nothing of yours — but it
leaves RE holding pointers into a catalog that no longer contains what they
point at. Rebuilding after the 2026-08-26 redeploy took four operations run by
hand in a specific order, and one of them did not exist. This is that sequence,
made a first-class thing.

Two rules the whole module is built around:

**Unreachable is never stale.** A failed lookup means we could not tell, and is
reported as such — never cleared. Reading a network error as "gone" would clear
a live catalog, which is exactly what an earlier version of the sweep script
did: it declared all 23 valid GUIDs stale on a healthy Egeria.

**Ambiguity is reported, never guessed.** Some drift has one correct repair and
some needs a person. A resource whose Egeria Project was deleted cannot be
re-bound automatically — RE does not know which Project it should join now, and
picking one would recreate the class of wrong-but-plausible answer this codebase
keeps removing. Those land in `decisions`, not `repairs`.

Scanning never writes. Repairs run only for the step names a caller asks for.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: Repairs, in dependency order. Order is load-bearing: clearing stale pointers
#: must happen before republishing (or a publish reuses a dead GUID), and
#: republishing before relinking (or a member still has no asset to link to).
REPAIR_STEPS = (
    "reauthor_survey_definitions",
    "clear_stale_assets",
    "clear_orphan_publish_claims",
    "flag_vanished_publishes",
    "flag_stale_dbfs_assets",
    "clear_stale_investigations",
    "clear_stale_contexts",
    "catalog_assets",
    "republish_survey_results",
    "relink_investigation_members",
)

#: Steps that WRITE to Egeria and cost real time, so the UI leaves them
#: unticked. Everything else here either clears a local record or is cheap
#: enough that ticking it by default is not a decision made on the user's
#: behalf. `catalog_assets` is deliberately NOT in this set: it publishes only
#: `repo_health`, which fetches no archives, and without an asset the relink
#: step below it has nothing to attach to — leaving it off by default would
#: make the ordered pass silently do half its job.
EXPENSIVE_STEPS = frozenset({"reauthor_survey_definitions", "republish_survey_results"})

#: What `catalog_assets` publishes. One step, chosen because it registers the
#: asset without any `fetch_cost="download"` work — the whole point of having a
#: cheap tier is that re-cataloguing a corpus does not re-download it.
CATALOG_STEPS = ("repo_health",)

#: The analysis ids a registration-only publish can leave behind. A repo whose
#: published analyses are a subset of this has an asset but no real survey
#: report, which is what `republish_survey_results` exists to fill in. Defined
#: as a set membership rather than a row count: "fewer than N analyses" would
#: be a number whose meaning drifts the moment the catalog gains an entry.
REGISTRATION_ONLY_ANALYSES = frozenset({"repository_health"})

#: The steps a scheduled, unattended pass is allowed to apply — see
#: scan_and_clear()/start_scheduler() below. Deliberately a strict subset of
#: REPAIR_STEPS: every one of these clears a LOCAL record after verifying live
#: (never guesses — a lookup failure is `undetermined`, not cleared), costs no
#: real time (none are in EXPENSIVE_STEPS), and never needs a human decision
#: (no finding these map to ever sets needs_decision=True). `catalog_assets`
#: is excluded even though it's cheap: it PUBLISHES to Egeria, which is a
#: judgment call about what an asset should look like, not a pure cleanup —
#: left for a human to trigger deliberately, same reasoning REPAIR_STEPS'
#: `republish_survey_results`/`relink_investigation_members` stay manual.
#:
#: `clear_stale_investigations`/`clear_stale_contexts` moved OUT 2026-09-17
#: (PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md item 4,
#: REPLY-PUBLISH-STATE-GO-AHEAD.md §3): `investigations.egeria_project_guid`/
#: `entity_egeria_project_context.egeria_project_guid` can hold a GUID this
#: app created OR one a person deliberately bound to an already-existing
#: Egeria Project (`bind_egeria_project`), with nothing distinguishing the
#: two — so "clears a LOCAL record after verifying live" was never actually
#: true for these two: a bound GUID a person chose is not a local record this
#: app can regenerate, and a transient resolve failure (an outage, a
#: permissions change, a restart) would silently unmake that choice with
#: nothing left to show it happened. That violates this set's own "never
#: needs a human decision" criterion — clearing a person's binding always
#: does. Both repair steps stay in REPAIR_STEPS for a human to apply
#: deliberately from Admin > Egeria Alignment, same as `catalog_assets`.
#:
#: `flag_vanished_publishes` added the same day, and belongs here for the
#: opposite reason those two left: it never deletes anything — it only WRITES
#: a flag (`egeria_linkage_status`), and the row it's about stays exactly as
#: it was (§4's "flag, do not delete"). A false positive here costs a reader
#: one wrong badge state until the next pass corrects it, not a decision
#: silently unmade.
#:
#: `flag_stale_dbfs_assets` added 2026-10-03 for the same reason as
#: `flag_vanished_publishes`: it only WRITES a flag, and never deletes. It is
#: the databases/filesystems half of what `clear_stale_assets` does for repos,
#: but deliberately weaker — `clear_stale_assets` clears a repo's GUID, whereas
#: this flags a database/filesystem row in `egeria_linkage_status` and leaves
#: `egeria_asset_guid` alone, so "published once" history survives and a row
#: that resolves again has its flag cleared. Found live 2026-10-03: two
#: databases kept GUIDs Egeria no longer held and read as published, because
#: no scheduled step looked at anything but the `projects` table. Clearing a
#: database/filesystem GUID stays a human's call (republish / re-survey /
#: discard from Admin > Egeria Links). Writes nothing to Egeria.
SAFE_SCHEDULED_STEPS = (
    "clear_stale_assets",
    "clear_orphan_publish_claims",
    "flag_vanished_publishes",
    "flag_stale_dbfs_assets",
)


@dataclass
class Finding:
    """One kind of drift: what it is, what it affects, and what to do."""
    key: str
    title: str
    detail: str
    items: list = field(default_factory=list)
    repair_step: str = ""       # "" when no automatic repair is correct
    needs_decision: bool = False

    @property
    def count(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict:
        return {
            "key": self.key, "title": self.title, "detail": self.detail,
            "count": self.count, "items": self.items[:50],
            "truncated": max(0, self.count - 50),
            "repair_step": self.repair_step, "needs_decision": self.needs_decision,
            # Surfaced so the UI can leave a costly repair unticked WITHOUT
            # keeping its own list of which those are. A second copy in the
            # frontend would be one more thing to forget when a step is added,
            # and the failure mode of forgetting is silent: a slow, catalog-
            # writing repair that runs because it defaulted to on.
            "expensive": self.repair_step in EXPENSIVE_STEPS,
            # Same reasoning as `expensive` above, for the other frontend
            # duplication resync.js's own header comment flagged: whether this
            # finding's repair already runs unattended (SAFE_SCHEDULED_STEPS)
            # is a fact about the backend's schedule, not something the UI
            # should keep a second, hand-maintained copy of.
            "scheduled": self.repair_step in SAFE_SCHEDULED_STEPS,
        }


@dataclass
class ScanResult:
    reachable: bool = True
    unreachable_reason: str = ""
    findings: list = field(default_factory=list)
    undetermined: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "reachable": self.reachable,
            "unreachable_reason": self.unreachable_reason,
            "findings": [f.as_dict() for f in self.findings],
            # Surfaced separately from findings so a lookup that FAILED is never
            # read as a lookup that found nothing.
            "undetermined": self.undetermined[:50],
            "undetermined_count": len(self.undetermined),
            "total": sum(f.count for f in self.findings),
            "repairable": sum(f.count for f in self.findings if f.repair_step),
            "needs_decision": sum(f.count for f in self.findings if f.needs_decision),
        }


class EgeriaResync:
    """Scan for drift, and repair the parts that have one correct repair."""

    def __init__(self, registry: "ProjectRegistry | None" = None) -> None:
        from resource_explorer.registry import ProjectRegistry

        self._registry = registry or ProjectRegistry()
        self._clients: dict = {}

    # ── connections ─────────────────────────────────────────────────────────
    def _connect(self) -> tuple[bool, str]:
        if self._clients:
            return True, ""
        try:
            from pyegeria import AssetMaker, ClassificationExplorer, CollectionManager, ProjectManager

            from resource_explorer.egeria_clients import current_principal, egeria_client

            # Who (Brief I): the scheduled pass runs inside acting_as(Daemon(RESYNC)) (`_loop`);
            # the admin's Scan/Apply routes run as the signed-in admin.
            clients = egeria_client(current_principal(), purpose="egeria resync")
            am = clients.of(AssetMaker)
            pm = clients.of(ProjectManager)
            cm = clients.of(CollectionManager)
            # ClassificationExplorer, not MetadataExpert —
            # PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md / REPLY-PUBLISH-STATE-GO-AHEAD.md §1:
            # MetadataExpert is for special situations with a differently-
            # shaped response; ClassificationExplorer.get_element_by_guid is
            # the plain existence check.
            ce = clients.of(ClassificationExplorer)
            self._clients = {"asset": am, "project": pm, "collection": cm, "classification": ce}
            return True, ""
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _resolves(lookup: Callable, guid: str) -> bool | None:
        """True / False / None — the third is "could not determine".

        Never collapse None into False. An unreachable Egeria, an auth failure,
        or a method that does not exist on the client are all reasons we cannot
        tell, and treating any of them as "gone" clears live data.
        """
        try:
            found = lookup(guid)
        except Exception as exc:
            name = type(exc).__name__
            if "NotFound" in name or "404" in str(exc):
                return False
            return None
        if found is None:
            # No answer is not "gone" (pyegeria ISSUE-134: up to 6.2.1 a swallowed failure returned None).
            return None
        if isinstance(found, str):
            return "No elements" not in found and bool(found.strip())
        return bool(found)

    # ── scan ────────────────────────────────────────────────────────────────
    def scan(self) -> ScanResult:
        res = ScanResult()
        ok, why = self._connect()
        if not ok:
            res.reachable = False
            res.unreachable_reason = why
            return res

        res.findings.append(self._scan_assets(res))
        res.findings.append(self._scan_orphan_publish_claims())
        res.findings.append(self._scan_vanished_publishes(res))
        res.findings.append(self._scan_flagged_publish_rows())
        res.findings.append(self._scan_dbfs_assets(res))
        res.findings.append(self._scan_flagged_dbfs_rows())
        res.findings.append(self._scan_investigation_guids(res))
        res.findings.append(self._scan_contexts(res))
        res.findings.append(self._scan_unlinked_members(res))
        res.findings.append(self._scan_unpublished_but_expected())
        res.findings.append(self._scan_unpublishable())
        res.findings.append(self._scan_registration_only())
        res.findings.append(self._scan_registration_blocked())
        res.findings.append(self._scan_local_investigations())
        res.findings.append(self._scan_definition_drift(res))
        res.findings.append(self._scan_specification_gap())
        res.findings = [f for f in res.findings if f.count]
        return res

    def _scan_assets(self, res: ScanResult) -> Finding:
        """Cached asset GUIDs that no longer resolve.

        Verified the way EgeriaPublisher._find_or_create_asset verifies before
        reusing one — search by qualifiedName and check the GUID is among the
        results. NOT get_asset_by_guid, which raises NotFound for GUIDs that
        plainly exist.
        """
        am = self._clients["asset"]
        with self._registry._conn() as conn:
            rows = conn.execute(
                "SELECT slug, github_url, egeria_asset_guid FROM projects "
                "WHERE coalesce(egeria_asset_guid, '') <> '' ORDER BY slug"
            ).fetchall()
        stale = []
        for r in rows:
            try:
                # find_assets, not find_software_capabilities: the repository
                # is a plain Asset since the 2026-09-14 SourceControlLibrary
                # correction (egeria_publisher.py's module docstring).
                check = am.find_assets(
                    search_string=f"GitHubRepository::{r['github_url']}",
                    starts_with=True, ignore_case=False, output_format="JSON",
                )
            except Exception as exc:
                res.undetermined.append({
                    "kind": "asset", "ref": r["slug"],
                    "reason": f"{type(exc).__name__}: {str(exc)[:80]}",
                })
                continue
            if check is None:
                # No answer is not "not live" (pyegeria ISSUE-134): undetermined, never cleared.
                res.undetermined.append({"kind": "asset", "ref": r["slug"],
                                         "reason": "Egeria gave no answer (the client returned nothing)"})
                continue
            live = isinstance(check, list) and any(
                (e.get("elementHeader") or {}).get("guid") == r["egeria_asset_guid"]
                for e in check
            )
            if not live:
                stale.append({"slug": r["slug"], "guid": r["egeria_asset_guid"]})
        return Finding(
            key="stale_assets",
            title="Cached asset GUIDs pointing at nothing",
            detail="These repos render as Published while their Egeria entry is gone. "
                   "Clearing also drops their survey records and publish claims, which "
                   "are local claims about what Egeria holds.",
            items=stale, repair_step="clear_stale_assets",
        )

    def _scan_orphan_publish_claims(self) -> Finding:
        """Publish claims that name a report this registry no longer records.

        The docstring here used to say: "These outlive the GUID the clearing
        keys on, so anything an earlier pass missed becomes unreachable the
        moment that GUID is cleared." It then keyed on asset absence anyway,
        and the thing it warned about happened.

        Measured 2026-08-31, right after `catalog_assets` ran: this scan
        reported **0** while **36** orphaned claims existed. Giving the repos
        their assets back made every one of their pre-wipe claims invisible to
        the scan, so the card never rendered and its repair — which by then
        could clear them — was unreachable from the panel. A finding that
        reports zero is read as "nothing to do", which is why this failure
        needs no error to be costly.

        Keyed on the claim now, matching `_do_clear_orphan_publish_claims`:
        a claim whose report_guid is not in `project_egeria_surveys` names a
        report nothing stands behind, whether or not the project has an asset.
        The asset-absence arm is kept as a second reason, for a claim that
        carries no report guid at all and so cannot be joined.
        """
        with self._registry._conn() as conn:
            rows = conn.execute(
                "SELECT slug, sum(n) AS n FROM ("
                "  SELECT p.slug AS slug, count(*) AS n "
                "  FROM project_published_annotation_types t "
                "  JOIN projects p ON p.slug = t.project_slug "
                "  WHERE coalesce(p.egeria_asset_guid, '') = '' "
                "     OR (coalesce(t.egeria_report_guid, '') <> '' "
                "         AND t.egeria_report_guid NOT IN ("
                "           SELECT egeria_report_guid FROM project_egeria_surveys "
                "           WHERE coalesce(egeria_report_guid, '') <> '')) "
                "  GROUP BY p.slug "
                "  UNION ALL "
                "  SELECT p.slug AS slug, count(*) AS n "
                "  FROM project_published_analyses a "
                "  JOIN projects p ON p.slug = a.project_slug "
                "  WHERE coalesce(p.egeria_asset_guid, '') = '' "
                "     OR (coalesce(a.egeria_report_guid, '') <> '' "
                "         AND a.egeria_report_guid NOT IN ("
                "           SELECT egeria_report_guid FROM project_egeria_surveys "
                "           WHERE coalesce(egeria_report_guid, '') <> '')) "
                "  GROUP BY p.slug"
                ") u GROUP BY slug ORDER BY n DESC"
            ).fetchall()
        return Finding(
            key="orphan_publish_claims",
            title="Publish claims with no asset behind them",
            detail="The Analyses cards' Published badge is derived from these. "
                   "Nothing points at an Egeria entry, so the badge is false.",
            items=[{"slug": r["slug"], "claims": r["n"]} for r in rows],
            repair_step="clear_orphan_publish_claims",
        )

    def _scan_vanished_publishes(self, res: ScanResult) -> Finding:
        """Publish claims that ARE locally coherent, whose report Egeria no
        longer has. PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md /
        REPLY-PUBLISH-STATE-GO-AHEAD.md §2 — the condition
        `_scan_orphan_publish_claims` cannot see, because it only checks local
        consistency. This is the other one: `egeria_report_guid` names a
        record `project_egeria_surveys` still has, and Egeria does not.

        Never deletes. `_do_flag_vanished_publishes` only writes a flag
        (`egeria_linkage_status`, entity_type='repo_publish') — the row itself
        is the only evidence a publish happened, and it stays exactly as it
        is (§4's rule).
        """
        ce = self._clients["classification"]
        latest = self._registry.get_latest_egeria_surveys_all_projects()
        vanished = []
        for slug, survey in latest.items():
            guid = survey["egeria_report_guid"]
            v = self._resolves(
                lambda g: ce.get_element_by_guid(g, graph_query_depth=0), guid)
            if v is False:
                vanished.append({"slug": slug, "guid": guid})
            elif v is None:
                res.undetermined.append({"kind": "publish", "ref": slug, "reason": "lookup failed"})
        return Finding(
            key="vanished_publishes",
            title="Published elements no longer in the store",
            detail="The report these claims point to no longer resolves in Egeria "
                   "— flagged on the record, not deleted.",
            items=vanished,
            repair_step="flag_vanished_publishes",
        )

    def _scan_flagged_publish_rows(self) -> Finding:
        """Existing publish flags that need re-checking, keyed on the ROWS.

        Exists because `scan()` drops empty findings: once a republish
        resolves, `vanished_publishes` is empty and nothing would ever offer
        (or schedule) the heal step. A flag that is still on the record is a
        reason to run `flag_vanished_publishes` whatever the vanished finding
        says. 'uncatalogued' rows count only once a report exists to re-read —
        otherwise the pass has nothing to do for them.
        """
        latest = self._registry.get_latest_egeria_surveys_all_projects()
        items = []
        for row in self._registry.list_egeria_linkages("repo_publish"):
            if row["status"] == "stale" or row["entity_slug"] in latest:
                items.append({"slug": row["entity_slug"], "status": row["status"]})
        return Finding(
            key="flagged_publish_rows",
            title="Publish flags awaiting re-check",
            detail="These repos carry a publish flag. Re-checking reads each "
                   "repo's latest report and clears the flag if it resolves.",
            items=items,
            repair_step="flag_vanished_publishes",
        )

    #: entity_type -> registry lister, for the databases/filesystems scan.
    _DBFS_TYPES = (("database", "list_databases"), ("filesystem", "list_filesystems"))

    def _dbfs_with_guid(self):
        """(entity_type, slug, guid) for every database/filesystem that carries
        a cached GUID. One with NO GUID is never yielded: there is nothing to
        verify, and flagging it would claim it was published once."""
        for etype, lister in self._DBFS_TYPES:
            for ent in getattr(self._registry, lister)():
                guid = getattr(ent, "egeria_asset_guid", "") or ""
                if guid:
                    yield etype, ent.slug, guid

    def _resolve_dbfs(self, guid: str) -> bool | None:
        ce = self._clients["classification"]
        return self._resolves(
            lambda g: ce.get_element_by_guid(g, graph_query_depth=0), guid)

    def _scan_dbfs_assets(self, res: ScanResult) -> Finding:
        """Databases and filesystems whose cached asset GUID no longer resolves.

        The databases/filesystems counterpart of `_scan_assets` (which reads the
        `projects` table only, and so never saw these). Uses the type-agnostic
        element read, as `_scan_vanished_publishes` does. Re-verifies every
        cached GUID every pass — flagged AND published — so a flag can also be
        cleared. Undetermined is never gone.
        """
        dead = []
        for etype, slug, guid in self._dbfs_with_guid():
            v = self._resolve_dbfs(guid)
            if v is False:
                dead.append({"entity_type": etype, "slug": slug, "guid": guid})
            elif v is None:
                res.undetermined.append({
                    "kind": etype, "ref": slug, "reason": "lookup failed"})
        return Finding(
            key="stale_dbfs_assets",
            title="Database/filesystem asset GUIDs pointing at nothing",
            detail="These render as published while their Egeria entry is gone. "
                   "Flagged stale in the linkage table, never cleared: the GUID "
                   "stays so the history survives, and the flag is removed if the "
                   "element resolves again.",
            items=dead, repair_step="flag_stale_dbfs_assets",
        )

    def _scan_flagged_dbfs_rows(self) -> Finding:
        """Existing database/filesystem stale flags, keyed on the ROWS.

        `scan()` drops empty findings, so once a flagged GUID resolves again
        `stale_dbfs_assets` is empty and nothing would offer (or schedule) the
        step that clears the flag. Same shape as `_scan_flagged_publish_rows`.
        """
        items = []
        for etype, _ in self._DBFS_TYPES:
            for row in self._registry.list_egeria_linkages(etype, statuses=("stale",)):
                items.append({"entity_type": etype, "slug": row["entity_slug"],
                              "status": row["status"]})
        return Finding(
            key="flagged_dbfs_rows",
            title="Database/filesystem stale flags awaiting re-check",
            detail="These carry a stale flag. Re-checking clears it if the "
                   "asset resolves again.",
            items=items, repair_step="flag_stale_dbfs_assets",
        )

    def _scan_investigation_guids(self, res: ScanResult) -> Finding:
        pm, cm = self._clients["project"], self._clients["collection"]
        stale = []
        with self._registry._conn() as conn:
            invs = conn.execute(
                "SELECT slug, egeria_project_guid FROM investigations "
                "WHERE coalesce(egeria_project_guid, '') <> ''"
            ).fetchall()
            sets = conn.execute(
                "SELECT slug, egeria_collection_guid FROM working_sets "
                "WHERE coalesce(egeria_collection_guid, '') <> ''"
            ).fetchall()
        for r in invs:
            v = self._resolves(pm.get_project_by_guid, r["egeria_project_guid"])
            if v is False:
                stale.append({"kind": "investigation", "ref": r["slug"],
                              "guid": r["egeria_project_guid"]})
            elif v is None:
                res.undetermined.append({"kind": "investigation", "ref": r["slug"],
                                         "reason": "lookup failed"})
        for r in sets:
            v = self._resolves(cm.get_collection_by_guid, r["egeria_collection_guid"])
            if v is False:
                stale.append({"kind": "working set", "ref": r["slug"],
                              "guid": r["egeria_collection_guid"]})
            elif v is None:
                res.undetermined.append({"kind": "working set", "ref": r["slug"],
                                         "reason": "lookup failed"})
        return Finding(
            key="stale_investigation_guids",
            title="Investigation Projects and working sets that are gone",
            detail="A dangling asset GUID makes a badge lie; a dangling Project GUID "
                   "makes promote fail outright against a Project nothing resolves.",
            items=stale, repair_step="clear_stale_investigations",
        )

    def _scan_contexts(self, res: ScanResult) -> Finding:
        """entity_egeria_project_context — the row that decides whether a
        resource may publish and what it attaches to."""
        pm = self._clients["project"]
        with self._registry._conn() as conn:
            rows = conn.execute(
                "SELECT entity_type, entity_slug, egeria_project_guid "
                "FROM entity_egeria_project_context "
                "WHERE coalesce(egeria_project_guid, '') <> ''"
            ).fetchall()
        seen: dict = {}
        stale = []
        for r in rows:
            guid = r["egeria_project_guid"]
            if guid not in seen:
                seen[guid] = self._resolves(pm.get_project_by_guid, guid)
            if seen[guid] is False:
                stale.append({"ref": f"{r['entity_type']}:{r['entity_slug']}", "guid": guid})
            elif seen[guid] is None:
                res.undetermined.append({
                    "kind": "project context",
                    "ref": f"{r['entity_type']}:{r['entity_slug']}", "reason": "lookup failed",
                })
        return Finding(
            key="stale_contexts",
            title="Resources bound to an Egeria Project that no longer exists",
            detail="Publishing these would attach them to a Project that is gone. "
                   "Clearing returns them to 'unset' so the question is asked again.",
            items=stale, repair_step="clear_stale_contexts",
        )

    def _scan_unlinked_members(self, res: ScanResult) -> Finding:
        """In-scope resources that have an asset but are not in the Collection.

        promote() links membership once and refuses to run again, so members
        published AFTER promotion have no way in.

        Checks actual Egeria Collection membership (get_member_list), not just
        "has an asset" — the first version of this scan conflated the two, so
        it kept reporting the same members as "linkable" forever, even
        immediately after a successful relink_investigation_members repair
        actually attached them. Confirmed live 2026-08-26: repair reported
        22 members linked with zero errors, and the very next scan showed the
        identical 2 investigations again, because the scan never checked
        whether they were already there. "Has an asset" only proves a member
        is *linkable*, not that it still *needs* linking.

        A collection whose membership can't be read (unreachable, auth
        failure, method missing) goes to `undetermined`, not into `items` —
        reporting it as needing a repair we can't actually verify is done
        would be the same "guess dressed as a fact" this module's docstring
        warns against elsewhere.
        """
        cm = self._clients["collection"]
        items = []
        for inv in self._registry.list_investigations():
            if not inv.get("egeria_project_guid"):
                continue
            ws = self._registry.get_or_create_working_set(inv["slug"])
            collection_guid = (ws or {}).get("egeria_collection_guid")
            if not collection_guid:
                continue
            candidates = [
                m for m in self._registry.list_investigation_members(inv["slug"])
                if self._asset_guid(m["entity_type"], m["entity_slug"])
            ]
            if not candidates:
                continue
            try:
                members = cm.get_member_list(collection_guid=collection_guid)
                # pyegeria signals an EMPTY collection with the string
                # "No members found" rather than []. That is a definite answer
                # — zero members — and the strictness here turned it into
                # "could not determine".
                #
                # Measured 2026-08-31, after catalog_assets: all three
                # investigations returned it, because a freshly rebuilt
                # platform is exactly the case where no member is linked yet.
                # All three went to `undetermined`, the finding reported 0, and
                # the relink card never rendered — so the repair was
                # unreachable in precisely the state it exists for.
                #
                # This module's rule is "unreachable is never stale", and it is
                # right; the error was classing a definite empty result as
                # unreachable. An empty collection means every member is
                # unlinked, which is the strongest possible reason to show the
                # card, not a reason to hide it.
                if isinstance(members, str) and "No members found" in members:
                    members = []
                elif not isinstance(members, list):
                    raise ValueError(f"get_member_list returned {members!r}, not a list")
            except Exception as exc:
                res.undetermined.append({
                    "kind": "collection membership",
                    "ref": f"investigation:{inv['slug']}",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                continue
            already_linked = {m.get("guid", "") for m in members}
            linkable = [
                m for m in candidates
                if self._asset_guid(m["entity_type"], m["entity_slug"]) not in already_linked
            ]
            if linkable:
                items.append({"investigation": inv["slug"], "linkable": len(linkable)})
        return Finding(
            key="unlinked_members",
            title="Investigation members that can be attached",
            detail="Safe to re-run: CollectionMembership is uni-link, so attaching an "
                   "already-attached member upserts rather than duplicating.",
            items=items, repair_step="relink_investigation_members",
        )

    def _publish_readiness(self) -> list[dict]:
        """Every unpublished repo, with the SAME readiness question the gate asks.

        The publish route (`web/routes/egeria.py`, Part 5 gate) accepts a repo
        when its own context is anything but `unset`/`unbound` **or** when an investigation
        it is in scope for supplies one by inheritance. An earlier version of
        this scan answered only the first half — `publish_ready = status !=
        "unset"` — and the two answers agreed right up until they did not.

        Measured 2026-08-31, straight after the three investigations were
        promoted: 25 of 27 repos carried `unset` and were reported blocked,
        while all 27 inherited a context and would have published. The field was
        a correct reading of the context row and a wrong answer to the question
        it was labelled with, and it went wrong at the moment the user fixed the
        underlying problem — the panel said the work was blocked precisely
        because it had just been unblocked.

        So this asks the registry the same question the route asks, rather than
        a cheaper one that usually matches.

        Population is also wider than the old scan's: that one joined
        `entity_egeria_project_context`, so a repo with no context row at all
        was invisible even when an investigation would supply one. `egeria_trellis`
        was exactly that — needed by a promoted investigation, publishable, and
        absent from the panel.
        """
        out = []
        with self._registry._conn() as conn:
            rows = conn.execute(
                "SELECT p.slug AS slug, coalesce(c.status, '') AS status "
                "FROM projects p "
                "LEFT JOIN entity_egeria_project_context c "
                "  ON c.entity_type = 'repo' AND c.entity_slug = p.slug "
                "WHERE coalesce(p.egeria_asset_guid, '') = '' "
                "ORDER BY p.slug"
            ).fetchall()
            for r in rows:
                published_before = conn.execute(
                    "SELECT 1 FROM activity_log WHERE entity_slug = ? "
                    "AND operation = 'catalog' AND status = 'ok' LIMIT 1",
                    (r["slug"],),
                ).fetchone() is not None
                out.append({
                    "slug": r["slug"], "context": r["status"] or "none",
                    "was_published": published_before,
                    # Kept per-item, not only in the finding title. The title
                    # says "4 lost an asset, 3 never had one"; only this says
                    # WHICH, and the expanded item list is where someone checks
                    # whether the repo they care about is the one that lost
                    # something. Dropping it while preserving the counts was a
                    # regression caught by the test written for the original
                    # distinction — the counts alone cannot tell a reader that
                    # the repo in front of them never had an asset to lose.
                    "cause": ("asset lost — it was published before"
                              if published_before else
                              "never published — no publish has run for it"),
                })

        # Inheritance is asked OUTSIDE the connection above: it opens its own.
        for item in out:
            item["publish_ready"], item["ready_via"] = self._ready_via(
                item["slug"], item["context"])
        return out

    def _ready_via(self, slug: str, context: str) -> tuple:
        """(publish_ready, ready_via) for one repo: the gate's own question.

        `context` is the repo's own context status ('' / 'none' / 'unset' mean
        none was decided). Shared by `_publish_readiness` and
        `_scan_registration_only` so the two cannot answer differently.
        """
        if context not in ("", "none", "unset", "unbound"):
            return True, f"context '{context}'"
        try:
            inherited = self._registry.inherited_egeria_project_context("repo", slug)
        except Exception as exc:
            # Could not ask is never "not ready" — the same rule the rest of
            # this module runs on. A repo we cannot answer for is offered no
            # button and says why.
            return None, f"undetermined ({type(exc).__name__})"
        if inherited:
            return True, f"inherits from '{inherited['_inherited_from_name']}'"
        return False, ""

    def _scan_unpublished_but_expected(self) -> Finding:
        """Repos with no Egeria asset that CAN be catalogued now.

        Two causes are kept apart in the wording, because they need different
        reassurance even though the call is identical. Measured 2026-08-27: of
        seven, four really had been published and lost the asset to the
        redeploy, while three carried only a scout import and were never
        catalogued at all. Telling a reader they lost something they never had
        sends them looking for a fault that does not exist.

        The repair is offered here — unlike the earlier version of this scan,
        which set `repair_step=""` on the grounds that "publishing is a write
        and can be slow, so it is never done for you". That reasoning held for a
        full survey. It does not hold for `CATALOG_STEPS`, which publishes one
        API-only step, downloads nothing, and is the thing
        `relink_investigation_members` needs to exist before it can attach
        anything. Withholding it did not protect anyone from a cost; it left the
        ordered pass unable to finish, and left the user to run 27 publishes by
        hand.
        """
        items = [i for i in self._publish_readiness() if i["publish_ready"] is True]
        lost = sum(1 for i in items if i["was_published"])
        return Finding(
            key="needs_catalog",
            title=(f"Repos with no Egeria asset that can be published "
                   f"({lost} lost an asset, {len(items) - lost} never had one)"),
            detail=("Publishes " + "/".join(CATALOG_STEPS) + " for each — enough to "
                    "register the asset, and deliberately nothing that downloads an "
                    "archive. Having an Egeria Project and having an asset are "
                    "different things, and only publishing produces the asset.\n\n"
                    "Run this before relinking investigation members: a member with "
                    "no asset behind it cannot be attached, which is why a promotion "
                    "on a freshly rebuilt platform reports every member unlinkable.\n\n"
                    "Full survey results are a separate, slower step — this one does "
                    "not fetch repository archives."),
            items=items, repair_step="catalog_assets",
        )

    def _scan_unpublishable(self) -> Finding:
        """Repos that cannot be catalogued until someone answers a question.

        Split out from the repairable finding above so the button's population
        and the button's action agree: every item under a repair step is one
        that step can actually complete. A mixed list would put a "fix" tick
        over rows the fix is guaranteed to fail on.

        `publish_ready is None` — the lookup failed — lands here too, and is
        counted as undetermined rather than blocked.
        """
        rows = self._publish_readiness()
        items = [i for i in rows if i["publish_ready"] is not True]
        for i in items:
            i["blocked_reason"] = (
                "could not determine whether an Egeria Project applies"
                if i["publish_ready"] is None else
                "no Egeria Project decided, and no investigation supplies one — "
                "publishing is refused (428) until one is set")
        return Finding(
            key="unpublishable",
            title=f"Repos with no Egeria asset and no Project to publish under",
            detail=("Assign an Egeria Project to these, or add them to the working "
                    "set of an investigation that has one — membership of a bound "
                    "investigation supplies the binding by inheritance, which is why "
                    "promoting an investigation can move a repo out of this list "
                    "without anyone touching the repo itself.\n\n"
                    "No button: RE does not know which Project these should join, "
                    "and picking one would be a plausible guess written into the "
                    "catalog as though it were a decision."),
            items=items, repair_step="", needs_decision=True,
        )

    def _scan_registration_only(self) -> Finding:
        """Assets that exist but carry no real survey report, and CAN be published.

        The sequel to `catalog_assets`: cataloguing registers the asset with one
        cheap step, which is the right trade when the goal is to make relinking
        possible, but it leaves the catalog holding an asset and almost nothing
        about it. This finds those and offers the full survey publish.

        Two rules decide membership, both about proof rather than claims:

        * READY. A repo the publish gate would refuse (no Egeria Project
          context, none inherited) is NOT listed here: a tick-box over a row the
          repair is guaranteed to fail on is what `_scan_unpublishable` exists
          to prevent. Those go to `_scan_registration_blocked` as a sentence.
        * PUBLISHED. A repo is published when `project_egeria_surveys` holds a
          row for it — the real proof, written by EVERY publish path. It used
          to be `project_published_analyses`, which only the full-survey path
          writes, so the scheduled refresh published a repo and this scan kept
          offering it (docling and egeria_docs, 2026-10-01). Where claims exist
          they are still read, because a catalog-step publish also leaves a
          survey row and only the claims say it covered nothing but
          registration.
        """
        return Finding(
            key="registration_only",
            title=("Egeria assets with no survey results published"),
            detail=("Runs the FULL survey for each and publishes it as a SurveyReport. "
                    "This is the slow path: it includes steps that download the "
                    "repository archive, so across a large corpus it is minutes and "
                    "real GitHub API budget, not seconds.\n\n"
                    "Left unticked for that reason. The assets are already registered "
                    "and investigation members can already be attached to them — this "
                    "adds what is known ABOUT each repo, and nothing depends on it."),
            items=[i for i in self._registration_only_rows() if i["publish_ready"] is True],
            repair_step="republish_survey_results",
        )

    def _scan_registration_blocked(self) -> Finding:
        """Catalogued repos that lack survey results but cannot be published.

        Plain sentence, no tick-box: the publish gate refuses a repo with no
        Egeria Project context, so offering the repair would only fail.
        """
        items = []
        for i in self._registration_only_rows():
            if i["publish_ready"] is True:
                continue
            i["blocked_reason"] = (
                "could not determine whether an Egeria Project applies"
                if i["publish_ready"] is None else
                f"{i['slug']} has no Egeria Project, and no investigation supplies one, "
                "so its survey results cannot be published until one is set")
            items.append(i)
        return Finding(
            key="registration_blocked",
            title="Egeria assets with no survey results and no Project to publish under",
            detail=("Assign an Egeria Project to these, or add them to an investigation "
                    "that has one. No button: the publish is refused (428) until then."),
            items=items, repair_step="", needs_decision=True,
        )

    def _registration_only_rows(self) -> list:
        items = []
        with self._registry._conn() as conn:
            rows = conn.execute(
                "SELECT p.slug AS slug, coalesce(c.status, '') AS status "
                "FROM projects p "
                "LEFT JOIN entity_egeria_project_context c "
                "  ON c.entity_type = 'repo' AND c.entity_slug = p.slug "
                "WHERE coalesce(p.egeria_asset_guid, '') <> '' "
                "ORDER BY p.slug").fetchall()
            for r in rows:
                # JOINed to project_egeria_surveys, not read on its own. A row
                # in project_published_analyses is a CLAIM that something was
                # published; it does not stop being a row when the report it
                # names is destroyed (36 of 64 after the 2026-08-31 redeploy).
                # Read raw, it silently excluded 5 repos from this finding.
                # project_egeria_surveys is cleared alongside the asset by
                # clear_egeria_registration, so a claim whose report_guid is
                # absent from it is one nothing stands behind.
                published = {x["analysis_id"] for x in conn.execute(
                    "SELECT DISTINCT a.analysis_id FROM project_published_analyses a "
                    "JOIN project_egeria_surveys s "
                    "  ON s.egeria_report_guid = a.egeria_report_guid "
                    "WHERE a.project_slug = ?", (r["slug"],)).fetchall()}
                has_survey_row = conn.execute(
                    "SELECT 1 FROM project_egeria_surveys WHERE project_slug = ? LIMIT 1",
                    (r["slug"],)).fetchone() is not None
                if published and not published <= REGISTRATION_ONLY_ANALYSES:
                    continue
                if not published and has_survey_row:
                    # Published by a path that records no per-analysis claims
                    # (the scheduled refresh). The survey row is the proof.
                    continue
                items.append({
                    "slug": r["slug"], "context": r["status"] or "none",
                    "published_analyses": ", ".join(sorted(published)) or "none",
                })
        for item in items:
            item["publish_ready"], item["ready_via"] = self._ready_via(
                item["slug"], item["context"])
        return items

    def _scan_local_investigations(self) -> Finding:
        items = [
            {"slug": i["slug"], "members": len(self._registry.list_investigation_members(i["slug"]))}
            for i in self._registry.list_investigations()
            if not i.get("egeria_project_guid")
        ]
        return Finding(
            key="local_investigations",
            title="Investigations with no Egeria Project",
            detail="Creating a Project is a write into a shared system and names a real "
                   "Egeria object, so it stays your decision — bind an existing Project "
                   "or create one from the investigation's own card.",
            items=items, repair_step="", needs_decision=True,
        )

    def _scan_definition_drift(self, res: ScanResult) -> Finding:
        """RECOVERY: Egeria no longer matches the authored documents.

        The documents are the definition — they carry guards, request
        parameters and ordering the CSV has no column for — so this is the
        comparison the repair acts on, and it tolerates nothing.

        Order is compared, not just membership. Run order is load-bearing:
        repo_foss_scorecard reads what repo_cve_scan writes, and for a while
        ran before it, scoring the previous run's advisories.

        A definition live in Egeria with no document of ours is NOT drift. It
        is somebody else's — an Egeria-native survey, or one authored directly
        — and we have nothing to restore it from. Reporting it would be
        claiming authority over a definition RE did not write.
        """
        documented = _documented_definitions()
        behind, foreign = [], []
        try:
            reader = _reader()
            for cand in reader.find_candidate_process_guids("Git Repository"):
                name = cand["qualified_name"].split("::")[-1]
                doc = documented.get(name)
                if doc is None:
                    foreign.append(name)
                    continue
                want = doc["steps"]
                try:
                    live_steps = reader.fetch(cand["guid"]).steps
                except Exception as exc:
                    res.undetermined.append({
                        "kind": "survey definition", "ref": name,
                        "reason": type(exc).__name__,
                    })
                    continue
                live = [st.re_analysis_step for st in live_steps]

                # Descriptions are part of the definition too. Comparing only
                # step keys is what let Egeria sit on a description the
                # documents had already moved past — invisible to a scan whose
                # whole job is to notice exactly that.
                stale = sorted(
                    st.re_analysis_step for st in live_steps
                    if st.re_analysis_step in doc["descriptions"]
                    and doc["descriptions"][st.re_analysis_step]
                    and (st.description or "").strip()
                    != doc["descriptions"][st.re_analysis_step].strip()
                )
                if live == want and not stale:
                    continue
                behind.append({
                    "definition": name,
                    "missing": [k for k in want if k not in live],
                    "extra": [k for k in live if k and k not in want],
                    "reordered": (live != want and set(live) == set(want)),
                    "stale_descriptions": stale,
                    "live_steps": len(live), "documented_steps": len(want),
                })
        except Exception as exc:
            res.undetermined.append({"kind": "survey definition", "ref": "(all)",
                                     "reason": f"{type(exc).__name__}: {str(exc)[:60]}"})

        if foreign:
            log.info("survey definitions live in Egeria with no document here: %s",
                     ", ".join(sorted(foreign)))
        return Finding(
            key="definition_drift",
            title="Survey Definitions in Egeria differ from their authored documents",
            detail="Steps that exist and are runnable per-card but appear in no survey, "
                   "or run in the wrong order. Repairing re-executes the Dr.Egeria "
                   "documents AND runs the step-link reconciler — never one without "
                   "the other, because Link Next Process Step is not idempotent: the "
                   "relationship carries a guard and is multi-link by design, so "
                   "re-running it adds a second edge rather than merging, and a "
                   "definition with two outgoing next-steps is refused by the reader "
                   "and renders with zero steps.",
            items=behind, repair_step="reauthor_survey_definitions",
        )

    def _scan_specification_gap(self) -> Finding:
        """COVERAGE: the CSV specifies a survey that no document defines.

        A different question from drift, and it was conflated with it until
        2026-08-26. The CSV is a specification of what surveys are NEEDED; the
        documents are the definitions. A gap here means a step will never reach
        Egeria no matter how many times the repair runs, because nothing
        authored it in the first place.

        Deliberately no repair step. Closing this means running the generator,
        which edits the source tree — and may rightly refuse, if a document has
        been hand-authored since. This panel repairs the catalog, not the
        repository, and a button here that rewrote committed files would be a
        different kind of thing wearing the same shape.

        Membership only, never order: the CSV's full-survey row is the sentinel
        "*", which has no order to compare, and its rows are not stored sorted.
        The documents are where order is authoritative.
        """
        documented = _documented_definitions()
        specified = _intended_steps_from_csv()
        gaps = []
        for name, want in sorted(specified.items()):
            entry = documented.get(name)
            have = entry["steps"] if entry else None
            if have is None:
                gaps.append({"definition": name, "reason": "no document defines it",
                             "missing": sorted(want), "specified_steps": len(want)})
                continue
            missing = [k for k in want if k not in have]
            if missing:
                gaps.append({"definition": name,
                             "reason": "the document is behind the CSV",
                             "missing": missing, "specified_steps": len(want),
                             "documented_steps": len(have)})
        # A document with no CSV row is not a gap. The CSV is not required to
        # be complete — that is the whole point of it being a specification of
        # what is needed rather than a definition of what exists.
        return Finding(
            key="specification_gap",
            title="Surveys specified in the CSV that no document defines",
            detail="repo_survey_types.csv says these surveys are needed, but no "
                   "Dr.Egeria document defines them (or the document predates the "
                   "CSV row). Until a document exists, re-authoring cannot put "
                   "these steps into Egeria. Fix by running "
                   "scripts/generate_repo_survey_definition.py — a source-tree "
                   "change, which is why there is no repair button here.",
            items=gaps, needs_decision=True,
        )

    def _asset_guid(self, entity_type: str, entity_slug: str) -> str:
        # The one per-kind lookup (`member_assets`), shared with the relink repair's publisher: a repo-only copy
        # here hid every database and file-system member from this finding, so the relink card never rendered for them.
        from resource_explorer.member_assets import MemberUnresolved, NotInEgeriaYet, member_egeria_guid

        try:
            return member_egeria_guid(self._registry, entity_type, entity_slug)
        except (NotInEgeriaYet, MemberUnresolved):
            return ""

    # ── repair ──────────────────────────────────────────────────────────────
    def apply(self, steps: list) -> dict:
        """Run the named repairs, in REPAIR_STEPS order regardless of input order.

        Order is load-bearing — clearing must precede relinking — so a caller
        cannot get it wrong by listing them in a different order.
        """
        unknown = [s for s in steps if s not in REPAIR_STEPS]
        if unknown:
            raise ValueError(f"unknown repair step(s): {unknown}")
        ok, why = self._connect()
        if not ok:
            return {"reachable": False, "unreachable_reason": why, "applied": {}}

        applied: dict = {}
        for step in REPAIR_STEPS:
            if step in steps:
                applied[step] = getattr(self, f"_do_{step}")()
        return {"reachable": True, "unreachable_reason": "", "applied": applied}

    def _do_reauthor_survey_definitions(self) -> dict:
        """Re-execute the survey-definitions batch, then reconcile its links.

        Delegates to bootstrap.heal_batch, which already runs a batch's
        documents in dependency order and its declared post_heal script — for
        this batch, the link reconciler. Reusing it means the reconciler cannot
        be forgotten, which is the failure mode that takes a definition out of
        service: forgetting it is far easier than noticing the duplicate edges
        it prevents.
        """
        from resource_explorer.bootstrap import discover_batches, heal_batch

        batch = next((b for b in discover_batches()
                      if b.batch_id == "survey-definitions"), None)
        if batch is None:
            return {"reauthored": False,
                    "error": "the survey-definitions batch was not found"}

        # An interlock stood here (2026-08-26): the repair refused to run while any
        # document carried a guard other than "Any", because the link reconciler
        # keyed duplicates on (previous, next) and would have deleted the branch.
        # The reconciler is keyed on (previous, next, guard) now and reconciles
        # against the authored document rather than a linear chain, so a branch
        # survives the repair and the refusal is no longer honest.
        #
        # A branching definition is still not RUNNABLE by RE: SurveyDefinitionReader
        # walks a single chain and raises UnsupportedSurveyDefinitionError. That is a
        # loud, safe failure rather than silent destruction, and coordinating a
        # branching process is Egeria's job under the engine-host model
        # (docs/survey-model-and-engine-host-design.md section 4), not RE's.
        ok, detail = heal_batch(batch)
        return {"reauthored": bool(ok), "detail": detail,
                "documents": len(batch.files),
                "post_heal": (batch.post_heal or {}).get("script", "")}

    def _do_clear_stale_assets(self) -> dict:
        res = ScanResult()
        finding = self._scan_assets(res)
        cleared, surveys, claims = 0, 0, 0
        for item in finding.items:
            r = self._registry.clear_egeria_registration(item["slug"])
            cleared += 1
            surveys += r.get("surveys_deleted", 0)
            claims += r.get("published_types_deleted", 0)
        return {"cleared": cleared, "survey_records_deleted": surveys,
                "publish_claims_deleted": claims,
                "undetermined": len(res.undetermined)}

    def _do_clear_orphan_publish_claims(self) -> dict:
        """Delete publish claims that nothing stands behind.

        Originally keyed on "the project has no asset", and scoped to one of
        the two claim tables. Both limits turned out to matter after the
        2026-08-31 redeploy:

        - `project_published_analyses` was never cleaned at all, and held 36
          rows naming SurveyReports the redeploy had destroyed.
        - Keying on asset absence means CATALOGUING A REPO CLOSES THE WINDOW.
          Once `catalog_assets` gives it an asset, its pre-wipe claims are
          permanently unreachable by this repair — which is exactly what
          happened: the claims survived precisely because the situation had
          been half-repaired.

        Keyed on the claim instead: a claim whose `egeria_report_guid` is not
        in `project_egeria_surveys` names a report this registry no longer
        records. That is true whether or not the project currently has an
        asset, so it cannot be outrun by a repair that runs first.

        The asset-absence delete is KEPT as well rather than replaced — it
        catches a claim carrying no report guid at all, which the join cannot
        see.
        """
        with self._registry._conn() as conn:
            deleted = 0
            for table in ("project_published_annotation_types",
                          "project_published_analyses"):
                deleted += conn.execute(
                    f"DELETE FROM {table} WHERE project_slug IN ("
                    "SELECT slug FROM projects WHERE coalesce(egeria_asset_guid, '') = '')"
                ).rowcount
                deleted += conn.execute(
                    f"DELETE FROM {table} WHERE coalesce(egeria_report_guid, '') <> '' "
                    "AND egeria_report_guid NOT IN "
                    "(SELECT egeria_report_guid FROM project_egeria_surveys "
                    " WHERE coalesce(egeria_report_guid, '') <> '')"
                ).rowcount
        return {"claims_deleted": deleted}

    def _do_flag_vanished_publishes(self) -> dict:
        """Flag, never delete — the counterpart to `_do_clear_orphan_publish_
        claims` for the row that IS locally coherent.

        Also self-heals, keyed on the FLAGGED ROWS themselves (not on whether
        this scan happens to contain a vanished finding — `scan()` drops empty
        findings, so a heal gated on one is unreachable exactly when the thing
        it heals has resolved). Every pass, for each existing 'repo_publish'
        flag (stale or uncatalogued), re-read that slug's CURRENT latest
        report: resolves -> flag cleared; still absent -> flag kept with
        `last_checked_at` advanced; no report at all -> 'uncatalogued'
        ("not catalogued, publish needed"). A republish is therefore not stuck
        showing "no longer in the store" forever.
        """
        res = ScanResult()
        finding = self._scan_vanished_publishes(res)
        vanished_slugs = {i["slug"] for i in finding.items}
        for item in finding.items:
            self._registry.mark_egeria_linkage_stale(
                "repo_publish", item["slug"], stale_guid=item["guid"],
                detail="the published report no longer resolves in Egeria",
            )
        latest = self._registry.get_latest_egeria_surveys_all_projects()
        ce = self._clients["classification"]
        healed_slugs: list[str] = []
        uncatalogued = 0
        for row in self._registry.list_egeria_linkages("repo_publish"):
            slug = row["entity_slug"]
            if slug in vanished_slugs:
                continue  # just re-flagged above, last_checked_at already advanced
            survey = latest.get(slug)
            if not survey:
                self._registry.mark_egeria_linkage_uncatalogued(
                    "repo_publish", slug,
                    detail="no survey report has been published for this repo",
                )
                uncatalogued += 1
                continue
            guid = survey["egeria_report_guid"]
            v = self._resolves(
                lambda g: ce.get_element_by_guid(g, graph_query_depth=0), guid)
            if v is True:
                self._registry.clear_egeria_linkage_status("repo_publish", slug)
                healed_slugs.append(slug)
                self._log_heal(slug, row, guid)
                log.info("egeria_resync: cleared publish flag for %s at %s "
                         "(latest report %s resolves)", slug, _now(), guid)
            elif v is False:
                self._registry.mark_egeria_linkage_stale(
                    "repo_publish", slug, stale_guid=guid,
                    detail="the published report no longer resolves in Egeria",
                )
            else:
                res.undetermined.append(
                    {"kind": "publish", "ref": slug, "reason": "lookup failed"})
        return {"flagged": len(finding.items), "healed": len(healed_slugs),
                "healed_slugs": healed_slugs, "healed_at": _now() if healed_slugs else "",
                "uncatalogued": uncatalogued,
                "undetermined": len(res.undetermined)}

    def _do_flag_stale_dbfs_assets(self) -> dict:
        """Flag, never delete — the databases/filesystems counterpart of
        `_do_flag_vanished_publishes`.

        Re-verifies every database/filesystem that has a cached GUID against
        Egeria. Gone -> `egeria_linkage_status` flagged stale (GUID untouched).
        Resolves -> any flag cleared. Undetermined (unreachable, auth, missing
        method) -> row left exactly as it was and counted. No GUID -> skipped.
        Writes nothing to Egeria and deletes no GUID. The write half is
        `egeria_linkage.record_linkage_verdict`, shared with
        `recheck_all_linkages`.
        """
        from resource_explorer.egeria_linkage import record_linkage_verdict

        flagged, cleared, undetermined = [], [], []
        for etype, slug, guid in self._dbfs_with_guid():
            v = self._resolve_dbfs(guid)
            if v is None:
                undetermined.append({"entity_type": etype, "slug": slug})
                continue
            outcome = record_linkage_verdict(
                self._registry, etype, slug, guid, v,
                detail="the cached asset no longer resolves in Egeria")
            if outcome == "flagged":
                flagged.append({"entity_type": etype, "slug": slug})
            elif outcome == "cleared":
                cleared.append({"entity_type": etype, "slug": slug})
                log.info("egeria_resync: cleared %s stale flag for %s at %s "
                         "(asset %s resolves)", etype, slug, _now(), guid)
        return {"flagged": len(flagged), "flagged_items": flagged,
                "healed": len(cleared), "healed_items": cleared,
                "undetermined": len(undetermined)}

    def _log_heal(self, slug: str, row: dict, report_guid: str) -> None:
        """Visible evidence that a publish flag was cleared (the row itself is
        deleted — absence means healthy — so without this nothing shows it
        happened). One activity-log entry per healed slug."""
        import uuid
        from resource_explorer.registry import ActivityEntry
        try:
            self._registry.write_activity(ActivityEntry(
                id=str(uuid.uuid4()), ts=_now(), operation="refresh",
                intent="enrichment", entity_type="repo", entity_slug=slug,
                status="ok",
                summary=f"Cleared the '{row.get('status', 'stale')}' publish flag: "
                        f"latest report {report_guid} resolves in Egeria",
                detail=f"Resync heal (flag_vanished_publishes). Flag had been set "
                       f"{row.get('detected_at', '')}; previously flagged guid "
                       f"{row.get('stale_guid', '')}.",
                items=[{"report_guid": report_guid}],
            ))
        except Exception:
            log.exception("egeria_resync: could not write heal activity for %s", slug)

    def _do_clear_stale_investigations(self) -> dict:
        res = ScanResult()
        finding = self._scan_investigation_guids(res)
        invs = [i for i in finding.items if i["kind"] == "investigation"]
        sets = [i for i in finding.items if i["kind"] == "working set"]
        with self._registry._conn() as conn:
            for i in invs:
                conn.execute(
                    "UPDATE investigations SET egeria_project_guid = '', "
                    "egeria_project_status = 'local' WHERE slug = ?", (i["ref"],))
            for i in sets:
                conn.execute(
                    "UPDATE working_sets SET egeria_collection_guid = '' WHERE slug = ?",
                    (i["ref"],))
        return {"investigations_cleared": len(invs), "working_sets_cleared": len(sets),
                "undetermined": len(res.undetermined)}

    def _do_clear_stale_contexts(self) -> dict:
        res = ScanResult()
        finding = self._scan_contexts(res)
        with self._registry._conn() as conn:
            for item in finding.items:
                etype, _, eslug = item["ref"].partition(":")
                # Back to 'unset', not 'personal' or 'declined': the decision
                # that was made pointed at something gone, so it must be asked
                # again rather than reinterpreted as an answer never given. ('unset', not 'unbound': this
                # heal is for a GUID that went stale WITHOUT a reset marker; 'unbound' is only what the
                # post-reset clean-up script writes. Both gate a publish identically.)
                conn.execute(
                    "UPDATE entity_egeria_project_context SET egeria_project_guid = '', "
                    "egeria_project_qualified_name = '', status = 'unset' "
                    "WHERE entity_type = ? AND entity_slug = ?", (etype, eslug))
        return {"contexts_cleared": len(finding.items),
                "undetermined": len(res.undetermined)}

    def _refusal(self, entity_type: str, slug: str, write: str = "publish") -> str:
        """Brief Z2 round 2: the per-resource access check every repair write asks, as the person who pressed
        Apply (`resource_access`), or, run unattended by the scheduler, the no-requester rule (open only with no
        zone in use). "" = proceed, else the resource's own words "not permitted · <reason>"."""
        from resource_explorer.workflows.curate import person_write_refusal

        return person_write_refusal(self._registry, entity_type, slug, write)

    def _publish_one(self, slug: str, steps) -> dict:
        """`_publish_one_unlogged`, plus a durable record of any failure.

        Refusals at the gate and failures before the orchestrator runs never
        reach the publisher, so nothing else writes an activity_log row for
        them: an Apply that failed on every repo left no trace anywhere a
        person looks (rule 16). Every not-ok outcome is logged here.
        """
        out = self._publish_one_unlogged(slug, steps)
        if out.get("not_permitted"):
            return out                    # a refusal, not a failure: said by _publish_many, never logged as one
        if not out.get("ok"):
            self._log_publish_failure(slug, out.get("error", "unknown error"))
        return out

    def _log_publish_failure(self, slug: str, error: str) -> None:
        import uuid
        from resource_explorer.registry import ActivityEntry
        try:
            self._registry.write_activity(ActivityEntry(
                id=str(uuid.uuid4()), ts=_now(), operation="publish",
                intent="enrichment", entity_type="repo", entity_slug=slug,
                status="error",
                summary=f"Resync publish failed: {error}",
                detail="Resync apply (republish). The repo was not published.",
                items=[{"error": error}],
            ))
        except Exception:
            log.exception("egeria_resync: could not write failure activity for %s", slug)

    def _publish_one_unlogged(self, slug: str, steps) -> dict:
        """Survey then publish one repo, mirroring the publish route's own order.

        Deliberately reimplements the route's sequence rather than calling it:
        the route is an async FastAPI handler that returns HTTP responses, and
        importing it here to get at its body would couple a repair to a
        transport. What IS shared is the thing that must not drift — the Part 5
        context gate, including its inheritance write, reproduced below.

        The inherited context is WRITTEN, not merely used, exactly as the route
        writes it. An inheritance that left no row would make the resource's
        context depend on working-set membership at read time, so removing it
        from the set later would silently un-answer a settled question.
        """
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
        from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

        reg = self._registry
        context = reg.get_project_context("repo", slug)
        if not context or context.get("status") in ("unset", "unbound"):
            inherited = reg.inherited_egeria_project_context("repo", slug)
            if not inherited:
                # Reached only if the context changed between scan and apply:
                # the scans no longer offer a repo the gate would refuse. Say
                # so plainly rather than raising, so one repo that drifted
                # cannot abort the other twenty-six.
                return {"slug": slug, "ok": False,
                        "error": "no Egeria Project context (428) — re-scan"}
            reg.set_project_context(
                "repo", slug, status="linked",
                egeria_project_guid=inherited["egeria_project_guid"],
                egeria_project_qualified_name=inherited["egeria_project_qualified_name"],
                free_text_name=(f"inherited from investigation "
                                f"'{inherited['_inherited_from_name']}'"))

        try:
            survey = SurveyOrchestrator(registry=reg).run(
                slug, steps=list(steps) if steps else None)
        except Exception as exc:
            return {"slug": slug, "ok": False, "error": f"survey failed: {exc}"}

        from resource_explorer.private_publish import PrivateAwaitsOwner, record_waiting
        from resource_explorer.workflows.curate import WriteNotPermitted

        try:
            # A repair re-publish waits for Egeria (RUNS_PUBLISH_INLINE governs a run's auto-publish only).
            guid = EgeriaPublisher(registry=reg).publish(survey, defer_drain=False)
        except WriteNotPermitted as exc:
            # Round 3: an adopted asset the caller may not change is a refusal, reported with its words.
            return {"slug": slug, "ok": False, "not_permitted": str(exc)}
        except PrivateAwaitsOwner as exc:
            # Waiting, not failed (2026-10-13 review): only the private investigation's owner can publish it.
            record_waiting(reg, exc, run_key=survey.surveyed_at.isoformat(), source="resync")
            return {"slug": slug, "ok": False, "awaits_owner": str(exc)}
        except Exception as exc:
            return {"slug": slug, "ok": False, "error": f"publish failed: {exc}"}
        return {"slug": slug, "ok": True, "report_guid": guid,
                "annotations": len(survey.annotations)}

    def _publish_many(self, slugs, steps) -> dict:
        """Publish each, one at a time, isolating failures.

        Per-repo try/except rather than one batch transaction: a corpus-wide
        repair that aborts on repo nine leaves the catalog half-written with no
        record of where it stopped. Every outcome is reported, and `ok` counts
        only what actually succeeded — a repair that says it did 27 when it did
        9 is the exact class of claim this module exists to stop making.
        """
        results, skipped, waiting = [], [], []
        for s in slugs:
            why = self._refusal("repo", s)
            if why:                       # Brief Z2 round 2: skipped with its own words, the rest proceed
                skipped.append({"slug": s, "words": why})
                continue
            out = self._publish_one(s, steps)
            if out.get("not_permitted"):
                skipped.append({"slug": s, "words": out["not_permitted"]})
                continue
            if out.get("awaits_owner"):
                waiting.append({"slug": s, "words": out["awaits_owner"]})
                continue
            results.append(out)
        failed = [r for r in results if not r["ok"]]
        return {
            "attempted": len(results),
            "published": len(results) - len(failed),
            "failed": len(failed),
            "not_permitted": skipped,
            "awaits_owner": waiting,
            "steps": list(steps) if steps else "full survey",
            "errors": [{"slug": r["slug"], "error": r["error"]} for r in failed][:20],
        }

    def _do_catalog_assets(self) -> dict:
        """Register an Egeria asset for every repo that can have one now."""
        finding = self._scan_unpublished_but_expected()
        return self._publish_many([i["slug"] for i in finding.items], CATALOG_STEPS)

    def _do_republish_survey_results(self) -> dict:
        """Full survey publish for assets that carry no survey results yet."""
        finding = self._scan_registration_only()
        return self._publish_many([i["slug"] for i in finding.items], None)

    def _do_relink_investigation_members(self) -> dict:
        from resource_explorer.surveyors.egeria_investigation_publisher import (
            EgeriaInvestigationPublisher,
        )

        from resource_explorer.workflows.egeria_work_access import investigation_member_refusal

        pub = EgeriaInvestigationPublisher(self._registry)
        linked, unlinkable, per_inv = 0, 0, {}
        for inv in self._registry.list_investigations():
            if not inv.get("egeria_project_guid"):
                continue
            # Brief Z3: the same per-member check the relink route asks, both ends (the member's asset and the
            # Folio, judged on its Project), as the person who pressed Apply or, unattended, the no-requester rule.
            r = pub.relink_members(inv["slug"], refusal=investigation_member_refusal(
                self._registry, inv["slug"], unattended=True))
            linked += len(r.members_linked)
            unlinkable += len(r.members_unlinkable)
            per_inv[inv["slug"]] = {
                "linked": len(r.members_linked),
                "unlinkable": len(r.members_unlinkable),
                "not_permitted": [{"entity_type": m.get("entity_type"), "slug": m.get("entity_slug"),
                                   "words": m["reason"]} for m in r.members_unlinkable
                                  if str(m.get("reason", "")).startswith("not permitted")],
                "errors": r.errors,
            }
        return {"members_linked": linked, "members_unlinkable": unlinkable,
                "per_investigation": per_inv}


def _as_int(value) -> int:
    """A step_order as a number, or 0 when it cannot be read.

    Used only for sorting. An unreadable order sorts first rather than raising:
    the callers of this compare membership, not sequence, so a wrong position
    is cosmetic where an exception would lose the whole CSV.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _intended_steps_from_csv() -> dict:
    """{definition name: [step_key, ...]} from repo_survey_types.csv.

    A fallback for when the reconciler module cannot be imported. The CSV is
    the source of truth either way — the generated documents are derived from
    it, so comparing Egeria against it is comparing against what SHOULD have
    been authored.
    """
    import csv
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "docs" / "dr-egeria"
            / "repo_survey_types.csv")
    out: dict = {}
    try:
        with path.open() as fh:
            # Sorted by the column that means order — rows are not stored in
            # it. Even so, callers compare membership rather than sequence:
            # the full-survey sentinel expands to a set with no order at all,
            # so a sorted list here is tidier, not authoritative. The documents
            # are where order is authoritative.
            rows = sorted(csv.DictReader(fh),
                          key=lambda r: _as_int(r.get("step_order")))
            for row in rows:
                group = (row.get("survey_group") or "").strip()
                step = (row.get("step_key") or "").strip()
                if not group or not step:
                    continue
                if step == _FULL_SURVEY_SENTINEL:
                    # The full bundle is generated FROM STEP_REGISTRY rather
                    # than enumerated here, so the CSV carries a sentinel. A
                    # literal read would make it look like a one-step survey
                    # and report every other step as missing.
                    out[group] = _all_step_keys()
                    continue
                out.setdefault(group, []).append(step)
    except OSError:
        return {}
    return out


#: The CSV's stand-in for "every registered step", used by the full-survey row.
_FULL_SURVEY_SENTINEL = "*"


def _all_step_keys() -> list:
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            STEP_REGISTRY,
        )
    except ImportError:  # pragma: no cover - defensive
        return []
    return sorted(STEP_REGISTRY.keys())


def _documented_definitions() -> dict:
    """{name: {"steps": [...], "descriptions": {...}}} for the scan's use.

    A thin projection of survey_definition_docs, which is the single parser for
    these files — the link reconciler reads the same documents to build its
    expected edge set, and two parsers of one format is how they drift apart.
    """
    from resource_explorer.surveyors.survey_definition_docs import (
        documented_definitions,
    )
    return {name: {"steps": doc.steps, "descriptions": doc.descriptions}
            for name, doc in documented_definitions().items()}


def _reader():
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinitionReader,
    )
    return SurveyDefinitionReader()


# ── scheduled scan-and-clear ─────────────────────────────────────────────────
# 2026-09-04, after the full-repository-store-wipe redeploy: the human-driven
# half of recovery (Admin > Egeria Alignment) worked exactly as designed, but
# nothing ran it until someone remembered to. This automates only the part
# that's safe to run unattended and repeatedly — see SAFE_SCHEDULED_STEPS'
# comment for why each excluded step stays manual. Mirrors bootstrap.py's
# check_and_heal scheduler shape deliberately (same interval, same
# never-block-startup/never-die-on-exception discipline, same status-tracking
# contract) rather than inventing a second pattern for the same problem.

CHECK_INTERVAL_SECONDS = 600

_status_lock = threading.Lock()
_status: dict = {
    "last_run_at": "",
    "last_reachable": None,
    "last_unreachable_reason": "",
    "last_applied": {},
    "last_error": "",
    "consecutive_failures": 0,
}
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scan_and_clear(registry: "ProjectRegistry | None" = None) -> dict:
    """One pass: scan, then apply only the SAFE_SCHEDULED_STEPS whose findings
    are actually present right now. Never guesses (every step re-verifies live
    before writing, same as a human clicking Apply) and never touches anything
    `needs_decision` or expensive — asserted explicitly below, not just
    inherited from SAFE_SCHEDULED_STEPS' own comment, so a future edit to
    REPAIR_STEPS' semantics can't silently make this unsafe.

    Safe to call repeatedly and on any schedule: an unreachable Egeria or an
    empty finding set both return cleanly with nothing applied, the same
    'unreachable is never stale' discipline the rest of this module uses.
    """
    resync = EgeriaResync(registry=registry)
    result = resync.scan()
    if not result.reachable:
        return {"reachable": False, "unreachable_reason": result.unreachable_reason, "applied": {}}

    present_by_step = {f.repair_step: f for f in result.findings if f.repair_step}
    to_apply = []
    for step in SAFE_SCHEDULED_STEPS:
        finding = present_by_step.get(step)
        if finding is None:
            continue
        if finding.needs_decision or step in EXPENSIVE_STEPS:
            # Defense in depth — should be unreachable given SAFE_SCHEDULED_
            # STEPS' own construction, but an automated, unattended write path
            # is exactly the place to assert an invariant rather than trust it.
            log.warning("egeria_resync scheduler: skipping %r — needs_decision=%s expensive=%s",
                        step, finding.needs_decision, step in EXPENSIVE_STEPS)
            continue
        to_apply.append(step)

    if not to_apply:
        return {"reachable": True, "unreachable_reason": "", "applied": {}}

    applied = resync.apply(to_apply)
    return applied


def _loop(interval: int, stop: threading.Event) -> None:
    while not stop.is_set():
        # Before the scan and outside every lock: heal a missing secrets file.
        from resource_explorer.omsecrets_reproject import heal_missing

        heal_missing()
        try:
            from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

            with acting_as(Daemon(DaemonReason.RESYNC)):
                result = scan_and_clear()
            with _status_lock:
                _status["last_run_at"] = _now()
                _status["last_reachable"] = result.get("reachable", True)
                _status["last_unreachable_reason"] = result.get("unreachable_reason", "")
                _status["last_applied"] = result.get("applied", {})
                _status["last_error"] = ""
                _status["consecutive_failures"] = 0
        except Exception as exc:
            # Never let a bad pass kill the loop — the next tick should still
            # get a chance, same discipline bootstrap.py's loop uses.
            log.exception("egeria_resync: scheduled scan-and-clear pass failed")
            with _status_lock:
                _status["last_run_at"] = _now()
                _status["last_error"] = str(exc)
                _status["consecutive_failures"] += 1
        stop.wait(interval)


def get_status() -> dict:
    """Status for the admin banner — same shape convention as
    bootstrap.get_status(), so both scheduled-recovery mechanisms read the
    same way in the UI."""
    with _status_lock:
        return dict(_status)


def start_scheduler(interval: int = CHECK_INTERVAL_SECONDS) -> None:
    """Start the background scan-and-clear loop. Never blocks or fails
    startup — matches bootstrap.start_scheduler()'s exact contract, including
    running the first pass inside the thread rather than before returning."""
    global _stop_event, _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event = threading.Event()
    _thread = threading.Thread(
        target=_loop, args=(interval, _stop_event),
        name="egeria-resync-scheduler", daemon=True,
    )
    _thread.start()
    log.info("egeria_resync: scheduled scan-and-clear started (check interval: %ds)", interval)


def stop_scheduler() -> None:
    global _stop_event, _thread
    if _stop_event is not None:
        _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
    _stop_event = None
    _thread = None

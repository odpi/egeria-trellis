"""
Publishes a SurveyResult to Egeria via pyegeria.

**Element type, corrected 2026-09-14** (docs/Backlog.md, "Catalogue in
layers" — the repository-as-SourceControlLibrary misreading of 0056 Resource
Managers): a repository is not a resource MANAGER, it is what one manages.
Since August, `_find_or_create_asset` created one `SourceControlLibrary` per
repository — a SoftwareCapability, not an Asset, and a type naming the
*service* (GitHub) rather than the thing the service holds. It is now:

- **One `SourceControlLibrary` for GitHub itself** — a singleton, found or
  created once per Egeria instance (`_find_or_create_github_scl`), cached
  under `app_settings` (there is exactly one, so a per-project column would
  be the wrong shape).
- **Each repository is its own `Asset`** (generic — CapabilityAssetUse's own
  type definition declares its asset end as `Asset`, not a specific
  subtype; nothing in the open metadata types names a narrower "repository"
  concept), qualifiedName `GitHubRepository::<github_url>`, linked to the
  GitHub SourceControlLibrary via `CapabilityAssetUse` (`useType: OWNS`) at
  creation. SurveyReports still attach to THIS element exactly as before —
  `_create_survey_report`'s `parentGUID` is type-agnostic, so nothing
  downstream of `_find_or_create_asset` needed to change.

The repository being a genuine Asset now, rather than a SoftwareCapability,
also resolves the bug named in `egeria_linkage.py`'s comment history:
`AssetMaker.get_asset_by_guid` will actually work on it. That file still
uses the type-agnostic `get_metadata_element_by_guid` rather than switching
to `get_asset_by_guid` — safer regardless of an element's declared type, and
not worth touching in the same change as the type correction itself.

No migration for already-published elements: this is a dev environment, and
the project owner's 2026-09-14 decision is to wipe and redeploy Egeria fresh
rather than re-parent or re-type what is already there (docs/Backlog.md).
This fix has to land *before* that redeploy, or the fresh database is
repopulated with the same wrong structure — which is what makes it worth
fixing now rather than folding into "schedule, do not hot-fix".

The method below is still called _find_or_create_asset and the registry
column is still egeria_asset_guid; both names are now accurate rather than
inherited-and-misleading, which is a nice side effect of the fix rather than
its point.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

from resource_explorer.egeria_timing import time_egeria_call
from resource_explorer.surveyors.survey_report import AnnotationType, SurveyResult
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

# ── Egeria connection defaults (standard pyegeria env vars) ──────────────────
_DEFAULT_PLATFORM_URL = "https://localhost:9443"
_DEFAULT_VIEW_SERVER = "qs-view-server"
_DEFAULT_USER = "erinoverview"
_DEFAULT_TIMEOUT = 30


def governance_summary(outcomes: dict, *, private: bool = False) -> str:
    """The governance sentence of the publish summary, each part from its OWN recorded outcome.

    `outcomes` is `{guid: {"ownership": bool, "zone_membership": bool, "zones": [...], ...}}`
    as `stamp_published` returns it. Ownership and zone membership are reported separately, and
    "no zone configured" is a deployment choice (zones are left to Egeria), never a failure: only
    a classification that was attempted and did not land gets the warning.

    `private`: the publish was for a member of a private investigation. "Zones left to Egeria" is
    then never true of it (its report was made private, or the publish raised before saying
    anything), so an outcome set with no zone attempted, or no outcome at all, is said as the gap
    it is rather than as a deployment choice (2026-10-13 review).
    """
    if not outcomes:
        return " (⚠ private publish: no governance outcome recorded)" if private else ""
    n = len(outcomes)
    owned = sum(1 for o in outcomes.values() if o.get("ownership"))
    parts: list[str] = []
    if owned < n:
        parts.append(f"⚠ Ownership NOT set on {n - owned} of {n} element(s) — published but unowned")
    else:
        parts.append(f"Ownership set on {n} element(s)")
    attempted = [o for o in outcomes.values() if o.get("zones")]
    if not attempted:
        parts.append("⚠ private publish but NO zone was set on any element" if private
                     else "zones left to Egeria")
    else:
        zoned = sum(1 for o in attempted if o.get("zone_membership"))
        if zoned < len(attempted):
            parts.append(f"⚠ ZoneMembership NOT set on {len(attempted) - zoned} of {len(attempted)} element(s)"
                         " — outside the draft zone")
        else:
            parts.append(f"ZoneMembership set on {len(attempted)} element(s)")
    return " (" + "; ".join(parts) + ")"


class EgeriaConnectionError(RuntimeError):
    """Raised when Egeria credentials are absent or the platform is unreachable."""


class PrivateReportNotConfirmed(RuntimeError):
    """A private investigation's SurveyReport could not be confirmed to be in its private zones.

    Raised BEFORE any annotation is written beneath it, so a member of a private investigation
    reads as FAILED rather than "published" with its findings world-readable (2026-10-13 review).
    """

    def __init__(self, report_guid: str, owner: str, reason: str):
        self.report_guid, self.owner, self.reason = report_guid, owner, reason
        super().__init__(
            f"Not published: this resource belongs to {owner}'s private investigation, and its "
            f"survey report {report_guid[:8] or '(none)'} could not be confirmed private — {reason}. "
            "No findings were written to Egeria. The report element itself may exist; nothing "
            "beneath it was published. Publish again as the investigation's owner once Egeria "
            "can classify and read it."
        )


def sub_resource_display_name(path: str, kind: str, repository: str) -> str:
    """The display name of a cataloged file or folder: `<relative path> · <repository>`
    ("LICENSE · egeria_git", "docs/README.md · egeria-workspaces"); a folder's path ends in "/".
    The file name stays the basename; only this name carries the path, so a LICENSE in every
    repository is no longer a thousand indistinguishable rows (ruling 2026-10-07)."""
    if kind == "folder":
        shown = (path.rstrip("/") + "/") if path else "/"
    else:
        shown = path
    return f"{shown} \u00b7 {repository}"


def rename_sentence(counts: dict) -> str:
    """The tail of a publish sentence about names: "" when nothing was renamed or failed."""
    if not isinstance(counts, dict) or not (counts.get("updated") or counts.get("failed")):
        return ""
    tail = f" \u00b7 {counts.get('updated', 0)} name{'' if counts.get('updated') == 1 else 's'} updated"
    if counts.get("failed"):
        tail += f" \u00b7 {counts['failed']} could not be updated"
    return tail


#: A sub-resource whose parent folder's look-up could not be made: it is not checked and not created either.
PARENT_NOT_CHECKED = "not checked: its parent folder could not be checked"

class EgeriaPublisherError(RuntimeError):
    """Raised when an Egeria-native operation triggered through EgeriaPublisher
    (e.g. trigger_survey_by_guid) fails — mirrors
    EgeriaDatabaseSurveyorError/EgeriaFileSystemSurveyorError."""


def _analyses_for_steps(step_keys) -> set:
    """The analyses whose steps these are.

    An analysis counts as published when ANY of its steps ran: a partial run
    still published real findings under its name, and reporting nothing would
    lose them. Imported lazily — the adapter imports this module.
    """
    if not step_keys:
        return set()
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_STEP_MAP,
        )
    except ImportError:  # pragma: no cover - defensive
        return set()
    wanted = set(step_keys)
    return {aid for aid, keys in REPO_ANALYSIS_STEP_MAP.items()
            if wanted & set(keys)}


class EgeriaPublisher:
    """
    Converts a SurveyResult into Egeria API calls:
      1. Find or create the repository's Asset (and the GitHub
         SourceControlLibrary it belongs to, via CapabilityAssetUse)
      2. Create a SurveyReport linked via ReportSubject
      3. Create one Annotation per SurveyResult.annotation

    All Egeria coupling is contained here — sub-surveyors and SurveyResult
    have no pyegeria dependency.
    """

    def __init__(
        self,
        platform_url: str | None = None,
        view_server: str | None = None,
        user_id: str | None = None,
        user_password: str | None = None,
        timeout: int | None = None,
        registry: "ProjectRegistry | None" = None,
        zone_names: list[str] | None = None,
        identity: "Any | None" = None,
    ) -> None:
        # Who this publish runs as. `None` means "resolve from the caller when
        # `_connect` runs" — deliberately deferred rather than captured here,
        # because a publisher is routinely built at the top of a CLI command or
        # a route and used further down, and the identity that matters is the
        # one in force at the call, not at construction.
        self._identity = identity
        self.platform_url = platform_url or egeria_platform_url()
        self.view_server = view_server or egeria_view_server()
        self.user_id = user_id or os.getenv("EGERIA_USER", _DEFAULT_USER)
        # Not used to authenticate: who acts is the factory's business (Brief I). Kept as an
        # attribute only because callers still pass it; never read from the environment.
        self.user_password = user_password or ""
        self.timeout = timeout or int(os.getenv("PYEGERIA_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT)))
        self._registry = registry
        #: What `_stamp_governance` last managed to set, per element GUID.
        #: Read by `publish()` to warn when a publish left an element
        #: unowned or outside the draft zone.
        self.last_governance: dict = {}
        #: Set by `_create_annotations()` when `RunsConfig.publish_inline` is
        #: False: the annotation outbox rows were enqueued but NOT drained
        #: (the scheduler's periodic `_drain_egeria_outbox` will apply them,
        #: within its cycle). Read by `run_analysis()` after `publish()`
        #: returns to decide between the three-state `published` values
        #: True ("landed in Egeria before this call returned") and "queued"
        #: ("durably enqueued; not yet applied"). Stays False for the
        #: no-registry direct-write path, which has no outbox to defer to.
        self.publish_deferred = False
        #: True only when this publish's `publish_read_back` outbox row is DONE before `publish()` returns (an
        #: inline publish whose drain landed every write and whose read-back found them under the report). The
        #: proof itself is that row (`registry.publish_proofs`); this mirrors it for the caller. When False on the
        #: registry path, `publish_deferred` is True too: the run is "queued for publish" until the drain reads
        #: it back, whichever branch ran.
        self.publish_read_back = False
        #: Per-call override of `RunsConfig.publish_inline`, set by `publish(
        #: defer_drain=...)` — the run-in-background choice (project owner,
        #: 2026-09-13): the caller decides per RUN whether to wait for the
        #: Egeria publish or enqueue and move on, rather than that being a
        #: single deployment-wide flag. `None` (the default, and what every
        #: caller that never passes `defer_drain` leaves it at) means "follow
        #: `RunsConfig.publish_inline`", preserving old behaviour byte for
        #: byte. Read by `_create_annotations`, which is also reachable
        #: directly (bypassing `publish()`) by tests exercising the enqueue-
        #: vs-drain decision in isolation — those still get the config
        #: default, unaffected by this override existing.
        self._defer_drain_override: bool | None = None
        #: The outbox run_id (the annotation qualified_name prefix) this
        #: publish's annotations were enqueued under — set by
        #: `_create_annotations`. Empty until a publish actually reaches that
        #: point. `run_analysis` reads this after a deferred publish to tag
        #: the activity row, so a later drain can find it again and flip
        #: `published` from "queued" to True (registry.
        #: complete_publish_run_if_done).
        self.publish_run_id: str = ""
        #: Brief F round 4: whether this publish reached the asset and sent the documentation sources itself, so the
        #: page's press does not send them a second time (single_publish.send_with_publish(doc_sources=...)).
        self.doc_sources_attempted: bool = False
        #: True when the SurveyReport create met a 409 on its name and the report Egeria already
        #: held was reused instead (parity slice G1, PI-001: never a second report). Reset per publish.
        self.report_reused = False
        #: Set by `publish()` for a deferred publish only — see that
        #: assignment's own comment for what it carries and why.
        self.pending_published_record: dict | None = None
        self._asset_maker = None
        self._discovery = None
        self._automated_curation = None
        self._external_references = None
        self._metadata_expert = None
        # Zone names: caller-supplied > RE's draft zone.
        #
        # **Changed 2026-09-04 (plan §4, "one zone per app").** This used to
        # fall back to `egeria.default_catalog_zones`, which is empty on every
        # checkout — so everything RE published landed in the view server's
        # default zones with nothing distinguishing "surveyed, not yet
        # reviewed" from "curated". Now an unreviewed publish goes to the
        # configured draft zone (if any) and `curate accept` promotes it (see
        # `workflows/curate.promote_to_publish_zones`). `default_catalog_zones`
        # is still honoured as the *publish* target — see
        # `egeria_identity.configured_publish_zones` — which is the question
        # it was actually asking. There is no default publish zone
        # (configured-only, 2026-10-07): with none configured promotion
        # clears the draft zone instead.
        if zone_names is not None:
            self.zone_names = zone_names
        else:
            from resource_explorer.egeria_identity import draft_zones

            self.zone_names = draft_zones()
        #: Set per-publish by `publish()`. Empty means "not a private resource".
        self._private_owner = ""

    # ── public entry point ────────────────────────────────────────────────────

    def publish(self, result: SurveyResult, zone_names: list[str] | None = None, *,
               defer_drain: bool | None = None) -> str:
        """
        Push the full SurveyResult to Egeria.
        Returns the GUID of the created SurveyReport.
        Raises EgeriaConnectionError if credentials are missing or platform unreachable.
        zone_names overrides the instance-level zone_names for this call only.

        defer_drain: the run-in-background choice, per call. True enqueues
        this publish's annotations (and their evidence links) without
        draining them — `publish_deferred` ends up True and the caller reports
        `published: "queued"`. False drains inline, unchanged original
        behaviour. None (the default) follows `RunsConfig.publish_inline`,
        which is what every caller that never passes this argument keeps
        doing. See `_create_annotations` for where this is actually read.
        """
        if zone_names is not None:
            self.zone_names = zone_names
        self._defer_drain_override = defer_drain
        self.report_reused = False
        self.publish_deferred = False
        self.publish_read_back = False
        self.publish_run_id = ""
        # Privacy is decided here, before anything is written, and it OVERRIDES
        # the caller's zone_names rather than deferring to them. A caller that
        # passes explicit zones is asking where a normal publish should land;
        # it is not authorising a private resource to be published publicly,
        # and a private artifact in the wrong zone cannot be un-leaked.
        self._private_owner = self._resolve_private_owner(result.resource_slug)
        if self._private_owner:
            self._require_enforced_private_zone(result.resource_slug)
            from resource_explorer.egeria_identity import private_zones

            self.zone_names = private_zones(self._private_owner)
            # Only the signed-in OWNER can write a private report and the findings beneath it: Egeria judges a
            # create by the zones it is born with, and an annotation by its anchor's. Anyone else (a daemon, a
            # queued run, another person) is refused here, BEFORE the asset, homepage, documentation-source or
            # GitHub-library writes, rather than by a raw 403 at the report create after them (2026-10-13 review).
            self._require_owner_identity(result.resource_slug)
        self._require_zones_grant_someone(result.resource_slug)
        self._connect()

        # Everything below depends on the asset GUID RE cached for this project,
        # directly or through the report created against it. If Egeria no longer
        # knows that GUID, this raises a named error and records the divergence
        # instead of surfacing a 500-word server stack — see egeria_linkage.py.
        cached_guid = ""
        if self._registry:
            try:
                cached_guid = self._registry.get_egeria_asset_guid(result.resource_slug) or ""
            except Exception:
                cached_guid = ""

        from resource_explorer.egeria_linkage import guard_linkage

        try:
            with guard_linkage(self._registry, "repo", result.resource_slug,
                               result.project_display_name, cached_guid):
                asset_guid = self._find_or_create_asset(result)
                # Best-effort and deliberately not fatal — see the method's docstring.
                self._publish_homepage_reference(result, asset_guid)
                # E2: declared documentation sources for a repo, same hook the
                # database/filesystem publish routes already have. Best-effort.
                self._publish_local_doc_sources(result.resource_slug, asset_guid)
                # Brief F round 2: tags and the group are NOT sent here any more. This publish also runs for analysis
                # auto-publish, bulk ops, resync and the CLI (as the Daemon); the owner's rule is ONE Publish per page,
                # so only a person's press sends them (single_publish.send_with_publish, the db/fs publish routes).
                report_guid = self._create_survey_report(result, asset_guid)
                # Ownership + draft zone, on both elements this publish is
                # responsible for, before the annotations are written. Ordered
                # this way deliberately: the classification is what makes the
                # element *this person's draft*, and a reader who catches the
                # catalogue mid-publish should see an owned draft rather than an
                # unowned element in the deployment's normal zones.
                # The asset and the report are stamped DIFFERENTLY when the
                # resource is private, and conflating them is a real bug this
                # nearly shipped with.
                #
                # The asset is `GitHubRepository::<github_url>` — ONE per repo,
                # shared by every investigation that references it. Zoning it
                # private would hide a public repository from everyone else in the
                # catalog because one person put it in a personal investigation:
                # design §3.6, "an investigation zones what it produced, never what
                # it references". A privacy feature that deletes other people's
                # access is a data-loss feature.
                #
                # The report IS produced by this investigation, so it takes the
                # private zones, and its annotations inherit them by anchor.
                self._stamp_governance(report_guid)
                self._stamp_governance(asset_guid, produced=False)
                if self._private_owner:
                    # Fail closed: the findings go beneath the report only once Egeria SHOWS the
                    # report in its private zones. Raises otherwise; `publish()` records why.
                    self._confirm_report_private(result, report_guid)
                link_counts = self._create_annotations(result, report_guid)
        except PrivateReportNotConfirmed as exc:
            # The ONE place a refused private publish is recorded, whichever guard refused it (the reused-report
            # check, a classification client that could not be built, or the read-back).
            self._record_private_refusal(result, exc.report_guid, exc.reason)
            raise
        # Best-effort, deliberately outside the guard_linkage block above —
        # this is local bookkeeping for the Survey Results dashboards'
        # per-card "last published" badge (get_last_published_annotation_
        # types), not part of the real Egeria write; a failure here must
        # never raise and make an already-successful publish look failed.
        # Not silently log-only, though (tests/test_no_silent_success.py's
        # ratchet caught the first version of this doing exactly that): the
        # outcome is folded into the log_catalog activity_log entry below —
        # a real, greppable, user-visible signal (Activity tab), not a debug
        # line nobody reads. Recoverable either way: a card just shows "Not
        # published" until the next successful publish, or via
        # scripts/backfill_published_annotation_types.py.
        annotation_types_warning = ""
        #: Set below ONLY for a deferred publish — what would have been
        #: recorded into the badge tables, for `registry.
        #: complete_publish_run_if_done()` to apply once the run's outbox
        #: rows actually land. `None` for every other path (inline, no
        #: registry, no annotations). Read by `run_analysis()` and carried
        #: onto the activity row's detail so the flip has it later.
        self.pending_published_record: dict | None = None
        if self._registry:
            if self.publish_deferred:
                # Nothing has reached Egeria yet — writing project_published_
                # annotation_types/project_published_analyses now would flip
                # the ☁ Published badge before any annotation actually
                # landed, for exactly as long as the drain takes. Stash the
                # SAME two calls' arguments instead; complete_publish_run_
                # if_done() makes them once the run is verifiably done.
                self.pending_published_record = {
                    "slug": result.resource_slug,
                    "annotation_types": sorted(
                        {a.annotation_type.value for a in result.annotations}
                    ),
                    # Which ANALYSES this publish covers, recorded directly
                    # rather than inferred later from shared annotation
                    # types — same reasoning as the inline branch below.
                    "analyses": sorted(_analyses_for_steps(result.steps_run)),
                    "report_guid": report_guid,
                }
            elif result.annotations:
                # Nothing published (no annotation) records no badge: a ☁ needs something behind it.
                try:
                    self._registry.record_published_annotation_types(
                        result.resource_slug,
                        {a.annotation_type.value for a in result.annotations},
                        report_guid,
                    )
                    # Which ANALYSES this publish covered, recorded directly rather
                    # than inferred later from shared annotation types. The
                    # orchestrator put the step keys on the result; mapping them is
                    # a lookup, not a guess.
                    self._registry.record_published_analyses(
                        result.resource_slug,
                        _analyses_for_steps(result.steps_run),
                        report_guid,
                    )
                except Exception as exc:
                    annotation_types_warning = f" (⚠ last-published tracking not recorded: {exc})"
                    log.warning("record_published_annotation_types failed (non-fatal): %s", exc)

        # annotation-linking-plan Phase 2: partial link failure must stay
        # visible to a reader of the same summary a person actually looks
        # at, not only to an operator who thinks to check Admin → Publish
        # Queue. `links_created`/`links_failed`/`links_skipped` come back
        # from `_create_annotations` above regardless of failure — this is
        # additive to the summary text, never gating it (the survey report
        # and every annotation are already durable by this point).
        # The cap is a safety net, and a net nobody is told about is a hole.
        # enqueue_annotations logs a warning; this is the half a person sees.
        cap_warning = ""
        produced = link_counts.get("annotations_produced")
        queued = link_counts.get("annotations_queued")
        if produced is not None and queued is not None and queued < produced:
            cap_warning = (
                f" (⚠ CAPPED: {produced - queued} of {produced} annotation(s) NOT "
                f"published — see the outbox enqueue cap)"
            )

        # Governance: an element published without its Ownership or its draft
        # zone is *published* but not *governed* — it has no owner to curate it
        # and it is not held out of the deployment's normal zones. Silence
        # about that would be the "we never measured this looks like we
        # measured nothing" failure, so it gets its own sentence in the same
        # summary the counts appear in.
        governance_warning = governance_summary(getattr(self, "last_governance", {}) or {},
                                                private=bool(self._private_owner))

        link_warning = ""
        failed_or_skipped = link_counts.get("links_failed", 0) + link_counts.get("links_skipped", 0)
        if failed_or_skipped:
            link_warning = (
                f" (⚠ {link_counts['links_created']}/{link_counts['links_attempted'] + link_counts['links_skipped']}"
                f" evidence link(s) created)"
            )

        # RunsConfig.publish_inline=False: nothing above has actually landed
        # in Egeria yet — the annotation rows are durably enqueued, not
        # applied. "Published to Egeria: N annotations" below would be a lie
        # in that case, so this note is prepended into the same warning slots
        # rather than left implicit in a count that looks identical either way.
        deferred_note = ""
        if self.publish_deferred:
            deferred_note = (
                f" — {link_counts.get('annotations_queued', 0)} Egeria write(s) "
                f"queued for publish (not read back yet; next scheduled drain <=15 min)"
            )
            if link_counts.get("links_queued"):
                deferred_note += (
                    f", {link_counts['links_queued']} evidence link(s) queued"
                    f" (will apply once the annotations they reference do)"
                )
        log.info(
            "Published survey for %s → SurveyReport GUID %s (%d annotations)",
            result.resource_slug,
            report_guid,
            len(result.annotations),
        )

        if self._registry:
            try:
                from resource_explorer.activity_logger import log_catalog
                # The lead word follows the read-back proof: "published" only once the run was read back.
                lead = ("Queued for publish to Egeria: " if self.publish_deferred
                        else "Published to Egeria · read back: " if self.publish_read_back
                        else "Nothing to publish to Egeria: ")      # no annotation queued: no run, no proof
                catalog_entry = log_catalog(
                    self._registry,
                    entity_type="repo",
                    entity_slug=result.resource_slug,
                    entity_name=result.project_display_name,
                    entity_location=result.github_url,
                    status="ok",
                    summary=(
                        # The QUEUED count when there is one, not the produced
                        # count — a capped run must not report the number it
                        # made as the number it published.
                        f"{lead}"
                        f"{link_counts.get('annotations_queued', len(result.annotations))}"
                        f" annotations"
                        f" → {(report_guid or 'no-guid')[:12]}…"
                        f"{annotation_types_warning}{link_warning}{cap_warning}"
                        f"{governance_warning}{deferred_note}"
                    ),
                    items=[
                        {
                            "kind": "GitHubRepository",
                            "display_name": result.project_display_name,
                            "qualified_name": f"GitHubRepository::{result.github_url}",
                            "guid": asset_guid,
                            "location": result.github_url,
                        },
                        {
                            "kind": "SurveyReport",
                            "display_name": f"Survey: {result.project_display_name}",
                            "qualified_name": (
                                f"SurveyReport::GitHubRepo::{result.resource_slug}"
                                f"::{result.surveyed_at.isoformat()}"
                            ),
                            "guid": report_guid,
                            "location": "",
                        },
                    ],
                )
                if self.publish_run_id and catalog_entry:
                    # The Activity panel shows this entry's word from the run's proof rows, as it does the run's.
                    self._registry.tag_activity_publish_run(catalog_entry, self.publish_run_id)
            except Exception as exc:
                log.warning("Could not write activity log entry: %s", exc)

        return report_guid

    def get_survey_reports_by_guid(self, project_guid: str) -> list[dict]:
        """All SurveyReports linked to this repo's asset GUID via the real
        ReportSubject relationship — regardless of which engine created them.
        Thin wrapper over the engine-agnostic reader shared with
        EgeriaDatabaseSurveyor/EgeriaFileSystemSurveyor (egeria_survey_reader.py)."""
        from resource_explorer.surveyors.egeria_survey_reader import get_survey_reports_by_guid

        self._connect()
        return get_survey_reports_by_guid(self._asset_maker, project_guid)

    def get_annotations_by_report_guid(self, report_guid: str) -> list[dict]:
        """All annotations attached to a specific SurveyReport via the real
        ReportedAnnotation relationship. Thin wrapper over the engine-agnostic
        reader in egeria_survey_reader.py."""
        from resource_explorer.surveyors.egeria_survey_reader import get_annotations_by_report_guid

        self._connect()
        return get_annotations_by_report_guid(self._asset_maker, report_guid)

    # ── Egeria-native survey triggering (Backlog "Path B3") ─────────────────
    #
    # Repos, unlike databases and filesystems, are cataloged in Egeria as a
    # plain generic `Asset` (see this module's own docstring, "Element type,
    # corrected 2026-09-14") — there is no registered Egeria Technology Type
    # for "repository" the way there is "PostgreSQL Relational Database" or
    # "File System Directory". The one repo-specific string this codebase
    # already uses when talking to Egeria about a repository is
    # `additionalProperties.deployed_implementation_type = "GitHub Repository"`
    # (set in `_find_or_create_asset` above) — that is the string used here,
    # rather than `_ADAPTER.technology_type` ("Git Repository") in
    # repo_survey_definition_adapter.py, which is a *different*, RE-internal
    # convention (see ResourceTypeAdapter's own docstring in
    # survey_definition_executor.py for why the two are deliberately not the
    # same value and are not guaranteed to match).
    _REPO_TECH_TYPE = "GitHub Repository"

    def trigger_survey_by_guid(self, repo_guid: str, start_time: "datetime | None" = None) -> str:
        """Initiate Egeria's own native repository survey using a stored
        Asset GUID — the repo equivalent of
        EgeriaDatabaseSurveyor.trigger_survey_by_guid /
        EgeriaFileSystemSurveyor.trigger_survey_by_guid.

        Use this when the repository is already cataloged in Egeria
        (repo_guid known) — Egeria surveys the already-cataloged Asset, it
        does not create one as a side effect.

        Unlike EgeriaFileSystemSurveyor's version (which triggers a single,
        confirmed-live GovernanceActionType by a hardcoded qualifiedName),
        this mirrors EgeriaDatabaseSurveyor's *dynamic* discovery mechanism
        (`_initiate_survey`/`_find_survey_process_name`, generic over a
        `tech_type` string) rather than a repo-specific one — added
        2026-09-20 on an explicit project-owner decision to build this
        plumbing now even though no live repo survey action service may
        exist in Egeria yet ("we will probably have some surveys that
        execute there at some point"). Until such a service is authored and
        registered in Egeria (or configured in
        configdata/technology_type_processes.yaml), `_initiate_survey` below
        will legitimately find zero candidates and raise a clear,
        specific error — that is the correct, expected behavior for now,
        not a bug (see docs/Backlog.md).
        """
        self._connect()
        try:
            action_guid = self._initiate_survey(self._REPO_TECH_TYPE, repo_guid, start_time=start_time)
            log.info(f"Egeria repository survey initiated: {action_guid}")
            return action_guid
        except Exception as exc:
            raise EgeriaPublisherError(
                f"Failed to initiate Egeria survey for repo_guid={repo_guid}: {exc}"
            ) from exc

    def _initiate_survey(self, tech_type: str, target_guid: str, start_time: "datetime | None" = None) -> str:
        """Dynamically find a user-authored Survey Definition process for
        tech_type and initiate it as a GovernanceActionProcess; if none is
        found (or it fails to initiate), fall back to the native survey
        GovernanceActionType configured for this tech_type in
        config/technology_type_processes.yaml (entity_type="repo").

        Copied from EgeriaDatabaseSurveyor._initiate_survey rather than
        shared via a common base class — see that method's docstring for the
        two-step discover-then-fallback shape this mirrors exactly, generic
        over tech_type/entity_type rather than PostgreSQL-specific.
        """
        process_name = self._find_survey_process_name(tech_type)
        if process_name:
            try:
                targets = [
                    {
                        "class": "NewActionTarget",
                        "actionTargetName": "serverToSurvey",
                        "actionTargetGUID": target_guid,
                    }
                ]
                guid = self._automated_curation.initiate_gov_action_process(
                    action_type_qualified_name=process_name,
                    action_targets=targets,
                    start_time=start_time,
                )
                log.info(f"Initiated dynamically discovered survey process {process_name} on {target_guid}: {guid}"
                         + (f" (start_time={start_time.isoformat()})" if start_time else ""))
                return guid
            except Exception as exc:
                log.warning(f"Failed to initiate dynamically discovered survey process {process_name}: {exc}. Trying fallback...")

        from resource_explorer.surveyors.technology_type_processes import (
            KIND_SURVEY_EXISTING,
            get_process_by_kind,
        )

        native = get_process_by_kind("repo", tech_type, KIND_SURVEY_EXISTING)
        if not native:
            raise RuntimeError(
                f"No native survey process configured for technology_type={tech_type!r} "
                "(entity_type='repo') — add an entry to "
                "config/technology_type_processes.yaml once a repo survey action "
                "service exists and is confirmed live against Egeria; until then "
                "this is the expected, correct outcome, not a bug."
            )
        if start_time:
            log.info(
                f"start_time={start_time.isoformat()} requested but the native-survey fallback path "
                f"has no start_time parameter — firing immediately."
            )
        import asyncio

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self._automated_curation._async_initiate_survey(native.qualified_name, target_guid)
        )

    def _find_survey_process_name(self, tech_type: str) -> str | None:
        """Query for a user-authored Survey Definition process supporting
        tech_type and return its qualifiedName. Delegates to
        SurveyDefinitionReader.find_candidate_process_guids (same mechanism
        EgeriaDatabaseSurveyor._find_survey_process_name uses), keeping this
        method's single-result contract for _initiate_survey above."""
        from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

        self._connect()
        reader = SurveyDefinitionReader(
            self.platform_url, self.view_server, identity=self.resolve_identity()
        )
        reader._automated_curation = self._automated_curation
        candidates = reader.find_candidate_process_guids(tech_type)
        if candidates:
            qn = candidates[0]["qualified_name"]
            log.info(f"Dynamically discovered Egeria survey process for {tech_type}: {qn}")
            return qn
        return None

    # ── connection ────────────────────────────────────────────────────────────

    def resolve_identity(self):
        """The `EgeriaIdentity` this publish runs as. Resolved once, at connect.

        An identity handed to the constructor (the outbox drain's `Daemon(OUTBOX)`), else
        `egeria_clients.current_principal()`: the declared daemon job (scheduler, resync, a queued
        run's `Daemon(RUN_QUEUE, requested_by)`), else the signed-in Caller. With none of those it
        raises `NoCallerIdentity` — no silent service-account fallback (Brief I).
        """
        if self._identity is not None:
            return self._identity
        from resource_explorer.egeria_clients import current_principal

        self._identity = current_principal()
        return self._identity

    def _connect(self) -> None:
        if not self.platform_url:
            raise EgeriaConnectionError(
                "EGERIA_PLATFORM_URL is not set. "
                "Add it to your .env file or pass platform_url= to EgeriaPublisher."
            )
        identity = self.resolve_identity()
        try:
            from pyegeria import AssetMaker, AutomatedCuration, CollectionManager, ExternalReferences
            from pyegeria.omvs.data_discovery import DataDiscovery
            from pyegeria.omvs.metadata_expert import MetadataExpert

            from resource_explorer.egeria_clients import egeria_client

            # Every sub-client from the one factory, sharing one token: a person's clients carry
            # THEIR bearer token, so Egeria's provenance records them; a daemon's mint once.
            clients = egeria_client(identity, purpose="publish", view_server=self.view_server,
                                    platform_url=self.platform_url)
            self._clients = clients
            self.user_id = clients.user_id
            self._asset_maker = clients.of(AssetMaker)
            self._discovery = clients.of(DataDiscovery)
            # Used only by _catalog_sub_resources() (D6) — template-based
            # FileFolder/DataFile creation for worthy sub-resources.
            self._automated_curation = clients.of(AutomatedCuration)
            # Used only by _publish_homepage_reference().
            self._external_references = clients.of(ExternalReferences)
            # The only client that can create a bare relationship (AnnotationExtension) between
            # two existing GUIDs (docs/annotation-linking-plan.md, discovery finding #2).
            self._metadata_expert = clients.of(MetadataExpert)
            # egeria_outbox's collection_membership rows need it (2026-09-04 fix).
            self._collection_manager = clients.of(CollectionManager)
        except ImportError as exc:
            raise EgeriaConnectionError(
                "pyegeria is not installed. Add it to your dependencies."
            ) from exc
        except PermissionError:
            raise          # NoCallerIdentity / CallerTokenExpired: a 401, not a connection error
        except Exception as exc:
            raise EgeriaConnectionError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    # ── asset registration ────────────────────────────────────────────────────

    #: The singleton's qualifiedName. Freely changeable at will — nothing
    #: keys on the string itself, only on the GUID `app_settings` caches
    #: under `_GITHUB_SCL_SETTING_KEY`.
    _GITHUB_SCL_QUALIFIED_NAME = "SourceControlLibrary::github.com"
    _GITHUB_SCL_SETTING_KEY = "egeria.github_source_control_library_guid"

    def _find_or_create_github_scl(self) -> str:
        """Return the GUID of the one `SourceControlLibrary` representing
        GitHub itself — genuinely a SoftwareCapability, unlike a repository.

        Same verify-then-search-then-create shape as `_find_or_create_asset`,
        but keyed on a fixed qualifiedName and cached as a single global
        setting (`app_settings`) rather than a per-project column: there is
        exactly one of these across the whole catalog.
        """
        qualified_name = self._GITHUB_SCL_QUALIFIED_NAME

        if self._registry:
            cached = self._registry.get_setting(self._GITHUB_SCL_SETTING_KEY)
            if cached:
                try:
                    with time_egeria_call(self._registry, "find_software_capabilities", "query"):
                        check = self._asset_maker.find_software_capabilities(
                            search_string=qualified_name, starts_with=True,
                            ignore_case=False, output_format="JSON",
                        )
                    if isinstance(check, list) and any(
                        e.get("elementHeader", {}).get("guid") == cached for e in check
                    ):
                        return cached
                    log.warning("Cached GitHub SourceControlLibrary GUID %s no longer in "
                                "Egeria — will re-register", cached)
                except Exception as exc:
                    log.debug("GitHub SCL cache verification failed (will proceed): %s", exc)
                    return cached  # network error — trust the cache rather than re-create

        try:
            with time_egeria_call(self._registry, "find_software_capabilities", "query"):
                existing = self._asset_maker.find_software_capabilities(
                    search_string=qualified_name, starts_with=True,
                    ignore_case=False, output_format="JSON",
                )
            match = next(
                (e for e in (existing or [])
                 if (e.get("properties") or {}).get("qualifiedName") == qualified_name),
                None,
            ) if isinstance(existing, list) else None
            guid = (match or {}).get("elementHeader", {}).get("guid") if match else None
            if guid:
                if self._registry:
                    self._registry.set_setting(self._GITHUB_SCL_SETTING_KEY, guid)
                return guid
        except Exception as exc:
            log.debug("GitHub SCL search failed (will create): %s", exc)

        props = {
            "class": "SoftwareCapabilityProperties",
            "typeName": "SourceControlLibrary",
            "qualifiedName": qualified_name,
            "displayName": "GitHub",
            "description": "GitHub — the source control service every repository asset "
                            "this catalog holds is an asset of.",
            "url": "https://github.com",
        }
        # A REFERENCED element, deployment-wide (§3.6): never this publish's zones, which are the
        # private ones when the resource belongs to a private investigation.
        zones = self._referenced_zones()
        if zones:
            props["zoneMembership"] = zones
        with time_egeria_call(self._registry, "create_software_capability", "write"):
            guid = self._asset_maker.create_software_capability(
                body={"class": "NewElementRequestBody", "properties": props})
        log.info("Created the GitHub SourceControlLibrary, GUID %s", guid)
        if self._registry:
            self._registry.set_setting(self._GITHUB_SCL_SETTING_KEY, guid)
        return guid

    def _find_or_create_asset(self, result: SurveyResult) -> str:
        """Return the GUID of the Asset representing this GitHub repository.

        Checks the registry cache first to avoid an unnecessary search call on
        repeated --publish runs. Creates the asset — and links it to the
        GitHub SourceControlLibrary via CapabilityAssetUse — when not already
        registered.
        """
        qualified_name = f"GitHubRepository::{result.github_url}"

        # Check local cache first, but verify the GUID still exists in Egeria.
        # If the Egeria database was reset, the cached GUID is stale — clear it
        # and fall through to the search/create path rather than failing later.
        if self._registry:
            cached = self._registry.get_egeria_asset_guid(result.resource_slug)
            if cached:
                try:
                    with time_egeria_call(self._registry, "find_assets", "query"):
                        check = self._asset_maker.find_assets(
                            search_string=qualified_name,
                            starts_with=True,
                            ignore_case=False,
                            output_format="JSON",
                        )
                    if isinstance(check, list) and any(
                        e.get("elementHeader", {}).get("guid") == cached for e in check
                    ):
                        log.info("Using cached Egeria asset GUID %s for %s", cached, result.resource_slug)
                        return cached
                    # GUID not found — Egeria DB may have been reset; clear the stale cache
                    log.warning(
                        "Cached GUID %s no longer in Egeria for %s — will re-register",
                        cached, result.resource_slug,
                    )
                    self._registry.clear_egeria_registration(result.resource_slug)
                except Exception as exc:
                    log.debug("Cache verification failed (will proceed): %s", exc)
                    return cached  # network error — trust the cache rather than re-create

        # Search Egeria for an existing asset with this qualifiedName
        try:
            with time_egeria_call(self._registry, "find_assets", "query"):
                existing = self._asset_maker.find_assets(
                    search_string=qualified_name,
                    starts_with=True,
                    ignore_case=False,
                    output_format="JSON",
                )
            if isinstance(existing, list) and existing:
                # EXACT qualifiedName only. The search is starts_with=True, so
                # a repo whose URL is a PREFIX of another's matches both — and
                # taking existing[0] adopted the wrong asset. Measured live
                # 2026-08-25: docling (.../docling) matched docling-eval and
                # docling-core, and docling + docling_eval ended up sharing one
                # asset GUID, so one repo's survey reports were attaching to the
                # other's catalog entry. Nothing errored; the catalog was just
                # wrong.
                #
                # starts_with is kept rather than narrowed, because the search
                # is also how a legitimately-renamed asset is still found; the
                # filtering belongs here, on the result.
                match = next(
                    (e for e in existing
                     if (e.get("properties") or {}).get("qualifiedName") == qualified_name),
                    None,
                )
                guid = (match or {}).get("elementHeader", {}).get("guid") if match else None
                if guid:
                    # Brief Z2 round 2: an asset ADOPTED by qualifiedName was never checked (the route checked
                    # the repository as not in Egeria, or a stale GUID). Check THIS element before any write.
                    from resource_explorer.workflows.curate import WriteNotPermitted, person_write_refusal

                    if self._registry is not None:
                        why = person_write_refusal(self._registry, "repo", result.resource_slug, "publish",
                                                   element_guid=guid)
                        if why:
                            raise WriteNotPermitted(why)
                    log.info("Found existing Egeria asset GUID %s for %s", guid, result.resource_slug)
                    self._cache_asset_guid(result.resource_slug, guid)
                    return guid
                if existing:
                    # Prefix matches that are not this asset are the normal case
                    # for sibling repos; say so rather than silently creating.
                    log.info(
                        "%d prefix match(es) for %s but none with an exact qualifiedName — creating",
                        len(existing), result.resource_slug,
                    )
        except Exception as exc:
            from resource_explorer.workflows.curate import WriteNotPermitted

            if isinstance(exc, WriteNotPermitted):
                raise                      # a refusal, never "search failed, create one"
            log.debug("Asset search failed (will create): %s", exc)

        # Gather project stats for additional metadata
        primary_language = ""
        license_name = ""
        topics_csv = ""
        if self._registry:
            stats = self._registry.get_latest_project_stats(result.resource_slug)
            if stats:
                primary_language = stats.get("primary_language") or ""
                license_name = stats.get("license") or ""
                topics_csv = stats.get("topics") or ""

        props: dict = {
            "class": "AssetProperties",
            "typeName": "Asset",
            "qualifiedName": qualified_name,
            "displayName": result.project_display_name,
            "description": f"GitHub repository: {result.github_url}",
            # The repo's own location, in the attribute Egeria defines for it.
            # `url` is inherited from Referenceable (Asset -> Referenceable,
            # same base SourceControlLibrary's chain went through). Previously
            # the git URL existed only inside additionalProperties and
            # interpolated into the description text, so nothing generic could
            # find it: anything walking the catalog for "where does this live"
            # looks at `url`, not at a private extension key.
            #
            # additionalProperties["github_url"] is kept rather than moved —
            # existing consumers read it, and the duplication is cheap.
            "url": result.github_url,
            "additionalProperties": {
                "github_url": result.github_url,
                "project_slug": result.resource_slug,
                "primary_language": primary_language,
                "license": license_name,
                "topics": topics_csv,
                # Descriptive, not a type constraint — `deployedImplementationType`
                # and `libraryType` belong to SoftwareCapability/ResourceManager,
                # not to a generic Asset, so they moved here rather than staying
                # top-level properties a strict server-side schema might reject.
                "deployed_implementation_type": "GitHub Repository",
            },
        }
        # The repository's Asset is REFERENCED by every investigation that names it, never produced
        # by one (§3.6), so it is created with the referenced-element zones whatever `zone_names`
        # this publish carries. Until 2026-10-13 this sent `self.zone_names`, which for a member of
        # a private investigation is `[private, owner]`. It never reached Egeria only because
        # `AssetProperties` has no `zoneMembership` field (unknown keys are ignored); the zones that
        # take effect are the referenced-element stamp below, `_stamp_governance(produced=False)`.
        zones = self._referenced_zones()
        if zones:
            props["zoneMembership"] = zones
        body = {"class": "NewElementRequestBody", "properties": props}
        with time_egeria_call(self._registry, "create_asset", "write", params={"typeName": "Asset"}):
            guid = self._asset_maker.create_asset(body=body)
        log.info("Created GitHub repository asset GUID %s for %s", guid, result.resource_slug)
        # Own it: GitHub is the service the repository asset belongs to,
        # exactly once, at creation — a cache hit or a found-existing match
        # above means this link already exists from a prior run.
        try:
            scl_guid = self._find_or_create_github_scl()
            with time_egeria_call(self._registry, "add_capability_asset_use", "write"):
                self._asset_maker.add_capability_asset_use(
                    scl_guid, guid,
                    body={"class": "NewRelationshipRequestBody",
                          "properties": {"class": "CapabilityAssetUseProperties", "useType": "OWNS"}},
                )
        except Exception as exc:
            # Best-effort: the repository asset is real and usable either way
            # (the report attaches to IT, not to the SCL) -- a missing
            # CapabilityAssetUse edge is a fact worth logging, not a reason to
            # fail the publish over.
            log.warning("Could not link %s to the GitHub SourceControlLibrary: %s", guid, exc)
        self._cache_asset_guid(result.resource_slug, guid)
        return guid

    # ── ownership and zones ───────────────────────────────────────────────────

    def _resolve_private_owner(self, resource_slug: str) -> str:
        """The userId this resource's artifacts belong to, or `""`.

        Unscoped by design — see `ProjectRegistry.private_owner_for_entity`. No registry means no investigation
        can exist: `""`. A registry that cannot ANSWER fails CLOSED (2026-10-13 review): it raises
        `private_publish.PrivateOwnerUnknown`, recorded once per state, and nothing is written. It used to answer
        `""` ("publish normally"), which published a private member's findings publicly, in silence.
        """
        from resource_explorer.private_publish import PrivateOwnerUnknown, record_once, resolve_private_owner

        try:
            return resolve_private_owner(self._registry, "repo", resource_slug)
        except PrivateOwnerUnknown as exc:
            log.warning("%s", exc)
            record_once(self._registry, entity_type="repo", slug=resource_slug, status="error", summary=str(exc),
                        marker="private-owner-unknown · repo", per="state")
            raise

    def _require_owner_identity(self, resource_slug: str) -> None:
        """Raise `private_publish.PrivateAwaitsOwner` unless this publish runs as the private owner, signed in."""
        from resource_explorer.private_publish import PrivateAwaitsOwner, acting_user, is_owner

        try:
            identity = self.resolve_identity()
        except PermissionError:            # nobody signed in, or an expired sign-in: nobody here is the owner
            identity = None
        if not is_owner(identity, self._private_owner):
            raise PrivateAwaitsOwner("repo", resource_slug, self._private_owner, acting_user(identity))

    def _require_enforced_private_zone(self, resource_slug: str) -> None:
        """Refuse the publish unless the private zone is known to deny.

        This is the loud half of the design. If the `resource-explorer-private`
        security access control does not exist, the zone name is one the
        security connector does not recognise — and an unrecognised zone is
        *ignored*, not treated as restrictive, so `validateZoneAccess` falls
        through to `return true` and the artifact is world-readable. Meanwhile
        every RE screen still says "private", because that filter is local and
        works whether or not Egeria is co-operating.

        Those two halves disagreeing silently, with the reassuring one visible,
        is the worst outcome available here. So a publish that cannot be made
        private does not happen at all: refusing is recoverable and obvious,
        publishing is neither.
        """
        from resource_explorer.egeria_identity import (
            ensure_private_zone_exists, private_zone, private_zone_is_enforced,
            private_zone_status,
        )

        if private_zone_is_enforced():
            return
        # "Nobody has asked yet" is not "not enforced" — it is a question, and
        # this is the moment to answer it. The zone state is per-process, and
        # the bootstrap that populates it runs in the WORKER role; a deployment
        # that serves the web from a separate process would otherwise refuse
        # every private publish while the zone was perfectly healthy.
        # Idempotent and never raises.
        if private_zone_status().get("status") == "unknown":
            ensure_private_zone_exists()
            if private_zone_is_enforced():
                return
        status = private_zone_status()
        raise EgeriaConnectionError(
            f"'{resource_slug}' belongs to a private investigation, but the "
            f"'{private_zone()}' governance zone is not confirmed to be enforced "
            f"({status.get('status')}: {status.get('detail') or 'no detail'}). "
            "Publishing now would put private artifacts in a zone Egeria ignores, "
            "which is world-readable while Resource Explorer still shows them as "
            "private. Nothing was written. "
            + (status.get("remedy") or "See Admin → Egeria for the zone's status.")
        )

    def _require_zones_grant_someone(self, resource_slug: str) -> None:
        """Refuse the publish when a zone RE stamps on it would grant nobody once Egeria e9fd817f4c ships
        (`egeria_identity.zone_lockout_refusal`: no control, or neither READ nor DEFAULT, or unreadable).

        The twin of `_require_enforced_private_zone` for the draft and publish zones, and for the same reason
        raised before anything is written: an element born in a zone that grants nobody cannot be read or
        unzoned afterwards, by RE's daemon or anyone else."""
        from resource_explorer.egeria_identity import zone_lockout_refusal

        refusal = zone_lockout_refusal([*(self.zone_names or []), *self._referenced_zones()])
        if refusal:
            raise EgeriaConnectionError(
                f"'{resource_slug}' was not published: {refusal}. Nothing was written. "
                "See Admin → Egeria for every zone's control.")

    @staticmethod
    def _referenced_zones() -> list[str]:
        """Zones for an element this publish REFERENCES (the repo's Asset, the GitHub library).

        §3.6: never `self.zone_names`, which is `[private, owner]` for a private investigation's
        member. One helper (`egeria_identity.referenced_element_zones`) for the creates and the
        referenced-element stamp alike."""
        from resource_explorer.egeria_identity import referenced_element_zones

        return referenced_element_zones()

    def _confirm_report_private(self, result: SurveyResult, report_guid: str) -> None:
        """Raise `PrivateReportNotConfirmed` unless Egeria shows the report in its private zones.

        The deciding guard is a READ of the report's `ZoneMembership` as the publishing identity,
        after the born-private create and the stamp: a returned GUID or an accepted classify call
        proves the call worked, not what Egeria holds. The stamp's own answer is recorded but not
        decisive either way — a stamp that said False on a report the read shows private is fine
        (it was born private), and a stamp that said True is not trusted without the read.

        Every refusal is recorded (an activity entry with status `error`, and the publish raises,
        so `repo_publish` writes its failure proof) before anything is written beneath the report.
        """
        from resource_explorer.egeria_identity import ZoneReadError, read_zones

        expected = list(self.zone_names)
        stamped = (self.last_governance or {}).get(report_guid) or {}
        reason = ""
        if not report_guid:
            reason = "Egeria returned no report GUID"
        elif not stamped:
            reason = "no governance stamp was recorded for the report"
        else:
            try:
                held = read_zones(report_guid, self.resolve_identity())
            except ZoneReadError as exc:
                held = None
                reason = f"its zones could not be read back ({exc})"
            if held is not None and sorted(held) != sorted(expected):
                reason = (f"Egeria holds zones {held or '(none)'} on it, not {expected}"
                          + ("" if stamped.get("zone_membership") else
                             "; the ZoneMembership stamp did not land either"))
            if held is not None and not reason:
                stamped = {**stamped, "zone_membership": True, "zones_read_back": list(held)}
                self.last_governance = {**(self.last_governance or {}), report_guid: stamped}
                return
        raise PrivateReportNotConfirmed(report_guid, self._private_owner, reason)

    def _require_reused_report_private(self, report_guid: str) -> None:
        """A 409 reuse for a PRIVATE member: the report Egeria already holds under this run's name must already be in
        exactly `[private, owner]`. A report that is public (published before the resource joined a private
        investigation) is never made private after the fact and never has private findings attached beneath it:
        anyone may already have read it, and re-zoning is not un-reading (2026-10-13 review)."""
        from resource_explorer.egeria_identity import ZoneReadError, read_zones

        try:
            held = read_zones(report_guid, self.resolve_identity())
        except ZoneReadError as exc:
            raise PrivateReportNotConfirmed(
                report_guid, self._private_owner,
                f"a report with this run's name already exists in Egeria and its zones could not be read ({exc})")
        if sorted(held) != sorted(self.zone_names):
            raise PrivateReportNotConfirmed(
                report_guid, self._private_owner,
                f"a report with this run's name already exists in Egeria in zones {held or '(none)'}, not "
                f"{list(self.zone_names)}; private findings are never attached beneath a report that was public")

    def _record_private_refusal(self, result: SurveyResult, report_guid: str, reason: str) -> None:
        """The activity entry a refused private publish leaves, in words (status `error`)."""
        if not self._registry:
            return
        try:
            from resource_explorer.activity_logger import log_catalog

            log_catalog(
                self._registry, entity_type="repo", entity_slug=result.resource_slug,
                entity_name=result.project_display_name, entity_location=result.github_url,
                status="error",
                summary=(f"NOT published: private investigation's report "
                         f"{(report_guid or 'no-guid')[:12]}… could not be confirmed private "
                         f"({reason}); no findings were written"),
                detail=reason,
            )
        except Exception as exc:  # noqa: BLE001 - the raise that follows is the signal
            log.warning("Could not record the refused private publish of %s: %s",
                        result.resource_slug, exc)

    def _stamp_governance(self, *element_guids: str, produced: bool = True) -> dict:
        """`Ownership` and `ZoneMembership` on everything this publish created.

        Plan §4: everything trellis publishes gets `Ownership` set to the
        publishing user's `UserIdentity`, and the configured draft zone (none by
        default since 2026-10-08) until a curator accepts it. Ownership is attribution: who may curate follows
        Egeria's zones (`workflows/curate.curation_access`, Brief Z), and
        Egeria's own `isUserAnOwner` reads this classification's `userIds`.

        Best-effort, and reported rather than raised: the survey report and its
        annotations are already real by the time this runs, so a classification
        that did not land is a governance gap to fix, not a reason to tell the
        user their publish failed. The result is folded into the publish
        summary the same way `annotation_types_warning` is, so it is visible in
        the Activity tab rather than only in a log line.

        One exception (2026-10-13): a PRODUCED element of a private investigation whose
        classification client cannot be built raises `PrivateReportNotConfirmed` — private work is
        never published on a best-effort basis. Any other client-build failure is recorded per
        element as not stamped, with its reason, and never returned as a silent `{}`.
        """
        from resource_explorer.egeria_identity import stamp_published

        identity = self.resolve_identity()
        # For a private resource the owner is the INVESTIGATION's creator, not
        # whoever ran the publish. The worker publishes as the service account,
        # so taking the publishing identity here would stamp a queued private
        # survey as owned by `erinoverview`. `Ownership` is attribution: since
        # Brief Z (2026-10-10) who may curate follows Egeria's zones
        # (`workflows/curate.curation_access`), not Ownership.
        from resource_explorer.egeria_identity import on_behalf_of

        # The one on-behalf helper (Brief I): the requester, never the service account; a private
        # investigation's owner keeps its own (unchanged private-zone handling).
        owner = getattr(self, "_private_owner", "") or on_behalf_of(identity).owner
        zones = self.zone_names
        if not produced:
            # A REFERENCED element — the repo's own asset. It is not this
            # investigation's to hide or to own, however private the
            # investigation is, so it gets the ordinary treatment: the
            # deployment's draft zone and the publishing identity.
            owner = on_behalf_of(identity).owner          # the requester; never the service account
            zones = self._referenced_zones()
        results: dict[str, dict] = {}
        client = None
        for guid in element_guids:
            if not guid:
                continue
            if client is None:
                from resource_explorer.egeria_identity import classification_client

                try:
                    client = classification_client(identity)
                except PermissionError:
                    raise   # no caller / expired / platform not allowed: never a silent unowned publish
                except Exception as exc:
                    reason = (f"the classification client could not be built "
                              f"({type(exc).__name__}: {str(exc)[:160]})")
                    log.warning("Could not build a classification client — Ownership/"
                                "ZoneMembership not set for this publish: %s", reason)
                    if produced and getattr(self, "_private_owner", ""):
                        # A private investigation's report with no way to classify it: refused
                        # here, before a single finding is written beneath it (fail closed).
                        raise PrivateReportNotConfirmed(guid, self._private_owner, reason) from exc
                    # Never `{}`: an empty outcome makes the publish summary say nothing at all,
                    # which reads as "governed". Each element is recorded as NOT stamped, with why.
                    results = {g: {"ownership": False, "zone_membership": False, "owner": owner,
                                   "zones": list(zones), "reason": reason}
                               for g in element_guids if g}
                    self.last_governance = {**(self.last_governance or {}), **results}
                    return results
            results[guid] = stamp_published(
                guid, owner, identity=identity, client=client, zones=zones,
            )
        self.last_governance = {**(self.last_governance or {}), **results}
        return results

    def _publish_local_doc_sources(self, slug: str, asset_guid: str) -> None:
        if not (self._registry and asset_guid):
            return
        self.doc_sources_attempted = True
        try:
            from resource_explorer.web.routes.doc_sources import publish_local_doc_sources
            publish_local_doc_sources("repo", slug, asset_guid, registry=self._registry)
        except Exception as exc:
            log.warning("Could not publish documentation sources for %s: %s", slug, exc)

    def _cache_asset_guid(self, slug: str, guid: str) -> None:
        if self._registry:
            try:
                self._registry.set_egeria_asset_guid(slug, guid)
            except Exception as exc:
                log.warning("Could not persist Egeria asset GUID to registry: %s", exc)

    def _publish_homepage_reference(self, result: SurveyResult, asset_guid: str) -> str:
        """Catalog the project's external website as an ExternalReference linked
        to the repository's Asset.

        The URL comes from HomepageSurveyor (projects.homepage_url), which tries
        GitHub's declared homepage, then the packaging manifests, then the README,
        then the repo URL itself. The repo-URL fallback is deliberately NOT
        published here: an ExternalReference pointing back at the same repo the
        library element already describes adds a catalog entry and no
        information. Scouting still shows it as a link; only Egeria skips it.

        Best-effort throughout. A failure here must not fail the publish — the
        survey and its annotations are the point, this is an extra.

        Returns the ExternalReference GUID, or "" if nothing was published.
        """
        if not self._registry:
            return ""
        try:
            project = self._registry.get(result.resource_slug)
        except Exception:
            return ""
        homepage = (getattr(project, "homepage_url", "") or "").strip()
        if not homepage:
            return ""

        # Skip the repo-URL fallback (see docstring). Compared host-wise rather
        # than by string equality so ".git"/trailing-slash variants still match.
        from urllib.parse import urlparse

        def _same(a: str, b: str) -> bool:
            pa, pb = urlparse(a), urlparse(b)
            return (pa.hostname or "").lower() == (pb.hostname or "").lower() and \
                   pa.path.rstrip("/").removesuffix(".git") == pb.path.rstrip("/").removesuffix(".git")

        if _same(homepage, result.github_url):
            log.debug("Homepage for %s is the repo itself — not cataloged separately",
                      result.resource_slug)
            return ""

        qualified_name = f"ExternalReference::{homepage}"
        try:
            existing = self._find_element_guid(qualified_name)
            if existing:
                ref_guid = existing
            else:
                body = {
                    "class": "NewElementRequestBody",
                    "properties": {
                        "class": "ExternalReferenceProperties",
                        "typeName": "ExternalReference",
                        "qualifiedName": qualified_name,
                        "displayName": f"{result.project_display_name} — project website",
                        "description": (
                            f"External website for {result.project_display_name}, derived by "
                            f"Resource Explorer's Homepage survey step."
                        ),
                        # `url` is inherited from Referenceable, same as on the
                        # library element above.
                        "url": homepage,
                        "referenceTitle": f"{result.project_display_name} website",
                    },
                }
                ref_guid = self._external_references.create_external_reference(body=body)
                log.info("Created ExternalReference %s for %s (%s)",
                         ref_guid, result.resource_slug, homepage)

            # ExternalReferenceLink is multi-link: every link call made another copy on each
            # re-publish. Link only when none exists; the helper logs its own failures at warning
            # and never links when it cannot check. The reference itself is kept either way.
            from resource_explorer.egeria_extref_link import link_external_reference_once

            link_external_reference_once(self._metadata_expert, self._external_references,
                                         asset_guid, ref_guid)
            return ref_guid
        except Exception as exc:
            log.warning("Could not publish homepage ExternalReference for %s: %s",
                        result.resource_slug, exc)
            return ""

    # ── survey report ─────────────────────────────────────────────────────────

    def _create_survey_report(self, result: SurveyResult, asset_guid: str) -> str:
        surveyed_at_iso = result.surveyed_at.isoformat()
        qualified_name = (
            f"SurveyReport::GitHubRepo::{result.resource_slug}::{surveyed_at_iso}"
        )
        body = {
            "class": "NewElementRequestBody",
            "parentGUID": asset_guid,
            "parentRelationshipTypeName": "ReportSubject",
            "properties": {
                "class": "AssetProperties",
                "typeName": "SurveyReport",
                "qualifiedName": qualified_name,
                "displayName": f"Survey: {result.project_display_name}",
                "description": (
                    f"Automated survey of {result.github_url} "
                    f"run at {surveyed_at_iso} by resource-explorer."
                ),
                "additionalProperties": {
                    "annotation_count": str(len(result.annotations)),
                    "error_count": str(len(result.errors)),
                },
            },
        }
        if getattr(self, "_private_owner", ""):
            # PRODUCED by a private investigation: the report is BORN in `[private, owner]`, so
            # there is no moment at which it, or a finding anchored to it, is world-readable
            # (2026-10-13 review). `initialClassifications` is the create-time shape Egeria
            # honours; a `zoneMembership` key in the properties would be ignored. The post-create
            # stamp and the read-back in `_confirm_report_private` stay as the second guard.
            from resource_explorer.egeria_identity import zone_membership_initial_classification

            body["initialClassifications"] = zone_membership_initial_classification(self.zone_names)
        try:
            with time_egeria_call(self._registry, "create_asset", "write", params={"typeName": "SurveyReport"}):
                report_guid = self._asset_maker.create_asset(body=body)
        except Exception as exc:
            # A 409 on the report's own name means this survey run's report is already in Egeria: reuse
            # it (looked up by its exact name), never create a second one. Any other failure propagates.
            text = " ".join(str(exc).split())
            if not ("409" in text and ("is not available for use" in text or "OMAG-COMMON-409" in text)):
                raise
            report_guid = self._find_element_guid(qualified_name)
            if not report_guid:
                raise RuntimeError(
                    "Egeria refused the report as a duplicate but no report with that name "
                    f"could be read ({qualified_name})") from exc
            self.report_reused = True
            log.info("SurveyReport %s already in Egeria (same survey run): reusing it", report_guid)
            if getattr(self, "_private_owner", ""):
                self._require_reused_report_private(report_guid)

        # Persist so the pull path (EgeriaReader) and CLI can reference it
        if self._registry:
            try:
                self._registry.record_egeria_survey(
                    result.resource_slug,
                    surveyed_at_iso,
                    report_guid,
                    annotation_count=len(result.annotations),
                )
            except Exception as exc:
                log.warning("Could not persist Egeria survey record to registry: %s", exc)

        return report_guid

    # ── annotations ───────────────────────────────────────────────────────────

    def _create_annotations(self, result: SurveyResult, report_guid: str) -> dict:
        """Write this run's annotations to Egeria, through the outbox, then
        (annotation-linking-plan Phase 2) link every annotation whose
        `evidence_of` is set to its aggregate — both created in this same
        call, so both GUIDs are in hand with no cross-run GUID resolution
        needed (that is Phase 3's unsolved problem, not attempted here).

        The qualifiedName prefix built here — and specifically
        `result.surveyed_at.isoformat()`, this run's own timestamp, NOT
        recomputed at publish time — is this run's identity; do not remove it
        as a "fix". It is also what makes a retry safe: the outbox replays the
        stored payload, so a retried annotation carries a byte-identical
        qualifiedName and converges on the element already written instead of
        minting a second one.

        **What changed, and what deliberately did not** (design §6 step 4).
        Previously this called `publish_annotations` directly, which logs a
        failed annotation and moves on — so a SurveyReport was reported
        published even when every annotation beneath it failed to write, and
        nothing anywhere remembered that they had not. Now each annotation is
        recorded as a durable outbox row *first*, then applied immediately.

        The happy path is therefore unchanged: rows are enqueued and drained
        within this call, so a successful publish still writes everything
        before returning, and callers see exactly what they saw before. What
        changes is the unhappy path — a failure is now a row the scheduler
        will retry with backoff, not a warning in a log nobody reads.

        **Deferred (background) publish** — `defer_drain=True` passed to
        `publish()`, or `RunsConfig.publish_inline=False` when the caller
        never chose (default stays inline, unchanged): measured 2026-09-13,
        the inline drain is where an auto-published analysis run spends
        nearly all its wall time — 546.61s of a 558.89s run, one blocking
        Egeria REST call per annotation, zero concurrency. When deferred,
        this method enqueues the annotation rows and returns WITHOUT draining
        them — `publish_deferred` is set so the caller can report "queued"
        rather than "published".

        Evidence-link rows ARE enqueued in this case too (they were not,
        originally — see git history for the "cannot be enqueued, Phase 3's
        unsolved problem" version of this paragraph; that limitation is
        replaced, not merely worked around): each link is enqueued keyed by
        its two referent annotations' OWN outbox row ids rather than their
        Egeria GUIDs, since no GUID exists yet for an annotation that has not
        been drained. `apply_element` resolves those row ids to real GUIDs
        AT APPLY TIME (`registry.get_outbox_guids`) — if a referent has not
        landed yet the link row is left queued, not failed (see
        `egeria_outbox.OutboxNotReadyError`), and is retried on a later
        drain once it has. Rows drain in id order, so in practice a run's own
        annotations apply before its own links are even attempted. Reported
        as `links_queued`, not `links_deferred` — every link that had a
        pair of real annotation indices to enqueue IS enqueued; only a link
        whose referent annotation was itself dropped by the outbox cap
        (`_MAX_ANNOTATIONS_PER_RUN`) is `links_skipped`, same meaning that
        counter already had in the inline path.

        This method still does not raise on a failed annotation, or a failed
        link. That is deliberate and unchanged: `publish()`'s contract is that
        a publish problem must not turn an otherwise-successful survey into a
        reported error. The difference is that the work is no longer lost
        when it is swallowed, AND (new here) this now returns a dict —
        `links_attempted`/`links_created`/`links_failed`/`links_skipped`/
        `links_queued` — so `publish()` can fold a partial link failure (or a
        deferred count) into what a reader actually sees (the Activity tab
        entry), rather than that being visible only to an operator who thinks
        to check Admin → Publish Queue. `links_skipped` counts a link whose
        evidence or summary annotation itself failed to publish, or was
        dropped by the cap — not a link-creation failure of its own (that
        annotation's own failure is already visible via its own outbox row),
        but still a link that did not happen, so it is still counted, not
        folded silently into `links_failed`.

        Falls back to the direct path when there is no registry — an
        EgeriaPublisher built without one has nowhere to record an outbox row,
        and silently skipping the annotations would be worse than writing them
        the old way.
        """
        qualified_name_prefix = f"Annotation::{result.resource_slug}::{result.surveyed_at.isoformat()}"
        link_pairs = [
            (i, ann.evidence_of)
            for i, ann in enumerate(result.annotations)
            if ann.evidence_of is not None
        ]

        if self._registry is None:
            from resource_explorer.surveyors.annotation_props import publish_annotations

            guids = publish_annotations(
                self._discovery, self._find_element_guid,
                result.annotations, report_guid, qualified_name_prefix,
            )
            return self._link_evidence_direct(guids, link_pairs)

        from resource_explorer.egeria_outbox import (
            OutboxClients, drain_outbox, enqueue_annotations,
        )

        row_ids = enqueue_annotations(
            self._registry, "repo", result.resource_slug, result.annotations,
            report_guid, qualified_name_prefix, run_id=qualified_name_prefix,
        )
        # A run id only when something was queued under it: with nothing to publish there is no proof row, and the
        # word is "nothing to publish", never "queued" or "published".
        self.publish_run_id = qualified_name_prefix if row_ids else ""

        from resource_explorer.config import get_config

        defer = (not get_config().runs.publish_inline if self._defer_drain_override is None
                else self._defer_drain_override)
        if getattr(self, "_private_owner", ""):
            # A private member's findings are drained NOW, as the signed-in owner. Deferred, they would wait for the
            # background drain, which runs as the service account Egeria refuses beneath a private report: every
            # row would go dead (2026-10-13 review). `egeria_outbox` parks any that still reach it.
            defer = False
        if defer:
            # Enqueue-only: leave draining to the scheduler's existing
            # periodic `_drain_egeria_outbox` cycle (<=15 min). See this
            # method's docstring for how evidence links are enqueued too, by
            # row id rather than GUID, and resolved once their referent
            # annotations are applied.
            self.publish_deferred = bool(row_ids)
            counts = dict(self._LINK_COUNTS_ZERO)
            counts["annotations_queued"] = len(row_ids)
            counts["annotations_produced"] = len(result.annotations)
            links_queued = 0
            if link_pairs:
                from resource_explorer.egeria_outbox import enqueue_annotation_links

                link_refs = []
                for evidence_idx, summary_idx in link_pairs:
                    if evidence_idx >= len(row_ids) or summary_idx >= len(row_ids):
                        # The referent annotation itself was dropped by the
                        # outbox cap — same meaning `links_skipped` already
                        # carries in the inline path below.
                        counts["links_skipped"] += 1
                        continue
                    link_refs.append({
                        "summary_row_id": row_ids[summary_idx],
                        "evidence_row_id": row_ids[evidence_idx],
                    })
                if link_refs:
                    enqueue_annotation_links(
                        self._registry, "repo", result.resource_slug, link_refs,
                        run_id=f"{qualified_name_prefix}::links",
                    )
                    links_queued = len(link_refs)
            counts["links_queued"] = links_queued
            if row_ids:
                # Last, after the links: the drain reads the run back once every write above has landed.
                from resource_explorer.egeria_outbox import enqueue_publish_read_back

                enqueue_publish_read_back(self._registry, "repo", result.resource_slug,
                                          qualified_name_prefix, report_guid)
            return counts

        drain_outbox(
            self._registry, OutboxClients(discovery=self._discovery), self._find_element_guid,
            limit=max(len(result.annotations), 1), run_id=qualified_name_prefix,
        )
        counts = self._link_evidence_outbox(
            result, row_ids, link_pairs, qualified_name_prefix,
        )
        self._read_back_inline(result, row_ids, qualified_name_prefix, report_guid)
        # What was actually QUEUED, which is not len(result.annotations) once
        # the outbox cap bites. Reported so the Activity summary can state a
        # published count rather than a produced one — counting what a
        # surveyor made and calling it what was published is the same
        # correct-number-wrong-label defect this codebase keeps paying for.
        counts["annotations_queued"] = len(row_ids)
        counts["annotations_produced"] = len(result.annotations)
        return counts

    def _read_back_inline(self, result: SurveyResult, row_ids: list[int], qualified_name_prefix: str,
                          report_guid: str) -> None:
        """The inline publish (`RUNS_PUBLISH_INLINE=true`, or the per-run "wait"): queue the run's read-back and
        drain it now, as the same caller. Published only if that row is done; otherwise the run is reported
        queued (`publish_deferred`) and the background drain finishes it, so the word follows the proof whichever
        branch ran. Never raises: the writes are durable and the read-back stays queued."""
        if not row_ids:
            return
        from resource_explorer.egeria_outbox import (
            READ_BACK_SUFFIX, OutboxClients, drain_outbox, enqueue_publish_read_back,
        )

        row_id = enqueue_publish_read_back(self._registry, "repo", result.resource_slug,
                                           qualified_name_prefix, report_guid)
        try:
            drain_outbox(self._registry, OutboxClients(discovery=self._discovery, registry=self._registry),
                         self._find_element_guid, limit=1, run_id=f"{qualified_name_prefix}{READ_BACK_SUFFIX}")
        except Exception as exc:  # noqa: BLE001 - the read-back row stays queued; the word says queued
            log.warning("inline read-back of %s did not run: %s", qualified_name_prefix, exc)
        done = self._registry.get_outbox_guids([row_id])
        self.publish_read_back = bool(done.get(row_id))
        if not self.publish_read_back:
            self.publish_deferred = True

    # ── evidence linking (annotation-linking-plan Phase 2, Tier 1) ─────────────

    _LINK_COUNTS_ZERO = {
        "links_attempted": 0, "links_created": 0, "links_failed": 0, "links_skipped": 0,
        "links_queued": 0,
    }

    def _link_evidence_direct(self, guids: list, link_pairs: list[tuple[int, int]]) -> dict:
        """No-registry path: create every same-run evidence link directly via
        MetadataExpert, no outbox row, same as `publish_annotations` above
        writes annotations directly for this same code path.
        """
        counts = dict(self._LINK_COUNTS_ZERO)
        if not link_pairs:
            return counts

        links = []
        for evidence_idx, summary_idx in link_pairs:
            evidence_guid = guids[evidence_idx] if evidence_idx < len(guids) else None
            summary_guid = guids[summary_idx] if summary_idx < len(guids) else None
            if not evidence_guid or not summary_guid:
                counts["links_skipped"] += 1
                continue
            links.append({"summary_guid": summary_guid, "evidence_guid": evidence_guid})
        counts["links_attempted"] = len(links)
        if not links:
            return counts

        from resource_explorer.surveyors.annotation_props import publish_annotation_links

        link_guids = publish_annotation_links(self._metadata_expert, links)
        counts["links_created"] = sum(1 for g in link_guids if g)
        counts["links_failed"] = sum(1 for g in link_guids if not g)
        return counts

    def _link_evidence_outbox(
        self, result: SurveyResult, row_ids: list[int],
        link_pairs: list[tuple[int, int]], qualified_name_prefix: str,
    ) -> dict:
        """Registry path: resolve each same-run annotation's real GUID from
        the outbox rows `_create_annotations` just enqueued and drained
        (`registry.get_outbox_guids`), then enqueue+drain the
        `AnnotationExtension` links through the SAME outbox mechanism — one
        more `element_kind`, no new failure-visibility surface to build,
        because dead-letter/retry/activity-log observability already exists
        for every other outbox kind and this reuses it verbatim (see
        `_create_annotation_link`'s docstring for why create-blind is safe
        here specifically).

        A distinct run_id (`::links` suffix) rather than reusing
        `qualified_name_prefix` — so this second `drain_outbox` call claims
        only the link rows it just enqueued, not any annotation row still
        retrying under the same run_id, which would double-count into
        `links_created` and misattribute an annotation's own retry outcome to
        the linking pass.
        """
        counts = dict(self._LINK_COUNTS_ZERO)
        if not link_pairs:
            return counts

        guid_by_row = self._registry.get_outbox_guids(row_ids)
        links = []
        for evidence_idx, summary_idx in link_pairs:
            evidence_guid = guid_by_row.get(row_ids[evidence_idx], "")
            summary_guid = guid_by_row.get(row_ids[summary_idx], "")
            if not evidence_guid or not summary_guid:
                # The annotation itself did not publish — already visible via
                # its own outbox row (failed/dead); not a link failure of its
                # own, but still a link that did not happen, so still counted.
                counts["links_skipped"] += 1
                continue
            links.append({"summary_guid": summary_guid, "evidence_guid": evidence_guid})
        counts["links_attempted"] = len(links)
        if not links:
            return counts

        from resource_explorer.egeria_outbox import (
            OutboxClients, drain_outbox, enqueue_annotation_links,
        )

        link_run_id = f"{qualified_name_prefix}::links"
        enqueue_annotation_links(
            self._registry, "repo", result.resource_slug, links, run_id=link_run_id,
        )
        summary = drain_outbox(
            self._registry,
            OutboxClients(discovery=self._discovery, metadata_expert=self._metadata_expert),
            self._find_element_guid, limit=max(len(links), 1), run_id=link_run_id,
        )
        counts["links_created"] = summary.get("done", 0)
        counts["links_failed"] = summary.get("failed", 0) + summary.get("dead", 0)
        return counts

    # ── sub-resource cataloging (repo scope-narrowing funnel doc, D2/D3) ────

    _FOLDER_TECH_TYPE = "File System Directory"

    def publish_sub_resources(
        self, resource_slug: str, github_url: str, asset_guid: str, locators: list[str],
    ) -> dict[str, str]:
        """Publish specific, already-locally-catalogued sub-resources (repo
        only today — resource_type='repo') to Egeria as FileFolder/DataFile
        assets, parented under the repo's Asset (or an already-catalogued
        ancestor folder).

        **Known gap, not fixed here (2026-09-14 SourceControlLibrary
        correction):** the root-level-folder case below still requests
        `parentRelationshipTypeName: "CapabilityAssetUse"` against
        `asset_guid`, which was type-valid when the repo's own element was a
        SoftwareCapability (CapabilityAssetUse's end1) and is NOT valid now
        that it is a generic Asset — model 0220's Files-and-Folders
        relationships (FolderHierarchy, NestedFile) are strictly
        FileFolder-to-FileFolder/DataFile, and neither is a documented
        Asset-to-FileFolder edge. Left as a TODO rather than guessed at: the
        right relationship needs checking against a live Egeria server, not
        invented here. The failure mode is soft — `_create_sub_resource`'s
        caller catches and logs, so a wrong relationship type here skips that
        one root folder rather than failing the publish — but the read is
        real and worth fixing before this path sees real use.

        This replaces the old auto-publish behavior (finding "worthy" ->
        immediately create in Egeria, with every publish() call). Egeria
        publish is now always an explicit, separate action from local
        cataloging (registry.catalog_sub_resource) — see
        docs/repo-scope-narrowing-funnel.md D2/D3. Only locators that are
        already rows in the local `sub_resources` table are published;
        the caller (the catalog route) is responsible for ensuring every
        selected file's ancestor folders are locally catalogued too, since
        NestedFile strictly requires a FileFolder parent.

        Returns {locator: guid} for everything successfully created or
        already found. Idempotent — re-publishing an already-published
        locator finds it by qualifiedName rather than duplicating it.
        """
        if not self._registry:
            return {}
        self._connect()

        rows = {
            r["locator"]: r
            for r in self._registry.list_sub_resources("repo", resource_slug)
            if r["locator"] in locators
        }
        entries = []
        for locator, row in rows.items():
            detail = {}
            if row.get("detail_json"):
                try:
                    detail = json.loads(row["detail_json"])
                except (TypeError, ValueError):
                    detail = {}
            entries.append({
                "path": locator,
                "kind": row["kind"],
                "owners": detail.get("owners", []),
                "last_updated_at": detail.get("last_updated_at", ""),
                "first_added_at": detail.get("first_added_at", ""),
            })
        if not entries:
            return {}

        # Folders before files — a file's parentGUID must already exist.
        entries.sort(key=lambda e: 0 if e["kind"] == "folder" else 1)

        guids_by_path: dict[str, str] = {}
        template_guid_cache: dict[str, str] = {}
        results: dict[str, str] = {}

        self.rename_counts = {"updated": 0, "unchanged": 0, "failed": 0}
        #: path -> why its look-up could not be made, so nothing was created for it (this call only).
        self.sub_resources_not_checked: dict[str, str] = {}
        project = self._registry.get(resource_slug)
        repository = getattr(project, "display_name", "")
        if not isinstance(repository, str) or not repository:
            repository = resource_slug
        for entry in entries:
            entry["display_name"] = sub_resource_display_name(entry["path"], entry["kind"], repository)
            path = entry["path"]
            qualified_name = f"GitHubRepository::{github_url}::{path}"
            try:
                existing_guid = self._find_element_guid(qualified_name)
            except Exception as exc:
                from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

                if not lookup_exception_is_absent(exc):
                    # Could not check is not "not there": nothing is created for this path (it used to be,
                    # "will attempt create"), and a file under a folder skipped here has no parent, so it is
                    # skipped too. Recorded so a caller can tell "not catalogued" from "RE could not check".
                    self.sub_resources_not_checked[path] = lookup_could_not_be_made(qualified_name, exc)
                    log.warning("Sub-resource %s not cataloged: %s", path or "(root)",
                                self.sub_resources_not_checked[path])
                    continue
                existing_guid = ""
            if existing_guid:
                guids_by_path[path] = existing_guid
                results[path] = existing_guid
                self._registry.set_sub_resource_egeria_guid("repo", resource_slug, path, existing_guid)
                self._rename_forward(existing_guid, entry["display_name"])
                continue

            parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
            if entry["kind"] == "file":
                # NestedFile strictly requires a FileFolder parent — the
                # caller (catalog route) guarantees an ancestor chain exists
                # in the local table for every selected file.
                parent_guid = guids_by_path.get(parent_path)
                relationship_type = "NestedFile"
            elif path == "" or parent_path == "":
                # A root-level (or the synthetic root itself) folder attaches
                # directly to the repo's Asset. "CapabilityAssetUse" is
                # STALE here since the 2026-09-14 SourceControlLibrary fix
                # (see this method's docstring) -- kept rather than guessed
                # at a replacement; expect this specific create to fail soft
                # (caught and logged by the caller) until it is.
                parent_guid = asset_guid
                relationship_type = "CapabilityAssetUse"
            else:
                parent_guid = guids_by_path.get(parent_path)
                relationship_type = "FolderHierarchy"

            if not parent_guid:
                if parent_path in self.sub_resources_not_checked:
                    # Its folder could not be checked, so neither can it be placed: not checked, not created
                    # (never "Egeria returned no GUID", which would claim a create was tried).
                    self.sub_resources_not_checked[path] = PARENT_NOT_CHECKED
                log.warning(
                    "Skipping sub-resource %s — no parent GUID resolved for %r",
                    path or "(root)", parent_path,
                )
                continue

            try:
                guid = self._create_sub_resource(
                    entry, qualified_name, parent_guid, relationship_type, template_guid_cache
                )
            except Exception as exc:
                log.warning("Could not catalog sub-resource %s: %s", path or "(root)", exc)
                continue
            if guid:
                guids_by_path[path] = guid
                results[path] = guid
                self._registry.set_sub_resource_egeria_guid("repo", resource_slug, path, guid)

        return results

    def _create_sub_resource(
        self,
        entry: dict,
        qualified_name: str,
        parent_guid: str,
        relationship_type: str,
        template_guid_cache: dict[str, str],
    ) -> str:
        path = entry["path"]
        # The root folder has no basename: it is "/" (its displayName carries the repository). The
        # old fallback was the GitHub URL, so the `or "/"` below could never fire.
        name = path.rsplit("/", 1)[-1] if path else "/"

        if entry["kind"] == "folder":
            tech_type = self._FOLDER_TECH_TYPE
            placeholder_values = {
                "directoryPathName": qualified_name,
                "directoryName": name or "/",
                "description": f"Sub-resource folder cataloged from survey: {path or '(root)'}",
            }
        else:
            from resource_explorer.surveyors.sub_resource_templates import resolve_technology_type

            tech_type = resolve_technology_type(path)
            ext = path.rsplit(".", 1)[-1].lower() if "." in name else ""
            placeholder_values = {
                "fileName": name,
                "fileType": tech_type,
                "filePathName": qualified_name,
                "fileExtension": ext,
                "description": f"Sub-resource file cataloged from survey: {path}",
                "versionIdentifier": "1.0",
            }

        template_guid = template_guid_cache.get(tech_type)
        if not template_guid:
            template_guid = self._resolve_template_guid(tech_type)
            template_guid_cache[tech_type] = template_guid

        additional_props = {"path": path or "(root)", "kind": entry["kind"]}
        if entry.get("owners"):
            additional_props["owners"] = ",".join(entry["owners"])
        if entry.get("last_updated_at"):
            additional_props["last_updated_at"] = entry["last_updated_at"]
        if entry.get("first_added_at"):
            additional_props["first_added_at"] = entry["first_added_at"]

        body = {
            "class": "TemplateRequestBody",
            "templateGUID": template_guid,
            "isOwnAnchor": False,
            "parentGUID": parent_guid,
            "parentRelationshipTypeName": relationship_type,
            "placeholderPropertyValues": placeholder_values,
            # Without this, the template derives its own qualifiedName from
            # its placeholder values using Egeria's own convention
            # ("FileFolder::~{fileSystemName}~:<directoryPathName>", not our
            # qualified_name string byte-for-byte) — confirmed live,
            # 2026-08-11: that mismatch silently breaks D8's find-by-
            # qualifiedName idempotency guard (every publish re-creates a
            # duplicate). "class": "AssetProperties" is required here or
            # Egeria 400s (InvalidTypeIdException: missing "class" on
            # EntityProperties), also confirmed live.
            "replacementProperties": {
                "class": "AssetProperties", "qualifiedName": qualified_name,
                # The path and the repository; fileName (a placeholder above) stays the basename.
                "displayName": entry["display_name"],
            },
        }
        guid = self._automated_curation.create_elem_from_template(body)
        if guid and additional_props:
            # Best-effort: template creation doesn't accept additionalProperties
            # directly, so attach D9 metadata via a follow-up update if the
            # client supports it; skip quietly on older pyegeria versions.
            try:
                self._asset_maker.update_asset(
                    guid, body={"class": "UpdateElementRequestBody",
                                 "mergeUpdate": True,
                                 "properties": {"class": "AssetProperties",
                                                 "additionalProperties": additional_props}},
                )
            except Exception as exc:
                log.debug("Could not attach additionalProperties to %s: %s", guid, exc)
        return guid

    @staticmethod
    def _current_display_name(element) -> str | None:
        """displayName from a get_asset_by_guid answer, whichever shape it has; None if not found."""
        if not isinstance(element, dict):
            return None
        props = element.get("properties")
        for src in (props if isinstance(props, dict) else {}, element.get("elementProperties") or {}, element):
            if isinstance(src, dict) and isinstance(src.get("displayName"), str):
                return src["displayName"]
        return None

    def _rename_forward(self, guid: str, display_name: str) -> None:
        """An element published before the naming rule is renamed when the SAME locator is published
        again: ONE read of the element (the lookup returns only a GUID), and a merge update of
        displayName only when it differs, so an unchanged republish writes nothing. Never a sweep,
        never a delete. A failure is counted in `rename_counts` and logged; it does not stop the publish."""
        try:
            current = self._asset_maker.get_asset_by_guid(guid, graph_query_depth=0, output_format="JSON")
        except Exception as exc:
            log.debug("Could not read %s before renaming (will update): %s", guid, exc)
            current = None
        if self._current_display_name(current) == display_name:
            self.rename_counts["unchanged"] += 1
            return
        try:
            self._asset_maker.update_asset(
                guid, body={"class": "UpdateElementRequestBody", "mergeUpdate": True,
                            "properties": {"class": "AssetProperties", "displayName": display_name}})
            self.rename_counts["updated"] += 1
        except Exception as exc:
            self.rename_counts["failed"] += 1
            log.warning("Could not rename %s to %r (left as it was): %s", guid, display_name, exc)

    def _resolve_template_guid(self, technology_type: str) -> str:
        import asyncio

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self._automated_curation._async_get_template_guid_for_technology_type(technology_type)
        )

    def _find_element_guid(self, qualified_name: str) -> str:
        """Return the GUID of an existing Egeria element by qualified name,
        or '' if not found — the D8 idempotency guard for sub-resources."""
        from resource_explorer.egeria_absence import guid_from_answer

        result = self._automated_curation.get_guid_for_name(qualified_name)
        # Found, a real miss (""), or could-not-check (raised; None included, which up to 6.2.1 was a failure
        # swallowed by `@dynamic_catch`, ISSUE-134). An odd answer used to read as "" and create.
        return guid_from_answer(result, qualified_name)

    @staticmethod
    def _to_string_map(d: dict) -> dict[str, str]:
        """Delegates to the shared implementation — see annotation_props.py."""
        from resource_explorer.surveyors.annotation_props import to_string_map

        return to_string_map(d)
    def _build_annotation_props(self, ann, qualified_name: str) -> dict:
        """Delegates to the shared implementation — see annotation_props.py.

        Kept as a method because tests and call sites reach for it by name."""
        from resource_explorer.surveyors.annotation_props import build_annotation_props

        return build_annotation_props(ann, qualified_name)

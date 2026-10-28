"""Change an investigation's classification, and move its Egeria elements with it.

`docs/investigation-classification-and-zoning-design.md` §5 and Phase 6 — the
owner's point 7: "It should be possible to change the classification of a project
subsequent to its being created and that could lead to visibility changes."

**A flow with a report, not an UPDATE.** Reclassifying can move an investigation
between visibility regimes, which is several Egeria operations that fail
independently, so the caller gets a per-element account rather than a boolean.

The two directions are not symmetric, and the asymmetry decides the whole design:

* **Loosening** (Personal/Experiment -> Task/Campaign/Study). Artifacts become
  visible. A step that fails leaves something *private* that should be shared:
  annoying, safe, and visible to the person who asked.
* **Tightening** (Task/Campaign/Study -> Personal/Experiment). Artifacts are
  *already public* and every one has to be moved. Anything the sweep misses stays
  readable **while RE's own UI says private**, because Phase 3's filter is local
  and works whether or not Egeria co-operates. That is the dangerous direction,
  and it is why this module verifies by reading back rather than trusting a
  write, and why it refuses to record the new classification locally until
  Egeria actually holds it.

**Order is chosen so a partial failure cannot lie.** Egeria first, local last.
Recording the classification locally first would make RE claim "private" during
the window in which Egeria is still serving the artifacts to everyone — the exact
disagreement, with the reassuring half visible, that the whole feature exists to
prevent. If Egeria only partly succeeds, the local classification is left alone
and the failure is reported; the investigation stays what it was, which is true.

**What has to move, and what does not.** Phase 4 anchoring does most of the work:

    investigation Project ....... re-zoned here
      +-- Folio (Collection) .... ANCHORED -> follows, nothing to do
    SurveyReport (per member) ... re-zoned here
      +-- Annotations ........... ANCHORED -> follow, nothing to do

Measured 2026-09-08: enforcement reads the LIVE anchor, not the copy cached on
the child, so an anchored element's access changes the moment its anchor's zones
do. The sweep is therefore over Projects and SurveyReports only — bounded and
enumerable — rather than over every annotation ever written.

**One thing RE cannot do.** Moving an element *out of* a zone requires
`AccessOperation.PUBLISH` on its ORIGINAL zones. `egeria-runtime`'s security list
does not include RE's account, so an artifact already promoted into the
deployment's publish zones cannot be pulled back by RE — confirmed live
(`OPEN-METADATA-SECURITY-403-005`). Those are reported as unmovable with the
reason, never skipped silently: an element that stays public is exactly what the
person reclassifying needs to know about.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Direction labels. `lateral` is a real outcome — Task -> Campaign changes the
#: classification and nothing about visibility — and is kept distinct from
#: "nothing happened" so the caller can say which it was.
TIGHTEN, LOOSEN, LATERAL = "tighten", "loosen", "lateral"


@dataclass
class ReclassificationResult:
    """What actually happened, per element."""
    slug: str = ""
    from_classification: str = ""
    to_classification: str = ""
    direction: str = LATERAL
    #: The Egeria Project, or "" for a local-only investigation. Needed to tell
    #: "the Project is still public" from "there is no Project" — see
    #: `still_public`, which reported the former for the latter until this was
    #: added.
    project_guid: str = ""
    #: True when the Project's zones were changed AND read back as expected.
    project_rezoned: bool = False
    #: Report GUIDs confirmed in their new zones.
    reports_moved: list[str] = field(default_factory=list)
    #: [{"guid", "reason"}] — reports that could not be moved. On a tightening
    #: these are STILL PUBLIC and the caller must be told so plainly.
    reports_unmovable: list[dict] = field(default_factory=list)
    #: Whether the Egeria Project's KIND classification was swapped and read
    #: back. Separate from `project_rezoned`: zones are visibility, this is
    #: metadata, and they fail independently.
    egeria_kind_changed: bool = False
    #: Whether the local classification was updated. False on any Egeria
    #: failure during a tightening — see the module docstring on ordering.
    local_applied: bool = False
    errors: list[str] = field(default_factory=list)
    #: Brief Z3: the caller may not move the investigation Project ("not permitted · <reason>"); nothing was
    #: changed. The route answers 403 with the reason.
    not_permitted: str = ""

    @property
    def ok(self) -> bool:
        return self.local_applied and not self.errors and not self.reports_unmovable

    @property
    def still_public(self) -> list[str]:
        """Elements that should be private and are not. Empty on any direction
        other than a tightening — the concept only means something there."""
        if self.direction != TIGHTEN:
            return []
        out = [u["guid"] for u in self.reports_unmovable]
        # Only when there IS a Project. A local-only investigation has nothing
        # in Egeria, so nothing of it can be public — the first version named
        # the Project unconditionally and told the user their unpromoted
        # investigation was exposed, which was a false alarm rather than a leak
        # but still a wrong statement. Caught by the live test.
        if self.project_guid and not self.project_rezoned:
            out.append("the investigation Project")
        return out

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "project_guid": self.project_guid,
            "from_classification": self.from_classification,
            "to_classification": self.to_classification,
            "direction": self.direction,
            "project_rezoned": self.project_rezoned,
            "egeria_kind_changed": self.egeria_kind_changed,
            "reports_moved": self.reports_moved,
            "reports_unmovable": self.reports_unmovable,
            "local_applied": self.local_applied,
            "errors": self.errors,
            "still_public": self.still_public,
            "not_permitted": self.not_permitted,
            "ok": self.ok,
        }


def direction_of(from_cls: str, to_cls: str, private_set) -> str:
    was, now = from_cls in private_set, to_cls in private_set
    if was == now:
        return LATERAL
    return TIGHTEN if now else LOOSEN


class InvestigationReclassifier:
    """Moves one investigation between classifications, Egeria side first."""

    def __init__(self, registry):
        self._registry = registry

    # ── zone helpers ──────────────────────────────────────────────────────

    def _move_zones(self, guid: str, target: list[str], owner: str = "") -> tuple[bool, str]:
        """Move one element's zones and CONFIRM it, by reading back.

        Returns `(moved, reason)`. `moved` is True only when the element's zones
        actually read back as `target` — a write that returned without raising is
        not evidence, which is the lesson this codebase has now learned three
        times in one feature.

        A no-op is success, not a failure: Egeria's connector *rejects* a zone
        change whose before and after are equal
        (`OMAG-SERVER-SECURITY-403-005`), so re-running a reclassification, or
        reclassifying an element that was already right, must not report a
        permissions error.
        """
        from resource_explorer.egeria_identity import current_zones, set_zone_membership

        if not target:
            return self._clear_zones(guid, owner)

        before = current_zones(guid)
        if before and set(before) == set(target):
            return True, "already in the target zones"
        if not set_zone_membership(guid, target):
            # The likeliest cause by far, and worth naming rather than leaving
            # the caller with "Egeria said no": moving OUT of a zone needs
            # PUBLISH on the ORIGINAL zones, and RE's account does not hold that
            # for the deployment's publish zones.
            hint = ""
            if before:
                hint = (f" (it is in {before}; moving out of a zone needs PUBLISH "
                        "rights on that zone, which RE's account may not hold)")
            return False, f"Egeria did not accept the zone change{hint}"
        after = current_zones(guid)
        if after and set(after) != set(target):
            return False, f"zones read back as {after}, not {target}"
        if not after:
            # `current_zones` returns [] when it could not tell. Not proof of
            # failure, and not proof of success either — so it is reported as
            # unverified rather than counted as moved.
            return False, "could not read the zones back to confirm the change"
        return True, ""

    def _clear_zones(self, guid: str, owner: str = "") -> tuple[bool, str]:
        """Loosen with NO configured zone: take RE's own zones off the element and leave everyone else's.

        RE's own zones are its private zone, the investigation owner's zone and its draft zone. If the
        element carries only those, the `ZoneMembership` is removed ("zones left to Egeria"). The clear call
        removes the WHOLE classification, so when a foreign zone is also there it is NOT used: the zones are
        set to the foreign ones alone (a replace) and read back. Foreign zones only: nothing is touched.
        Every outcome is confirmed by a strict read; an unreadable element is reported, never counted."""
        from resource_explorer.egeria_identity import (
            ZoneReadError, clear_zone_membership, draft_zones, private_zone, read_zones, set_zone_membership,
        )

        try:
            before = read_zones(guid)
        except ZoneReadError as exc:
            return False, str(exc)
        if not before:
            return True, "already in no zone (zones left to Egeria)"
        own = {private_zone(), *draft_zones()} | ({owner} if owner else set())
        foreign = [z for z in before if z not in own]
        if foreign and len(foreign) == len(before):
            return True, f"zones left as they are · {', '.join(before)} · none is RE's"
        if foreign:
            if not set_zone_membership(guid, foreign):
                return False, f"Egeria did not accept keeping only {foreign} (it is in {before})"
            try:
                after = read_zones(guid)
            except ZoneReadError as exc:
                return False, f"could not read the zones back to confirm the change: {exc}"
            if set(after) != set(foreign):
                return False, f"zones read back as {after}, not {foreign}"
            return True, f"RE's zones removed · kept the zones that are not RE's: {', '.join(foreign)}"
        if not clear_zone_membership(guid):
            return False, (f"Egeria did not accept clearing the zones (it is in {before}; moving out "
                           "of a zone needs PUBLISH rights on that zone, which RE's account may not hold)")
        try:
            after = read_zones(guid)
        except ZoneReadError as exc:
            return False, f"could not read the zones back to confirm the clear: {exc}"
        if after:
            return False, f"zones read back as {after}, not cleared"
        return True, ""

    def _move_kind_classification(self, guid: str, from_kind: str, to_kind: str,
                                  hypothesis: str) -> tuple[bool, str]:
        """Swap the Project's KIND classification in Egeria.

        `(moved, reason)`. Zones are handled separately and first — this is the
        metadata half, and a failure here is an inconsistency (Egeria says Task,
        RE says PersonalProject) rather than a leak.

        **Only the kind is touched.** The old classification is removed by name,
        and only when it is one of `PROJECT_CLASSIFICATIONS`. Everything else on
        the Project survives untouched: `Anchors`, `Ownership`, `ZoneMembership`,
        and in particular the **`Investigation` marker** the project owner added
        (2026-09-08) — that is an orthogonal marker meaning "this Project is an
        investigation", it coexists with the kind, and it must not be disturbed
        by a change of kind. A blanket "remove the classifications" would have
        taken it with them.

        Verified by reading `elementHeader.projectKinds` back, the same measured
        shape `egeria_investigation_publisher._confirm_classification` uses.
        """
        from resource_explorer.surveyors.egeria_investigation_publisher import (
            _confirm_classification, _initial_classifications,
        )

        try:
            from pyegeria import ProjectManager
            from pyegeria.omvs.metadata_expert import MetadataExpert

            from resource_explorer.egeria_clients import current_principal, egeria_client

            clients = egeria_client(current_principal(), purpose="investigation reclassify")
            me = clients.of(MetadataExpert)
            # A ProjectManager as well, ONLY for the read-back. The two clients
            # return different payload shapes for the same element:
            # `MetadataExpert.get_metadata_element_by_guid` gives the raw form
            # (`classifications`, `elementGUID`, ...) while
            # `ProjectManager.get_project_by_guid` gives the
            # `elementHeader.projectKinds` form that `_confirm_classification`
            # was measured against. Reading with the wrong one made the check
            # report "could not tell" on every call — correctly refusing to
            # guess, but checking nothing. Found live 2026-09-08.
            pm = clients.of(ProjectManager)
        except PermissionError:
            raise      # NoCallerIdentity / CallerTokenExpired: a 401, never "could not reach Egeria"
        except Exception as exc:
            return False, f"could not reach Egeria: {type(exc).__name__}: {exc}"

        from resource_explorer.registry import ProjectRegistry

        declassify_error = ""
        if from_kind and from_kind in ProjectRegistry.PROJECT_CLASSIFICATIONS:
            try:
                # The explicit body is REQUIRED despite the parameter being
                # declared Optional: pyegeria calls `.model_dump()` on it
                # unconditionally, so omitting it raises
                # `AttributeError: 'NoneType' object has no attribute
                # 'model_dump'` and the classification is silently left in
                # place. Measured live 2026-09-08 and logged as an upstream
                # issue rather than patched here, per this repo's standing rule.
                me.declassify_metadata_element(
                    guid, from_kind, {"class": "MetadataSourceRequestBody"})
            except Exception as exc:
                # Not fatal on its own: the old kind may already be absent (an
                # investigation promoted before Phase 1 carries none at all), and
                # the add below is what actually matters. The read-back below is
                # what decides — if the old kind is genuinely still there, this
                # returns False whatever happened here.
                #
                # Kept rather than only logged, because the read-back can say
                # THAT the Project carries both kinds and never why. These two
                # causes need opposite responses: pyegeria rejecting the request
                # body (upstream ISSUE-93, the reason the explicit body above
                # exists) is a code fix, while Egeria refusing the declassify is
                # a permissions or state problem on the platform. A reason
                # string that names neither sends the reader to read this
                # function instead of the error.
                declassify_error = f"{type(exc).__name__}: {exc}"
                log.info("could not remove the %s classification from %s (it may not "
                         "be there): %s", from_kind, guid, exc)
        props = _initial_classifications(to_kind, hypothesis)[to_kind]
        try:
            me.classify_metadata_element(guid, to_kind, {
                "class": "NewClassificationRequestBody", "properties": props})
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

        confirmed = _confirm_classification(pm, guid, to_kind)
        if confirmed == to_kind:
            # The OLD kind must also be gone. Egeria happily carries both, and
            # `_confirm_classification` only asks whether the new one is
            # present — so without this a failed declassify would report
            # success over a Project claiming to be two kinds at once. Seen
            # live before the declassify body was fixed.
            still = _confirm_classification(pm, guid, from_kind)
            if from_kind and still == from_kind:
                why = (f" (removing it failed: {declassify_error})"
                       if declassify_error else
                       " (removing it reported no error, so Egeria accepted the "
                       "request and kept the classification anyway)")
                return False, (f"the {to_kind} classification was added but "
                               f"{from_kind} is still there — the Project now carries "
                               f"both kinds{why}")
            return True, ""
        if confirmed == "":
            return False, f"Egeria does not carry the {to_kind} classification after the change"
        return False, "could not read the classification back to confirm the change"

    def _target_zones(self, direction: str, owner: str) -> list[str]:
        from resource_explorer.egeria_identity import configured_publish_zones, private_zones

        # Loosening goes to the CONFIGURED publish zones only (owner, 2026-10-07: no default zone).
        # `[]` means "nothing configured": `_move_zones` then clears the private zones and leaves
        # zones to Egeria, rather than inventing one.
        return private_zones(owner) if direction == TIGHTEN else configured_publish_zones()

    def _report_guids(self, slug: str) -> tuple[list[str], list[str]]:
        """`(report_guids, members_that_could_not_be_enumerated)`.

        Every published SurveyReport belonging to this investigation's members.
        Annotations are NOT enumerated: Phase 4 anchors them to their report, and
        enforcement reads the live anchor, so moving the report moves them.

        **The second return value is the point.** A member whose published
        surveys cannot be listed is not "a member with no surveys" — it is a
        member whose surveys we did not look at. Logging and continuing (which is
        what this did first) means a tightening moves everything it managed to
        find, reports success, and leaves that member's reports PUBLIC while RE
        shows the investigation as private. Exactly the failure this whole
        feature exists to prevent, reproduced inside the code meant to prevent
        it.

        So the caller gets told, and on a tightening it refuses. `''` from a
        registry that could not answer is a fact about us, not about the
        catalogue.
        """
        guids: list[str] = []
        unreadable: list[str] = []
        for member in self._registry.list_investigation_members(slug):
            if member.get("entity_type") != "repo":
                continue
            entity_slug = member.get("entity_slug") or "?"
            try:
                surveys = self._registry.get_egeria_surveys(entity_slug) or []
            except Exception as exc:
                log.warning("could not list published surveys for %s: %s: %s",
                            entity_slug, type(exc).__name__, exc)
                unreadable.append(entity_slug)
                continue
            guids.extend(s["egeria_report_guid"] for s in surveys
                         if s.get("egeria_report_guid"))
        return list(dict.fromkeys(guids)), unreadable

    # ── the flow ──────────────────────────────────────────────────────────

    def reclassify(self, slug: str, to_classification: str,
                   hypothesis: str = "", *, access=None) -> ReclassificationResult:
        from resource_explorer.registry import ProjectRegistry

        res = ReclassificationResult(slug=slug, to_classification=to_classification)
        inv = self._registry.get_investigation(slug)
        if not inv:
            res.errors.append(f"investigation '{slug}' not found")
            return res
        res.from_classification = inv.get("project_classification") or ""
        if to_classification not in ProjectRegistry.PROJECT_CLASSIFICATIONS:
            res.errors.append(
                f"unknown classification {to_classification!r}; "
                f"valid: {list(ProjectRegistry.PROJECT_CLASSIFICATIONS)}")
            return res
        if res.from_classification == to_classification:
            res.errors.append(f"already classified {to_classification}")
            return res

        hypothesis = (hypothesis or "").strip()
        needs_hypothesis = to_classification in ProjectRegistry.HYPOTHESIS_REQUIRED_FOR
        if needs_hypothesis and not (hypothesis or inv.get("hypothesis")):
            res.errors.append(
                f"{to_classification} requires a hypothesis — it is the attribute "
                "the classification exists to record")
            return res

        res.direction = direction_of(res.from_classification, to_classification,
                                     ProjectRegistry.PRIVATE_CLASSIFICATIONS)
        owner = (inv.get("created_by") or "").strip()
        if res.direction == TIGHTEN and not owner:
            # Zoning to an empty owner yields `[private_zone]` alone, readable by
            # NOBODY — including the person whose investigation it is. Refusing
            # is better than making somebody's work inaccessible to themselves.
            res.errors.append(
                "this investigation has no recorded creator (it predates ownership "
                "being recorded), so there is nobody to make it private to. "
                "Recreate it, or set an owner, before classifying it as "
                f"{to_classification}.")
            from resource_explorer.surveyors.egeria_investigation_publisher import (
                record_ownerless_refusal,
            )

            record_ownerless_refusal(self._registry, inv, res.errors[-1], f"reclassify to {to_classification}")
            return res

        project_guid = (inv.get("egeria_project_guid") or "").strip()
        res.project_guid = project_guid

        # A BOUND private investigation with no owner (design ruling 2026-10-13): its Project is already in
        # Egeria, and it was published unzoned because there was nobody to zone it to. Any reclassify moves
        # that Project's zones or kind — to `[PRIVATE_ZONE]` alone (nobody can read it) or onward as if it had
        # been private. Refused in every direction; the owner decides what happens to the element. A local-only
        # one has nothing in Egeria, so a loosening or lateral change there is still a local edit.
        if project_guid:
            from resource_explorer.surveyors.egeria_investigation_publisher import (
                ownerless_private_words,
                record_ownerless_refusal,
            )

            words = ownerless_private_words(inv, verb="reclassified")
            if words:
                res.errors.append(words)
                record_ownerless_refusal(self._registry, inv, words, f"reclassify to {to_classification}")
                return res

        # A purely local investigation has no Egeria elements to move, so the
        # classification change is the whole operation.
        if not project_guid:
            return self._apply_locally(res, to_classification, hypothesis)

        if res.direction == LATERAL:
            # Nothing moves. Task -> Campaign is a real change with no visibility
            # consequence, and pretending otherwise would re-zone elements for no
            # reason — which the connector rejects as a no-op anyway.
            return self._apply_locally(res, to_classification, hypothesis)

        # Brief Z3: every element this moves is checked first, before anything else is asked of Egeria (even the
        # private zone's set-up below) (`access(writes) -> words`, the caller's
        # `egeria_work_access.access_words`), from the same list the moves below walk. A refused Project changes
        # nothing (`not_permitted`, the route's 403). A refused SurveyReport: on a tightening nothing moves (it
        # would stay readable by everyone while RE showed the investigation private, the case this refuses
        # anyway after the fact); on a loosening it is unmovable with its words and the rest move.
        guids, unreadable = self._report_guids(slug)
        refused_reports: dict[str, str] = {}
        if access is not None:
            from resource_explorer.egeria_identity import draft_zones, private_zone
            from resource_explorer.workflows.egeria_work_access import (
                reclassify_kind_after_move, reclassify_project_write, report_write,
            )

            # Round 2 (F6): the kind change is also judged on the zones the move puts the Project in, which is
            # where Egeria checks it; a non-owner's tightening is refused here, before anything moves.
            target_after = self._target_zones(res.direction, owner)
            own = (private_zone(), *draft_zones(), *([owner] if owner else []))
            why = access([reclassify_project_write(project_guid, res.from_classification),
                          reclassify_kind_after_move(project_guid, res.from_classification, target_after, own)])
            if why:
                res.not_permitted = why
                res.errors.append(f"the investigation Project: {why}. Nothing was changed.")
                return res
            refused_reports = {g: w for g in guids if (w := access([report_write(g)]))}
            if refused_reports and res.direction == TIGHTEN:
                res.reports_unmovable += [{"guid": g, "reason": w} for g, w in refused_reports.items()]
                res.errors.append(
                    "the classification was NOT changed and nothing was moved: you may not move "
                    f"{len(refused_reports)} of this investigation's reports, which would stay readable "
                    "by everyone.")
                return res

        if res.direction == TIGHTEN:
            from resource_explorer.egeria_identity import (
                ensure_private_zone_exists, private_zone, private_zone_is_enforced,
                private_zone_status,
            )

            # "Nobody has asked yet" is a question, not an answer — the same
            # lazy check `EgeriaPublisher._require_enforced_private_zone` does,
            # and needed here for the same reason. The zone state is
            # per-process and the bootstrap that fills it runs in the WORKER
            # role, so a web or CLI process that never ran it would refuse
            # every tightening while the zone was perfectly healthy. Found by
            # the live test, which hit exactly that in a fresh process.
            if private_zone_status().get("status") == "unknown":
                ensure_private_zone_exists()

            if not private_zone_is_enforced():
                res.errors.append(
                    f"the '{private_zone()}' zone is not confirmed to be enforced, so "
                    "this investigation cannot be made private. Nothing was changed — "
                    "moving its artifacts into a zone Egeria ignores would leave them "
                    "readable by everyone while Resource Explorer showed them as "
                    "private.")
                return res

        target = self._target_zones(res.direction, owner)
        if res.direction != TIGHTEN:
            # Loosening moves into the configured publish zones: never into one that grants nobody once Egeria
            # e9fd817f4c ships (or whose control could not be read). Refused before anything moves.
            from resource_explorer.egeria_identity import zone_lockout_refusal

            refusal = zone_lockout_refusal(target)
            if refusal:
                res.errors.append(f"nothing was moved: {refusal}.")
                return res

        moved, reason = self._move_zones(project_guid, target, owner)
        res.project_rezoned = moved
        if not moved:
            res.errors.append(f"could not move the investigation Project: {reason}")

        for member_slug in unreadable:
            # Recorded as unmovable rather than ignored: we do not know what this
            # member has published, so on a tightening we cannot claim it is
            # private. Naming the member (not a GUID) because that is what the
            # person can act on.
            res.reports_unmovable.append({
                "guid": f"(unknown — member '{member_slug}')",
                "reason": ("could not list this member's published surveys, so its "
                           "reports were never checked or moved"),
            })
        for guid in guids:
            if guid in refused_reports:
                res.reports_unmovable.append({"guid": guid, "reason": refused_reports[guid]})
                continue
            ok, why = self._move_zones(guid, target, owner)
            if ok:
                res.reports_moved.append(guid)
            else:
                res.reports_unmovable.append({"guid": guid, "reason": why})

        # The metadata half, after the zones. Order matters: zones are the
        # safety property and go first, so a failure here leaves the
        # visibility correct and only the classification stale.
        moved, why = self._move_kind_classification(
            project_guid, res.from_classification, to_classification,
            hypothesis or (inv.get("hypothesis") or ""))
        res.egeria_kind_changed = moved
        if not moved:
            res.errors.append(
                f"the Egeria Project still carries {res.from_classification!r} rather "
                f"than {to_classification!r}: {why}. Visibility was handled "
                "separately and is correct; this is a metadata inconsistency, not an "
                "exposure.")

        # Local last, and only when Egeria actually holds the new state. On a
        # tightening, applying it anyway would make RE claim private over
        # artifacts that are still public — see the module docstring.
        if res.direction == TIGHTEN and (not res.project_rezoned or res.reports_unmovable):
            res.errors.append(
                "the classification was NOT changed, because some elements are still "
                "readable by everyone. Resource Explorer would have shown this "
                "investigation as private while Egeria served them.")
            return res
        return self._apply_locally(res, to_classification, hypothesis)

    def _apply_locally(self, res: ReclassificationResult, to_classification: str,
                       hypothesis: str) -> ReclassificationResult:
        try:
            self._registry.set_investigation_classification(
                res.slug, to_classification, hypothesis=hypothesis)
            res.local_applied = True
        except Exception as exc:
            res.errors.append(
                f"Egeria was updated but the local classification was not: "
                f"{type(exc).__name__}: {exc}")
        return res

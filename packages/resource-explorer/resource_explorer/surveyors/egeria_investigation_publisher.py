"""Promote a local investigation into Egeria.

`docs/investigation-framing-design.md` §1 and §6. The local record was shaped
from the start so this is a **replay, not a migration**:

    investigations                -> Project (+ purposes on its charter)
    working_sets                  -> Collection
    investigation_resource_lists  -> ResourceList  (Project -> Collection)
    working_set_members           -> CollectionMembership (Collection -> asset)

Every step reports what it actually did. A partially-promoted investigation is a
real outcome — a member whose repo has never been published to Egeria has no
asset GUID to link, and inventing one would be worse than saying so.

This never edits pyegeria. Where a call fails, the failure is returned; per the
standing rule, pyegeria bugs get logged in PYEGERIA_ISSUES.md rather than worked
around here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


def _create_typed_collection(cm, type_name: str, display_name: str,
                             description: str, qualified_name: str = "",
                             anchor_guid: str = "", additional_properties: dict | None = None) -> str:
    """Create a Collection SUBTYPE (Folio, WorkingSet, ...).

    `create_collection`'s convenience parameters build a plain Collection, so the
    subtype goes through `body` as `typeName` — the same route the publisher
    already uses for asset properties. Folio and WorkingSet are subtypes of
    Collection, not classifications, so this is a typeName rather than an
    initial_classification.
    """
    # qualifiedName is REQUIRED and is not generated for you once you pass an
    # explicit body — the convenience path supplies one, the body path does not.
    # Egeria rejects the create with OPEN-METADATA-400-004 otherwise.
    body: dict = {
        "class": "NewElementRequestBody",
        "properties": {
            "class": "CollectionProperties",
            "typeName": type_name,
            "qualifiedName": qualified_name or f"{type_name}::{display_name}",
            "displayName": display_name,
            "description": description,
        },
    }
    if additional_properties:
        # Sent only when given (a work list's link back to RE), so every other caller's body is unchanged.
        body["properties"]["additionalProperties"] = dict(additional_properties)
    if anchor_guid:
        # Anchored to the investigation's Project, which is what makes the
        # collection inherit the Project's governance zones instead of carrying
        # none. Measured 2026-09-08: existing Folios came back `anchorGUID:
        # None` — their own anchor — with no ZoneMembership, so a private
        # investigation's collection was world-readable. It holds the
        # membership list rather than findings, but it names the investigation.
        #
        # Inheritance rather than a second `set_zone_membership` call, because
        # the anchor is one property set at creation and stays correct when the
        # Project is re-zoned later: enforcement reads the LIVE anchor, not the
        # copy cached on the child.
        body["isOwnAnchor"] = False
        body["anchorGUID"] = anchor_guid
    return cm.create_collection(body=body) or ""


#: `Project` is a valid `Project Type` in Dr.Egeria's vocabulary but it is NOT
#: a classification — it means "no classification". Sending it would ask Egeria
#: for a `ProjectProperties` classification that does not exist.
_UNCLASSIFIED = "Project"


def _initial_classifications(classification: str, hypothesis: str = "") -> dict:
    """The `initialClassifications` entry for one Project classification.

    Egeria's shape is `{"<Name>": {"class": "<Name>Properties"}}` — confirmed
    against pyegeria's own functional test for `create_project`
    (`tests/functional-tests/test_project_manager_omvs.py`, PersonalProject),
    and all five verified live 2026-09-07. The properties class name is the
    classification name plus `Properties` for every one of Campaign / Task /
    PersonalProject / StudyProject / Experiment (`OpenMetadataType.java`,
    model 0130).

    `Experiment` additionally carries `hypothesis` — *"a project conducting an
    experiment that is testing a hypothesis (documented in the hypothesis
    attribute)"*. Sending the classification without it would publish an
    Experiment whose defining property is empty, so the registry requires one
    at create time and it is passed through here.
    """
    props: dict = {"class": f"{classification}Properties"}
    if hypothesis and classification in _HYPOTHESIS_CLASSIFICATIONS:
        props["hypothesis"] = hypothesis
    return {classification: props}


#: Kept local rather than imported from the registry so this module stays
#: usable with any registry-shaped object, which is how the tests drive it.
#: Mirrors `ProjectRegistry.HYPOTHESIS_REQUIRED_FOR`; the invariant that the
#: two agree is pinned by a test rather than left to be noticed.
_HYPOTHESIS_CLASSIFICATIONS = ("Experiment",)


def _confirm_classification(pm, project_guid: str, expected: str) -> "str | None":
    """Read the classification back off the Project Egeria just created.

    Returns the confirmed name, `""` when Egeria holds no such classification,
    and `None` when we could not tell. The three are kept distinct on purpose:
    a create that returned a GUID proves the *call* worked and says nothing
    about whether the classification survived it — which is precisely how this
    field came to be silently dropped for the whole life of the feature. A
    test that asserts `create_project` was called with a body would have passed
    throughout.

    An unreachable read-back is reported as "could not tell", never as
    confirmation and never as absence — a fact about us is not a fact about
    Egeria.

    **The payload shape, measured live 2026-09-07 against qs-view-server** (the
    first version of this function guessed it and was wrong in the direction
    that hides the bug: it looked for a top-level `classifications` list, found
    nothing, and returned `None` — "could not tell" — on every single call, so
    the check never checked). `get_project_by_guid(..., output_format="JSON")`
    returns an `OpenMetadataRootElement`:

        {"class": "OpenMetadataRootElement",
         "elementHeader": {"guid": ..., "type": {...}, "anchor": {...},
                           "projectKinds": [{"class": "ElementClassification",
                                             "classificationName": "Task",
                                             "type": {"typeName": "Task",
                                                      "superTypeNames": ["ProjectKind"]}}]},
         "properties": {...}, "resourceList": [...], "mermaidGraph": ...}

    Two things to know about it:

    * The project classifications live under `elementHeader.projectKinds`,
      grouped away from `elementHeader.anchor` (which carries `Anchors`). They
      are `ProjectKind` subtypes, which is what makes the grouping possible.
    * **`projectKinds` is OMITTED, not null, when the Project has no kind** —
      36 of 50 live Projects carried the key, and the other 14 lacked it
      entirely. So an absent key cannot by itself be read as "not classified":
      a changed payload shape looks identical. `elementHeader.guid` is the
      sentinel that separates the two — if the header is there and recognisable,
      the absence of `projectKinds` is Egeria's answer, not our confusion.
    """
    try:
        proj = pm.get_project_by_guid(project_guid, output_format="JSON")
    except Exception as exc:
        log.warning("could not read back classification for %s — %s: %s",
                    project_guid, type(exc).__name__, exc)
        return None
    header = (proj or {}).get("elementHeader") if isinstance(proj, dict) else None
    if not isinstance(header, dict) or not header.get("guid"):
        # Not the payload we know how to read. Saying "absent" here would turn
        # our own parsing failure into an accusation about Egeria.
        log.warning("could not read back classification for %s — unrecognised "
                    "payload shape (keys: %s)", project_guid,
                    sorted(proj.keys()) if isinstance(proj, dict) else type(proj).__name__)
        return None
    names = set()
    for kind in header.get("projectKinds") or []:
        if isinstance(kind, dict):
            names.add(kind.get("classificationName")
                      or (kind.get("type") or {}).get("typeName"))
    names.discard(None)
    return expected if expected in names else ""


#: The Egeria classification RE stamps on every Project it creates, so a Project
#: that is an RE investigation says so in the catalogue.
#:
#: Added by the project owner 2026-09-08 as an ORTHOGONAL MARKER: it coexists
#: with the kind (`PersonalProject` / `Task` / `StudyProject` / `Campaign` /
#: `Experiment`), does not replace it, and does not drive zones. Their words:
#: "you can have both Investigation and PersonalProject." So a change of kind
#: leaves it alone — `investigation_reclassifier` removes the old kind BY NAME,
#: gated on `PROJECT_CLASSIFICATIONS`, and this is not in that set.
INVESTIGATION_MARKER = "Investigation"


def _apply_investigation_marker(pm, project_guid: str) -> tuple[bool, str]:
    """Stamp `Investigation` on a Project RE created. `(applied, reason)`.

    **A separate call after the create, deliberately not in
    `initialClassifications`.** In the create body a classification the platform
    does not have fails the WHOLE create — and this type did not exist until the
    2026-09-08 redeploy, and will not exist on any deployment running an older
    Egeria. As a follow-on step, a missing type costs the marker and not the
    investigation.

    That is also why a failure here is reported rather than raised: the marker is
    a catalogue convenience, not a correctness property. Nothing in RE reads it —
    the kind classification is what drives behaviour — so an investigation
    without it is fully functional, just less findable by anyone browsing Egeria
    for RE's work.
    """
    try:
        from pyegeria.omvs.metadata_expert import MetadataExpert

        from resource_explorer.egeria_clients import current_principal, egeria_client

        me = egeria_client(current_principal(), purpose="investigation marker").of(MetadataExpert)
        me.classify_metadata_element(project_guid, INVESTIGATION_MARKER, {
            "class": "NewClassificationRequestBody",
            "properties": {"class": "ClassificationProperties",
                           "typeName": INVESTIGATION_MARKER}})
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        # An older platform simply does not have the type. Worth saying in those
        # words rather than surfacing a raw type error, because it is a
        # deployment fact and not something the user did wrong.
        if "Unknown type" in detail or "TypeErrorException" in detail or "400-006" in detail:
            return False, (f"this Egeria does not have the {INVESTIGATION_MARKER!r} "
                           "classification (it needs a newer platform); the "
                           "investigation is otherwise complete")
        return False, detail
    return True, ""


def is_private_investigation(inv: dict) -> bool:
    """Whether this investigation's Egeria elements must be zoned private: see
    `EgeriaInvestigationPublisher._is_private`, which delegates here (Brief Z3: the access check asks the same
    rule without needing the publisher class)."""
    from resource_explorer.registry import ProjectRegistry

    return bool(
        (inv.get("project_classification") or "") in ProjectRegistry.PRIVATE_CLASSIFICATIONS
        and (inv.get("created_by") or "").strip()
    )


#: The proof-row kind of a refusal to publish a private investigation that has no owner (design ruling
#: 2026-10-13). Carried in the activity row's `detail` and `items` so it is told apart from a failed write.
OWNERLESS_PRIVATE_REFUSAL = "private_without_owner"

_PRIVATE_KIND_WORDS = {"PersonalProject": "personal", "Experiment": "experiment"}


def ownerless_private_words(inv: dict, verb: str = "published") -> str:
    """The refusal words when `inv` is a privately-classified investigation with NO owner, else `""`.

    Design ruling 2026-10-13. A private kind maps to `[PRIVATE_ZONE, owner]`; with no owner there is nobody to
    make it private TO, and `is_private_investigation` (which requires an owner) used to answer "not private"
    for exactly these rows, so they were published unzoned and readable by everyone (`egeria-understanding`,
    found live 2026-10-13). Every write path refuses instead: never an unzoned element, never a fallback.
    """
    from resource_explorer.registry import ProjectRegistry

    kind = (inv.get("project_classification") or "").strip()
    if kind not in ProjectRegistry.PRIVATE_CLASSIFICATIONS or (inv.get("created_by") or "").strip():
        return ""
    label = _PRIVATE_KIND_WORDS.get(kind, kind)
    return f"not {verb} · this {label} investigation has no owner, so it cannot be made private"


def _latest_ownerless_refusal(registry, slug: str, action: str) -> dict | None:
    """The newest refusal row for this investigation and action, as its decoded `detail` plus `summary`."""
    import json

    for row in registry.list_activity(entity_type="investigation", entity_slug=slug, operation="publish",
                                      limit=50):
        try:
            detail = json.loads(row.get("detail") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(detail, dict) and detail.get("refused") == OWNERLESS_PRIVATE_REFUSAL \
                and detail.get("action") == action:
            return {**detail, "summary": row.get("summary") or ""}
    return None


def record_ownerless_refusal(registry, inv: dict, words: str, action: str) -> None:
    """Record the refusal as its own proof row (activity_log, operation `publish`, status `error`, kind
    `private_without_owner`), ONCE per state: when the newest refusal row for this investigation + action already
    says the same words about the same Project, nothing is written (resync's relink walks every bound
    investigation each pass, and a row per pass would grow forever). The caller's words are returned to it every
    time regardless; only the record is shared. The warning is logged when a row is written, at debug otherwise.
    A failure to record is logged, never raised: the refusal stands either way."""
    import json
    import uuid
    from datetime import UTC, datetime

    slug = inv.get("slug") or ""
    guid = inv.get("egeria_project_guid") or ""
    try:
        latest = _latest_ownerless_refusal(registry, slug, action)
        if latest and latest.get("summary") == words and (latest.get("egeria_project_guid") or "") == guid:
            log.debug("investigation %s: %s [%s] (already recorded)", slug, words, action)
            return
        log.warning("investigation %s (%s, owner %r): %s [%s]", slug, inv.get("project_classification"),
                    inv.get("created_by") or "", words, action)
        from resource_explorer.registry import ActivityEntry

        registry.write_activity(ActivityEntry(
            id=str(uuid.uuid4()), ts=datetime.now(UTC).isoformat(), operation="publish",
            intent="enrichment", entity_type="investigation", entity_slug=slug,
            entity_name=inv.get("display_name") or "", status="error", summary=words,
            detail=json.dumps({"refused": OWNERLESS_PRIVATE_REFUSAL, "action": action,
                               "classification": inv.get("project_classification") or "",
                               "egeria_project_guid": guid}),
            items=[{"refused": OWNERLESS_PRIVATE_REFUSAL, "action": action}],
        ))
    except Exception:  # the refusal is the safety property; the row is its record
        log.exception("could not record the ownerless-private refusal for %s (%s): %s", slug, action, words)


def _unattached_reason(row: dict, unknown: str = "unknown error") -> str:
    """Why a membership row is not attached, decided by the row's STATUS (Brief Z3 round 3): a dead row is never
    retried (the access check's "not permitted · ...", Egeria's own security refusal, a destructive kind, or its
    attempts used up), so it says its last error and nothing more; only a row the outbox will still attempt
    carries the retry clause."""
    error = str(row.get("last_error") or "")
    if row.get("status") == "dead":
        return error or "not attached (no error recorded) — not retried"
    if error.startswith("waiting for the owner, "):
        # A private investigation's row the background drain left for its owner (`OutboxAwaitsOwner`): the queue
        # will not retry it, the owner's Relink will. Its own words, no "queued for retry".
        return error
    return f"attach failed ({error or unknown}) — queued for retry, see Admin → Publish Queue"


def owner_absent_words(inv: dict) -> str:
    """The words when `inv` is a PRIVATE investigation and whoever this runs as is not its signed-in owner (RE's
    service account in the resync loop, or another person); "" otherwise (a shared one, or the owner is here).

    Egeria refuses the service account a private investigation's elements by design (OPEN-METADATA-SECURITY-
    403-020), so a write or a check made as it can only fail; nothing is sent and the owner's next press does it
    as them (found live 2026-10-13: the outbox's access check read the owner's Project as the service account)."""
    from resource_explorer.workflows.egeria_work_access import owner_identity, owner_waiting_words, private_owner

    owner = private_owner(inv)
    if not owner:
        return ""
    from resource_explorer.egeria_clients import current_principal

    try:
        who = current_principal()
    except PermissionError:
        who = None
    if who is not None and owner_identity(inv, who) is not None:
        return ""
    return owner_waiting_words(owner)


def resource_list_landed(cm, project_guid: str, collection_guid: str) -> "tuple[bool | None, str]":
    """Is `collection_guid` attached to `project_guid` (a ResourceList), read back on `cm` (the owner's client for
    a private investigation)? (True, "") / (False, "") from Egeria's answer; (None, why) when it could not be
    read or the answer is not one RE knows: never "attached" from a write that only returned."""
    from resource_explorer.egeria_absence import ABSENT, is_absent

    try:
        attached = cm.get_attached_collections(project_guid)
    except Exception as exc:  # noqa: BLE001 - could not tell, said in its words
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"
    if isinstance(attached, str):
        return (False, "") if is_absent(attached) == ABSENT else (None, f"unexpected answer {attached[:80]!r}")
    if not isinstance(attached, list):
        return None, f"unexpected answer of type {type(attached).__name__}"
    guids = {((e or {}).get("elementHeader") or {}).get("guid", "") for e in attached if isinstance(e, dict)}
    return collection_guid in guids, ""


@dataclass
class PromotionResult:
    """What actually happened, step by step."""
    project_guid: str = ""
    project_qualified_name: str = ""
    #: The classification we asked Egeria to apply, and what Egeria actually
    #: holds. Deliberately two fields with a tri-state on the second, because
    #: "we did not check" and "we checked and it is not there" are different
    #: facts and only one of them is about Egeria:
    #:   None -> could not verify (read-back unavailable or failed)
    #:   ""   -> verified, and the classification is NOT there (the bug)
    #:   name -> verified present
    classification_requested: str = ""
    classification_confirmed: "str | None" = None
    #: Whether the `Investigation` marker classification was applied. False on a
    #: platform without the type — see `_apply_investigation_marker`; the
    #: investigation is fully functional either way.
    investigation_marked: bool = False
    #: True only when the private zone was actually applied to the Project.
    #: False on a shared investigation AND on a private one whose zoning
    #: failed — the second case also appends to `errors`, which is what
    #: distinguishes them.
    private_zoned: bool = False
    collection_guid: str = ""
    resource_list_linked: bool = False
    members_linked: list[str] = field(default_factory=list)
    members_unlinkable: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: The refusal words when nothing was sent because a private investigation has no owner
    #: (`ownerless_private_words`); "" otherwise. Also in `errors`.
    not_published: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.project_guid) and not self.errors

    def as_dict(self) -> dict:
        return {
            "project_guid": self.project_guid,
            "project_qualified_name": self.project_qualified_name,
            "investigation_marked": self.investigation_marked,
            "classification_requested": self.classification_requested,
            "classification_confirmed": self.classification_confirmed,
            "private_zoned": self.private_zoned,
            "collection_guid": self.collection_guid,
            "resource_list_linked": self.resource_list_linked,
            "members_linked": self.members_linked,
            "members_unlinkable": self.members_unlinkable,
            "errors": self.errors,
            "not_published": self.not_published,
            "ok": self.ok,
        }


class EgeriaInvestigationPublisher:
    """Creates the Egeria side of an investigation. Read-only on the local row —
    the caller decides whether to record the returned GUIDs."""

    def __init__(self, registry, *, project_manager=None, collection_manager=None):
        self._registry = registry
        self._pm = project_manager
        self._cm = collection_manager

    @staticmethod
    def _is_private(inv: dict) -> bool:
        """Whether this investigation's Egeria elements must be zoned private.

        Keyed on the same `PRIVATE_CLASSIFICATIONS` the registry uses, read off
        the row rather than re-derived, and requiring a recorded creator —
        zoning to an empty owner would produce `[private_zone]` alone, which is
        readable by nobody at all, including the person it belongs to.
        """
        return is_private_investigation(inv)

    def _refuse_ownerless(self, inv: dict, res: PromotionResult, action: str) -> bool:
        """True (and `res` carries the words, and the refusal is logged and recorded) when `inv` is a private
        investigation with no owner. Asked before any Egeria client is built, so nothing is sent."""
        words = ownerless_private_words(inv)
        if not words:
            return False
        res.not_published = words
        res.errors.append(words)
        record_ownerless_refusal(self._registry, inv, words, action)
        return True

    def _refuse_without_owner(self, inv: dict, res: "PromotionResult") -> bool:
        """True (and `res` carries the words) when `inv` is private and this does not run as its signed-in owner
        (`owner_absent_words`). Asked before any Egeria client is built, so nothing is sent as the service
        account, which cannot see a private investigation's elements."""
        words = owner_absent_words(inv)
        if not words:
            return False
        res.errors.append(words)
        log.info("investigation %s: %s", inv.get("slug") or "", words)
        return True

    def _managers(self):
        if self._pm is not None and self._cm is not None:
            return self._pm, self._cm
        from pyegeria import CollectionManager, ProjectManager

        from resource_explorer.egeria_clients import current_principal, egeria_client

        # Who (Brief I): the person who pressed promote (it used to be the service account).
        clients = egeria_client(current_principal(), purpose="investigation promote")
        pm, cm = clients.of(ProjectManager), clients.of(CollectionManager)
        self._pm, self._cm = pm, cm
        return pm, cm

    def promote(self, investigation_slug: str, *, refusal=None) -> PromotionResult:
        """`refusal(member) -> str` (Brief Z3): each member's end of its CollectionMembership, checked before it
        is queued; a member it refuses is reported unlinkable with those words ("not permitted · <reason>") and
        the rest are linked. The Project's own writes are checked by the caller before this runs."""
        res = PromotionResult()
        inv = self._registry.get_investigation(investigation_slug)
        if not inv:
            res.errors.append(f"investigation '{investigation_slug}' not found")
            return res
        if inv.get("egeria_project_guid"):
            res.errors.append(
                "already bound to an Egeria Project — unbind first rather than "
                "creating a second one for the same body of work"
            )
            return res
        # Ad-hoc means "no Egeria Project, by choice" (registry.BINDING_LOCAL).
        # Promoting one anyway would silently overturn a decision somebody made
        # — and the row would end up bound while still flagged local. The
        # caller is not blocked, only made to say so: changing the binding is a
        # one-field edit, and then this promotes normally.
        if (inv.get("egeria_binding") or "egeria") == "local":
            res.errors.append(
                "this investigation is ad-hoc — it was created with no Egeria "
                "Project on purpose. Change its binding to 'egeria' first if it "
                "should have one."
            )
            return res
        # A private kind with no owner: refused before any client is built (ruling 2026-10-13). Zoning it to
        # `[PRIVATE_ZONE]` alone locks everyone out; creating it unzoned publishes it to everyone.
        if self._refuse_ownerless(inv, res, "promote"):
            return res

        try:
            pm, cm = self._managers()
        except Exception as exc:
            res.errors.append(f"could not reach Egeria: {type(exc).__name__}: {exc}")
            return res

        # 1. the Project itself.
        #
        # Purposes go in `additionalProperties`, NOT appended to the description.
        # `mission` and `purposes` are both `ProjectCharter` (0442) properties,
        # and a charter is a separate element `create_project` cannot make — so
        # neither is reachable here. `Project` (0130) is a `Referenceable`
        # though, and `additionalProperties` is the sanctioned carrier for
        # exactly this: structured data the type has no dedicated slot for.
        # Stuffing them into free-text description would make them unreadable
        # by anything but a human, and would have to be parsed back out when the
        # charter is eventually written.
        #
        # (Corrected after review: the first version did use the description.
        # Same error the SolutionPort work hit earlier — reading a type's own
        # attribute list, finding no home, and settling, when the answer was one
        # inheritance edge away.)
        purposes = inv.get("purposes") or []
        qualified_name = f"Project::Investigation::{investigation_slug}"
        properties: dict = {
            "class": "ProjectProperties",
            "typeName": "Project",
            "qualifiedName": qualified_name,
            "displayName": inv["display_name"],
            "description": inv.get("description") or "",
            "identifier": investigation_slug,
        }
        if purposes:
            properties["additionalProperties"] = {
                "purposes": ", ".join(purposes),
                "purposeSource": "ProjectCharter.purposes (pending a real charter)",
                "createdBy": "resource-explorer",
            }
        # The classification the user chose at creation. It has been stored,
        # validated and shown in the UI since the feature shipped, and until
        # now it was dropped right here: the create body carried `properties`
        # and nothing else, so every investigation RE ever promoted landed in
        # Egeria as an unclassified `Project`. See
        # `docs/investigation-classification-and-zoning-design.md` §2.
        classification = (inv.get("project_classification") or "").strip()
        body: dict = {"class": "NewElementRequestBody", "properties": properties}
        if classification and classification != _UNCLASSIFIED:
            known = getattr(self._registry, "PROJECT_CLASSIFICATIONS", ())
            if known and classification not in known:
                # Refuse BEFORE writing anything. Promoting anyway would create
                # a Project silently missing the classification — reproducing
                # the exact defect this change exists to fix, but with a real
                # element left behind in Egeria to clean up.
                res.errors.append(
                    f"investigation carries classification {classification!r}, which is "
                    f"not one of {list(known)}. Nothing was written to Egeria; correct "
                    "the investigation and promote again."
                )
                return res
            body["initialClassifications"] = _initial_classifications(
                classification, inv.get("hypothesis") or "")
            res.classification_requested = classification

        try:
            res.project_guid = pm.create_project(body=body) or ""
            res.project_qualified_name = qualified_name
        except Exception as exc:
            # A qualifiedName collision is a specific, actionable situation, not
            # a generic failure: a Project for this investigation already exists
            # in Egeria — typically because a previous promotion succeeded and
            # the local binding was cleared, orphaning it. Egeria signals this as
            # a 409 wrapped in a large Java error payload; surfacing that raw to
            # a user is useless, so it is translated once here.
            detail = str(exc)
            if "OMAG-COMMON-409-001" in detail or "is not available for use" in detail:
                res.errors.append(
                    f"An Egeria Project with qualifiedName '{qualified_name}' already "
                    "exists. This investigation was promoted before and the local "
                    "binding was cleared, leaving it orphaned. Bind to the existing "
                    "Project instead of creating a second one, or delete it in Egeria "
                    "first."
                )
            else:
                res.errors.append(f"create_project failed: {type(exc).__name__}: {detail}")
            return res
        if not res.project_guid:
            res.errors.append("create_project returned no GUID")
            return res

        # 1a-pre. The Investigation marker, so every Project RE creates says in
        # the catalogue that it is one — not only the ones somebody classified by
        # hand through Dr.Egeria. Owner's decision 2026-09-08.
        #
        # Best-effort and never fatal: it is a marker, nothing reads it, and an
        # older platform does not have the type at all.
        marked, mark_why = _apply_investigation_marker(pm, res.project_guid)
        res.investigation_marked = marked
        if not marked:
            log.info("investigation marker not applied to %s: %s",
                     res.project_guid, mark_why)

        # 1a. Zone the Project itself when the investigation is private.
        #
        # Phase 5 zoned the ARTIFACTS a private investigation produces and left
        # the investigation's own Project public — so its name, description,
        # purposes and membership were readable by everyone while its surveys
        # were not. The owner's point 5 is "the project AND all related
        # artifacts", and the Project is the half that names the work.
        #
        # It is also the anchor: the Folio below inherits from it, so this has
        # to happen before anything is anchored to it.
        private_owner = (inv.get("created_by") or "") if self._is_private(inv) else ""
        if private_owner:
            from resource_explorer.egeria_identity import (
                private_zone, private_zone_is_enforced, private_zones,
                set_zone_membership,
            )

            if not private_zone_is_enforced():
                # Same refusal as a publish, for the same reason: an unenforced
                # zone is an ignored zone, which is public. The Project exists
                # by now, so this is reported rather than raised — but it is an
                # error, so `ok` is False and the caller is told.
                res.errors.append(
                    f"this is a private investigation, but the '{private_zone()}' zone "
                    "is not confirmed to be enforced, so its Egeria Project could not be "
                    "made private. It is visible to everyone until the zone is fixed."
                )
            elif not set_zone_membership(res.project_guid, private_zones(private_owner)):
                res.errors.append(
                    "could not apply the private zone to the Egeria Project — it is "
                    "visible to everyone until this is fixed."
                )
            else:
                res.private_zoned = True

        # 1b. Did the classification actually survive the create?
        #
        # A returned GUID proves the call worked, not that the body was
        # honoured. Silent loss here is the whole reason for this change, so it
        # is checked rather than assumed — and a confirmed absence is an error
        # even though the Project itself exists. The route binds on
        # `project_guid`, not on `ok`, so reporting this does not orphan the
        # element it is complaining about.
        if res.classification_requested:
            res.classification_confirmed = _confirm_classification(
                pm, res.project_guid, res.classification_requested)
            if res.classification_confirmed == "":
                res.errors.append(
                    f"Egeria created the Project but it does not carry the "
                    f"{res.classification_requested!r} classification that was requested. "
                    "The Project is bound and usable; the classification needs applying "
                    "separately."
                )

        # 2. the working set, as a Collection
        members = self._registry.list_investigation_members(investigation_slug)
        try:
            res.collection_guid = _create_typed_collection(
                cm, "Folio", inv["display_name"],
                "Everything in scope for this investigation.",
                qualified_name=f"Folio::Investigation::{investigation_slug}",
                anchor_guid=res.project_guid,
            )
        except Exception as exc:
            res.errors.append(f"create_collection failed: {type(exc).__name__}: {exc}")
            return res

        # Record it: a Collection created and then forgotten is an orphan the
        # next promotion cannot see.
        ws_slug = self._registry.investigation_working_set_slug(investigation_slug)
        if not ws_slug:
            ws_slug = (self._registry.get_or_create_working_set(investigation_slug) or {}).get("slug", "")
        if ws_slug and res.collection_guid:
            self._registry.set_working_set_egeria_collection(ws_slug, res.collection_guid)

        # 3. ResourceList: Project -> Collection, through the outbox.
        #
        # Queued rather than called directly because a failure here used to be
        # appended to res.errors and forgotten — leaving a Project and a
        # Collection that both exist and are not linked, with nothing to notice.
        # Enqueue-then-drain inline keeps the happy path identical.
        from resource_explorer.egeria_outbox import (
            OutboxClients, drain_outbox, enqueue_resource_list,
        )

        rl_run_id = f"Investigation::{investigation_slug}::{res.collection_guid}::resource-list"
        rl_row = enqueue_resource_list(
            self._registry, investigation_slug, res.project_guid, res.collection_guid,
            run_id=rl_run_id,
        )
        drain_outbox(
            self._registry, OutboxClients(collection_manager=cm), lambda qn: "",
            limit=1, run_id=rl_run_id,
        )
        rl_rows = self._registry.list_outbox_elements(run_id=rl_run_id, limit=1)
        if private_owner:
            # A private investigation (2026-10-13): the inline drain above ran as the person who pressed promote
            # (its owner: their client `cm`, and its access check read the Project and Folio as them). "Attached"
            # only from a read-back as the owner; a row that did not land waits for the owner, never for the
            # service account, which cannot see a private investigation.
            self._settle_private_resource_list(cm, res, rl_rows[0] if rl_rows else None, private_owner)
        else:
            res.resource_list_linked = bool(rl_rows) and rl_rows[0]["status"] == "done"
            if not res.resource_list_linked:
                err = rl_rows[0]["last_error"] if rl_rows else "row disappeared"
                res.errors.append(
                    f"attach_collection failed ({err}) — queued for retry, "
                    f"see Admin → Publish Queue"
                )

        # 4. CollectionMembership per member, where the resource actually exists
        #    in Egeria. A member whose repo was never published has no asset to
        #    link — reported, never invented.
        self._link_members(cm, res, members, investigation_slug, refusal=refusal, private=bool(private_owner))
        return res

    def _settle_private_resource_list(self, cm, res: "PromotionResult", row: "dict | None", owner: str) -> None:
        """A private investigation's ResourceList, from proof: `resource_list_linked` only when a read-back as the
        owner (`cm` is their client) shows the Folio attached to the Project; otherwise the words of what did not
        happen, and the owner's Relink is the retry (the background drain will not attempt it)."""
        from resource_explorer.workflows.egeria_work_access import owner_waiting_words

        if row is not None and row.get("status") == "done":
            landed, why = resource_list_landed(cm, res.project_guid, res.collection_guid)
            if landed:
                res.resource_list_linked = True
            elif landed is False:
                res.errors.append(f"the working set was sent, but Egeria does not show it attached to the "
                                  f"Project (read back as {owner})")
            else:
                res.errors.append(f"the working set was sent, but it could not be read back as {owner} ({why}), "
                                  "so it is not confirmed attached")
            return
        err = str((row or {}).get("last_error") or "row disappeared")
        if (row or {}).get("status") == "dead":
            res.errors.append(f"attach_collection failed ({err}) — not retried")
            return
        waiting = owner_waiting_words(owner)
        res.errors.append(err if err.startswith(waiting) else
                          f"attach_collection failed ({err}) — {waiting}; press Relink to retry")

    def _reattach_private_resource_list(self, cm, res: "PromotionResult", slug: str, owner: str) -> None:
        """Relink by a private investigation's owner: make sure the Folio is attached to the Project, as them.

        Read back first; when it is attached and the newest row for it is not waiting, nothing is sent. Otherwise
        the newest row for this key carries the retry (a waiting one made due, a dead one revived; a done one gets
        a fresh row beside it), drained now as the owner, and settled from a read-back as them. This is
        the retry a private row waits for (`egeria_outbox.OutboxAwaitsOwner`), and how a row the old drain killed
        (live row 76009, 2026-10-13) is applied."""
        from resource_explorer.egeria_outbox import OutboxClients, drain_outbox, enqueue_resource_list

        key = f"ResourceList::{res.project_guid}::{res.collection_guid}"
        rows = [r for r in self._registry.list_outbox_rows_for_kinds("investigation", slug, ("resource_list",))
                if r.get("qualified_name") == key]          # newest first
        newest = rows[0] if rows else None
        waiting = newest is not None and newest.get("status") in ("pending", "failed", "dead")
        landed, _ = resource_list_landed(cm, res.project_guid, res.collection_guid)
        if landed and not waiting:
            res.resource_list_linked = True
            return
        # Which row carries the retry is decided here, from the NEWEST row for the key (review of 1841e4a8): a
        # waiting one is made due, a dead one revived; a done (or retired) one cannot be re-sent, so a ResourceList
        # detached in Egeria after its row was done gets a fresh row. `enqueue_outbox_element_once` would return an
        # older done row ahead of a newer dead one, and never re-attach a detached one.
        if newest is not None and newest.get("status") == "dead":
            row_id = int(newest["id"])
            self._registry.retry_outbox_element(row_id)
        elif waiting:
            row_id = int(newest["id"])
        else:
            row_id = enqueue_resource_list(
                self._registry, slug, res.project_guid, res.collection_guid,
                run_id=f"Investigation::{slug}::{res.collection_guid}::resource-list",
            )
        self._registry.make_outbox_row_due(row_id)
        drain_outbox(self._registry, OutboxClients(collection_manager=cm), lambda qn: "", element_id=row_id)
        self._settle_private_resource_list(cm, res, self._registry.get_outbox_element(row_id), owner)

    def _link_members(self, cm, res: "PromotionResult", members: list,
                      investigation_slug: str, *, refusal=None, private: bool = False) -> None:
        """Attach every member that HAS an asset to the working set, via the outbox.

        CollectionMembership is uni-link, so add_to_collection is an upsert:
        calling it for an already-attached member is safe and does not
        duplicate. That is what lets this be re-run — and, now, what makes a
        queued retry safe by the relationship's own semantics rather than by a
        qualifiedName lookup, since a membership has no qualifiedName to search
        for. Measured live 2026-08-25; the return value is the test.

        **Why this is the part that goes through the outbox** (design §6 step 5).
        The Project and the Collection are created synchronously above: their
        GUIDs are needed by the steps that follow and are returned in the
        PromotionResult, so they cannot become deferred work. Members are the
        plural part, and — exactly as with the repo publisher's annotations —
        the part that swallowed failures one at a time. A member whose
        add_to_collection failed was reported as unlinkable and then forgotten;
        nothing ever tried again.

        Two outcomes stay firmly distinct, because conflating them is how a
        real gap in an investigation reads as a transient glitch:

        - **No asset GUID** is not a failed write. The member's repo was never
          published, so there is nothing to attach. It is reported as
          unlinkable and NOT queued — retrying it forever would be retrying a
          fact about the catalog, not an error.
        - **A failed attach** is a failed write, and is now a durable row the
          scheduler retries with backoff.

        The happy path is unchanged: rows are enqueued and drained inline, so a
        successful promotion still links everything before returning.
        """
        from resource_explorer.egeria_outbox import (
            OutboxClients, collection_membership_qn, drain_outbox,
            enqueue_collection_members,
        )

        linkable: list[dict] = []
        for m in members:
            asset_guid, why_none = self._member_asset(m["entity_type"], m["entity_slug"])
            if not asset_guid:
                res.members_unlinkable.append({
                    **m, "reason": why_none,
                })
                continue
            # Brief Z2 round 2 / Z3: a member the access check refuses is not queued; its words are its reason.
            why = refusal({**m, "member_guid": asset_guid}) if refusal is not None else ""
            if why:
                res.members_unlinkable.append({**m, "reason": why})
                continue
            linkable.append({**m, "member_guid": asset_guid})

        if not linkable:
            return

        run_id = f"Investigation::{investigation_slug}::{res.collection_guid}"
        row_ids = enqueue_collection_members(
            self._registry, investigation_slug, res.collection_guid, linkable,
            run_id=run_id,
        )
        if private:
            # The owner's own press is the retry a private row waits for (`OutboxAwaitsOwner` parks it out of the
            # background drain's way): make the ones already queued due now, so this drain takes them.
            for row_id in row_ids:
                self._registry.make_outbox_row_due(row_id)
        summary = drain_outbox(
            self._registry, OutboxClients(collection_manager=cm), lambda qn: "",
            limit=max(len(linkable), 1), run_id=run_id,
        )

        # Report per member from what the drain actually did, not from what was
        # enqueued — an enqueued row is a promise, not a result.
        by_qn = {
            collection_membership_qn(res.collection_guid, m["member_guid"]): m
            for m in linkable
        }
        outstanding = {
            r["qualified_name"]: r
            for r in self._registry.list_outbox_elements(run_id=run_id, limit=len(linkable) * 2)
            if r["status"] != "done"
        }
        for qn, m in by_qn.items():
            row = outstanding.get(qn)
            if row is None:
                res.members_linked.append(m["entity_slug"])
            else:
                res.members_unlinkable.append({
                    **{k: v for k, v in m.items() if k != "member_guid"},
                    "reason": _unattached_reason(row),
                })
        if summary.get("failed") or summary.get("dead"):
            log.warning("Investigation %s: %d membership(s) queued for retry",
                        investigation_slug, summary.get("failed", 0) + summary.get("dead", 0))

    def relink_members(self, investigation_slug: str, *, refusal=None) -> "PromotionResult":
        """Re-attach an already-promoted investigation's members.

        promote() links members once, at promotion time, and refuses to run
        again on a bound investigation. So a member that had no asset GUID THEN
        — every one of them, if the investigation was promoted before its repos
        were published — has no way to be linked later, and the Egeria
        Collection stays permanently empty while RE shows the resources in
        scope.

        Hit for real after the 2026-08-26 redeploy: both investigations
        promoted with `members_linked: []` because no repo had been
        republished yet, and nothing existed to run afterwards.

        Idempotent: add_to_collection upserts on a uni-link relationship.

        `refusal(member) -> str` (Brief Z2 round 2, resync's per-resource access check; Brief Z3 the route's): a
        member it refuses is not attached and is reported unlinkable with those words ("not permitted · <reason>").
        It is asked only for a member that has an asset to attach, and the member dict carries `member_guid`.
        """
        res = PromotionResult()
        inv = self._registry.get_investigation(investigation_slug)
        if not inv:
            res.errors.append(f"investigation '{investigation_slug}' not found")
            return res
        res.project_guid = inv.get("egeria_project_guid") or ""
        if not res.project_guid:
            res.errors.append("not bound to an Egeria Project — promote or bind it first")
            return res
        if self._refuse_ownerless(inv, res, "relink_members"):
            return res
        if self._refuse_without_owner(inv, res):
            return res

        ws = self._registry.get_or_create_working_set(investigation_slug)
        res.collection_guid = (ws or {}).get("egeria_collection_guid") or ""
        if not res.collection_guid:
            res.errors.append(
                "no working set Collection — run ensure_working_set first"
            )
            return res
        owner = (inv.get("created_by") or "").strip() if self._is_private(inv) else ""
        if not owner:
            res.resource_list_linked = True

        try:
            _, cm = self._managers()
        except Exception as exc:
            res.errors.append(f"could not reach Egeria: {type(exc).__name__}: {exc}")
            return res

        if owner:
            # A private investigation: the owner's Relink is also the retry of its ResourceList (2026-10-13).
            self._reattach_private_resource_list(cm, res, investigation_slug, owner)
        members = self._registry.list_investigation_members(investigation_slug)
        self._link_members(cm, res, members, investigation_slug, refusal=refusal, private=bool(owner))
        return res

    def link_status(self, investigation_slug: str) -> dict:
        """Which members are linked to the working set, from the rows relink writes.

        Reads the SAME inputs `_link_members` decides from -- `_asset_guid` for
        "is there anything to attach" and the outbox row keyed by
        `collection_membership_qn` for "was it attached" -- so the answer cannot
        disagree with what Link members would do. No Egeria call: "linked" is
        the outbox's record of a finished attach (a `done` row), not a live
        check of Egeria's membership.

        Three states per member, and "could not tell" is never folded into
        "not linked" or into a zero count: with no working set Collection to
        key on, nothing can be said about any member.
        """
        from resource_explorer.egeria_outbox import collection_membership_qn

        inv = self._registry.get_investigation(investigation_slug)
        members = self._registry.list_investigation_members(investigation_slug)
        bound = bool((inv or {}).get("egeria_project_guid"))
        # Read-only lookup: a GET must not create the Folio row.
        ws_slug = self._registry.investigation_working_set_slug(investigation_slug) if bound else ""
        ws = self._registry.get_working_set(ws_slug) if ws_slug else None
        collection_guid = (ws or {}).get("egeria_collection_guid") or ""
        out: list[dict] = []
        rows_by_qn: dict = {}
        if collection_guid:
            for r in self._registry.list_outbox_elements(
                entity_slug=investigation_slug, limit=100000,
            ):
                if (r.get("entity_type") == "investigation"
                        and r.get("element_kind") == "collection_membership"):
                    # list is newest first; keep the newest row per key.
                    rows_by_qn.setdefault(r["qualified_name"], r)
        for m in members:
            base = {"entity_type": m["entity_type"], "entity_slug": m["entity_slug"]}
            if not bound:
                out.append({**base, "state": "could_not_tell",
                            "reason": "not bound to an Egeria Project"})
                continue
            if not collection_guid:
                out.append({**base, "state": "could_not_tell",
                            "reason": ("no working set Collection yet" if ws
                                       else "no working set yet")})
                continue
            asset_guid, why_none = self._member_asset(m["entity_type"], m["entity_slug"])
            if not asset_guid:
                out.append({**base, "state": "not_linked", "reason": why_none})
                continue
            row = rows_by_qn.get(collection_membership_qn(collection_guid, asset_guid))
            if row is None:
                out.append({**base, "state": "not_linked", "linkable": True,
                            "reason": "not attached yet — use Link members"})
            elif row["status"] == "done":
                out.append({**base, "state": "linked", "reason": ""})
            elif row["status"] in ("cancelled", "superseded"):
                out.append({**base, "state": "not_linked", "linkable": True,
                            "reason": "not attached — use Link members"})
            else:
                out.append({**base, "state": "not_linked", "linkable": True,
                            "reason": _unattached_reason(row, "pending")})
        counts = {s: sum(1 for o in out if o["state"] == s)
                  for s in ("linked", "not_linked", "could_not_tell")}
        # Linkable: an asset GUID exists and no finished attach is recorded -- the members `_link_members`
        # (what relink runs) would enqueue and drain. A member with no asset GUID is not linkable.
        counts["linkable"] = sum(1 for o in out if o.get("linkable"))
        return {"slug": investigation_slug, "bound": bound, "members": out, "counts": counts}

    def ensure_working_set(self, investigation_slug: str) -> PromotionResult:
        """Give an already-bound investigation its Egeria working set.

        Binding to an EXISTING Project (§1's second starting mode) previously
        recorded the GUID and stopped there — leaving the investigation with a
        Project but no Collection, so its membership had nowhere to go in
        Egeria. Creating the Project and creating the working set are the same
        need arriving by two different routes, so both routes now run this.

        Idempotent by the recorded collection GUID: called twice it does
        nothing the second time, rather than creating a second Collection and
        orphaning the first.
        """
        res = PromotionResult()
        inv = self._registry.get_investigation(investigation_slug)
        if not inv:
            res.errors.append(f"investigation '{investigation_slug}' not found")
            return res
        project_guid = inv.get("egeria_project_guid") or ""
        if not project_guid:
            res.errors.append("not bound to an Egeria Project — nothing to attach a working set to")
            return res
        res.project_guid = project_guid
        if self._refuse_ownerless(inv, res, "ensure_working_set"):
            return res
        if self._refuse_without_owner(inv, res):
            return res

        # A private investigation's ResourceList is "linked" only from a read-back as its owner (review of
        # 1841e4a8): never from the registry's record, nor from a direct attach that returned.
        owner = (inv.get("created_by") or "").strip() if self._is_private(inv) else ""
        ws = self._registry.get_or_create_working_set(investigation_slug)
        if ws and ws.get("egeria_collection_guid"):
            res.collection_guid = ws["egeria_collection_guid"]
            if not owner:
                res.resource_list_linked = True
                return res

        try:
            _, cm = self._managers()
        except Exception as exc:
            res.errors.append(f"could not reach Egeria: {type(exc).__name__}: {exc}")
            return res
        if owner and res.collection_guid:
            self._reattach_private_resource_list(cm, res, investigation_slug, owner)
            return res

        # Idempotent against EGERIA, not just against our own record. An
        # investigation promoted before the GUID was persisted has a real
        # Collection in Egeria and a blank column here; creating another would
        # orphan the first and silently split its membership across two. Ask
        # Egeria what is already attached before making anything.
        try:
            attached = cm.get_attached_collections(project_guid) or []
            for entry in attached if isinstance(attached, list) else []:
                guid = ((entry or {}).get("elementHeader") or {}).get("guid", "")
                if guid:
                    res.collection_guid = guid
                    res.resource_list_linked = True
                    self._registry.set_working_set_egeria_collection(ws["slug"], guid)
                    return res
        except Exception as exc:
            # Not fatal — an unreadable attachment list must not stop a
            # genuinely missing working set being made — but NOT silent either.
            # Failing to look is exactly when a duplicate Collection gets
            # created, so the caller has to be able to see that the check did
            # not happen. Recorded on the result, not just logged.
            res.errors.append(
                f"could not check for an existing working set "
                f"({type(exc).__name__}: {exc}) — if one already exists in Egeria "
                "this may have created a second"
            )
            log.warning("could not list attached collections for %s: %s", project_guid, exc)

        try:
            res.collection_guid = _create_typed_collection(
                cm, "Folio", inv["display_name"],
                "Everything in scope for this investigation.",
                qualified_name=f"Folio::Investigation::{investigation_slug}",
                anchor_guid=project_guid,
            )
        except Exception as exc:
            res.errors.append(f"create_collection failed: {type(exc).__name__}: {exc}")
            return res
        if owner:
            # Recorded first, so a ResourceList that does not land now is retried against THIS Folio, then attached
            # through the outbox (the owner's check) and settled from a read-back as the owner.
            self._registry.set_working_set_egeria_collection(ws["slug"], res.collection_guid)
            self._reattach_private_resource_list(cm, res, investigation_slug, owner)
            return res
        try:
            cm.attach_collection(project_guid, res.collection_guid)
            res.resource_list_linked = True
        except Exception as exc:
            res.errors.append(f"attach_collection failed: {type(exc).__name__}: {exc}")
        self._registry.set_working_set_egeria_collection(ws["slug"], res.collection_guid)
        return res

    def sync_project_properties(self, investigation_slug: str) -> PromotionResult:
        """Push a renamed/re-described investigation to its Egeria Project.

        Without this a rename silently drifts: the local record says one thing
        and the catalog another, about the same body of work. That is the
        two-sources-disagree failure this codebase keeps finding, and a rename
        is exactly when it starts.

        Not called automatically from the PATCH route — that route is a local
        edit and must not depend on Egeria being reachable to succeed. This is
        the deliberate follow-up.
        """
        res = PromotionResult()
        inv = self._registry.get_investigation(investigation_slug)
        if not inv:
            res.errors.append(f"investigation '{investigation_slug}' not found")
            return res
        guid = inv.get("egeria_project_guid") or ""
        if not guid:
            res.errors.append("not bound to an Egeria Project — nothing to sync")
            return res
        res.project_guid = guid
        if self._refuse_ownerless(inv, res, "sync_project_properties"):
            return res
        if self._refuse_without_owner(inv, res):
            return res
        try:
            pm, _ = self._managers()
        except Exception as exc:
            res.errors.append(f"could not reach Egeria: {type(exc).__name__}: {exc}")
            return res
        # Explicit body, NOT the display_name convenience parameter.
        # pyegeria's _async_update_project sends `name` where ProjectProperties
        # expects `displayName`, so the convenience path returns cleanly, logs
        # success, and changes nothing (PYEGERIA_ISSUES.md ISSUE-73, reproduced
        # live 2026-08-25). Its own create path uses displayName correctly, so
        # this is a spelling mismatch between the two, not a server behaviour.
        # pyegeria is not patched here — the body parameter is a supported API.
        body = {
            "class": "UpdateElementRequestBody",
            "properties": {
                "class": "ProjectProperties",
                "displayName": inv["display_name"],
                "description": inv.get("description") or "",
            },
        }
        try:
            pm.update_project(guid, body=body)
        except Exception as exc:
            res.errors.append(f"update_project failed: {type(exc).__name__}: {exc}")
            return res

        # Read back. A rename that reports success and changes nothing is the
        # exact failure above, so "no exception" is not evidence here.
        try:
            after = pm.get_project_by_guid(guid)
            props = (after or {}).get("properties", {}) if isinstance(after, dict) else {}
            if props.get("displayName") != inv["display_name"]:
                res.errors.append(
                    "update reported success but Egeria still shows "
                    f"{props.get('displayName')!r} — the rename did not apply"
                )
        except Exception as exc:
            res.errors.append(f"could not verify the rename applied: {type(exc).__name__}: {exc}")
        return res

    def sync_disposition_sets(self, investigation_slug: str) -> dict:
        """Create an Egeria WorkingSet per disposition IN USE, and link it.

        Only dispositions that actually have members: a WorkingSet carries a
        single Disposition, so pre-creating all six per investigation would fill
        the catalog with empty Collections that cannot be told apart from ones
        nobody has used.

        Each is a Collection subtype linked to the Project by its own
        ResourceList, with `resourceUse` naming the disposition — so the catalog
        answers "what is being tracked here" without reading membership.
        """
        out: dict = {"created": [], "skipped": [], "errors": []}
        inv = self._registry.get_investigation(investigation_slug)
        if not inv or not inv.get("egeria_project_guid"):
            out["errors"].append("not bound to an Egeria Project")
            return out
        project_guid = inv["egeria_project_guid"]
        words = ownerless_private_words(inv)
        if words:
            out["errors"].append(words)
            out["not_published"] = words
            record_ownerless_refusal(self._registry, inv, words, "sync_disposition_sets")
            return out
        waiting = owner_absent_words(inv)
        if waiting:
            out["errors"].append(waiting)
            return out
        try:
            _, cm = self._managers()
        except Exception as exc:
            out["errors"].append(f"could not reach Egeria: {type(exc).__name__}: {exc}")
            return out

        for disposition in self._registry.investigation_dispositions(investigation_slug):
            ws = self._registry.get_or_create_disposition_set(investigation_slug, disposition)
            if not ws:
                continue
            if ws.get("egeria_collection_guid"):
                out["skipped"].append(disposition)
                continue
            try:
                guid = _create_typed_collection(
                    cm, "WorkingSet", ws["display_name"],
                    f"Resources in this investigation with disposition '{disposition}'.",
                    # Slug-based, NOT derived from display_name. A qualifiedName
                    # built from a mutable label breaks the moment someone
                    # renames the investigation — which happened today — leaving
                    # an orphan whose name no longer matches anything, or a
                    # collision with the new one.
                    qualified_name=f"WorkingSet::Investigation::{investigation_slug}::{disposition}",
                )
                cm.attach_collection(project_guid, guid)
                self._registry.set_working_set_egeria_collection(ws["slug"], guid)
                out["created"].append({"disposition": disposition, "guid": guid})
            except Exception as exc:
                out["errors"].append(f"{disposition}: {type(exc).__name__}: {exc}")
        return out

    def _member_asset(self, entity_type: str, entity_slug: str) -> "tuple[str, str]":
        """(asset GUID, reason it is empty). THE one question "is there anything to link?", asked by promote and
        relink (`_link_members`) and by `link_status`, so the three cannot disagree. The GUID is the member's OWN
        asset as RE recorded it: the repo's project asset, the database's RelationalDatabase
        (`databases.egeria_asset_guid`), the file system's root FileFolder (`file_systems.egeria_asset_guid`)
        -- never the server or the host. "not in Egeria yet" is said only when that kind's own pointer is empty;
        a kind or slug RE cannot resolve has its own words (`resource_explorer.member_assets`)."""
        # The ONE per-kind lookup, shared with work lists (`work_lists.member_egeria_guid`): no second copy here.
        from resource_explorer.member_assets import MemberUnresolved, NotInEgeriaYet, member_egeria_guid

        try:
            return member_egeria_guid(self._registry, entity_type, entity_slug), ""
        except NotInEgeriaYet as exc:
            return "", str(exc)
        except MemberUnresolved as exc:
            return "", str(exc)

    def _asset_guid(self, entity_type: str, entity_slug: str) -> str:
        return self._member_asset(entity_type, entity_slug)[0]

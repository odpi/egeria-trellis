"""Delete an investigation (owner ruling 2026-10-21): permanent in Resource Explorer, ARCHIVED in Egeria.

Two choices for an investigation that was promoted to an Egeria Project:

* ``re_only``         "Delete in Resource Explorer only": the local investigation and everything that exists only for
                      it go; the Egeria Project, its working-set Collection and its journal Notes stay in Egeria.
* ``re_and_egeria``   "Delete in Resource Explorer, archive in Egeria": the Project is ARCHIVED (never deleted), and
                      then the local investigation goes. The value keeps its name (it is the route's ``egeria=true``).

An investigation that was never promoted has only the first choice.

Who: its owner (``investigations.created_by``) or a Portal admin or curator (``workflows.curate.CURATOR_ROLES``, the
role the rest of RE already honours as "may curate anything"). Nobody else; an ownerless investigation is an admin's.

How Egeria archives (``egeria`` source, main 05a5e982, read for this change):

* ``OpenMetadataStoreRESTServices.deleteMetadataElementInStore`` switches on the body's ``deleteMethod`` and routes
  ``ARCHIVE`` to ``archiveMetadataElementInStore``. The Project Manager view service's ``/projects/{guid}/delete``
  and the Collection Manager's delete both end in that same switch (``ProjectHandler.deleteProject`` and
  ``CollectionHandler.deleteCollection`` call ``openMetadataClient.deleteMetadataElementInStore`` with the caller's
  ``DeleteOptions``), and pyegeria's ``delete_project`` / ``delete_collection`` send a dict body through
  ``DeleteElementRequestBody`` unchanged, ``deleteMethod`` included.
* The archive adds the Memento classification through ``archiveBeanInRepository``, renames the qualifiedName to
  ``<qn>_archivedOn_<date>`` and stamps the whole operation with ONE ``lastOperationId``. Archiving an anchor
  archives its whole anchored graph (``collectArchiveTargets``), and the operation can be restored by that id.
  Notes and NoteLogs go with their anchor and are never archived or deleted separately.

The order, one element per call, as the signed-in person (``Caller()``), never a daemon, never a cascade:

    0. refuse while RE's ISSUE-117 archive/delete block is ON (``catalogue_gateway.issue_117_blocked``);
    1. READ ONLY: what is anchored to the Project (``get_anchored_element_graph``), and who made each element. What RE
       made is named as RE's; every other element is OTHER PEOPLE'S, listed by type, author and date. The confirm
       showed that list (``GET .../delete-preview``); the delete refuses, writing nothing, if it changed since;
    2. ``remove_from_collection`` for each member of RE's own Collections (the CollectionMembership only; the member's
       own asset is never touched);
    3. a Collection of RE's that is NOT anchored to the Project (the disposition sets are attached, not anchored) is
       archived on its own (``delete_collection`` with ``deleteMethod: ARCHIVE``); one anchored to the Project goes in
       the Project's operation and gets no call of its own;
    4. ``_retire_project``: ``delete_project`` with ``deleteMethod: ARCHIVE``, only for a Project RE made;
    5. every element the archive took is read back by GUID (``forLineage``), and each gets a proof row in
       ``catalogue_commit_proofs`` under ``investigation/<slug>``: the GUID, Egeria's ``lastOperationId``, the new
       qualifiedName and the date. Those rows outlive the investigation, so the history links and a restore stays
       possible.

A Project RE did not create (bound, not promoted) is left alone and reported. A NoteLog or Collection RE did not make
is left when the Project step would not take it, and is named as other people's when it would.

If an archive is refused or cannot be confirmed, everything stops: nothing is retried, the local investigation is
kept, and the result names what was archived and what remains, with Egeria's words. Pressing Delete again resumes
(an element already archived is skipped and its proof row is written if it has none).

The local side is ``ProjectRegistry.delete_investigation_local`` (one transaction, which also writes the activity row).
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable

log = logging.getLogger(__name__)

CHOICE_RE_ONLY = "re_only"
CHOICE_RE_AND_EGERIA = "re_and_egeria"
CHOICES = (CHOICE_RE_ONLY, CHOICE_RE_AND_EGERIA)

#: The proof word for an archive read back by GUID.
P_ARCHIVED = "archived"
P_ARCHIVE_REFUSED = "archive_refused"


def _issue_117_words() -> str:
    from resource_explorer.catalogue_gateway import ISSUE_117_WORDS

    return ISSUE_117_WORDS


#: Placeholder words (the WORDS table for the designer lives in the front end; these are the server's).
WORDS = {
    "sign_in": "Sign in to delete an investigation.",
    "not_yours": "Only the person who created this investigation, or a Portal admin or curator, can delete it.",
    "ownerless": "This investigation has no recorded owner, so only a Portal admin or curator can delete it.",
    "not_promoted": "This investigation was never promoted to Egeria, so there is nothing in Egeria to archive.",
    "publish_running": "A publish for this investigation is running now. Try again in a minute.",
    "project_not_ours": ("left in Egeria: this Project was already in Egeria when the investigation was linked to it, "
                         "so Resource Explorer did not create it"),
    "note_log_not_anchored": ("left in Egeria: this NoteLog is not anchored to the Project, so archiving the Project "
                              "does not take it"),
    "collection_not_ours": "left in Egeria: this Collection was not made by Resource Explorer for this investigation",
    "already_archived": "already archived in Egeria",
    "already_gone": "already gone from Egeria",
    "foreign_changed": ("What is attached to the Project changed since you looked, so nothing was archived. "
                        "Open Delete again to see the current list"),
    "local_failed": ("Egeria's side finished, but removing the investigation from Resource Explorer failed, so it "
                     "is still here. Pressing Delete again skips what is already archived in Egeria"),
    "archived_in_egeria": "archived in Egeria",
}


def archive_blocked_words() -> str:
    """The archive-cascade block's own sentence (``catalogue_gateway.ISSUE_117_WORDS``), unchanged."""
    return _issue_117_words()


class ArchiveBlocked(Exception):
    """The ISSUE-117 archive/delete block is ON: the Egeria choice is refused, nothing was changed anywhere."""

    def __init__(self, words: str):
        super().__init__(words)
        self.words = words


def archive_body() -> dict:
    """The body of an archive: the delete endpoint with ``deleteMethod: ARCHIVE``, and ``forLineage`` and
    ``forDuplicateProcessing`` true, the form RE's catalogue archive already proved live (the archive endpoint
    itself answers 500 on this build; see ``CatalogueGateway.delete_element``). A fresh dict on every call."""
    return {"class": "DeleteElementRequestBody", "deleteMethod": "ARCHIVE",
            "forLineage": True, "forDuplicateProcessing": True}


def read_body() -> dict:
    """A by-GUID read that also sees an archived element (a Memento is visible only with ``forLineage``)."""
    return {"class": "GetRequestBody", "forLineage": True, "forDuplicateProcessing": True}


def project_qualified_name(slug: str) -> str:
    """The qualifiedName ``EgeriaInvestigationPublisher.promote`` gives a Project it creates."""
    return f"Project::Investigation::{slug}"


def delete_rights(inv: dict, identity) -> tuple[bool, str]:
    """May this caller delete this investigation? ``(allowed, words)``; words are "" when allowed.

    ``identity`` is ``a2a_auth.caller()``: owner = the signed-in user whose id equals ``created_by``; admin = a
    signed-in user whose Portal role is in ``CURATOR_ROLES`` (the same override ``curation_access`` rule 2 uses)."""
    from resource_explorer.workflows.curate import CURATOR_ROLES

    if identity is None or getattr(identity, "auth_source", "") == "anonymous" or not getattr(identity, "user_id", ""):
        return False, WORDS["sign_in"]
    if (getattr(identity, "role", "") or "").lower() in CURATOR_ROLES:
        return True, ""
    owner = (inv.get("created_by") or "").strip()
    if owner and owner == identity.user_id:
        return True, ""
    return False, WORDS["not_yours"] if owner else WORDS["ownerless"]


@dataclass
class DeleteResult:
    slug: str
    choice: str
    #: True only when the local investigation was removed.
    local_deleted: bool = False
    #: True when the Egeria side was asked for and completed (always False for ``re_only``).
    egeria_done: bool = False
    #: Egeria elements archived and read back as Mementos: ``{kind, guid, name, operation_id, words, with_project}``.
    archived: list[dict] = field(default_factory=list)
    #: Egeria elements this delete meant to archive and did not: ``{kind, guid, name, why}``.
    remaining: list[dict] = field(default_factory=list)
    #: Collection memberships removed (the member's own element is untouched): ``{kind, guid, name, collection}``.
    unlinked: list[dict] = field(default_factory=list)
    #: Egeria elements deliberately not touched: ``{kind, guid, name, why}``.
    left: list[dict] = field(default_factory=list)
    #: Other people's items anchored to the Project, archived with it (named in the confirm):
    #: ``{kind, guid, name, author, created}``.
    foreign: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    local_counts: dict = field(default_factory=dict)
    activity_id: str = ""

    @property
    def ok(self) -> bool:
        return self.local_deleted and not self.remaining and not self.errors

    def as_dict(self) -> dict:
        return {"slug": self.slug, "choice": self.choice, "ok": self.ok, "local_deleted": self.local_deleted,
                "egeria_done": self.egeria_done, "archived": self.archived, "unlinked": self.unlinked,
                "remaining": self.remaining, "left": self.left, "foreign": self.foreign, "errors": self.errors,
                "local_counts": self.local_counts, "activity_id": self.activity_id}


class _Stop(Exception):
    """Stop the Egeria side: the words say why."""


@dataclass
class EgeriaHandles:
    pm: object   # ProjectManager
    cm: object   # CollectionManager
    me: object   # MetadataExpert


def caller_handles() -> EgeriaHandles:
    """The three pyegeria clients, as the signed-in person (``Caller()``); never the daemon."""
    from pyegeria import CollectionManager, MetadataExpert, ProjectManager

    from resource_explorer.egeria_clients import Caller, egeria_client

    clients = egeria_client(Caller(), purpose="investigation delete")
    return EgeriaHandles(pm=clients.of(ProjectManager), cm=clients.of(CollectionManager),
                         me=clients.of(MetadataExpert))


def collection_qualified_name(slug: str, record: dict) -> str:
    """The qualifiedName ``EgeriaInvestigationPublisher`` gave the Collection of one working set: the Folio's, or the
    disposition set's. Compared by equality; a record that fits neither has no expected name (so nothing matches)."""
    if record.get("collection_kind") == "folio":
        return f"Folio::Investigation::{slug}"
    disposition = (record.get("disposition") or "").strip()
    return f"WorkingSet::Investigation::{slug}::{disposition}" if disposition else ""


def _words_of(exc: BaseException) -> str:
    """Egeria's own words for a failure: the security refusal sentence, else the exception as pyegeria raised it."""
    from resource_explorer.egeria_outbox import _refusal_sentence, is_security_refusal

    if isinstance(exc, Exception) and is_security_refusal(exc):
        return f"refused by Egeria: {_refusal_sentence(exc)}"
    return f"{type(exc).__name__}: {' '.join(str(exc).split())[:400]}"


def _guid(el) -> str:
    if not isinstance(el, dict):
        return ""
    return str((el.get("elementHeader") or {}).get("guid") or el.get("guid") or "")


def _props(el) -> dict:
    p = el.get("properties") if isinstance(el, dict) else None
    return p if isinstance(p, dict) else {}


def _name(el) -> str:
    p = _props(el)
    return str(p.get("displayName") or p.get("qualifiedName") or "")


def _type_name(el) -> str:
    h = el.get("elementHeader") if isinstance(el, dict) else None
    t = (h or {}).get("type") if isinstance(h, dict) else None
    return str((t or {}).get("typeName") or "") if isinstance(t, dict) else ""


def _display(el) -> str:
    """A name for an anchored element: its displayName or qualifiedName, else (a Comment, a Rating) a clip of its text."""
    p = _props(el)
    name = p.get("displayName") or p.get("qualifiedName")
    if name:
        return str(name)
    text = p.get("commentText") or p.get("review") or p.get("description") or ""
    return " ".join(str(text).split())[:80]


def _versions(el) -> dict:
    if not isinstance(el, dict):
        return {}
    h = el.get("elementHeader") if isinstance(el.get("elementHeader"), dict) else {}
    for versions in ((h or {}).get("versions"), el.get("versions")):
        if isinstance(versions, dict) and versions:
            return versions
    return {}


def _author(el) -> str:
    """Who created it, when Egeria says (``versions.createdBy`` on the header or the element); '' otherwise."""
    return str(_versions(el).get("createdBy") or "")


def _created(el) -> str:
    """The day it was created (``versions.createTime``), as ``YYYY-MM-DD``; '' when Egeria states none."""
    from resource_explorer.catalogue_gateway import _iso_of

    return _iso_of(_versions(el).get("createTime"))[:10]


def _operation_id(el) -> str:
    return str(_versions(el).get("lastOperationId") or "")


def _elements(answer, what: str) -> list[dict]:
    """The elements of a pyegeria list answer. "No elements found" (a string) is an honest empty; any other
    answer (None, other text, a wrong shape) is "could not read", which stops the delete rather than meaning none."""
    from resource_explorer.egeria_absence import ABSENT, is_absent

    if isinstance(answer, list):
        return [e for e in answer if isinstance(e, dict) and _guid(e)]
    if isinstance(answer, str) and is_absent(answer) == ABSENT:
        return []
    raise _Stop(f"could not read {what} from Egeria (it answered {str(answer)[:200]!r}), so nothing was archived there")


def foreign_token(foreign: list[dict]) -> str:
    """A short fingerprint of the other-people's-items list the confirm showed ('' when there are none). The delete
    recomputes it before it writes anything and refuses if it differs."""
    guids = sorted({f["guid"] for f in foreign})
    return hashlib.sha1("\n".join(guids).encode()).hexdigest()[:16] if guids else ""  # noqa: S324 - not a secret


@dataclass
class Survey:
    """What the read-only pre-flight found. Nothing here was written."""
    project_guid: str
    project_ours: bool
    #: "active" | "archived" | "absent" (an archived Project is read with forLineage and carries a Memento)
    project_state: str
    #: elements anchored to the Project (the Project itself excluded), by GUID
    in_graph: set = field(default_factory=set)
    #: what RE made that the Project's archive takes: ``{kind, guid, name}``
    ours: list = field(default_factory=list)
    #: what others made that the Project's archive takes: ``{kind, guid, name, author, created}``
    foreign: list = field(default_factory=list)
    #: RE's own recorded Collections: ``{guid, name, record, qn, state, anchored}`` (only those RE made)
    collections: list = field(default_factory=list)
    left: list = field(default_factory=list)

    @property
    def will_archive_project(self) -> bool:
        return self.project_ours and self.project_state == "active"


class InvestigationDeleter:
    """One delete. Build with the registry and the caller's rights already checked; ``run`` does the rest."""

    def __init__(self, registry, *, handles_factory: "Callable[[], EgeriaHandles] | None" = None,
                 sleep: "Callable[[float], None] | None" = None, confirm_tries: int = 3):
        self._registry = registry
        # Looked up at call time (not bound as a default) so a test can replace the module's `caller_handles`.
        self._handles_factory = handles_factory or (lambda: caller_handles())
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))
        self._tries = confirm_tries

    # ── Egeria reads ────────────────────────────────────────────────────────────────────────────────────

    def _read_archived(self, me, guid: str, tries: "int | None" = None) -> "tuple[str | None, dict | None]":
        """``(state, element)`` for one GUID, read with ``forLineage`` so an archived element is visible:
        ``archived`` (a Memento is on it), ``active`` (readable, no Memento), ``absent`` (Egeria said there is no
        such element: the string or a typed not-found), or ``None`` (could not tell: 401/403, an unavailable server,
        a timeout, any other exception or answer, a Memento in a shape RE cannot read). Retried a few times: an
        archive can take a moment to show."""
        from resource_explorer.catalogue_gateway import _is_memento
        from resource_explorer.egeria_absence import ABSENT, is_absent

        verdict: "tuple[str | None, dict | None]" = (None, None)
        tries = self._tries if tries is None else tries
        for attempt in range(tries):
            try:
                found = me.get_metadata_element_by_guid(guid, body=read_body())
            except Exception as exc:  # noqa: BLE001 - classified: only a typed not-found is "absent"
                log.info("read of %s raised %s", guid, type(exc).__name__)
                verdict = ("absent", None) if is_absent(exc) == ABSENT else (None, None)
            else:
                if isinstance(found, str):
                    verdict = ("absent", None) if is_absent(found) == ABSENT else (None, None)
                elif isinstance(found, dict) and found:
                    try:
                        verdict = ("archived" if _is_memento(found) else "active", found)
                    except Exception:  # noqa: BLE001 - UnreadableClassification: never read as live
                        verdict = (None, found)
                else:
                    verdict = (None, None)
            if verdict[0] in ("archived", "absent"):
                return verdict
            if attempt + 1 < tries:
                self._sleep(1.0)
        return verdict

    def _state(self, me, guid: str) -> str:
        """One read, before a write: the state, or a stop when it cannot be told (never guessed)."""
        state, _ = self._read_archived(me, guid, tries=1)
        if state is None:
            raise _Stop(f"could not read {guid} from Egeria to check its state, so nothing was archived there")
        return state

    def _collection_qualified_name(self, cm, guid: str) -> "str | None":
        """The qualifiedName of a recorded Collection, read as the caller; None when it could not be read."""
        try:
            got = cm.get_collection_by_guid(guid, graph_query_depth=0)
        except Exception as exc:  # noqa: BLE001
            log.info("could not read Collection %s: %s", guid, type(exc).__name__)
            return None
        if isinstance(got, dict):
            qn = _props(got).get("qualifiedName")
            return str(qn) if qn else None
        return None

    def _read_graph(self, me, project_guid: str) -> list[dict]:
        graph = me.get_anchored_element_graph(project_guid)
        if isinstance(graph, dict) and isinstance(graph.get("element"), dict) and "anchoredElements" not in graph:
            graph = graph["element"]
        if not isinstance(graph, dict):
            raise _Stop(f"could not read what is anchored to the Project (Egeria answered {str(graph)[:200]!r}), "
                        "so nothing was archived")
        return [el for el in (graph.get("anchoredElements") or []) if _guid(el) and _guid(el) != project_guid]

    # ── the read-only pre-flight ────────────────────────────────────────────────────────────────────────

    def _survey(self, h: EgeriaHandles, inv: dict) -> Survey:
        """Read what is anchored to the Project and who made it. Reads only: no write is made here, so the confirm can
        call it first and the delete can call it again before its first write."""
        slug = inv["slug"]
        pm, cm, me = h.pm, h.cm, h.me
        project_guid = (inv.get("egeria_project_guid") or "").strip()
        ours_project = (inv.get("egeria_project_qualified_name") or "").strip() == project_qualified_name(slug)
        sv = Survey(project_guid=project_guid, project_ours=ours_project, project_state=self._state(me, project_guid))
        will = sv.will_archive_project

        anchored = self._read_graph(me, project_guid) if will else []
        sv.in_graph = {_guid(e) for e in anchored}
        mine: dict[str, dict] = {}

        if will:
            from resource_explorer.feedback_egeria import note_log_qualified_name

            ours_qn = note_log_qualified_name(project_guid)
            for lg in _elements(pm.get_attached_note_logs(project_guid), "the Project's NoteLogs"):
                if _props(lg).get("qualifiedName") != ours_qn:
                    continue                                     # someone else's: named below if it is anchored
                lg_guid = _guid(lg)
                # RE anchors each Note to the Project (not to the log), so the Notes are the Project's to archive
                # whether or not the log itself is anchored there.
                for n in _elements(pm.get_notes_for_note_log(lg_guid),
                                   f"the Notes in NoteLog {_name(lg) or lg_guid}"):
                    if _guid(n) in sv.in_graph:
                        mine[_guid(n)] = {"kind": "Note", "guid": _guid(n), "name": _name(n)}
                if lg_guid in sv.in_graph:
                    mine[lg_guid] = {"kind": "NoteLog", "guid": lg_guid, "name": _name(lg)}
                else:
                    sv.left.append({"kind": "NoteLog", "guid": lg_guid, "name": _name(lg),
                                    "why": WORDS["note_log_not_anchored"]})

        for c in self._registry.investigation_egeria_collections(slug):
            guid = c["guid"]
            state = self._state(me, guid)
            row = {"guid": guid, "name": c["working_set_slug"], "record": c, "qn": "", "state": state,
                   "anchored": False}
            if state != "active":
                sv.collections.append(row)                       # archived/absent: nothing to do, noted on the run
                continue
            qn = self._collection_qualified_name(cm, guid)
            if qn is None:
                raise _Stop(f"could not read the Collection {guid} to check that Resource Explorer made it, "
                            "so nothing was archived")
            row["qn"] = qn
            if qn != collection_qualified_name(slug, c):
                if guid not in sv.in_graph:                      # not anchored: really left alone
                    sv.left.append({"kind": "Collection", "guid": guid, "name": qn,
                                    "why": WORDS["collection_not_ours"]})
                continue                                         # anchored: counted with the others' items below
            row["name"] = qn
            row["anchored"] = will and guid in sv.in_graph
            sv.collections.append(row)
            if row["anchored"]:
                mine[guid] = {"kind": "Collection", "guid": guid, "name": qn}

        for el in anchored:
            g = _guid(el)
            if g in mine:
                sv.ours.append(mine[g])
            else:
                sv.foreign.append({"kind": _type_name(el) or "element", "guid": g, "name": _display(el),
                                   "author": _author(el), "created": _created(el)})
        if not ours_project:
            sv.left.append({"kind": "Project", "guid": project_guid,
                            "name": inv.get("egeria_project_qualified_name") or "", "why": WORDS["project_not_ours"]})
        return sv

    def preview(self, slug: str) -> dict:
        """What the confirm shows, from reads only. ``{ok, blocked, blocked_words, project_ours, project_state,
        foreign, ours, left, token, error}``; ``ok`` is False (with ``error``) when Egeria could not be read, in which
        case the Egeria choice must not be offered."""
        inv = self._registry.get_investigation(slug, user_id="")
        if not inv:
            raise LookupError(slug)
        if not (inv.get("egeria_project_guid") or "").strip():
            raise ValueError(WORDS["not_promoted"])
        from resource_explorer.catalogue_gateway import issue_117_blocked

        # An investigation names no database slug, so the archive-cascade block (lifted per database) stays ON for it.
        blocked = issue_117_blocked("")
        out = {"slug": slug, "ok": True, "blocked": blocked,
               "blocked_words": archive_blocked_words() if blocked else "", "error": "",
               "project_ours": False, "project_state": "", "foreign": [], "ours": [], "left": [], "token": ""}
        try:
            sv = self._survey(self._handles_factory(), inv)
        except _Stop as stop:
            out.update(ok=False, error=str(stop))
            return out
        except Exception as exc:  # noqa: BLE001 - clients could not be built, a read raised
            out.update(ok=False, error=f"could not read Egeria: {_words_of(exc)}")
            return out
        out.update(project_ours=sv.project_ours, project_state=sv.project_state, foreign=sv.foreign, ours=sv.ours,
                   left=sv.left, token=foreign_token(sv.foreign))
        return out

    # ── proof rows ──────────────────────────────────────────────────────────────────────────────────────

    def _prove(self, res: DeleteResult, kind: str, guid: str, name: str, element: dict, *, by: str,
               with_project: bool) -> None:
        """Record one archived element: Egeria's own answer (renamed qualifiedName, Memento, operation id), as a
        proof row by GUID (once: a resumed run writes none for a GUID that already has one) and in the result."""
        from resource_explorer.catalogue_gateway import _archived_at_of

        op = _operation_id(element)
        archived_qn = str(_props(element).get("qualifiedName") or "")
        when = _archived_at_of(element)
        day = (when or datetime.now(UTC).isoformat())[:10]
        words = f"{WORDS['archived_in_egeria']} · {day}"
        slug = res.slug
        have = {p["element_guid"] for p in self._registry.list_investigation_archive_proofs(slug)
                if p["proof"] == P_ARCHIVED}
        if guid not in have:
            self._registry.append_investigation_archive_proof(
                slug, proof=P_ARCHIVED, kind=kind, element_guid=guid, qualified_name=archived_qn, recorded_by=by,
                detail={"words": words, "operation_id": op, "name": name, "archived_qualified_name": archived_qn,
                        "archived_at": when, "with_project": with_project, "egeria_sentence": _memento_sentence(element)})
        res.archived.append({"kind": kind, "guid": guid, "name": name, "operation_id": op, "words": words,
                             "with_project": with_project})

    def _refused(self, res: DeleteResult, kind: str, guid: str, name: str, why: str, *, by: str) -> None:
        res.remaining.append({"kind": kind, "guid": guid, "name": name, "why": why})
        try:
            self._registry.append_investigation_archive_proof(
                res.slug, proof=P_ARCHIVE_REFUSED, kind=kind, element_guid=guid, recorded_by=by,
                detail={"egeria_sentence": why, "name": name})
        except Exception:  # noqa: BLE001 - the stop is the safety property; the row is its record
            log.exception("could not record the refused archive of %s", guid)

    # ── the writes ──────────────────────────────────────────────────────────────────────────────────────

    def _archive(self, res: DeleteResult, me, kind: str, guid: str, name: str, call: Callable[[str, dict], object],
                 *, by: str) -> None:
        """Archive ONE element and read it back by GUID. Raises ``_Stop`` (after recording it) on a refusal or when
        it cannot be confirmed archived. An element already archived (or already gone) is skipped: nothing is re-sent."""
        state, element = self._read_archived(me, guid, tries=1)
        if state == "archived":
            self._prove(res, kind, guid, name, element, by=by, with_project=False)
            res.archived[-1]["note"] = WORDS["already_archived"]
            return
        if state == "absent":
            res.archived.append({"kind": kind, "guid": guid, "name": name, "note": WORDS["already_gone"]})
            return
        if state is None:
            self._refused(res, kind, guid, name, "could not be read to check whether it is already archived", by=by)
            raise _Stop(f"could not read the {kind} {name or guid} to check whether it is archived")
        try:
            call(guid, archive_body())
        except Exception as exc:  # noqa: BLE001
            why = _words_of(exc)
            self._refused(res, kind, guid, name, why, by=by)
            raise _Stop(f"Egeria would not archive the {kind} {name or guid}: {why}") from exc
        after, element = self._read_archived(me, guid)
        if after == "archived":
            self._prove(res, kind, guid, name, element, by=by, with_project=False)
            return
        why = ("the archive was sent but Egeria still returns it as live" if after == "active"
               else "the archive was sent but Egeria could not be asked whether it is archived" if after is None
               else "the archive was sent but Egeria no longer returns it")
        self._refused(res, kind, guid, name, why, by=by)
        raise _Stop(f"could not confirm the {kind} {name or guid} is archived: {why}")

    def _unlink_members(self, res: DeleteResult, cm, collection_guid: str, collection_name: str) -> None:
        """Take every member out of a Collection of RE's before it is archived (unlinked first, per the owner's
        ruling): one ``remove_from_collection`` per member, which removes the CollectionMembership only; the member
        (a repo, a database, a file system: an asset that is not RE's) is never archived, deleted or changed, and
        ``cascade`` is never used. The membership list is re-read after each pass (a long list is paged), and a pass
        that unlinks nothing new stops the delete."""
        seen: set[str] = set()
        for _ in range(50):
            members = _elements(cm.get_collection_members(collection_guid), f"the members of {collection_name}")
            todo = [m for m in members if _guid(m) not in seen]
            if not members:
                return
            if not todo:
                names = ", ".join(_guid(m) for m in members[:5])
                res.remaining.append({"kind": "Collection", "guid": collection_guid, "name": collection_name,
                                      "why": f"members were unlinked but Egeria still lists: {names}"})
                raise _Stop(f"the Collection {collection_name} still lists members after they were unlinked "
                            f"({names}), so it was not archived")
            for m in todo:
                mg = _guid(m)
                try:
                    cm.remove_from_collection(collection_guid, mg)
                except Exception as exc:  # noqa: BLE001
                    why = _words_of(exc)
                    res.remaining.append({"kind": "CollectionMembership", "guid": mg, "name": _name(m) or mg,
                                          "collection": collection_guid, "why": why})
                    raise _Stop(f"Egeria would not unlink {_name(m) or mg} from the Collection "
                                f"{collection_name}: {why}") from exc
                seen.add(mg)
                res.unlinked.append({"kind": "CollectionMembership", "guid": mg, "name": _name(m) or mg,
                                     "collection": collection_guid})
        raise _Stop(f"the Collection {collection_name} still has members after 50 passes, so it was not archived")

    def _retire_project(self, res: DeleteResult, me, pm, sv: Survey, *, by: str) -> None:
        """The final Project action: ARCHIVE, never delete. Egeria's archive takes everything anchored to the Project
        in one operation (``collectArchiveTargets``), so what is anchored gets no call of its own; each of those
        elements is read back by GUID afterwards and gets its own proof row."""
        name = project_qualified_name(res.slug)
        self._archive(res, me, "Project", sv.project_guid, name,
                      lambda guid, body: pm.delete_project(guid, body=body), by=by)
        project_op = next((a.get("operation_id") for a in res.archived if a["guid"] == sv.project_guid), "")
        taken = ([(o["kind"], o["guid"], o["name"]) for o in sv.ours]
                 + [(f["kind"], f["guid"], f["name"]) for f in sv.foreign])
        failed = []
        for kind, guid, nm in taken:
            state, element = self._read_archived(me, guid)
            if state == "archived":
                self._prove(res, kind, guid, nm, element, by=by, with_project=True)
                res.archived[-1]["same_operation"] = bool(project_op) and res.archived[-1]["operation_id"] == project_op
            else:
                why = ("archiving the Project was confirmed, but Egeria still returns this as live" if state == "active"
                       else "archiving the Project was confirmed, but this could not be read to check it")
                self._refused(res, kind, guid, nm, why, by=by)
                failed.append(f"{kind} {guid}")
        if failed:
            raise _Stop("the Project is archived, but these were not confirmed archived with it: " + ", ".join(failed))

    # ── the Egeria side ─────────────────────────────────────────────────────────────────────────────────

    def _archive_in_egeria(self, res: DeleteResult, inv: dict, *, by: str, confirmed: str) -> None:
        h = self._handles_factory()
        pm, cm, me = h.pm, h.cm, h.me

        # 1. reads only, all of them before the first write.
        sv = self._survey(h, inv)
        res.left.extend(sv.left)
        res.foreign = list(sv.foreign)
        if foreign_token(sv.foreign) != (confirmed or ""):
            named = "; ".join(f"{f['kind']} {f['guid']}" + (f" ({f['name']})" if f["name"] else "")
                              + (f" by {f['author']}" if f["author"] else "") for f in sv.foreign) or "none now"
            raise _Stop(f"{WORDS['foreign_changed']} (attached now: {named})")

        # 2. unlink the members of RE's own Collections that are still live.
        live = [c for c in sv.collections if c["state"] == "active" and c["qn"]]
        for c in live:
            self._unlink_members(res, cm, c["guid"], c["name"])

        # 3. a Collection the Project does not anchor is archived on its own; an anchored one rides with the Project.
        for c in sv.collections:
            if c["state"] == "archived":
                _state, element = self._read_archived(me, c["guid"], tries=1)
                self._prove(res, "Collection", c["guid"], c["name"], element or {}, by=by, with_project=False)
                res.archived[-1]["note"] = WORDS["already_archived"]
            elif c["state"] == "absent":
                res.archived.append({"kind": "Collection", "guid": c["guid"], "name": c["name"],
                                     "note": WORDS["already_gone"]})
            elif c["qn"] and not c["anchored"]:
                self._archive(res, me, "Collection", c["guid"], c["name"],
                              lambda guid, body: cm.delete_collection(guid, body=body), by=by)

        # 4. the Project: only a Project RE made.
        if sv.will_archive_project:
            self._retire_project(res, me, pm, sv, by=by)
        elif sv.project_ours and sv.project_state == "archived":
            state, element = self._read_archived(me, sv.project_guid, tries=1)
            self._prove(res, "Project", sv.project_guid, project_qualified_name(res.slug), element or {}, by=by,
                        with_project=False)
            res.archived[-1]["note"] = WORDS["already_archived"]
        elif sv.project_ours:
            res.archived.append({"kind": "Project", "guid": sv.project_guid, "name": project_qualified_name(res.slug),
                                 "note": WORDS["already_gone"]})

    # ── the whole delete ────────────────────────────────────────────────────────────────────────────────

    def run(self, slug: str, choice: str, *, by: str, role: str = "", confirmed: str = "") -> DeleteResult:
        """Delete the investigation. Raises ``ValueError`` for a choice that cannot apply (unknown, or Egeria on an
        investigation with no Project), ``LookupError`` when it is not there and ``ArchiveBlocked`` while the
        archive-cascade block is ON; everything else is in the result. ``confirmed`` is the ``token`` the delete-preview
        returned (the other-people's-items list the person saw)."""
        if choice not in CHOICES:
            raise ValueError(f"unknown choice {choice!r}; valid: {list(CHOICES)}")
        # Read as a service reader: rights were decided by the caller (it needs the row even for an admin who
        # could not otherwise see a private one); the row's existence is not in question here.
        inv = self._registry.get_investigation(slug, user_id="")
        if not inv:
            raise LookupError(slug)
        project_guid = (inv.get("egeria_project_guid") or "").strip()
        if choice == CHOICE_RE_AND_EGERIA and not project_guid:
            raise ValueError(WORDS["not_promoted"])
        if choice == CHOICE_RE_AND_EGERIA:
            from resource_explorer.catalogue_gateway import issue_117_blocked

            if issue_117_blocked(""):      # no database slug to name: blocked (the override is per database)
                log.warning("investigation archive refused by archive-cascade block: %s", slug)
                raise ArchiveBlocked(archive_blocked_words())      # before anything is changed or read
        res = DeleteResult(slug=slug, choice=choice)

        # The very first step, in one transaction: refuse (InvestigationPublishRunning, the route's 409) if a drain
        # is writing one of this investigation's publish rows, else cancel the ones still waiting so none starts
        # while this runs. Nothing has been changed anywhere when it refuses.
        self._registry.begin_investigation_delete(slug)

        if choice == CHOICE_RE_AND_EGERIA:
            try:
                self._archive_in_egeria(res, inv, by=by, confirmed=confirmed)
                res.egeria_done = True
            except _Stop as stop:
                res.errors.append(str(stop))
            except Exception as exc:  # noqa: BLE001 - clients could not be built, a read raised, ...
                res.errors.append(f"the Egeria side stopped: {_words_of(exc)}")
            if res.errors:
                # Local investigation kept. The activity row says what happened; the screen gets the same.
                self._write_activity(res, inv, by=by, role=role, deleted_locally=False)
                return res

        counts_activity = self._activity(res, inv, by=by, role=role, deleted_locally=True)
        try:
            res.local_counts = self._registry.delete_investigation_local(slug, counts_activity)
        except Exception as exc:  # noqa: BLE001 - includes a drain that claimed a row since the first step
            # Egeria may be fully archived by now. The local transaction rolled back, so the investigation is still
            # here; say so in words, and record in its OWN transaction what Egeria lost (the row that went with
            # the failed transaction is gone).
            log.exception("local delete of %s failed", slug)
            res.errors.append(f"{WORDS['local_failed'] if res.egeria_done else 'The investigation was not deleted'}"
                              f": {_words_of(exc)}")
            self._write_activity(res, inv, by=by, role=role, deleted_locally=False)
            return res
        res.activity_id = counts_activity.id
        res.local_deleted = True
        return res

    # ── the activity row ────────────────────────────────────────────────────────────────────────────────

    def _activity(self, res: DeleteResult, inv: dict, *, by: str, role: str, deleted_locally: bool):
        import json

        from resource_explorer.registry import ActivityEntry

        choice_words = ("Resource Explorer only" if res.choice == CHOICE_RE_ONLY
                        else "Resource Explorer, archived in Egeria")
        guid = (inv.get("egeria_project_guid") or "").strip()
        if res.choice == CHOICE_RE_ONLY:
            behind = ([{"kind": "Project", "guid": guid, "name": inv.get("egeria_project_qualified_name") or ""}]
                      + [{"kind": "Collection", "guid": c["guid"], "name": c["working_set_slug"]}
                         for c in self._registry.investigation_egeria_collections(inv["slug"])]) if guid else []
        else:
            behind = [dict(r) for r in res.remaining] + [dict(r) for r in res.left]
        if deleted_locally:
            summary = (f"deleted '{inv.get('display_name') or inv['slug']}' in {choice_words}"
                       f" · by {by or 'unknown'}"
                       + (f" · {len(res.archived)} Egeria element(s) archived" if res.choice == CHOICE_RE_AND_EGERIA
                          else "")
                       + (f" · {len(behind)} left in Egeria" if behind else ""))
            status = "ok"
        else:
            where = ("in Resource Explorer after Egeria's side finished" if res.egeria_done
                     else "in Resource Explorer" if res.choice == CHOICE_RE_ONLY else "in Egeria")
            summary = (f"delete of '{inv.get('display_name') or inv['slug']}' stopped {where} · by {by or 'unknown'}"
                       f" · {len(res.archived)} archived in Egeria, {len(res.remaining)} remain; "
                       "the investigation was kept")
            status = "error"
        detail = {"event": "investigation_delete", "choice": res.choice, "by": by, "role": role,
                  "deleted_locally": deleted_locally, "egeria_project_guid": guid,
                  "egeria_archived": res.archived, "egeria_unlinked": res.unlinked,
                  "egeria_remaining": res.remaining, "egeria_left": res.left, "egeria_foreign": res.foreign,
                  "left_behind_in_egeria": behind, "errors": res.errors}
        return ActivityEntry(
            id=str(uuid.uuid4()), ts=datetime.now(UTC).isoformat(), operation="delete", intent="enrichment",
            entity_type="investigation", entity_slug=inv["slug"], entity_name=inv.get("display_name") or "",
            status=status, summary=summary, detail=json.dumps(detail),
            items=[{"kind": d["kind"], "guid": d["guid"], "state": "archived"} for d in res.archived]
            + [{"kind": d["kind"], "guid": d["guid"], "state": "unlinked"} for d in res.unlinked]
            + [{"kind": d["kind"], "guid": d["guid"],
                "state": ("left" if d in res.left or res.choice == CHOICE_RE_ONLY else "remains")} for d in behind])

    def _write_activity(self, res: DeleteResult, inv: dict, *, by: str, role: str, deleted_locally: bool) -> None:
        try:
            entry = self._activity(res, inv, by=by, role=role, deleted_locally=deleted_locally)
            self._registry.write_activity(entry)
            res.activity_id = entry.id
        except Exception:  # noqa: BLE001 - the stop is the safety property; the row is its record
            log.exception("could not record the stopped delete of %s", inv.get("slug"))


def _memento_sentence(element: dict) -> str:
    """Egeria's own description of the archive, when its Memento carries one (``archiveProcess``), else ''."""
    from resource_explorer.catalogue_gateway import UnreadableClassification, classifications_named

    try:
        found = classifications_named(element, "Memento")
    except UnreadableClassification:
        return ""  # a Memento field that is not a classification: no sentence to quote (the archive itself is read elsewhere)
    for c in found:
        if not isinstance(c, dict):
            continue
        cp = c.get("classificationProperties") or c.get("properties") or {}
        if isinstance(cp, dict):
            text = cp.get("archiveProcess") or (cp.get("propertiesAsStrings") or {}).get("archiveProcess")
            if text:
                return " ".join(str(text).split())[:400]
    return ""

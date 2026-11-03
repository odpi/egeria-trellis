"""Ratings and notes go to Egeria as the person who wrote them (Brief F, 2026-10-11).

The project owner, 2026-10-11, verbatim where it matters:

* "It goes under the user who is logged in and entering the ratings etc. It is also possible for another user to
  login and evaluate the same resource and put in different feedback."
* "Ratings and notes should be public too."
* Notes are never edited: a correction is a later note, so RE never updates or deletes an Egeria Note.

What goes to Egeria, and through what:

    a person's rating (their newest rated entry in "What people say")
                         -> an Egeria Rating on the asset: RE's stars -> StarRating, RE's message -> review.
                            One rating per user per element is Egeria's own model (FeedbackHandler.java:56-100
                            finds the caller's existing Rating by `createdBy` and updates it), so a newer rating by
                            the same person replaces theirs and two people's ratings coexist.
                                                                         outbox kind `feedback_rating`
    each journal entry  -> one Note in the asset's NoteLog `NoteLog::RE::<asset GUID>` (found by qualifiedName,
                           created only when absent; a duplicate-name refusal adopts the one that won). The Note's
                           qualifiedName is `Note::RE::journal::<entry id>`, so a re-publish finds it, never adds a
                           second.                                       outbox kind `feedback_note`

**Never an update or a delete.** Nothing here calls update_note, remove_note, remove_note_log,
remove_rating_from_element or any element delete; a test fails if it does. A rating "replacing" an earlier one is
Egeria's own upsert of the caller's Rating, not an RE update.

**Authorship is the hard part.** Egeria records the CALLING user as the author (`createdBy`), so a feedback row is
only ever sent inside its author's own Publish request, as that person (`Caller()`):

* the rows of one person live under their own run id (`run_id_for(..., by)`), so their drain claims only theirs;
* the background (Daemon) drain never claims a feedback row (`egeria_outbox` excludes `PERSON_ONLY_KINDS` from
  every non-Caller claim), and a Caller drain applies one only when the row's `by` IS the caller;
* a failure, a refusal, or Egeria unreachable leaves the row dead with its reason ("not sent · <reason>"); it is
  never re-sent on its own, only by that person's next Publish (a NEW row).

**State comes from persisted rows** (the journal / feedback rows RE keeps, and the outbox rows): `feedback_plan`
is the one function that says what each entry is in Egeria and what the viewer's Publish would send; the band
draws it and `publish_my_feedback` sends exactly what it names.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

RATING_KIND = "feedback_rating"
NOTE_KIND = "feedback_note"
KINDS = (RATING_KIND, NOTE_KIND)
#: Outbox kinds only their author may send (the drain enforces it, egeria_outbox).
PERSON_ONLY_KINDS = frozenset(KINDS)

#: Item states. The band draws a cue for each and the word `word_for` gives.
SAVED = "saved"                  # the viewer's own, not yet in Egeria: their next Publish sends it
WAITING = "waiting"              # someone else's, not yet in Egeria: only THEIR Publish sends it
SENDING = "sending"              # a row is queued or being sent
PUBLISHED = "published"          # a done row
NOT_SENT = "not_sent"            # the last send failed (Egeria refused, failed or could not be reached)
NOT_PERMITTED = "not_permitted"  # curation access (or Egeria's security) said no
REPLACED = "replaced"            # an older rating by the same person: their newer one is the one in Egeria
KEPT = "kept"                    # stays in RE: a comment with no stars, or an entry with no author

#: Egeria's StarRating enum, by RE's star count (frameworks/openmetadata/enums/StarRating.java:25-50).
STAR_RATING = {0: "NOT_RECOMMENDED", 1: "ONE_STAR", 2: "TWO_STARS", 3: "THREE_STARS", 4: "FOUR_STARS",
               5: "FIVE_STARS"}

#: The Egeria operations each write needs, as Egeria checks them (egeria af400039c2):
#:   * a NEW Rating is an element anchored to the asset (FeedbackHandler.java:73-88 sets anchorGUID = the
#:     element): OpenMetadataAPIAnchorHandler.java:357 calls validateUserForAnchorMemberCreate, which is CREATE
#:     on the anchor's zones with the caller as maintainer (OpenMetadataAccessSecurityConnector.java:1965-1990);
#:     its AttachedRating parent link is made unchecked (MetadataElementHandler.java:2229-2313,
#:     uncheckedLinkElementToElement);
#:   * an UPDATE of the caller's own Rating (a newer rating) goes through validateAnchorEntity, where a Rating is a
#:     "feedback entity" (OpenMetadataAPIGenericHandler.java:670-686), so it is validateUserForAnchorAddFeedback:
#:     ADD_FEEDBACK on the anchor with the caller as maintainer (connector :2224-2250);
#:   * a NoteLog and a Note are NOT feedback types to Egeria's handlers (that list is InformalTag, Comment, Rating,
#:     Like: OpenMetadataAPIGenericHandler.java:672-675), so they are governed as anchored members: CREATE on the
#:     anchor (the asset; RE anchors each Note to the asset, not to the log).
#: Feedback is therefore NOT a property update: no UPDATE_PROPERTIES is asked for.
RATING_OPERATIONS = ("CREATE", "ADD_FEEDBACK")
NOTE_OPERATIONS = ("CREATE",)
OPERATIONS = {"rating": RATING_OPERATIONS, "note": NOTE_OPERATIONS}

NOT_PERMITTED_PREFIX = "not permitted · "
#: Round 4: the note a refused (never sent) feedback row is born cancelled with.
REFUSED_NOTE = "cancelled: " + NOT_PERMITTED_PREFIX
#: Round 5: access could not be READ at the press: not a refusal. Born cancelled (never claimable), read as
#: "not sent · couldn't check access", and re-sent by the person's next Publish.
UNREAD_NOTE = "cancelled: not sent · couldn't check access"
UNREACHED = "Egeria could not be reached; nothing was sent"
NOT_AUTHOR = "feedback is written only by its author, in their own Publish"


def run_id_for(entity_type: str, slug: str, by: str) -> str:
    """One person's feedback rows for one resource: their drain claims these and no one else's."""
    return f"Feedback::{entity_type}::{slug}::{by}"


def rating_key(element_guid: str, by: str) -> str:
    """The outbox identity of one person's Rating on one element (Egeria keeps one per user per element)."""
    return f"Rating::{element_guid}::{by}"


def note_qualified_name(entry_id: str) -> str:
    """The Note's qualifiedName in Egeria, and its outbox key: one journal entry, one Note, ever."""
    return f"Note::RE::journal::{entry_id}"


def note_log_qualified_name(element_guid: str) -> str:
    return f"NoteLog::RE::{element_guid}"


# ── the plan: one function decides each entry's state and what the viewer's Publish sends ──────────────────

@dataclass
class FeedbackItem:
    kind: str                 # "rating" | "note"
    id: str                   # resource_feedback.id | resource_journal.id
    author: str               # "" = unsigned
    mine: bool
    state: str
    reason: str = ""
    action: str = ""          # "send": the viewer's next Publish sends it
    row_id: int | None = None
    published_at: str = ""
    egeria_guid: str = ""

    @property
    def word(self) -> str:
        return word_for(self.state, self.author, self.reason, self.published_at, self.kind)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["word"] = self.word
        return d


def word_for(state: str, author: str, reason: str = "", published_at: str = "", kind: str = "") -> str:
    """The short word beside the cue (the brief's words)."""
    if state == PUBLISHED:
        return f"published {published_at[5:10]}" if len(published_at) >= 10 else "published"
    if state == SAVED:
        return "saved · will publish"
    if state == WAITING:
        return f"{author}'s · waiting for them"
    if state == SENDING:
        return "sending"
    if state == NOT_SENT:
        return f"not sent · {reason}" if reason else "not sent"
    if state == NOT_PERMITTED:
        return f"not permitted · {reason}" if reason else "not permitted"
    if state == REPLACED:
        return "replaced by a newer rating"
    if state == KEPT:
        return "kept in RE · unsigned" if not author else (
            "kept in RE · no rating" if kind == "rating" else "kept in RE")
    return state


def _payload(row: dict) -> dict:
    try:
        p = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return p if isinstance(p, dict) else {}


def _failure(row: dict) -> tuple[str, str]:
    """(state, reason) of a dead row: RE's access refusal or Egeria's security say "not permitted"."""
    from resource_explorer.egeria_outbox import REFUSED_BY_SECURITY

    err = " ".join(str(row.get("last_error") or "").split())
    if err.startswith(REFUSED_NOTE):
        return NOT_PERMITTED, err[len(REFUSED_NOTE):]
    if err.startswith(UNREAD_NOTE):
        return NOT_SENT, err[len("cancelled: not sent · "):]
    if err.startswith(NOT_PERMITTED_PREFIX):
        return NOT_PERMITTED, err[len(NOT_PERMITTED_PREFIX):]
    if err.startswith(REFUSED_BY_SECURITY):
        return NOT_PERMITTED, err
    return NOT_SENT, err


def _from_rows(rows: list[dict], mine: bool) -> dict:
    """State and action for one entry from its own rows (newest first)."""
    if not rows:
        return {"state": SAVED if mine else WAITING, "action": "send" if mine else ""}
    r = rows[0]
    status = r["status"]
    if status == "done":
        return {"state": PUBLISHED, "row_id": r["id"], "published_at": r.get("completed_at") or "",
                "egeria_guid": r.get("egeria_guid") or ""}
    if status in ("pending", "running"):
        return {"state": SENDING, "row_id": r["id"]}
    state, reason = _failure(r)                        # dead (or failed: person-only rows never back off)
    if not mine:
        return {"state": WAITING, "row_id": r["id"], "reason": reason}
    return {"state": state, "reason": reason, "row_id": r["id"], "action": "send"}


def feedback_plan(registry, entity_type: str, slug: str, *, viewer: str = "",
                  asset_guid: str | None = None) -> dict:
    """THE decision: per rating entry and per journal entry, what it is in Egeria and what `viewer`'s Publish
    sends. Read-only (no Egeria call). `asset_guid` overrides RE's cached GUID (the press that just created the
    asset). With no asset yet, the viewer's own entries are "saved · will publish": the Publish creates the asset
    first."""
    from resource_explorer.curation_egeria import element_guid
    from resource_explorer.journal import Journal

    guid = (asset_guid if asset_guid is not None else element_guid(registry, entity_type, slug)) or ""
    by_rating: dict[str, list[dict]] = {}
    by_note: dict[str, list[dict]] = {}
    if guid:
        for r in registry.list_outbox_rows_for_kinds(entity_type, slug, KINDS):
            refused = r["status"] == "cancelled" and str(r.get("last_error") or "").startswith((REFUSED_NOTE,
                                                                                                UNREAD_NOTE))
            if r["status"] in ("cancelled", "superseded") and not refused:
                continue
            p = _payload(r)
            if (p.get("element_guid") or "") != guid:
                continue                      # history for an asset that is no longer this resource's
            if r["element_kind"] == RATING_KIND and p.get("feedback_id"):
                by_rating.setdefault(str(p["feedback_id"]), []).append(r)
            elif r["element_kind"] == NOTE_KIND and p.get("entry_id"):
                by_note.setdefault(str(p["entry_id"]), []).append(r)

    feedback = registry.list_resource_feedback(entity_type, slug)          # newest first
    newest_rated: dict[str, str] = {}
    for f in feedback:
        if f.get("author") and f.get("rating") not in (None, "") and f["author"] not in newest_rated:
            newest_rated[f["author"]] = f["id"]
    current = {fid: FeedbackItem("rating", fid, author, bool(viewer) and author == viewer,
                                 **_from_rows(by_rating.get(fid, []), bool(viewer) and author == viewer))
               for author, fid in newest_rated.items()}
    # Round 2: an older rating is "replaced" only once the newer one is in Egeria. Until then the newest of the
    # person's older ratings that DID land (a done row) is still what Egeria holds: it keeps "published MM-DD".
    still_in_egeria: dict[str, str] = {}
    for f in feedback:
        author = f.get("author") or ""
        if (author and f.get("rating") not in (None, "") and f["id"] != newest_rated.get(author)
                and author not in still_in_egeria and current[newest_rated[author]].state != PUBLISHED
                and any(r["status"] == "done" for r in by_rating.get(f["id"], []))):
            still_in_egeria[author] = f["id"]
    ratings: list[FeedbackItem] = []
    for f in feedback:
        author = f.get("author") or ""
        mine = bool(viewer) and author == viewer
        if not author or f.get("rating") in (None, ""):
            ratings.append(FeedbackItem("rating", f["id"], author, mine, KEPT))
        elif f["id"] in current:
            ratings.append(current[f["id"]])
        elif still_in_egeria.get(author) == f["id"]:
            done = [r for r in by_rating[f["id"]] if r["status"] == "done"]
            ratings.append(FeedbackItem("rating", f["id"], author, mine, **_from_rows(done, mine)))
        else:
            ratings.append(FeedbackItem("rating", f["id"], author, mine, REPLACED))

    notes: list[FeedbackItem] = []
    for e in Journal(registry).entries(entity_type, slug):                 # newest first
        author = e.get("author") or ""
        mine = bool(viewer) and author == viewer
        if not author:
            notes.append(FeedbackItem("note", e["id"], author, mine, KEPT))
        else:
            notes.append(FeedbackItem("note", e["id"], author, mine, **_from_rows(by_note.get(e["id"], []), mine)))
    return {"entity_type": entity_type, "slug": slug, "element_guid": guid, "in_egeria": bool(guid),
            "viewer": viewer, "ratings": ratings, "notes": notes}


def to_send(plan: dict) -> list[FeedbackItem]:
    """What the viewer's Publish sends: their own entries the plan names, ratings first."""
    return [i for i in plan["ratings"] + plan["notes"] if i.mine and i.action == "send"]


def plan_as_dict(plan: dict) -> dict:
    return {**plan, "ratings": [i.as_dict() for i in plan["ratings"]], "notes": [i.as_dict() for i in plan["notes"]],
            "to_send": len(to_send(plan)),
            "waiting_for_others": sum(1 for i in plan["ratings"] + plan["notes"] if i.state == WAITING)}


# ── the write: queue what the plan names for the presser, then drain it as them ───────────────────────────

def _display_name(registry, entity_type: str, slug: str) -> str:
    from resource_explorer.curation_egeria import _entity

    if entity_type == "investigation":
        return str((registry.get_investigation(slug) or {}).get("display_name") or slug)
    try:
        ent = _entity(registry, entity_type, slug)
    except Exception:  # noqa: BLE001
        ent = None
    return str(getattr(ent, "display_name", "") or slug)


def entry_note_text(entry: dict) -> str:
    """What a journal entry sends to Egeria as its Note text: the derived body, then (owner 2026-10-22, "promote the
    note too") the person's own note as a separate trailing paragraph, never merged into the derived text. An entry
    with no note is sent exactly as before."""
    body = entry.get("body") or ""
    note = (entry.get("note") or "").strip()
    if not note:
        return body
    return f"{body}\n\nNote ({entry.get('author') or 'unsigned'}): {note}"


def _payload_for(registry, entity_type: str, slug: str, item: FeedbackItem, guid: str, by: str,
                 feedback: dict, entries: dict) -> tuple[str, str, dict]:
    base = {"entity_type": entity_type, "entity_slug": slug, "element_guid": guid, "by": by}
    if item.kind == "rating":
        f = feedback[item.id]
        return RATING_KIND, rating_key(guid, by), {
            **base, "feedback_id": item.id, "stars": int(f["rating"]), "review": f.get("message") or "",
            "rated_at": f.get("created_at") or ""}
    e = entries[item.id]
    return NOTE_KIND, note_qualified_name(item.id), {
        **base, "entry_id": item.id, "body": entry_note_text(e), "written_at": e.get("written_at") or "",
        "compile_id": e.get("compile_id") or "",
        "note_qualified_name": note_qualified_name(item.id),
        "note_log_qualified_name": note_log_qualified_name(guid),
        "resource_name": _display_name(registry, entity_type, slug)}


def publish_my_feedback(registry, entity_type: str, slug: str, asset_guid: str, presser: str, *,
                        identity=None, reader=None) -> dict:
    """The presser's pending ratings and notes, sent in their own Publish, as them. Never raises for an Egeria
    failure: every item's outcome is on its row, and the answer is the plan as re-read afterwards.

    Nothing is sent unless the identity this request acts as IS the presser (a Caller): a Daemon, another person
    or no caller sends nothing (`refused`). Each kind is checked with Brief Z's `curation_access` for the
    operations Egeria checks for it (`OPERATIONS`); a refused item gets a dead row "not permitted · <reason>"
    (its state is read from that row), and the rest are queued and drained inline."""
    from resource_explorer.egeria_outbox import drain_outbox

    presser = (presser or "").strip()
    if identity is None:
        try:
            from resource_explorer.egeria_clients import current_principal

            identity = current_principal()
        except PermissionError as exc:
            return {**plan_as_dict(feedback_plan(registry, entity_type, slug, viewer=presser)),
                    "refused": f"{NOT_AUTHOR} ({exc})", "queued": [], "drain": {}}
    if not presser or getattr(identity, "kind", "") != "caller" or (identity.user_id or "") != presser:
        return {**plan_as_dict(feedback_plan(registry, entity_type, slug, viewer=presser)),
                "refused": NOT_AUTHOR, "queued": [], "drain": {}}
    guid = asset_guid or ""
    plan = feedback_plan(registry, entity_type, slug, viewer=presser, asset_guid=guid)
    items = to_send(plan)
    # The presser's rows still waiting from an earlier press (a request that ended before its drain, a dead row an
    # admin revived): this press drains them too, as them. Nothing else ever does.
    waiting = [i.row_id for i in plan["ratings"] + plan["notes"] if i.mine and i.state == SENDING and i.row_id]
    if not guid or not (items or waiting):
        return {**plan_as_dict(plan), "refused": "", "queued": [], "drain": {},
                "skipped": "" if guid else "the resource is not in Egeria, so nothing was sent"}

    from resource_explorer.journal import Journal
    from resource_explorer.workflows.curate import curation_access

    feedback = {f["id"]: f for f in registry.list_resource_feedback(entity_type, slug)}
    entries = {e["id"]: e for e in Journal(registry).entries(entity_type, slug)}
    decisions: dict[str, str] = {}
    run_id = run_id_for(entity_type, slug, presser)
    queued: list[int] = []
    refused: list[dict] = []
    unread: list[dict] = []
    for item in items:
        if item.kind not in decisions:
            from resource_explorer.workflows.curate import BASIS_UNREADABLE

            d = curation_access(registry, entity_type, slug, "", operations=OPERATIONS[item.kind],
                                element_guid="", anchor_guid=guid, reader=reader)
            decisions[item.kind] = ("" if d.allowed
                                    else f"{UNREAD_NOTE} ({d.reason})" if d.basis == BASIS_UNREADABLE
                                    else REFUSED_NOTE + (d.reason or "refused"))
        kind, key, payload = _payload_for(registry, entity_type, slug, item, guid, presser, feedback, entries)
        if decisions[item.kind]:
            # Nothing is sent: the refusal is a row born dead with its words (round 3: never claimable in between),
            # so the band reads "not permitted" from a row, and the person's next Publish asks again (a new row).
            # Round 4: born CANCELLED (as curation's refusals are), so no admin retry or dead-row revive can bring
            # a refused rating or note back to life; the plan reads "not permitted" from the note.
            registry.enqueue_outbox_element(entity_type, slug, kind, key, payload, run_id=run_id,
                                            retired=("cancelled", decisions[item.kind]))
            # Round 6: an item whose access could not be READ is not a refusal; it is said under its own key.
            (unread if decisions[item.kind].startswith(UNREAD_NOTE) else refused).append(
                {"kind": item.kind, "id": item.id, "reason": decisions[item.kind][len("cancelled: "):]})
        else:
            queued.append(registry.enqueue_outbox_element(entity_type, slug, kind, key, payload, run_id=run_id))
    summary: dict = {}
    if queued or waiting:
        try:
            summary = drain_outbox(registry, run_id=run_id, limit=len(queued) + len(waiting) + 20,
                                   identity=identity)
        except Exception as exc:  # drain_outbox never raises; belt and braces for the request path
            log.warning("Feedback drain for %s %s did not run: %s", entity_type, slug, exc)
            summary = {"error": str(exc)}
        # A row the drain could not attempt (no Egeria client, a configuration error) is not left pending for
        # anyone else to send: it is "not sent", and only this person's next Publish sends it (a new row).
        why = summary.get("config_error") or summary.get("error") or summary.get("identity_error") or UNREACHED
        for row_id in queued + waiting:
            row = registry.get_outbox_element(row_id) or {}
            if row.get("status") == "pending":
                registry.mark_outbox_failed(row_id, why, max_attempts=1)
    after = feedback_plan(registry, entity_type, slug, viewer=presser, asset_guid=guid)
    return {**plan_as_dict(after), "refused": "", "not_permitted": refused, "unread": unread, "queued": queued,
            "drain": summary}


# ── the Egeria side: creators the outbox drain calls (egeria_outbox._CREATORS) ─────────────────────────────

def _elements(result) -> list[dict]:
    return [e for e in result if isinstance(e, dict)] if isinstance(result, list) else []


def _guid(el: dict) -> str:
    return str((el.get("elementHeader") or {}).get("guid") or el.get("guid") or "")


def _props(el: dict) -> dict:
    p = el.get("properties")
    return p if isinstance(p, dict) else {}


def _created_by(el: dict) -> str:
    versions = (el.get("elementHeader") or {}).get("versions") or {}
    return str(versions.get("createdBy") or "")


def rating_body(stars: int, review: str) -> dict:
    """The body Egeria's addRatingToElement takes: UpdateElementRequestBody with RatingProperties
    (FeedbackManagerResource.java:124-137; RatingProperties is a subtype of OpenMetadataRootProperties,
    OpenMetadataRootProperties.java:31). The star value is the enum NAME."""
    return {"class": "UpdateElementRequestBody",
            "properties": {"class": "RatingProperties", "starRating": STAR_RATING[int(stars)],
                           "review": review or ""}}


def find_my_rating(fb, element_guid: str, by: str) -> str:
    """The GUID of `by`'s Rating on the element (Egeria keeps one per user), or "". A read-back only, AFTER the add:
    it never decides whether to add. With no `createdBy` on the answer (or no match) it returns "" and the row is
    done with no GUID; nothing is created or retried, since the add is Egeria's own per-user upsert."""
    hits = [e for e in _elements(fb.get_attached_ratings(element_guid, metadata_element_type_name="Rating"))
            if by and _created_by(e) == by and _guid(e)]
    return _guid(sorted(hits, key=_guid)[0]) if hits else ""


def create_rating(clients, payload: dict) -> str:
    """Add (or, for the same person, replace: Egeria's own upsert) the person's Rating, public. Returns the
    Rating's GUID when a read finds it ("" when the read could not say; the add itself answered)."""
    from resource_explorer.egeria_outbox import OutboxApplyError

    fb = clients.require("feedback")
    out = fb.add_rating_to_element(payload["element_guid"], is_public=True,
                                   body=rating_body(payload["stars"], payload.get("review") or ""))
    if out is None:
        # pyegeria's logger-catch wrapper returns None for a failure it swallowed: that is not a success.
        raise OutboxApplyError("Egeria gave no answer to the rating (the client returned nothing)")
    try:
        return find_my_rating(fb, payload["element_guid"], payload.get("by") or "")
    except Exception as exc:  # noqa: BLE001 - the add answered; the GUID is a convenience
        log.info("Rating added for %s on %s; its GUID could not be read: %s", payload.get("by"),
                 payload["element_guid"], exc)
        return ""


def find_note_log(fb, qualified_name: str) -> str:
    """The NoteLog with exactly this qualifiedName (getNoteLogsByName matches qualified or display name,
    FeedbackManagerResource.java:980-990), or ""."""
    body = {"class": "FilterRequestBody", "filter": qualified_name, "metadataElementTypeName": "NoteLog"}
    hits = [e for e in _elements(fb.get_note_logs_by_name(qualified_name, body=body))
            if _props(e).get("qualifiedName") == qualified_name and _guid(e)]
    return _guid(sorted(hits, key=_guid)[0]) if hits else ""


def ensure_note_log(fb, payload: dict) -> str:
    """Find the asset's NoteLog by qualifiedName; create it (attached to the asset) only when absent; never a
    second one: a duplicate-name refusal (two Publishes at once) adopts the one that won."""
    from resource_explorer.egeria_outbox import OutboxApplyError, _is_duplicate_qualified_name

    qn = payload.get("note_log_qualified_name") or note_log_qualified_name(payload["element_guid"])
    found = find_note_log(fb, qn)
    if found:
        return found
    name = payload.get("resource_name") or payload.get("entity_slug") or ""
    body = {"class": "NewAttachmentRequestBody",
            "properties": {"class": "NoteLogProperties", "typeName": "NoteLog", "qualifiedName": qn,
                           "displayName": f"Notes on {name}",
                           "description": f"Notes people wrote about {name} in Resource Explorer."}}
    try:
        guid = fb.create_note_log(payload["element_guid"], body=body)
    except Exception as exc:
        if not _is_duplicate_qualified_name(exc):
            raise
        guid = find_note_log(fb, qn)
    if not isinstance(guid, str) or not guid:
        guid = find_note_log(fb, qn)
    if not guid:
        raise OutboxApplyError(f"NoteLog {qn!r} was neither found nor created")
    return guid


def find_note(fb, note_log_guid: str, qualified_name: str) -> str:
    """The Note with this qualifiedName in the log, or "". Round 2: the type is named. pyegeria 6.2.1's default
    `metadata_element_type_name="Action"` goes into the request body as a type filter
    (`_async_get_results_body_request`, filter_results_by_type). Egeria's typedef makes Note a subtype of
    Notification and Action (OpenMetadataTypesArchive6_1.java:780-784, 1_2.java:3517-3518), but RE's
    "never a second Note" must not rest on whether that filter includes subtypes: it asks for "Note"."""
    hits = [e for e in _elements(fb.get_notes_for_note_log(note_log_guid, metadata_element_type_name="Note"))
            if _props(e).get("qualifiedName") == qualified_name and _guid(e)]
    return _guid(sorted(hits, key=_guid)[0]) if hits else ""


def note_body(payload: dict, note_log_guid: str) -> dict:
    """One Note under the log, anchored to the ASSET (so Egeria judges it on the asset's zones, as the rest of the
    resource's feedback), parent the NoteLog by AttachedNoteLogEntry (pyegeria's own create_note shape)."""
    text = " ".join(str(payload.get("body") or "").split())
    return {"class": "NewElementRequestBody", "anchorGUID": payload["element_guid"], "isOwnAnchor": False,
            "parentGUID": note_log_guid, "parentRelationshipTypeName": "AttachedNoteLogEntry",
            "parentAtEnd1": True, "forLineage": False, "forDuplicateProcessing": False,
            "properties": {"class": "NoteProperties", "typeName": "Note",
                           "qualifiedName": payload.get("note_qualified_name") or note_qualified_name(payload["entry_id"]),
                           "displayName": text[:80] + ("…" if len(text) > 80 else ""),
                           "description": payload.get("body") or "",
                           "additionalProperties": {"journalEntryId": payload["entry_id"],
                                                    "writtenAt": payload.get("written_at") or "",
                                                    "compileId": payload.get("compile_id") or "no compile recorded",
                                                    "source": "Resource Explorer journal"}}}


def create_note(clients, payload: dict) -> str:
    """The journal entry's Note: found by its qualifiedName in the asset's NoteLog, else created; a duplicate-name
    refusal adopts the existing one. Never updates a Note."""
    from resource_explorer.egeria_outbox import OutboxApplyError, _is_duplicate_qualified_name

    fb = clients.require("feedback")
    log_guid = ensure_note_log(fb, payload)
    qn = payload.get("note_qualified_name") or note_qualified_name(payload["entry_id"])
    found = find_note(fb, log_guid, qn)
    if found:
        return found
    try:
        guid = fb.create_note(log_guid, body=note_body(payload, log_guid))
    except Exception as exc:
        if not _is_duplicate_qualified_name(exc):
            raise
        guid = find_note(fb, log_guid, qn)
    if not isinstance(guid, str) or not guid:
        raise OutboxApplyError(f"Note {qn!r} was not created (the client returned no GUID)")
    return guid

"""Draining the Egeria outbox — the apply side of egeria_outbox rows.

`docs/outbox-publishing-design.md` §5. The registry owns the rows; this module
owns turning one into an Egeria element, and the retry policy around that.

**Why this exists.** Publishing to Egeria has no transaction, no rollback, no
resume and no retry. `annotation_props.publish_annotations()` logs a failed
annotation and continues, so a SurveyReport is reported published even when
every annotation beneath it failed to write; every call site catches, logs and
records `published: False`, leaving whatever already landed in place; and if
the process dies mid-publish, `run_reconciler` marks the row `interrupted` and
stops, correctly, because it knows the run stopped but not that it failed.
Nothing finishes the job. This does.

**The shape is `rfa_actions`', deliberately.** Local-authoritative write,
best-effort remote sync, periodic reconcile — already shipping in this codebase
and driven by the same scheduler loop. What it does NOT inherit are that
precedent's three named gaps: no backoff, no max-attempts, no dead-letter.

**Idempotency is by qualifiedName, not by catching a duplicate error.** Egeria
identifies an element by GUID, and qualifiedName should also be unique, so the
supported way not to re-create something is to search for it first — which is
what `_find_or_create_asset` and `publish_annotations` already do. Building on
a duplicate-create *failing* would rest on an error path, and would misread any
other failure as "already applied".
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from resource_explorer.egeria_timing import time_egeria_call
from resource_explorer.surveyors.survey_report import (
    annotation_qualified_name, assert_unique_qualified_names)

log = logging.getLogger(__name__)


@dataclass
class OutboxClients:
    """The Egeria clients an apply step may need.

    A bundle rather than a single `discovery` argument because element kinds
    reach different managers: annotations go through `DataDiscovery`, while an
    investigation's collection memberships and ResourceList go through
    `CollectionManager`. Passing one client and hoping it has the right method
    would fail at the worst moment — inside a retry, on a row that had already
    been recorded as pending work.

    Fields are optional so a caller wires only what its kinds need; a creator
    asking for a client that is None raises, which the drain treats as any
    other failed attempt.
    """

    discovery: object | None = None
    collection_manager: object | None = None
    #: MetadataExpert — the only client that can create a bare relationship
    #: between two already-existing GUIDs (annotation-linking-plan Phase 2).
    #: DataDiscovery has no create/attach endpoint for AnnotationExtension.
    metadata_expert: object | None = None
    #: The database catalogue commit's door to Egeria (`catalogue_gateway`), and the
    #: registry its proof rows are written to. Both optional: the catalogue kinds
    #: build their own from the row's database when the drain was not given them.
    catalogue_gateway: object | None = None
    registry: object | None = None
    #: Any pyegeria client carrying the feedback-manager methods (`get_tags_by_name`, `create_informal_tag`,
    #: `add_tag_to_element`, `remove_tag_from_element`): Brief T's tag kinds; and Brief F's ratings and notes
    #: (`add_rating_to_element`, `get_attached_ratings`, `get_note_logs_by_name`, `create_note_log`,
    #: `get_notes_for_note_log`, `create_note`). `_default_clients` gives a
    #: ClassificationExplorer from the same factory, built on first use (`feedback_factory`) so a drain with no
    #: tag rows builds no extra client.
    feedback: object | None = None
    feedback_factory: "Callable[[], object] | None" = None
    #: The user id these clients were built for, so a refusal can say who was refused.
    #: pyegeria's own 401 text names no user for an Egeria-wrapped response.
    acting_as: str = ""

    def require(self, name: str):
        client = getattr(self, name, None)
        if client is None and name == "feedback" and self.feedback_factory is not None:
            client = self.feedback = self.feedback_factory()
        if client is None:
            raise OutboxApplyError(
                f"This element needs the {name!r} client, which the drain was not given"
            )
        return client

#: Rows attempted per drain pass. A proposal publish is up to ~2,100 rows
#: (largest run observed 2026-08-31), so a pass is deliberately bounded — the
#: scheduler loop comes back every 15 minutes and an unbounded pass would hold
#: one iteration open for the whole backlog.
DRAIN_BATCH = 200


class OutboxApplyError(RuntimeError):
    """One element could not be written. Carries no retry policy of its own —
    the caller decides, via `mark_outbox_failed`, whether this becomes another
    attempt or a dead letter."""


class OutboxLookupFailed(OutboxApplyError):
    """The pre-create look-up could not be made, so nothing was sent. Raised `from` the look-up's own
    exception: the drain reads that cause for a security refusal (dead at once) or a 401 (who was refused)."""


class OutboxNotKept(OutboxApplyError):
    """A write a resend cannot fix: the same row carries the same body, so the result would be the same.

    Raised for a membership property Egeria did not keep (it drops a name it does not know and answers
    success) and for a membership confidence RE refuses to send. The drain marks the row dead on the
    FIRST failure, with these words, through the same `max_attempts=1` terminal path a destructive or
    person-only row takes. Gate decision 2026-10-13: no retry with backoff."""


class OutboxIdentityError(RuntimeError):
    """The drain has no usable Egeria identity to call as: a configuration error.

    Not an Egeria failure and not an outage. `drain_outbox` handles it on its own
    path: no call is made, rows go back to 'pending' with no attempt burned and the
    sentence on each row, and the pass is logged at ERROR with `config_error` in its
    summary. It is never retried as an ordinary failure, which would burn the budget
    of good writes and dead-letter them for a reason that had nothing to do with them."""


class OutboxNotReadyError(RuntimeError):
    """A link row's referent annotation(s) have not been applied yet.

    NOT a failure — `drain_outbox` catches this separately from
    `OutboxApplyError`/other exceptions and hands the row back to 'pending'
    via `mark_outbox_deferred` without burning an attempt (see
    `mark_outbox_failed`'s exponential backoff, which this deliberately
    avoids: the row isn't broken, it's just early). Raised by
    `_resolve_link_referents` when a background/deferred publish enqueued an
    `annotation_link` row keyed by its two referent annotations' OWN outbox
    row ids (run in background — the annotations have not been drained yet,
    so no real GUID exists to enqueue the link with) and one or both of those
    referent rows have not reached 'done'. Rows drain in id order, so in
    practice a run's own annotations apply before its own links are even
    attempted — this exists for the pass where they do not (a referent that
    itself failed and is backing off, or a drain that claimed the link row
    before the annotation row in the same batch)."""


class OutboxAccessUnreadable(OutboxNotReadyError):
    """Brief F round 4: a tag or group row whose requester's access could not be read (Egeria unreachable). Not a
    refusal and not a failure: the row waits unsent, and is not tried again until `DELAY_SECONDS` later, so each
    drain does not re-read Egeria for it."""

    DELAY_SECONDS = 300


#: The words a deferred row carries while its access cannot be re-checked (the band shows them, not "sending").
ACCESS_WAIT_WORDS = "waiting to re-check access"


class OutboxAwaitsOwner(OutboxNotReadyError):
    """A PRIVATE investigation's row (its ResourceList or a member's CollectionMembership) claimed by a drain that
    is not its signed-in owner: the background drain (RE's service account) or another person's request.

    Egeria refuses the service account a private investigation's elements by design (OPEN-METADATA-SECURITY-
    403-020 on the Project), so a drain-time access check or write as that account can only fail, and it used to
    make the row dead with a misleading "not permitted · could not check access ... user ``" (live row 76009,
    2026-10-13). Not a refusal and not a failure: nothing is sent, no attempt is burned, the row stays pending
    with `egeria_work_access.owner_waiting_words(owner)`, and its owner's next Relink applies it inline as them.
    Not re-claimed for `DELAY_SECONDS`, so each background pass does not take it again."""

    DELAY_SECONDS = 3600


#: Egeria's signature for "this qualifiedName is already taken".
#:
#: Matched on the message rather than a status code because the client
#: surfaces it as a SERVER_ERROR_500 wrapping a relatedHTTPCode of 409 — the
#: outer code says nothing useful, and the inner one is only in the text.
#: Both the error id and the phrase are required, so a message that merely
#: mentions a 409 in passing does not qualify.
_DUPLICATE_NAME_MARKERS = ("OMAG-COMMON-409-001", "is not available for use")


def _is_duplicate_qualified_name(exc: Exception) -> bool:
    text = str(exc)
    return all(m in text for m in _DUPLICATE_NAME_MARKERS)


def _resolve_link_referents(payload: dict, resolve_row_guids) -> dict:
    """An `annotation_link` payload enqueued in ROW-ID form (background/
    deferred publish — see `enqueue_annotation_links`'s docstring) has no real
    GUIDs yet. Resolve `summary_row_id`/`evidence_row_id` to real GUIDs via
    `resolve_row_guids` (`registry.get_outbox_guids`), returning a payload
    shaped exactly like the GUID form so `_create_annotation_link` needs no
    knowledge of which form enqueued it.

    Raises `OutboxNotReadyError` — not `OutboxApplyError` — when either
    referent has not reached 'done' yet: this is not a broken row, it is an
    early one, and the caller (`drain_outbox`) must not burn an attempt or
    apply backoff for it.
    """
    if resolve_row_guids is None:
        raise OutboxNotReadyError(
            "this link was enqueued by row id but the drain was given no way "
            "to resolve outbox row ids to GUIDs"
        )
    summary_row_id = payload["summary_row_id"]
    evidence_row_id = payload["evidence_row_id"]
    guids = resolve_row_guids([summary_row_id, evidence_row_id])
    summary_guid = guids.get(summary_row_id)
    evidence_guid = guids.get(evidence_row_id)
    if not summary_guid or not evidence_guid:
        raise OutboxNotReadyError(
            f"referent annotation row(s) not yet applied: "
            f"summary_row_id={summary_row_id} evidence_row_id={evidence_row_id}"
        )
    return {"summary_guid": summary_guid, "evidence_guid": evidence_guid}


#: Kinds whose creator already does its own lookup-then-create — and, for
#: `doc_source_publish`, an extra LINK step the generic search below knows
#: nothing about (`doc_source_egeria.publish_doc_source`, mirroring
#: `egeria_publisher._publish_homepage_reference`'s identical "find or
#: create, THEN always link" shape). Skipping the generic step-2 search for
#: these kinds is deliberate: if it adopted a same-`qualified_name` element
#: found by coincidence (the homepage feature uses the exact same
#: `ExternalReference::<url>` convention), the row would be marked 'done'
#: with a real GUID that was never actually linked to THIS resource's
#: asset — a silent gap the generic annotation/membership/link kinds don't
#: have, because a bare create is everything their own creators do too.
_SELF_RESOLVING_KINDS = {"doc_source_publish", "doc_source_unpublish",
                         "catalogue_schema_attach", "catalogue_schema_leave_out",
                         # Brief T: a synthetic relationship key; each creator finds its tag / Folio itself.
                         "informal_tag_link", "informal_tag_detach",
                         "group_folio_membership", "group_folio_membership_detach",
                         # A work list's removal: no qualifiedName of its own to adopt; a hit would skip the detach.
                         "collection_membership_detach",
                         # Brief F: a person's Rating (keyed by element and person) and a journal entry's Note
                         # (found by its own qualifiedName in the asset's NoteLog by its creator).
                         "feedback_rating", "feedback_note",
                         # A run's read-back: no element of its own, its key is the run's.
                         "publish_read_back"}


#: Outbox kinds whose write ARCHIVES, DELETES or DETACHES something in Egeria. A retry of a destructive write is itself
#: a destructive write (row 69822, 2026-10-06, was retried 11 minutes after it failed), so the drain NEVER reschedules
#: one: a failure is terminal ('dead', the state the registry already has), carries "not retried: destructive write ·
#: <Egeria's sentence>", and a person re-presses. Add every new destructive kind here; tests/test_destructive_calls_logged.py
#: fails when a registered creator is neither listed here nor named non-destructive there.
DESTRUCTIVE_OUTBOX_KINDS = frozenset({
    "catalogue_schema_leave_out",   # detach the cataloguer target, archive / soft-delete the schema's elements
    "doc_source_unpublish",         # detach and delete the removed source's ExternalReference
    # Brief T: removing a tag's AttachedTag link, and an asset's membership of its group's Folio. Detaches only:
    # neither ever deletes the InformalTag or the Folio (curation_egeria's module docstring).
    "informal_tag_detach",
    "group_folio_membership_detach",
    # A member removed from a work list: its CollectionMembership to the WorkingSet. Never the collection or the asset.
    "collection_membership_detach",
})
NOT_RETRIED = "not retried: destructive write"

#: Words that mark a kind as destructive on top of the explicit list, so a new kind someone forgot to list above is
#: still caught by its name. The safe direction is the default.
DESTRUCTIVE_KIND_WORDS = ("archive", "delete", "unpublish", "leave_out", "detach", "remove")
CLAIM_LAPSED_DESTRUCTIVE = "claim lapsed; destructive writes are never re-sent"


#: Brief F: kinds that only their AUTHOR may send, inside their own Publish, as themselves (Egeria records the
#: caller as the feedback's author). The background drain never claims them; a Caller drain applies one only when
#: the row's `by` is that caller; a failure is never re-sent on its own (dead at once, the person's next Publish
#: queues a new row). Kept in step with `feedback_egeria.PERSON_ONLY_KINDS` (a test pins the two).
PERSON_ONLY_KINDS = frozenset({"feedback_rating", "feedback_note"})
NOT_THIS_PERSON = "waiting for its author's own Publish"


def is_person_only_kind(element_kind: str) -> bool:
    return (element_kind or "") in PERSON_ONLY_KINDS


def _person_only_refusal(row: dict, identity) -> str:
    """"" when this drain may send this row: it is not a person-only kind, or the drain runs as a Caller whose
    user IS the row's author. Otherwise the reason it is left alone."""
    if not is_person_only_kind(row.get("element_kind") or ""):
        return ""
    by = _row_requester(row)
    if identity is None or getattr(identity, "kind", "") != "caller" or not by \
            or (getattr(identity, "user_id", "") or "") != by:
        return NOT_THIS_PERSON
    return ""


def is_destructive_outbox_kind(element_kind: str) -> bool:
    """True for a kind whose write archives, deletes or detaches in Egeria (the explicit list, then the name words)."""
    kind = (element_kind or "").lower()
    return kind in DESTRUCTIVE_OUTBOX_KINDS or any(w in kind for w in DESTRUCTIVE_KIND_WORDS)

#: Egeria's security connector refusing an operation: `OPEN-METADATA-SECURITY-403-007 User X is not
#: authorized to issue operation Attach on SolutionBlueprint anchor element ...`. It is permanent for the
#: row until someone changes a permission or a zone, so retrying it for eight attempts only delays the
#: signal (2026-10-07: seven collection_membership rows retried for hours).
SECURITY_REFUSAL_MARKER = "OPEN-METADATA-SECURITY-403"
REFUSED_BY_SECURITY = "refused by Egeria's security"
NEEDS_PERMISSION_CHANGE = "needs a permission or zone change, not a retry"


def _http_code_of(exc: Exception) -> int | None:
    """The HTTP status an exception carries, when it carries one (pyegeria sets `related_http_code`
    and `response_code`; requests/httpx errors carry `response.status_code`)."""
    for holder, name in ((exc, "related_http_code"), (exc, "response_code"), (exc, "status_code"),
                         (getattr(exc, "response", None), "status_code")):
        value = getattr(holder, name, None)
        try:
            if value is not None and str(value).strip():
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def is_security_refusal(exc: Exception) -> bool:
    """True for an Egeria security-connector refusal: an OPEN-METADATA-SECURITY-403-* message, or an
    HTTP 403 on the exception. A bare 401 is NOT one (it can be a stale token and a retry can fix it);
    nor are 5xx, timeouts or network errors."""
    for e in _self_and_cause(exc):
        if SECURITY_REFUSAL_MARKER in str(e) or _http_code_of(e) == 403:
            return True
    return False


def _self_and_cause(exc: BaseException) -> list:
    """`exc`, then the exception it was raised from (one level) when that cause is EGERIA's: a pyegeria exception,
    or whatever an `OutboxLookupFailed` wraps (the look-up's own error). Any other cause (PlatformNotAllowed,
    EgeriaRefused, an httpx 403 from GitHub) is not read, so it is never called "refused by Egeria's security"."""
    cause = getattr(exc, "__cause__", None)
    if not isinstance(cause, BaseException):
        return [exc]
    from_egeria = type(cause).__module__.split(".")[0] == "pyegeria"
    return [exc, cause] if (from_egeria or isinstance(exc, OutboxLookupFailed)) else [exc]


def _refusal_sentence(exc: Exception) -> str:
    """Egeria's own message cut to its first sentence (from the 403 error id when the text has one)."""
    marked = [e for e in _self_and_cause(exc) if SECURITY_REFUSAL_MARKER in str(e)]
    text = " ".join(str(marked[-1] if marked else exc).split())
    at = text.find(SECURITY_REFUSAL_MARKER)
    if at >= 0:
        text = text[at:]
    cut = len(text)
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1] == " "):
            cut = i
            break
    sentence = text[:cut].strip()[:300]
    return sentence or "(Egeria gave no message)"


def apply_element(row: dict, clients: "OutboxClients", find_element_guid: Callable[[str], str],
                  resolve_row_guids: "Callable[[list[int]], dict[int, str]] | None" = None) -> str:
    """Write one outbox row to Egeria and return the element's GUID.

    Lookup-then-create, in that order, always:

    1. If the row already recorded `egeria_guid`, it landed on a previous
       attempt and nothing is written. The GUID is Egeria's primary identity.
    2. Otherwise search by `qualified_name`. A hit means Egeria wrote the
       element but we crashed before recording it — adopt the GUID rather than
       creating a second one with the same qualifiedName. Skipped for
       `_SELF_RESOLVING_KINDS` — see that set's own docstring.
    3. Only then create.

    Step 2 is the whole reason `qualified_name` is NOT NULL on the table.

    `resolve_row_guids` (typically `registry.get_outbox_guids`) is consulted
    ONLY for an `annotation_link` row whose payload carries
    `summary_row_id`/`evidence_row_id` instead of real GUIDs — the shape a
    background/deferred publish enqueues, since it has no real GUIDs to give
    at enqueue time. See `_resolve_link_referents`. Raises
    `OutboxNotReadyError` (not `OutboxApplyError`) when a referent is not yet
    applied — `drain_outbox` treats that as "try again later", never as a
    failed attempt.
    """
    existing = (row.get("egeria_guid") or "").strip()
    if existing:
        log.debug("Outbox row %s already recorded GUID %s — nothing to write",
                  row.get("id"), existing)
        return existing

    qualified_name = row["qualified_name"]
    kind = row["element_kind"]
    if kind not in _SELF_RESOLVING_KINDS:
        try:
            found = find_element_guid(qualified_name)
        except Exception as exc:
            # Could not check is not "not there". The look-up raising (unreachable, 401/403, 500, timeout, several
            # hits, or no answer at all, which the publisher's lookup raises for) used to be read as a miss and
            # the create went ahead, with Egeria's duplicate refusal the only backstop. Now nothing is sent: an
            # ordinary failed attempt, retried per policy. A typed not-found is still a real miss.
            from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

            if not lookup_exception_is_absent(exc):
                raise OutboxLookupFailed(lookup_could_not_be_made(qualified_name, exc)) from exc
            found = ""
        if found:
            log.info("Outbox row %s: %s already exists (GUID %s) — adopting, not re-creating",
                     row.get("id"), qualified_name, found)
            return found

    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError) as exc:
        # Unparseable payload will never parse on a later attempt either, so
        # it is raised like any other failure and left to burn through its
        # attempts into 'dead' rather than being special-cased into an
        # immediate kill: one code path to reason about, and the row stays
        # visible in the same place as every other stuck write.
        raise OutboxApplyError(f"payload_json is not valid JSON: {exc}") from exc

    if kind in ("catalogue_schema_attach", "catalogue_schema_leave_out"):
        # The proof rows name the outbox row that produced them. Only these kinds
        # take the extra key: the annotation creators pass the payload on as a body.
        payload = dict(payload, _outbox_id=row.get("id"))
    elif kind in ("collection_membership", COLLECTION_MEMBERSHIP_DETACH):
        # An add and a removal of one member of one collection apply in queue order (`_wait_for_earlier_membership`),
        # which needs the row's id and owner. Underscored keys: no creator sends them on.
        payload = dict(payload, _outbox_id=row.get("id"), _entity_type=row.get("entity_type") or "",
                       _entity_slug=row.get("entity_slug") or "")
    elif kind in ("informal_tag_link", "informal_tag_detach", "group_folio_membership",
                  "group_folio_membership_detach"):
        # Brief T round 2: a link and an unlink of one key apply in queue order (curation_egeria
        # `_wait_for_earlier_opposite`), which needs the row's id and key.
        payload = dict(payload, _outbox_id=row.get("id"), _key=row.get("qualified_name"))
    creator = _CREATORS.get(kind)
    if creator is None:
        raise OutboxApplyError(
            f"No creator registered for element_kind {kind!r}. "
            f"Known kinds: {sorted(_CREATORS)}"
        )
    if kind == "annotation_link" and "summary_row_id" in payload:
        # Row-id form (background/deferred publish) — resolve to real GUIDs
        # or bail out early (OutboxNotReadyError), never into the generic
        # except-Exception branch below, which would misread "not ready yet"
        # as a write failure and burn a retry attempt on it.
        payload = _resolve_link_referents(payload, resolve_row_guids)
    try:
        return creator(clients, payload) or ""
    except Exception as exc:
        if not _is_duplicate_qualified_name(exc):
            raise
        # Egeria says this qualifiedName is taken, which is proof the element
        # exists — so the create is not a failure, it is a late discovery
        # that step 2 should have made. Treating it as a failure is what
        # produced an exception storm on 2026-09-01: rows retrying a write
        # that had already succeeded, forever, because a 409 counted the same
        # as a network error.
        #
        # Look again rather than assuming: the first lookup missed it, and
        # the honest question is whether it can be found at all.
        try:
            found = find_element_guid(qualified_name)
        except Exception as lookup_exc:
            # The look-again failed: that is "could not read", not "cannot be found". Raised with its own
            # words (an ordinary failed attempt, retried later), never the inconsistency below.
            raise OutboxApplyError(
                f"Egeria rejected {qualified_name!r} as a duplicate qualifiedName and the look-up "
                f"for it then failed ({type(lookup_exc).__name__}: {str(lookup_exc)[:200]}); "
                f"no GUID can be recorded yet.") from exc
        if found:
            log.info("Outbox row %s: %s already existed (GUID %s) — adopted after a "
                     "duplicate-name rejection the pre-create lookup missed",
                     row.get("id"), qualified_name, found)
            return found
        # Egeria will not create it and we cannot find it. That is a real
        # inconsistency and must not be silently marked done: the row would
        # claim success with no GUID to show for it. Raised with its own
        # wording so it is distinguishable from an ordinary write failure.
        raise OutboxApplyError(
            f"Egeria rejected {qualified_name!r} as a duplicate qualifiedName but "
            f"the element cannot be found by that name — it exists to Egeria and "
            f"is invisible to our lookup, so no GUID can be recorded."
        ) from exc


def _create_annotation(clients: "OutboxClients", payload: dict) -> str:
    # A ClassificationAnnotation enqueued before 2026-10-15 carries its proposal in a field Egeria drops;
    # it is sent in Egeria's own fields instead (annotation_props.upgrade_classification_payload).
    from resource_explorer.surveyors.annotation_props import upgrade_classification_payload

    return _guid_of(clients.require("discovery").create_annotation(body=upgrade_classification_payload(payload)))


def _create_collection_membership(clients: "OutboxClients", payload: dict) -> str:
    """Attach one element to a Collection.

    Idempotency here is NOT lookup-then-create: a CollectionMembership has no
    qualifiedName of its own to search for. It comes from the operation being
    an upsert — `CollectionMembership` was measured live (2026-08-25) as
    uni-link, so `add_to_collection` returns None and adding the same element
    twice leaves one member. Replay is therefore safe by the relationship's
    own semantics.

    That measurement is load-bearing, and the return value IS the test: a
    multi-link relationship would silently accumulate one duplicate per retry
    with no error anywhere. Re-measure if pyegeria changes these signatures.
    """
    _wait_for_earlier_membership(clients, payload, COLLECTION_MEMBERSHIP_DETACH)
    cm = clients.require("collection_manager")
    props = collection_membership_properties(payload)
    if props:
        cm.add_to_collection(payload["collection_guid"], payload["member_guid"], body={
            "class": "NewRelationshipRequestBody",
            "properties": dict(props, **{"class": "CollectionMembershipProperties"}),
        })
    else:
        cm.add_to_collection(payload["collection_guid"], payload["member_guid"])
    return ""


#: `CollectionMembershipProperties.confidence` (Egeria 6.2): "the confidence level (0-100) that the matching
#: is correct". An int, default 0.
MEMBERSHIP_CONFIDENCE_MIN, MEMBERSHIP_CONFIDENCE_MAX = 0, 100


def membership_confidence(value) -> int:
    """A member's confidence as Egeria's `confidence` takes it: an int in 0-100.

    A float or a numeric string is coerced (a stored "80" is 80; 79.6 rounds to 80). Refused with
    ValueError: None, a bool, a non-number, and a value outside 0-100 (refused, never clamped: clamping
    would publish a number nobody chose)."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"confidence {value!r} is not a number")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"confidence {value!r} is not a number") from None
    if not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"confidence {value!r} is not a number")
    whole = int(round(value))
    if not MEMBERSHIP_CONFIDENCE_MIN <= whole <= MEMBERSHIP_CONFIDENCE_MAX:
        raise ValueError(f"confidence {value!r} is outside Egeria's "
                         f"{MEMBERSHIP_CONFIDENCE_MIN}-{MEMBERSHIP_CONFIDENCE_MAX}")
    return whole


def collection_membership_properties(payload: dict) -> dict:
    """The `CollectionMembershipProperties` fields one queued membership sends, by Egeria's own names.

    Every name here is a field of Egeria 6.2's `CollectionMembershipProperties`. That class is
    `@JsonIgnoreProperties(ignoreUnknown=true)`, so a wrong name is not refused: Egeria drops it and the
    write still succeeds. That is how `expectedConfidence` (not an Egeria property) published every
    work-list confidence as `confidence: 0` until 2026-10-13.

    * `membershipRationale` — the member's REASON for being in the collection travels with it.
    * `confidence` — sent only when RE recorded one. Egeria's `confidence` is an int defaulting to 0,
      so on a read-back "not recorded" and "recorded as 0" look the same; RE's own row
      (`work_list_members.confidence` NULL vs 0) is the only place they differ.

    The payload key stays `expected_confidence` (what queued rows already carry); `confidence` is also
    read. Sent only when a caller supplied a value: the investigation path supplies neither, and adding
    an empty body to its writes would change what every already-queued row means.

    Raises OutboxNotKept (dead at once) for a confidence that is not a number in 0-100: refused, not
    guessed at."""
    props: dict = {}
    if payload.get("membership_rationale"):
        props["membershipRationale"] = payload["membership_rationale"]
    raw = payload.get("expected_confidence", payload.get("confidence"))
    if raw is not None:
        try:
            props["confidence"] = membership_confidence(raw)
        except ValueError as exc:
            raise OutboxNotKept(f"not sent: {exc}") from None
    return props


#: The start of a dead membership row's words when RE refused the confidence it would have sent. A Retry
#: would send the same stored value, so the outbox pane draws none on it (`retry_refusal`).
NOT_SENT_CONFIDENCE = "not sent: confidence"

#: Members are read from the MEMBER's side: the collections one element belongs to, not every member of
#: the collection, so a read is small whatever the collection's size. Paged in the BODY (`startFrom`,
#: `pageSize`): pyegeria 6.2.1 ignores the `page_size`/`starting_at_end` keywords of
#: `get_related_metadata_elements` (fixed in 6.2.2, ISSUE-129); a body field works on both.
MEMBERSHIP_READ_BACK_PAGE = 100
#: A guard, not a rule: pages read before the read-back gives up and says so on the row.
MEMBERSHIP_READ_BACK_MAX_PAGES = 50


def _relationship_property(props, name: str):
    """One property of a relationship as MetadataExpert returns it: `propertyValueMap.<name>.primitiveValue`
    (or an enum's `symbolicName`); a flat `{name: value}` shape is read too. None when absent."""
    props = props if isinstance(props, dict) else {}
    value = (props.get("propertyValueMap") or {}).get(name)
    if isinstance(value, dict):
        for key in ("primitiveValue", "symbolicName"):
            if key in value:
                return value[key]
    return props.get(name)


def verify_membership_landed(row: dict, clients: "OutboxClients") -> str:
    """After a `collection_membership` write: read the relationship back and compare what was sent.

    Egeria drops a property name it does not know and still answers success, so only the relationship as
    it reads back can show a drop. Returns the note for the done row ('' when every sent property is
    there, or when nothing was sent: then no read at all). Raises OutboxNotKept naming each property that
    did not land: the row goes dead at once (a resend carries the same body).

    The read is from the member's side, `MetadataExpert.get_related_metadata_elements(member,
    "CollectionMembership", body, starting_at_end=2)`, paged in the body until this collection is found
    or a page comes back empty (a short page does not end it). The member is at end 2 of
    CollectionMembership and the collection at end 1, so a match must have `element.elementGUID` equal
    to the collection, `elementAtEnd1` True and `type.typeName` "CollectionMembership"; the values are
    `relationshipProperties.propertyValueMap`. Measured read-only on 6.2, 2026-10-13, member egeria_git:
    with pyegeria 6.2.2, starting_at_end=2 (or omitted) returns its 3 collections and 1 returns none;
    6.2.1 ignores the keyword and returns the 3 either way. Body paging (pageSize=1) works on both.

    A read that fails, or that does not show this collection, cannot tell either way: the row is done,
    and the note says the properties were not confirmed."""
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return ""
    sent = collection_membership_properties(payload)
    if not sent:
        return ""
    collection_guid, member_guid = payload.get("collection_guid", ""), payload.get("member_guid", "")
    rel = None
    try:
        me = clients.require("metadata_expert")
        for page in range(MEMBERSHIP_READ_BACK_MAX_PAGES):
            answer = me.get_related_metadata_elements(
                member_guid, "CollectionMembership",
                {"class": "ResultsRequestBody", "startFrom": page * MEMBERSHIP_READ_BACK_PAGE,
                 "pageSize": MEMBERSHIP_READ_BACK_PAGE},
                starting_at_end=2)
            items = (answer.get("elementList") or []) if isinstance(answer, dict) else []
            if not items:                                 # "No elements found" or an empty list: the end
                break
            for item in items:
                if (isinstance(item, dict)
                        and (item.get("type") or {}).get("typeName") == "CollectionMembership"
                        and item.get("elementAtEnd1") is True
                        and (item.get("element") or {}).get("elementGUID") == collection_guid):
                    rel = item.get("relationshipProperties") or {}
                    break
            if rel is not None:
                break
        else:
            return (f"written; properties not confirmed: the read-back stopped after "
                    f"{MEMBERSHIP_READ_BACK_MAX_PAGES} pages")
    except Exception as exc:  # noqa: BLE001 — any failure means "could not tell", said on the row
        return f"written; properties not confirmed: read-back failed ({type(exc).__name__}: {str(exc)[:200]})"
    if rel is None:
        return "written; properties not confirmed: the membership is not in the read-back"
    dropped = [f"{k} (sent {v!r}, read back {_relationship_property(rel, k)!r})"
               for k, v in sent.items() if _relationship_property(rel, k) != v]
    if dropped:
        raise OutboxNotKept("Egeria accepted the membership but did not keep: " + "; ".join(dropped))
    return ""


def retry_refusal(row: dict) -> str:
    """Why a dead row is not offered a Retry (beyond a destructive kind), or ''. A membership whose stored
    confidence RE refused would be refused again: the value has to be corrected on the work list first."""
    if (row.get("element_kind") == "collection_membership"
            and str(row.get("last_error") or "").startswith(NOT_SENT_CONFIDENCE)):
        return ("the stored confidence is not a number from 0 to 100, so a retry would refuse it again: "
                "correct it on the work list and publish again")
    return ""


def _create_resource_list(clients: "OutboxClients", payload: dict) -> str:
    """Attach a Collection to its parent Project (ResourceList).

    Same basis as membership above: `ResourceList` via `attach_collection` was
    measured uni-link on the same date — returns None, and attaching the same
    Collection twice leaves one attachment.
    """
    cm = clients.require("collection_manager")
    cm.attach_collection(payload["parent_guid"], payload["collection_guid"])
    return ""


def _create_annotation_link(clients: "OutboxClients", payload: dict) -> str:
    """Create one `AnnotationExtension` relationship between two annotations
    already created in the same publish run (annotation-linking-plan Phase 2,
    Tier 1: `data_profiler.py`/`dependency.py`'s per-file/per-ecosystem
    evidence linked to their aggregate).

    Idempotency: deliberately NO lookup-then-create, and no pre-check via
    `get_all_related_elements` either — unlike the MULTI_LINK branch the plan
    sketched before measurement. `AnnotationExtension` was measured live
    2026-09-01 (docs/annotation-linking-plan.md, Phase 0) as UNI_LINK: two
    identical creates against the same ordered pair returned the SAME
    relationship GUID, and two independent reads (`get_annotation_extensions`
    AND `get_all_related_elements`) each showed exactly one relationship.
    Create-blind is therefore safe and matches the precedent already used for
    `CollectionMembership`/`ResourceList` below — a retry (or a re-publish of
    the same run) converges on the single relationship Egeria already holds
    rather than accumulating duplicates. Re-measure before relying on this for
    any OTHER relationship type, including `AnnotationReview` (Phase 3, not
    measured, not covered by this).

    Direction: `metadataElement1GUID` is the SUMMARY, `metadataElement2GUID`
    is the EVIDENCE. Phase 0's measurement confirmed no reversal happens and
    that `get_annotation_extensions(X)`/`get_all_related_elements(X)` both
    return whatever is at end 2 when X is end 1 — so `get_annotation_
    extensions(summary_guid)` is the natural "what backs this summary" query
    for a consumer walking the catalog, which is the whole point of Tier 1.
    """
    body = {
        "class": "NewRelatedElementsRequestBody",
        "typeName": "AnnotationExtension",
        "metadataElement1GUID": payload["summary_guid"],
        "metadataElement2GUID": payload["evidence_guid"],
    }
    return _guid_of(clients.require("metadata_expert").create_related_elements(body=body))


def _create_doc_source_publish(clients: "OutboxClients", payload: dict) -> str:
    """Publish one declared documentation source's `ExternalReference` to
    Egeria — the async, retried half of the Egeria publish-state fix
    (2026-09-29, `DOC-SOURCES-DECLARE-AND-PROBE-IMPLEMENTED.md`). Enqueued by
    `enqueue_doc_source_publish` whenever a source is declared (or found
    un-published on a read) on a resource that is ALREADY published — never
    waits for the resource's own next full re-publish.

    Resolves the entity and the source row FRESH at apply time (never from
    the payload, which carries only `entity_type`/`entity_slug`/`source_id`)
    so a retry always sees current credentials and current local state:

    - If the source row is gone (removed locally before this row was
      drained — a real race between "declare" and "remove" within one
      15-minute window), there is nothing left to publish. Returning ''
      rather than raising: this is not a failure, the work item is simply
      moot now.
    - If it already carries BOTH a ref guid AND a link guid (a concurrent
      full publish via `publish_local_doc_sources` beat this row to it, or a
      previous attempt of THIS row wrote both and then crashed before
      `mark_outbox_done`), adopt the ref guid rather than publishing a
      second time — genuinely nothing left to do.
    - If it carries a ref guid but NO link guid (round 4, 2026-09-29: a
      read-back adopted an existing reference by URL but read-back cannot
      capture the relationship's own GUID, or a prior attempt created the
      reference and crashed before linking it — this is also exactly what
      `_compute_egeria_state`'s self-heal re-queues for), still call
      `publish_doc_source`, passing the known ref guid through so it links
      THAT reference rather than minting a second, independent one for the
      same row. **Not** treated as "already done" — a ref guid alone proves
      an `ExternalReference` element exists somewhere, never that IT IS
      LINKED to this asset, which is the whole distinction the adoption-race
      fix exists to enforce (see `derive_doc_source_egeria_state`).
    - Otherwise call `publish_doc_source` (its own lookup-then-create-then-
      link) and write the result straight back onto the `doc_sources` row —
      this creator is the ONLY path that runs for this kind (see
      `_SELF_RESOLVING_KINDS`), so this is the one and only place that
      write-back can happen.

    Raises `OutboxApplyError` on an unsuccessful `publish_doc_source` result
    so `drain_outbox` retries/backs off it like any other failed write —
    `publish_doc_source` itself never raises for an ordinary Egeria failure,
    it returns `{"ok": False, ...}`, which this translates into the outbox's
    own retry vocabulary.
    """
    from resource_explorer.doc_source_egeria import (
        publish_doc_source, ref_guid_exists, resolve_entity_for_doc_source,
    )
    from resource_explorer.registry import ProjectRegistry

    entity_type = payload["entity_type"]
    entity_slug = payload["entity_slug"]
    source_id = payload["source_id"]
    registry = ProjectRegistry()

    source = registry.get_doc_source(entity_type, entity_slug, source_id)
    if source is None:
        log.info("doc source outbox: %s/%s source %s was removed before its publish "
                 "was drained — nothing to do", entity_type, entity_slug, source_id)
        return ""
    if source.get("egeria_external_ref_guid") and source.get("egeria_link_relationship_guid"):
        # Both present — a real, already-linked reference. Nothing to do.
        # (A ref guid ALONE is not this condition — see the docstring above
        # and derive_doc_source_egeria_state; that shape still needs linking.)
        return source["egeria_external_ref_guid"]

    entity = resolve_entity_for_doc_source(registry, entity_type, entity_slug)
    if entity is None:
        raise OutboxApplyError(
            f"{entity_type} {entity_slug!r} no longer resolves — cannot publish doc source "
            f"{source_id} (no Egeria credentials to use)"
        )
    clients = _doc_source_clients(entity)
    result = publish_doc_source(
        source, entity.egeria_asset_guid or "",
        clients=clients,
        display_name=entity.display_name,
        known_ref_guid=source.get("egeria_external_ref_guid") or "",
        # Adoption-race guard (2026-09-29) — refuse to reuse (or keep) an
        # ExternalReference that a pending/running doc_source_unpublish
        # row is concurrently deleting; see publish_doc_source's own
        # docstring for the incident this closes.
        is_ref_unpublishing=lambda guid: registry.has_pending_unpublish_for_ref(
            entity_type, entity_slug, guid),
    )
    if not result["ok"]:
        raise OutboxApplyError(result["error"] or "publish_doc_source failed")
    ref_guid = result["ref_guid"]
    link_guid = result.get("link_guid", "")
    # Proof-row rule (design, round 5, 2026-09-29): "done" is a claim like
    # any other state this system renders, and needs the same evidence-
    # backing discipline as everything else -- reaching `done` without BOTH
    # guids actually written back must never happen again. This is the exact
    # incident this round fixes: `publish_doc_source`'s own best-effort link
    # step can return `ok: True` with `link_guid` empty (a duplicate-link
    # exception on a re-publish is swallowed there on purpose, see its own
    # docstring) -- that is a real, honest "not linked yet" outcome for the
    # OUTBOX's purposes, even though it isn't a `publish_doc_source` failure.
    if not ref_guid or not link_guid:
        raise OutboxApplyError(
            result["error"] or
            "publish_doc_source returned without both a ref guid and a link guid "
            "-- the reference exists but the link step did not complete; not "
            "marking this row done on an unlinked reference"
        )
    # "Verified", not just "attempted" (design's own wording): re-confirm the
    # ref guid we are about to persist as this row's proof still resolves in
    # Egeria RIGHT NOW, using the exact same existence check
    # `publish_doc_source` itself uses to decide whether an old guid is safe
    # to reuse -- rather than trusting our own just-returned result blindly.
    # Closes the live incident by construction: a stale/deleted ref guid can
    # no longer be written back as "done" from this path either.
    try:
        still_there = ref_guid_exists(ref_guid, clients=clients)
    except Exception as exc:                 # could not check: not proof either way, so not done
        raise OutboxApplyError(
            f"publish_doc_source reported ref {ref_guid} and link {link_guid}, but {exc}; "
            "not marking done on an unverified guid") from exc
    if not still_there:
        raise OutboxApplyError(
            f"publish_doc_source reported ref {ref_guid} and link {link_guid}, but "
            f"{ref_guid} does not resolve in Egeria right now -- not marking done on "
            "an unverified guid"
        )
    registry.set_doc_source_egeria_ref(entity_type, entity_slug, source_id, ref_guid, link_guid)
    return ref_guid


def _create_doc_source_unpublish(clients: "OutboxClients", payload: dict) -> str:
    """Detach+delete one removed documentation source's `ExternalReference`
    — the async, retried half of removal. Enqueued by `enqueue_doc_source_
    unpublish` right after the local `doc_sources` row is deleted, since by
    then there is no local row left to update; this row exists purely so
    the Egeria-side removal is retried if it fails the first time, same as
    every other outbox write.

    Only the ONE `ref_guid` the removed row carried is ever touched — see
    `unpublish_doc_source`'s own docstring for why a shared-URL collision is
    deliberately left alone rather than swept.
    """
    from resource_explorer.doc_source_egeria import (
        resolve_entity_for_doc_source, unpublish_doc_source,
    )
    from resource_explorer.registry import ProjectRegistry

    ref_guid = payload.get("ref_guid", "")
    if not ref_guid:
        return ""
    entity_type = payload["entity_type"]
    entity_slug = payload["entity_slug"]
    registry = ProjectRegistry()
    entity = resolve_entity_for_doc_source(registry, entity_type, entity_slug)
    if entity is None:
        raise OutboxApplyError(
            f"{entity_type} {entity_slug!r} no longer resolves — cannot resolve Egeria "
            f"credentials to remove ExternalReference {ref_guid}"
        )
    result = unpublish_doc_source(
        ref_guid, entity.egeria_asset_guid or "", clients=_doc_source_clients(entity),
    )
    if not result["ok"]:
        raise OutboxApplyError(result["error"] or "unpublish_doc_source failed")
    return ref_guid


def _row_requester(row: dict) -> str:
    """The person a row was enqueued for, when its payload says (`by`, as a catalog attach does)."""
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return ""
    by = payload.get("by") or payload.get("requested_by") or "" if isinstance(payload, dict) else ""
    return by.strip() if isinstance(by, str) else ""


class OutboxNotPermitted(RuntimeError):
    """A queued write its requester may no longer make (Brief Z2 round 2). The message is the row's words."""


#: The documentation-source kinds re-checked at drain time, and the resource write each makes.
DOC_SOURCE_WRITES = {"doc_source_publish": "link", "doc_source_unpublish": "unlink"}


def _run_requester(registry, row: dict) -> str:
    """Round 3: a documentation-source row queued before rows carried `by` names no one. When it belongs to a
    run (`run_id`), the run's own `requested_by` is the person; otherwise nobody, and the no-requester rule
    applies (open only with no zone in use). A run that cannot be read names nobody (the stricter answer)."""
    run_id = (row.get("run_id") or "").strip()
    if not run_id:
        return ""
    try:
        run = registry.get_run(run_id) or {}
    except Exception:  # noqa: BLE001
        return ""
    by = run.get("requested_by") if isinstance(run, dict) else ""
    return by.strip() if isinstance(by, str) else ""


def _doc_source_refusal(registry, row: dict) -> str:
    """Brief Z2 round 2: the drain-time re-check for a documentation source's ExternalReference write, as the
    person the row was queued for (its payload's `by`), by the same rule every run uses
    (`workflows.curate.person_write_refusal`): that person's access, or, with nobody recorded and nobody signed in,
    open only while no zone is in use. "" = apply it."""
    kind = row.get("element_kind")
    if kind not in DOC_SOURCE_WRITES:
        return ""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    from resource_explorer.workflows.curate import person_write_refusal

    by = _row_requester(row) or _run_requester(registry, row)
    reset = current_caller.set(CallerIdentity(user_id=by, egeria_token=None, auth_source="queued-run",
                                              role="user")) if by else None
    try:
        return person_write_refusal(registry, row.get("entity_type") or "", row.get("entity_slug") or "",
                                    DOC_SOURCE_WRITES[kind])
    finally:
        if reset is not None:
            current_caller.reset(reset)


#: Brief F round 3: the tag and group kinds, and the Egeria operations each checks (curation_egeria.operations_for).
CURATION_WRITES = {"informal_tag_link": ("tag", "link"), "informal_tag_detach": ("tag", "unlink"),
                   "group_folio_membership": ("group", "link"),
                   "group_folio_membership_detach": ("group", "unlink")}


def _curation_refusal(registry, row: dict) -> str:
    """Brief F round 3: the drain-time re-check for a tag or group row, as the person it was queued for (`by`), with
    the operations Egeria checks for it, on the element the row names: "" = apply it, else "not permitted · <why>".
    When access cannot be read at all it raises `OutboxNotReadyError` (deferred unsent, never killed by an outage).
    A refusal is decided before anything is sent (a destructive unlink included). A row with no requester (queued
    before rows carried one) is not re-checked here; Egeria checks whoever the drain runs as."""
    write = CURATION_WRITES.get(row.get("element_kind") or "")
    if not write:
        return ""
    by = _row_requester(row)
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    from resource_explorer.curation_egeria import operations_for
    from resource_explorer.workflows.curate import BASIS_UNREADABLE, curation_access, person_write_refusal

    try:
        guid = (json.loads(row.get("payload_json") or "{}") or {}).get("element_guid") or ""
    except (TypeError, ValueError):
        guid = ""
    et, slug = row.get("entity_type") or "", row.get("entity_slug") or ""
    if not by:
        # Round 4: a row nobody is recorded for (Brief T rows before `by`) is judged by the no-requester rule, as a
        # documentation-source row is: open only while no zone is in use, refused otherwise or when unreadable.
        reset = current_caller.set(None)
        try:
            why = person_write_refusal(registry, et, slug, write[1], element_guid=guid or None)
        finally:
            current_caller.reset(reset)
        if why.startswith("not permitted · could not check access"):
            # Round 5: an outage is not a refusal here either: the row waits unsent, as a requester's row does.
            raise OutboxAccessUnreadable(f"{ACCESS_WAIT_WORDS}: {why[len('not permitted · '):]}")
        return why
    # Round 5: a Portal curator/admin seen at the press (from the verified caller, `curation_egeria._enqueue`) is
    # judged as one here too, as the press judged them; never taken from anything a request sends. A pending row
    # honours the role recorded at its press (round 6); an admin revive of a dead row drops it
    # (`registry.retry_outbox_element`), so a revived row is re-checked as an ordinary user unless re-pressed.
    try:
        portal = bool((json.loads(row.get("payload_json") or "{}") or {}).get("portal_role"))
    except (TypeError, ValueError):
        portal = False
    reset = current_caller.set(CallerIdentity(user_id=by, egeria_token=None, auth_source="queued-run",
                                              role="curator" if portal else "user"))
    try:
        d = curation_access(registry, et, slug, "", operations=operations_for(*write), element_guid=guid or None)
    finally:
        current_caller.reset(reset)
    if not d.allowed and d.basis == BASIS_UNREADABLE:
        # Egeria could not be read to decide: not a refusal. The row waits (pending, no attempt burned), unsent,
        # and is not tried again for a while (round 4).
        raise OutboxAccessUnreadable(f"{ACCESS_WAIT_WORDS}: {d.reason}")
    return "" if d.allowed else f"not permitted · {d.reason}"


#: How `writes_refusal` words a check it could not make ("could not check access in Egeria (...)" or
#: "could not check access (<error>)"), as opposed to a refusal.
CHECK_FAILED_PREFIX = "not permitted · could not check access"

#: Brief Z3: the membership and ResourceList rows of an investigation's working set and a work list's WorkingSet,
#: re-checked at drain time as the person they were queued for.
COLLECTION_ENTITY_TYPES = ("investigation", "work_list")
#: A work list's re-publish removing a member (`work_lists.WorkLists.publish`): DETACH on both ends.
COLLECTION_MEMBERSHIP_DETACH = "collection_membership_detach"
COLLECTION_KINDS = ("collection_membership", "resource_list", COLLECTION_MEMBERSHIP_DETACH)


def _row_payload(row: dict) -> dict:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _private_investigation_reader(registry, row: dict, identity):
    """For an investigation's row whose investigation is PRIVATE: the access reader that reads its elements as the
    signed-in owner (`identity`, the inline drain's Caller). Raises `OutboxAwaitsOwner` when the drain is not
    that owner (nothing is read or sent). None for every other row: the check reads as before."""
    if row.get("entity_type") != "investigation":
        return None
    from resource_explorer.workflows.egeria_work_access import (
        investigation_unscoped, owner_identity, owner_waiting_words, private_owner,
    )
    from resource_explorer.zone_access import AccessUnreadable, EgeriaAccessReader

    try:
        inv = investigation_unscoped(registry, row.get("entity_slug") or "")
    except AccessUnreadable as exc:
        # Whether it is private cannot be read: never taken for shared (and checked as the service account). The
        # row waits unsent, as an outage does (review of 1841e4a8).
        raise OutboxAccessUnreadable(f"{ACCESS_WAIT_WORDS}: {exc}") from exc
    owner = private_owner(inv)
    if not owner:
        return None
    read_as = owner_identity(inv, identity) if identity is not None else None
    if read_as is None:
        raise OutboxAwaitsOwner(owner_waiting_words(owner))
    return EgeriaAccessReader(read_as=read_as)


#: The kinds a private member's survey writes beneath its private report (anchored to it, so judged by its zones).
PRIVATE_FINDING_KINDS = ("annotation", "annotation_link", "publish_read_back")


def _private_findings_await_owner(registry, row: dict, identity) -> None:
    """Belt and braces for a private member's findings (2026-10-13 review): an annotation row of a resource in a
    PRIVATE investigation is written only by a drain running as that owner, signed in. Any other drain (the
    background service account, another person) raises `OutboxAwaitsOwner`: nothing sent, no attempt burned, the row
    stays pending with the owner's words, never dead. `EgeriaPublisher` drains a private publish inline as the owner,
    so this fires only for rows that reached the background drain anyway. Whether the resource is private cannot be
    read: the row waits (`OutboxAccessUnreadable`), never taken for public."""
    if row.get("element_kind") not in PRIVATE_FINDING_KINDS:
        return
    from resource_explorer import private_publish as pp
    from resource_explorer.workflows.egeria_work_access import owner_waiting_words

    try:
        owner = pp.resolve_private_owner(registry, row.get("entity_type") or "", row.get("entity_slug") or "")
    except pp.PrivateOwnerUnknown as exc:
        raise OutboxAccessUnreadable(f"{ACCESS_WAIT_WORDS}: {exc}") from exc
    if owner and not pp.is_owner(identity, owner):
        raise OutboxAwaitsOwner(owner_waiting_words(owner))


def _collection_refusal(registry, row: dict, identity=None) -> str:
    """Brief Z3: the drain-time re-check for an investigation's or a work list's link (a CollectionMembership,
    or the ResourceList from the Project to its Folio): ATTACH on BOTH ends (`validateUserForElementAttach`
    :1582-1620; an anchored end on its anchor), as the person the row was queued for (`by`, with the Portal
    curator/admin override only when the row recorded it from the verified caller), by the same rule every
    queued write uses (`workflows.egeria_work_access.writes_refusal`): with nobody recorded and nobody signed in,
    open only while no zone is in use. "" = apply it. Rows of other entities (a blueprint's members) are not
    this check's."""
    kind = row.get("element_kind")
    if kind not in COLLECTION_KINDS or row.get("entity_type") not in COLLECTION_ENTITY_TYPES:
        return ""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    from resource_explorer.workflows.egeria_work_access import (
        collection_detach_write, collection_write, member_detach_write, member_write, writes_refusal,
    )

    payload = _row_payload(row)
    if kind == COLLECTION_MEMBERSHIP_DETACH:
        writes = [collection_detach_write(str(payload.get("collection_guid") or ""), "the collection"),
                  member_detach_write(str(payload.get("member_guid") or ""))]
    elif kind == "collection_membership":
        writes = [collection_write(str(payload.get("collection_guid") or ""), "the collection"),
                  member_write(str(payload.get("member_guid") or ""))]
    else:
        writes = [collection_write(str(payload.get("parent_guid") or "")),
                  collection_write(str(payload.get("collection_guid") or ""), "the working set")]
    writes = [w for w in writes if w.guid]
    # A private investigation's row: checked (and later written) only by its signed-in owner, reading as them.
    reader = _private_investigation_reader(registry, row, identity)
    by = _row_requester(row) or _run_requester(registry, row)
    role = "curator" if by and payload.get("portal_role") is True else "user"
    reset = current_caller.set(CallerIdentity(user_id=by, egeria_token=None, auth_source="queued-run",
                                              role=role)) if by else None
    try:
        why = writes_refusal(registry, writes, entity_type=row.get("entity_type") or "",
                             slug=row.get("entity_slug") or "", reader=reader)
    finally:
        if reset is not None:
            current_caller.reset(reset)
    if reader is not None and why.startswith(CHECK_FAILED_PREFIX):
        # A private investigation's owner check that could not be MADE (a timeout, an expired sign-in, a blip) is
        # not a refusal: the row waits unsent, no attempt burned, as `_curation_refusal` does (review of 1841e4a8).
        # Shared rows keep their path.
        raise OutboxAccessUnreadable(f"{ACCESS_WAIT_WORDS}: {why[len('not permitted · '):]}")
    return why


def _queued_write_refusal(registry, row: dict, identity=None) -> str:
    """Every drain-time re-check (Brief Z2 round 2's documentation sources, Brief Z3's collections, Brief F's tag
    and group rows). `identity`: who this drain runs as (a private investigation's rows need its owner)."""
    return (_doc_source_refusal(registry, row) or _collection_refusal(registry, row, identity)
            or _curation_refusal(registry, row))


def _row_identity(row: dict, drain_identity):
    """Brief I round 4: in the background loop (a Daemon drain) a row enqueued for a person is
    applied as `Daemon(OUTBOX, requested_by=<that person>)`, so a retry stamps the original author,
    never the service account. Without a recorded person the row runs as the plain daemon and the
    on-behalf helper stamps nothing. An inline drain (a Caller) keeps the person it runs as."""
    from contextlib import nullcontext

    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    by = _row_requester(row)
    if drain_identity is None or getattr(drain_identity, "kind", "") != "daemon" or not by:
        return nullcontext()
    return acting_as(Daemon(DaemonReason.OUTBOX, requested_by=by))


def _drain_principal(entity):
    """Who a doc-source or catalog row is applied as: `current_principal()` — the signed-in Caller
    for an inline drain inside a person's request, `Daemon(OUTBOX)` in the background loop. An
    entity's stored Egeria user/password are never used (owner's ruling, 2026-10-09)."""
    from resource_explorer.egeria_clients import current_principal

    return current_principal()


def _doc_source_clients(entity):
    from resource_explorer.doc_source_egeria import entity_clients

    return entity_clients(entity, _drain_principal(entity))


def _catalogue_gateway(clients: "OutboxClients", payload: dict):
    gateway = getattr(clients, "catalogue_gateway", None)
    if gateway is not None:
        return gateway
    registry = clients.require("registry")
    from resource_explorer.catalogue_commit import make_gateway
    entity = registry.get_database(payload["slug"], allow_unreadable=True)
    if entity is None:
        raise OutboxApplyError(f"database {payload['slug']!r} no longer resolves")
    return make_gateway(entity, identity=_drain_principal(entity))


def _create_catalogue_schema_attach(clients: "OutboxClients", payload: dict) -> str:
    """Create one schema element from the template and attach it to the JDBC
    cataloguer as a SCHEMA-kind target (slice B). Self-resolving: the generic
    lookup-by-qualified-name above would adopt an existing schema element and
    skip the attach, which is the half that matters. Idempotent by its own reads
    (the element by qualified name, the targets before and after)."""
    from resource_explorer.catalogue_commit import SchemaRefused, apply_attach
    from resource_explorer.catalogue_gateway import GatewayError
    registry = clients.require("registry")
    try:
        return apply_attach(registry, _catalogue_gateway(clients, payload), payload,
                            outbox_id=payload.get("_outbox_id"))
    except (GatewayError, SchemaRefused) as exc:
        raise OutboxApplyError(str(exc)) from exc


def _create_catalogue_schema_leave_out(clients: "OutboxClients", payload: dict) -> str:
    """Detach a schema's target and remove or archive its element (slice B).
    Per element, leaf first, `forLineage` true, never a cascade."""
    from resource_explorer.catalogue_commit import apply_leave_out
    from resource_explorer.catalogue_gateway import GatewayError
    registry = clients.require("registry")
    try:
        return apply_leave_out(registry, _catalogue_gateway(clients, payload), payload,
                               outbox_id=payload.get("_outbox_id"))
    except GatewayError as exc:
        raise OutboxApplyError(str(exc)) from exc


def _create_feedback_rating(clients: "OutboxClients", payload: dict) -> str:
    from resource_explorer.feedback_egeria import create_rating

    return create_rating(clients, payload)


def _create_feedback_note(clients: "OutboxClients", payload: dict) -> str:
    from resource_explorer.feedback_egeria import create_note

    return create_note(clients, payload)


def _create_informal_tag_link(clients: "OutboxClients", payload: dict) -> str:
    from resource_explorer.curation_egeria import create_tag_link

    return create_tag_link(clients, payload)


def _create_informal_tag_detach(clients: "OutboxClients", payload: dict) -> str:
    from resource_explorer.curation_egeria import create_tag_detach

    return create_tag_detach(clients, payload)


def _create_group_folio_membership(clients: "OutboxClients", payload: dict) -> str:
    from resource_explorer.curation_egeria import create_group_membership

    return create_group_membership(clients, payload)


def _create_group_folio_membership_detach(clients: "OutboxClients", payload: dict) -> str:
    from resource_explorer.curation_egeria import create_group_membership_detach

    return create_group_membership_detach(clients, payload)


def _wait_for_earlier_membership(clients, payload: dict, opposite_kind: str) -> None:
    """An add and a removal of the same member of the same collection apply in the order they were queued (the
    work-list re-publish; `curation_egeria._wait_for_earlier_opposite` is the tag/Folio twin). While an EARLIER
    row of the opposite kind for this pair is pending, being sent or backing off, this row is not attempted:
    `OutboxNotReadyError` hands it back with no attempt burned. Matched by the pair's `collection_membership_qn`
    (a removal's key carries a `::after-add-<id>` suffix after it)."""
    registry, row_id = getattr(clients, "registry", None), payload.get("_outbox_id")
    if registry is None or row_id is None or not payload.get("_entity_slug"):
        return
    key = collection_membership_qn(str(payload.get("collection_guid") or ""), str(payload.get("member_guid") or ""))
    for r in registry.list_outbox_rows_for_kinds(payload.get("_entity_type", ""), payload["_entity_slug"],
                                                 (opposite_kind,)):
        qn = r.get("qualified_name") or ""
        if (qn == key or qn.startswith(key + "::")) and int(r["id"]) < int(row_id) \
                and r["status"] in ("pending", "running", "failed"):
            raise OutboxNotReadyError(f"waiting for earlier {opposite_kind} row {r['id']} ({r['status']}) on {key}")


def _create_collection_membership_detach(clients: "OutboxClients", payload: dict) -> str:
    """Remove ONE member from a work list's WorkingSet (`remove_from_collection`, which detaches the
    CollectionMembership only: the collection and the member's asset are untouched).

    Destructive, so never re-sent (`DESTRUCTIVE_OUTBOX_KINDS`). Applied in queue order against the add of the same
    member (`_wait_for_earlier_membership`): while an earlier add of this member is unsettled the removal waits, so
    an add that lands late cannot leave the member in after RE removed it (and an add waits for an earlier removal
    the same way). Not the ISSUE-117 block's: that guards archive/delete of a database's elements, and this deletes
    one relationship, never an element."""
    _wait_for_earlier_membership(clients, payload, "collection_membership")
    cm = clients.require("collection_manager")
    cm.remove_from_collection(payload["collection_guid"], payload["member_guid"])
    return ""


def _guid_of(result) -> str:
    """pyegeria create_* calls variously return a GUID string, a dict, or
    nothing useful. An empty string is not an error here — the row is still
    marked done, and a later retry would find the element by qualifiedName."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("guid") or result.get("elementGUID") or ""
    return ""


# ── a run's read-back: the proof that turns "queued for publish" into "published · read back" ──────────────

#: The outbox kind, and the run_id suffixes a publish run's rows carry: the annotations under the base run_id,
#: their evidence links under `::links`, the read-back under `::read_back`.
PUBLISH_READ_BACK = "publish_read_back"
LINKS_SUFFIX = "::links"
READ_BACK_SUFFIX = "::read_back"
#: The run_id suffixes that belong to a base run (never a run of their own).
RUN_COMPANION_SUFFIXES = (LINKS_SUFFIX, READ_BACK_SUFFIX)
#: Page size of the read-back's `get_new_annotations` walk, and the guard on how many pages it reads.
READ_BACK_PAGE = 500
READ_BACK_MAX_PAGES = 20
#: The words on a done read-back row (`last_error` keeps a done row's note).
READ_BACK_DONE_WORDS = "read back"


def base_run_id(run_id: str) -> str:
    """The publish run a row belongs to: its run_id without a companion suffix."""
    for suffix in RUN_COMPANION_SUFFIXES:
        if run_id.endswith(suffix):
            return run_id[: -len(suffix)]
    return run_id


def enqueue_publish_read_back(registry, entity_type: str, entity_slug: str, publish_run_id: str,
                              report_guid: str) -> int:
    """Queue the read-back of one publish run, once (idempotent by its key). The drain applies it after every
    write of the run has landed; see `_read_back_publish`."""
    key = f"{publish_run_id}{READ_BACK_SUFFIX}"
    return registry.enqueue_outbox_element_once(
        entity_type, entity_slug, PUBLISH_READ_BACK, key,
        {"report_guid": report_guid, "publish_run_id": publish_run_id, **requester_fields()},
        run_id=key,
    )


#: The words on a read-back row that read every page it may and still could not see every annotation: neither
#: published nor failed. Kept on a done row (`last_error`), so the drain never re-reads it; `publish_proofs` says
#: "could not verify".
READ_BACK_UNVERIFIED = "could not verify"


class OutboxReadBackUnverified(RuntimeError):
    """The read-back reached its paging bound with annotations still unseen. Not a failure of any write and not a
    proof: the drain marks the row done with `READ_BACK_UNVERIFIED` and these words."""


class OutboxReadBackFailed(OutboxApplyError):
    """The run's writes landed but Egeria's answer does not hold them yet: an ordinary failed attempt (retried with
    backoff, so a slow index has time), dead after the usual attempts, in these words."""


def _read_back_publish(clients: "OutboxClients", payload: dict) -> str:
    """Read one publish run back from Egeria and return the report GUID it was read under.

    Waits (OutboxNotReadyError, no attempt burned) while any write of the run (its annotations, its `::links`) is
    still pending, failed-and-retrying or running. A dead write means the run cannot be read back whole: dead at
    once (OutboxNotKept), naming the dead write and Egeria's words for it. Otherwise it reads the report's
    annotations BY RELATIONSHIP (`DataDiscovery.get_new_annotations`, the call measured to answer them; see
    egeria_annotation_materializer.py) and requires every annotation GUID the run's rows recorded to be among
    them. An unreadable answer, or one that lacks some, is a failed attempt (OutboxReadBackFailed)."""
    registry = clients.registry
    if registry is None:
        raise OutboxApplyError("The read-back needs the registry, which the drain was not given")
    run_id = str(payload.get("publish_run_id") or "")
    report_guid = str(payload.get("report_guid") or "")
    if not run_id or not report_guid:
        raise OutboxNotKept("not read back: the row names no publish run or no report")
    rows = registry.list_outbox_rows_for_runs([run_id, f"{run_id}{LINKS_SUFFIX}"])
    # Dead first: a link waiting on an annotation that died can never land, so waiting for it would defer this row
    # on every drain for ever.
    dead = [r for r in rows if r["status"] == "dead"]
    if dead:
        first = dead[0]
        raise OutboxNotKept(f"not read back: {len(dead)} write(s) of this run failed · "
                            f"{first['element_kind']} {first['qualified_name']}: {first.get('last_error') or ''}")
    waiting = [r for r in rows if r["status"] in ("pending", "failed", "running")]
    if waiting:
        raise OutboxNotReadyError(f"waiting for {len(waiting)} write(s) of this run to land")
    expected = {(r.get("egeria_guid") or "").strip() for r in rows
                if r["element_kind"] == "annotation" and r["status"] == "done"}
    expected.discard("")
    if not expected:
        raise OutboxNotKept("not read back: no annotation of this run recorded a GUID")
    discovery = clients.require("discovery")
    seen: set[str] = set()
    # Enough pages for every annotation the run wrote, with room for a reused report's older ones.
    max_pages = max(READ_BACK_MAX_PAGES, -(-len(expected) // READ_BACK_PAGE) + 2)
    ended = False
    for page in range(max_pages):
        try:
            answer = discovery.get_new_annotations(report_guid, start_from=page * READ_BACK_PAGE,
                                                   page_size=READ_BACK_PAGE, output_format="JSON")
        except Exception as exc:
            raise OutboxReadBackFailed(f"the read-back could not read report {report_guid}: "
                                       f"{type(exc).__name__}: {str(exc)[:EGERIA_DETAIL_LIMIT]}") from exc
        if isinstance(answer, str):
            from resource_explorer.egeria_absence import ABSENT, is_absent

            if is_absent(answer) == ABSENT:
                ended = True
                break
            raise OutboxReadBackFailed(f"the read-back got an unreadable answer: {answer[:EGERIA_DETAIL_LIMIT]!r}")
        if not isinstance(answer, list):
            raise OutboxReadBackFailed(f"the read-back got an unreadable answer: {type(answer).__name__}")
        for el in answer:
            guid = ((el or {}).get("elementHeader") or {}).get("guid") if isinstance(el, dict) else ""
            if guid:
                seen.add(guid)
        if len(answer) < READ_BACK_PAGE:
            ended = True
            break
    missing = expected - seen
    if missing and not ended:
        raise OutboxReadBackUnverified(
            f"{READ_BACK_UNVERIFIED}: read {max_pages} page(s) of report {report_guid} and {len(missing)} of "
            f"{len(expected)} annotation(s) were not among them; the report holds more than the read-back reads")
    if missing:
        raise OutboxReadBackFailed(f"read back {len(expected) - len(missing)} of {len(expected)} annotation(s) "
                                   f"under report {report_guid}; Egeria does not answer {len(missing)} yet")
    return report_guid


#: element_kind -> creator. An unregistered kind raises rather than silently
#: succeeding — a row that reports 'done' without writing anything is precisely
#: the confident-wrong-answer failure this whole subsystem exists to remove.
#:
#: `asset` and `report` are deliberately absent: the repo publisher creates
#: both synchronously because `publish()` returns the report GUID and callers
#: record it, and the investigation publisher does the same with the Project
#: and Collection GUIDs. Only the plural, per-element writes are queued.
_CREATORS: dict[str, Callable[["OutboxClients", dict], str]] = {
    "annotation": _create_annotation,
    "collection_membership": _create_collection_membership,
    "resource_list": _create_resource_list,
    "annotation_link": _create_annotation_link,
    "doc_source_publish": _create_doc_source_publish,
    "doc_source_unpublish": _create_doc_source_unpublish,
    "catalogue_schema_attach": _create_catalogue_schema_attach,
    "catalogue_schema_leave_out": _create_catalogue_schema_leave_out,
    "informal_tag_link": _create_informal_tag_link,
    "informal_tag_detach": _create_informal_tag_detach,
    "group_folio_membership": _create_group_folio_membership,
    "group_folio_membership_detach": _create_group_folio_membership_detach,
    COLLECTION_MEMBERSHIP_DETACH: _create_collection_membership_detach,
    "feedback_rating": _create_feedback_rating,
    "feedback_note": _create_feedback_note,
    PUBLISH_READ_BACK: _read_back_publish,
}


def drain_outbox(registry, clients: "OutboxClients | None" = None, find_element_guid=None, *,
                 limit: int = DRAIN_BATCH, run_id: str | None = None,
                 element_id: int | None = None, identity=None) -> dict:
    """One drain pass, as `identity` (Brief I, owner's rule: a person's direct acts are made as
    that person). The background loop passes `Daemon(OUTBOX)`; an inline drain inside a
    person's request (a doc-source add/remove, a publish's link drain, a catalog commit) runs
    as the signed-in Caller, `current_principal()`. With neither, nothing is claimed and nothing
    is sent: the summary says so (`identity_error`). See `_drain_outbox`."""
    from contextlib import nullcontext

    from resource_explorer.egeria_clients import acting_as, current_principal

    if identity is None:
        try:
            identity = current_principal()
        except PermissionError as exc:                     # no caller / expired: never the daemon
            if clients is None or find_element_guid is None:
                log.warning("Outbox drain not attempted: %s", exc)
                return {"claimed": 0, "done": 0, "failed": 0, "dead": 0, "skipped": 0,
                        "publish_run_check_failed": 0, "identity_error": str(exc)}
            # Round 7 (CI, PR #579): a drain HANDED its clients (the investigation publisher's own
            # CollectionManager) needs no ambient identity — those clients already carry who acts,
            # and nothing here builds one. A row that would build its own (doc-source, catalog)
            # still refuses per row with NoCallerIdentity. Never the daemon.
            identity = None
    scope = acting_as(identity) if identity is not None and identity.kind == "daemon" else nullcontext()
    with scope:
        return _drain_outbox(registry, clients, find_element_guid, limit=limit, run_id=run_id,
                             element_id=element_id, identity=identity)


def _drain_outbox(registry, clients: "OutboxClients | None" = None, find_element_guid=None, *,
                  limit: int = DRAIN_BATCH, run_id: str | None = None,
                  element_id: int | None = None, identity=None) -> dict:
    """One drain pass. Returns a summary dict; never raises.

    Called from `scheduler.py`'s existing loop, once per iteration — the same
    loop that already drives `reconcile_rfa_actions()`, rather than starting a
    second runtime. It runs everywhere publishes originate, which is what the
    design means by putting the layer under the publishers rather than under
    either survey-launch path.

    `discovery` and `find_element_guid` are injected so this is testable
    without a live platform, and are resolved lazily from the repo publisher
    when not supplied — that publisher already owns a connected client and a
    proven `_find_element_guid`.

    `element_id` scopes this pass to exactly one outbox row — the "attempt
    this write right now" case (`doc_sources.py`'s add/remove routes, Egeria
    publish-state fix round 3, 2026-09-29): enqueue, then attempt that one
    row immediately, off the request thread, rather than only relying on the
    15-minute scheduler loop. This is deliberately the SAME function the
    scheduler calls for its full drain, not a second copy — `element_id`
    only narrows which rows `claim_due_outbox_elements` claims; every other
    step (apply, retry bookkeeping, dead-lettering, `record_drain_outcome`,
    publish-run completion) is unchanged, so a scoped call behaves exactly
    like the full drain would have behaved on that one row, including
    leaving it for the normal 15-minute retry loop on failure (see
    `mark_outbox_failed` below — nothing about a scoped call skips backoff
    or dead-lettering).
    """
    summary = {"claimed": 0, "done": 0, "failed": 0, "dead": 0, "skipped": 0,
              "publish_run_check_failed": 0}
    # Brief F: a drain that is not a person's own request (the background Daemon drain, or a drain handed its
    # clients with no person) never claims a feedback row: only its author's Publish sends it, as them.
    is_caller = identity is not None and getattr(identity, "kind", "") == "caller"
    try:
        rows = registry.claim_due_outbox_elements(limit=limit, run_id=run_id, element_id=element_id,
                                                  exclude_kinds=None if is_caller else PERSON_ONLY_KINDS)
    except Exception:
        log.exception("Outbox drain: could not read due rows")
        return summary
    if not rows:
        return summary
    summary["claimed"] = len(rows)

    if clients is None or find_element_guid is None:
        try:
            clients, find_element_guid = _default_clients(identity)
        except OutboxIdentityError as exc:
            # A configuration error, not an outage: say so on every row and in the log, and
            # make no Egeria call at all. Pending, attempts untouched, so the first drain after
            # the configuration is fixed takes them as if nothing had happened.
            for r in rows:
                registry.mark_outbox_deferred(r["id"], f"Not attempted - configuration error: {exc}",
                                              claimed_at=r.get("claimed_at"))
            log.error("Outbox drain: %s (%d row(s) left pending, none attempted)", exc, len(rows))
            summary["config_error"] = str(exc)
            summary["skipped"] = len(rows)
            return summary
        except Exception:
            # No platform reachable. Leave every row exactly as it is —
            # pending work is not failed work, and burning an attempt on
            # "Egeria was down" is how a transient outage dead-letters a
            # perfectly good write.
            # Hand the claims back. These rows are claimed at this point, and
            # an outage must leave them exactly as they were — not parked in
            # 'running' until the lease expires.
            registry.release_outbox_claim([r["id"] for r in rows])
            log.warning("Outbox drain: no Egeria client available, leaving %d rows pending",
                        len(rows))
            summary["skipped"] = len(rows)
            return summary

    if getattr(clients, "registry", None) is None:
        clients.registry = registry      # the catalogue kinds write their proof rows here

    troubled_runs: dict[str, str] = {}
    #: run_ids (the annotation qualified_name_prefix, never its `::links`
    #: companion — see below) touched by this pass, so completion can be
    #: checked once per pass instead of once per row.
    touched_run_ids: set[str] = set()
    for row in rows:
        run_id = row.get("run_id") or ""
        if run_id:
            # `::links` is a companion run_id, not a run of its own (see
            # egeria_publisher.py's `_link_evidence_outbox` / `_create_
            # annotations`'s deferred branch) — completion is checked against
            # the BASE run_id, which is what the activity row was tagged
            # with (see registry.complete_publish_run_if_done).
            touched_run_ids.add(base_run_id(run_id))
        not_theirs = _person_only_refusal(row, identity)
        if not_theirs:
            # Brief F: claimed by a drain that is not its author's (another person's request, or a claim before
            # the exclusion): hand it back untouched, no attempt burned, nothing sent.
            registry.mark_outbox_deferred(row["id"], not_theirs, claimed_at=row.get("claimed_at"))
            summary["skipped"] += 1
            continue
        try:
            # Every outbox creator is a write (the module docstring's own
            # comment on _CREATORS: "only the plural, per-element writes are
            # queued") -- `row["element_kind"]` names WHICH write
            # (annotation/collection_membership/resource_list/annotation_
            # link), the same per-call-name granularity the layer-2
            # catalogue-depth offer needs and nothing before this recorded.
            _private_findings_await_owner(registry, row, identity)
            refused = _queued_write_refusal(registry, row, identity)
            if refused:
                raise OutboxNotPermitted(refused)
            is_read = row.get("element_kind") == PUBLISH_READ_BACK
            with time_egeria_call(registry, f"outbox:{row.get('element_kind', 'unknown')}",
                                  "query" if is_read else "write"), \
                    _row_identity(row, identity):
                guid = apply_element(row, clients, find_element_guid,
                                     resolve_row_guids=registry.get_outbox_guids)
            done_note = READ_BACK_DONE_WORDS if is_read else ""
            if row.get("element_kind") == "collection_membership":
                # Timed apart from the write: the read-back is a query, not part of the write's latency.
                with time_egeria_call(registry, "outbox:collection_membership:read_back", "query"), \
                        _row_identity(row, identity):
                    done_note = verify_membership_landed(row, clients)
        except OutboxReadBackUnverified as exc:
            # Neither published nor failed: done, with its words, so the drain never re-reads it.
            registry.mark_outbox_done(row["id"], "", note=str(exc))
            summary["done"] += 1
            log.warning("Outbox row %s (%s) %s", row["id"], row["qualified_name"], exc)
            continue
        except OutboxNotReadyError as exc:
            # Not a failure: this link's referent annotation(s) have not
            # landed yet. Hand it back to 'pending' with no attempt burned —
            # see mark_outbox_deferred and OutboxNotReadyError's own
            # docstring for why this is not routed through mark_outbox_failed.
            registry.mark_outbox_deferred(row["id"], str(exc), claimed_at=row.get("claimed_at"),
                                          delay_seconds=getattr(exc, "DELAY_SECONDS", 0))
            summary["skipped"] += 1
            log.info("Outbox row %s (%s %s) deferred: %s",
                     row["id"], row["element_kind"], row["qualified_name"], exc)
            continue
        except OutboxNotPermitted as exc:
            # Brief Z2 round 2: the requester may not change the resource any more (or nobody asked and zones are
            # in use). Nothing was sent; permanent until a zone or grant changes: dead at once, said in its words.
            status = registry.mark_outbox_failed(row["id"], str(exc), max_attempts=1)
            summary["dead" if status == "dead" else "failed"] += 1
            summary["not_permitted"] = summary.get("not_permitted", 0) + 1
            troubled_runs[row.get("run_id") or ""] = row.get("entity_slug") or ""
            log.warning("Outbox row %s (%s %s) not permitted: %s",
                        row["id"], row["element_kind"], row["qualified_name"], exc)
            continue
        except Exception as exc:
            exc_text = _describe_refusal(exc, clients)
            if row.get("element_kind") == COLLECTION_MEMBERSHIP_DETACH and is_security_refusal(exc):
                # A work list's removal Egeria's security refused: "not permitted", in Egeria's words, dead at once
                # (destructive: never re-sent either way); the publish line counts it with the press-time refusals.
                status = registry.mark_outbox_failed(
                    row["id"], f"not permitted · {REFUSED_BY_SECURITY}: {_refusal_sentence(exc)}", max_attempts=1)
                summary["dead" if status == "dead" else "failed"] += 1
                summary["not_permitted"] = summary.get("not_permitted", 0) + 1
            elif is_destructive_outbox_kind(row.get("element_kind") or ""):
                # max_attempts=1: this failure is the last attempt. 'dead' is the existing terminal state.
                status = registry.mark_outbox_failed(row["id"], f"{NOT_RETRIED} · {exc_text}", max_attempts=1)
                summary["not_retried"] = summary.get("not_retried", 0) + 1
            elif is_security_refusal(exc):
                # Permanent until a permission or zone changes: dead on the FIRST refusal, no backoff.
                # (Brief F: a feedback row reads this as "not permitted" from the stored words.)
                status = registry.mark_outbox_failed(
                    row["id"],
                    f"{REFUSED_BY_SECURITY}"
                    f"{' (the look-up before the write; nothing was sent)' if isinstance(exc, OutboxLookupFailed) else ''}"
                    f": {_refusal_sentence(exc)} · {NEEDS_PERMISSION_CHANGE}",
                    max_attempts=1)
                summary["dead" if status == "dead" else "failed"] += 1
                summary["security_refused"] = summary.get("security_refused", 0) + 1
            elif isinstance(exc, OutboxNotKept):
                # A resend carries the same body and meets the same answer: dead on the first failure, its words.
                status = registry.mark_outbox_failed(row["id"], str(exc), max_attempts=1)
                summary["dead" if status == "dead" else "failed"] += 1
            elif is_person_only_kind(row.get("element_kind") or ""):
                # Brief F: never re-sent on its own (a retry would be the drain choosing when to write as the
                # person): dead at once with Egeria's words; the person's next Publish queues a new row.
                status = registry.mark_outbox_failed(row["id"], f"{type(exc).__name__}: {exc_text}", max_attempts=1)
                summary["dead" if status == "dead" else "failed"] += 1
            else:
                status = registry.mark_outbox_failed(row["id"], f"{type(exc).__name__}: {exc_text}")
                summary["dead" if status == "dead" else "failed"] += 1
            troubled_runs[row.get("run_id") or ""] = row.get("entity_slug") or ""
            log_at = log.error if status == "dead" else log.warning
            log_at("Outbox row %s (%s %s) -> %s: %s",
                   row["id"], row["element_kind"], row["qualified_name"], status, exc)
            continue
        registry.mark_outbox_done(row["id"], guid, note=done_note)
        summary["done"] += 1

    if summary["dead"]:
        log.error("Outbox drain: %d row(s) dead-lettered and need a human — "
                  "see registry.list_dead_outbox_elements()", summary["dead"])
    # Logging is not a surface here — see record_drain_outcome's docstring.
    record_drain_outcome(registry, summary, troubled_runs=troubled_runs)

    # A background/deferred publish's activity row reports `published:
    # "queued"` until every row of its run (its annotations AND its `::links`
    # companion) has reached a terminal state — see egeria_publisher.py's
    # `_create_annotations` docstring and workflows/analysis.py's
    # AnalysisRunResult docstring for the three-state contract. Checked once
    # per run_id touched by THIS pass, not on every pass, so an idle drain
    # with nothing to claim does not re-scan every "queued" activity row.
    for base_run in touched_run_ids:
        try:
            registry.complete_publish_run_if_done(base_run)
        except Exception:
            # Best-effort bookkeeping: a failure here must not fail the drain
            # that already succeeded above, but it also must not vanish into
            # a log line nobody reads (the exact failure this ratchet exists
            # to catch) — counted so a caller inspecting the returned summary
            # can tell "nothing to check" from "checked and failed".
            summary["publish_run_check_failed"] += 1
            log.exception("Could not check publish-run completion for run_id %s", base_run)

    return summary


def drain_outbox_row(registry, element_id: int, clients: "OutboxClients | None" = None,
                      find_element_guid=None, *, identity=None) -> dict:
    """Attempt exactly one outbox row right now. A thin, named convenience
    over `drain_outbox(..., element_id=...)` — same function, same apply/
    retry/dead-letter logic, nothing duplicated — for callers (`doc_sources.
    py`'s add/remove routes) that want "attempt this one write immediately"
    without spelling out `limit`/`run_id` at every call site. Never raises
    (drain_outbox itself never does); on failure the row is left exactly as
    `mark_outbox_failed` leaves any row, so it falls back to the normal
    15-minute scheduler drain like every other outbox row.
    """
    return drain_outbox(registry, clients, find_element_guid, element_id=element_id,
                        identity=identity)


def drain_identity(identity=None):
    """The identity a drain calls Egeria as: the one it was given (the background loop's
    `Daemon(OUTBOX)`, or the signed-in Caller for an inline drain), else `Daemon(OUTBOX)`.
    A blank daemon user id raises `OutboxIdentityError` rather than reaching Egeria.
    """
    from resource_explorer.egeria_clients import Daemon, DaemonReason

    identity = identity or Daemon(DaemonReason.OUTBOX)
    if identity.kind == "daemon" and not (identity.client_user or identity.user_id or "").strip():
        raise OutboxIdentityError(
            "The Egeria service identity has no user id, so the outbox cannot write to Egeria. "
            "Set EGERIA_USER_ID (and EGERIA_USER_PASSWORD) for the worker."
        )
    return identity


#: Bound on Egeria's own words appended to an outbox row's last_error.
EGERIA_DETAIL_LIMIT = 300


def _describe_refusal(exc: Exception, clients: "OutboxClients") -> str:
    """`str(exc)`, plus who was refused when Egeria said 401/403.

    pyegeria fills the user into that message from a field its Egeria-wrapped branch never
    sets, so the text ends "for user - ``" whoever was sent. Left alone, a row's last_error
    reads as "no user was sent" and sends the next reader after the wrong cause.
    """
    from resource_explorer.egeria_absence import egeria_error_detail

    text = str(exc)
    # pyegeria's 400 text names only the URL; Egeria's own reason (the FFDC body) is kept, bounded.
    said = egeria_error_detail(exc, limit=EGERIA_DETAIL_LIMIT)
    if said:
        text = f"{text} · Egeria said: {said}"
    if not any(type(e).__name__ == "PyegeriaUnauthorizedException" for e in _self_and_cause(exc)):
        return text
    who = getattr(clients, "acting_as", "") or "(not recorded)"
    return (f"{text} [Egeria refused the write for service identity {who!r}; pyegeria shows an "
            "empty user for this response shape even when one was sent]")


def _default_clients(identity=None):
    """The repo publisher's own connected clients — one client path, not a
    second one to keep in step with it.

    2026-09-04: was missing collection_manager (EgeriaPublisher._connect()
    didn't build one at all until this same fix added it) — every scheduled
    drain of a collection_membership row (blueprint member attachment among
    them) failed with "This element needs the 'collection_manager' client,
    which the drain was not given", confirmed live, same 3 rows failing on
    every 15-minute cycle. Blueprints materialized fine (a different outbox
    kind); their members never actually got attached."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    identity = drain_identity(identity)
    publisher = EgeriaPublisher(identity=identity)
    publisher._connect()
    factory = getattr(publisher, "_clients", None)     # the publisher's EgeriaClients (one token per identity)

    def feedback_client():
        from pyegeria import ClassificationExplorer

        return factory.of(ClassificationExplorer)

    return (
        OutboxClients(discovery=publisher._discovery, metadata_expert=publisher._metadata_expert,
                      collection_manager=publisher._collection_manager,
                      # Brief T's tag kinds: the feedback-manager calls, from the same factory and token.
                      feedback_factory=feedback_client if factory is not None else None,
                      acting_as=identity.client_user or identity.user_id),
        publisher._find_element_guid,
    )


#: Most annotations one publish will queue. Beyond this the run is capped and
#: the remainder is REPORTED, never silently dropped.
#:
#: Chosen from measured runs, 2026-09-02:
#:
#:     largest legitimate run     522  (data_prep_kit — 357 per-file
#:                                      SchemaAnalysisAnnotations plus
#:                                      classifications and measures)
#:     next legitimate            184, 134, 129, 127, 115 ...
#:     pathological             8,980  (pre-fix Committed Secrets, egeria_python_git)
#:                             48,583  (the same scan, full finding count)
#:
#: 2000 sits roughly 4x above anything legitimate that has been observed and
#: an order of magnitude below the failure it exists for. It is a safety net
#: against a rule regression or a pathological repo, NOT a policy about how
#: many annotations an analysis should emit — a surveyor whose normal output
#: approaches this should be emitting a summary with linked evidence instead.
_MAX_ANNOTATIONS_PER_RUN = 2000

def enqueue_annotations(
    registry, entity_type: str, entity_slug: str, annotations: list,
    report_guid: str, qualified_name_prefix: str, *, run_id: str = "",
) -> list[int]:
    """Record one outbox row per annotation. Returns the row ids, in order.

    This is the write half of write-then-publish. The rows are durable before
    anything is sent, so the per-process hole closes: a crash after this
    returns leaves work the drain will finish, where previously it left
    nothing at all.

    `qualified_name_prefix` carries this run's own timestamp and the caller
    owns its format entirely — the same contract `publish_annotations` has.
    That is what makes a retry replay a byte-identical qualifiedName instead
    of minting a new identity, so the replay converges on the element already
    written rather than duplicating it.

    No `depends_on_id`: the caller creates the SurveyReport synchronously and
    passes its GUID, so the dependency these rows have is already satisfied
    before they exist. Ordering matters when the report is itself queued —
    which is what step 4's later publishers will need, and why the column is
    there now.
    """
    from resource_explorer.surveyors.annotation_props import build_annotation_body

    assert_unique_qualified_names(qualified_name_prefix, annotations)

    total = len(annotations)
    capped = annotations[:_MAX_ANNOTATIONS_PER_RUN]
    if total > _MAX_ANNOTATIONS_PER_RUN:
        # Loud, and at warning level: a silently truncated publish looks
        # exactly like a complete one. The caller reports the shortfall to
        # the user by comparing len(row_ids) against len(annotations) — see
        # egeria_publisher.publish()'s cap_warning.
        log.warning(
            "Outbox enqueue capped for %s/%s: %d annotations produced, %d queued, "
            "%d NOT published. This is a safety net against a rule regression or "
            "a pathological resource; if it is legitimate output, the analysis "
            "should emit a summary with linked evidence instead of one annotation "
            "per finding.",
            entity_type, entity_slug, total, _MAX_ANNOTATIONS_PER_RUN,
            total - _MAX_ANNOTATIONS_PER_RUN,
        )

    row_ids: list[int] = []
    # enumerate over the CAPPED list but keep each annotation's ORIGINAL
    # index: qualified_name is f"{prefix}::{i}" and that string is the
    # retry-convergence identity. Renumbering after a cap would mint a
    # different qualifiedName for the same annotation on a re-run, so a
    # replay would create a second element instead of converging on the one
    # already written — the exact duplication the prefix contract exists to
    # prevent.
    #
    # Taking the FIRST n also matters: surveyors emit summary-then-detail
    # (secret_scan's ruleset-freshness and scan-summary annotations are
    # indices 0 and 1, with per-match findings after), so a head cap keeps
    # the annotations that describe the run and drops repetitive detail. A
    # surveyor emitting detail first would lose its summary — which is why
    # the cap is logged at warning rather than quietly applied.
    for i, ann in enumerate(capped):
        qualified_name = annotation_qualified_name(qualified_name_prefix, i, ann)
        body = build_annotation_body(ann, qualified_name, report_guid)
        row_ids.append(registry.enqueue_outbox_element(
            entity_type, entity_slug, "annotation", qualified_name, body, run_id=run_id,
        ))
    return row_ids


def enqueue_annotation_links(
    registry, entity_type: str, entity_slug: str, links: list[dict], *, run_id: str = "",
) -> list[int]:
    """Record one outbox row per same-run `AnnotationExtension` link.
    Returns the row ids, in order. annotation-linking-plan Phase 2, Tier 1.

    Each `link` dict is ONE of two shapes:

    * ``{"summary_guid", "evidence_guid"}`` — both ends are real Egeria GUIDs
      already, because the annotations were drained (inline publish) before
      this is called; see `_create_annotation_link`'s docstring for
      direction. The caller (`egeria_publisher.py::_link_evidence_outbox`) is
      responsible for filtering out any pair where either GUID is missing (a
      failed create) BEFORE calling this — enqueuing a link to a GUID that
      does not exist would just fail loudly later for a reason this module
      cannot diagnose.
    * ``{"summary_row_id", "evidence_row_id"}`` — both ends are THIS RUN'S OWN
      outbox row ids for the annotations that will be linked, used when a
      background/deferred publish enqueues its annotations WITHOUT draining
      them (see `egeria_publisher.py::_create_annotations`'s deferred
      branch) — there is no real GUID to give yet. `apply_element` resolves
      these to real GUIDs at APPLY time via `registry.get_outbox_guids`
      (`_resolve_link_referents`); if a referent row has not reached 'done'
      yet, the link row is left queued (`OutboxNotReadyError`, not a failure)
      and retried on a later drain. Rows drain in id order, so in practice a
      run's own annotations apply before its own links are even attempted.

    The qualifiedName is synthetic either way, same basis as
    `enqueue_resource_list`/`enqueue_collection_members` below:
    `AnnotationExtension` has no qualifiedName of its own, so this string
    exists purely as the outbox's idempotency key. Replay safety comes from
    the relationship being uni-link (see `_create_annotation_link`), not from
    this key resolving to anything searchable in Egeria. The row-id form's
    qualifiedName is built from the row ids rather than GUIDs, since none
    exist yet — still stable across retries of the same enqueue.
    """
    row_ids: list[int] = []
    for link in links:
        if "summary_guid" in link and "evidence_guid" in link:
            summary_guid, evidence_guid = link["summary_guid"], link["evidence_guid"]
            qualified_name = f"AnnotationExtension::{summary_guid}::{evidence_guid}"
            payload = {"summary_guid": summary_guid, "evidence_guid": evidence_guid}
        else:
            summary_row_id, evidence_row_id = link["summary_row_id"], link["evidence_row_id"]
            qualified_name = f"AnnotationExtension::row{summary_row_id}::row{evidence_row_id}"
            payload = {"summary_row_id": summary_row_id, "evidence_row_id": evidence_row_id}
        row_ids.append(registry.enqueue_outbox_element(
            entity_type, entity_slug, "annotation_link", qualified_name, payload, run_id=run_id,
        ))
    return row_ids


def enqueue_resource_list(
    registry, entity_slug: str, parent_guid: str, collection_guid: str, *, run_id: str = "",
) -> int:
    """Record the Project -> Collection ResourceList attach as a queued write.

    Same basis as membership: `ResourceList` via `attach_collection` was measured
    uni-link (2026-08-25), returning None and leaving one attachment however many
    times it is called, so replay converges. The qualifiedName is synthetic — the
    relationship has none — and exists only as the outbox's identity key.

    This one element is queued rather than written directly because its failure
    was previously appended to `PromotionResult.errors` and then forgotten: the
    Project and Collection both existed, unlinked, and nothing ever tried again.
    """
    return registry.enqueue_outbox_element(
        "investigation", entity_slug, "resource_list",
        f"ResourceList::{parent_guid}::{collection_guid}",
        {"parent_guid": parent_guid, "collection_guid": collection_guid, **requester_fields()},
        run_id=run_id,
    )


def collection_membership_qn(collection_guid: str, member_guid: str) -> str:
    """The outbox identity key for one CollectionMembership row.

    One definition for the writer (`enqueue_collection_members`, the publisher's
    per-member report) and the reader (`link-status`), so what the link decides
    to attach and what the status says is attached cannot drift apart.
    """
    return f"CollectionMembership::{collection_guid}::{member_guid}"


def enqueue_collection_members(
    registry, entity_slug: str, collection_guid: str,
    members: list[dict], *, run_id: str = "",
) -> list[int]:
    """Record one row per investigation member to attach. Returns row ids.

    `members` are dicts with `member_guid` and, for the record, the member's
    own `entity_type`/`entity_slug`. Members with no asset GUID must be
    filtered out BEFORE calling this — a member whose repo was never published
    has nothing to attach, which is a reportable fact about the investigation,
    not a failed write to retry.

    The qualifiedName is synthetic. A CollectionMembership has no qualifiedName
    of its own, so this string exists purely as the outbox's identity key —
    stable across retries of the same run, and never searched for in Egeria.
    Replay safety comes from the relationship being uni-link instead (see
    `_create_collection_membership`).

    Idempotent per (entity, kind, qualified_name): a repeated call returns the existing row ids rather
    than queueing a second set (see `enqueue_outbox_element_once`).
    """
    row_ids: list[int] = []
    for m in members:
        member_slug = m.get("entity_slug", "")
        qualified_name = collection_membership_qn(collection_guid, m["member_guid"])
        row_ids.append(registry.enqueue_outbox_element_once(
            "investigation", entity_slug, "collection_membership", qualified_name,
            {"collection_guid": collection_guid, "member_guid": m["member_guid"],
             "member_entity_type": m.get("entity_type", ""), "member_entity_slug": member_slug,
             **requester_fields()},
            run_id=run_id,
        ))
    return row_ids


def enqueue_blueprint_members(
    registry, entity_type: str, entity_slug: str, blueprint_guid: str,
    member_guids: list[str], *, run_id: str = "",
) -> list[int]:
    """One row per member (a `SolutionComponent` or a child `SolutionBlueprint`
    — both are ordinary `CollectionMembership` attachments to the blueprint's
    Collection) attached to a blueprint. docs/blueprint-materialization-plan.md
    Decision 5/Phase B.

    Reuses `element_kind="collection_membership"` unchanged — same creator,
    same idempotency basis as `enqueue_collection_members` above
    (`CollectionMembership` measured uni-link 2026-08-25; `add_to_collection`
    is a safe upsert regardless of which kind of Collection the blueprint
    is). Parallel in shape to `enqueue_collection_members`, not a call to it,
    because that function hardcodes `entity_type="investigation"` and this
    caller's entity is a repo (or whatever `entity_type` the blueprint's own
    resource is) — the member's own identity fields that function also
    records don't apply here, since a blueprint member is always this same
    resource's own component/child-blueprint, not a cross-resource reference.

    No `depends_on_id`: unlike annotation links, the blueprint GUID and every
    member GUID passed in are already resolved synchronously before this is
    called (Decision 5) — nothing here is still in flight.

    Idempotent per (entity, kind, qualified_name): a repeated verdict returns the existing row ids rather
    than queueing a second set (2026-10-07: egeria_git got 14 rows where 7 were meant). A dead row is
    revived, not duplicated (see `enqueue_outbox_element_once`).
    """
    row_ids: list[int] = []
    for member_guid in member_guids:
        qualified_name = f"CollectionMembership::{blueprint_guid}::{member_guid}"
        row_ids.append(registry.enqueue_outbox_element_once(
            entity_type, entity_slug, "collection_membership", qualified_name,
            {"collection_guid": blueprint_guid, "member_guid": member_guid},
            run_id=run_id,
        ))
    return row_ids


def requester_fields() -> dict:
    """What a queued investigation or work-list row records about who asked (Brief Z3), so the drain re-checks
    THEM: `by`, and `portal_role` only when the verified caller (the ContextVar) holds a Portal curator/admin
    role. Empty for a row nobody asked for (the no-requester rule then applies at drain time)."""
    by = _requester_now()
    if not by:
        return {}
    from resource_explorer.workflows.curate import CURATOR_ROLES, _caller_identity

    ident = _caller_identity()
    override = bool(ident and ident.user_id == by and (ident.role or "").lower() in CURATOR_ROLES)
    return {"by": by, **({"portal_role": True} if override else {})}


def _requester_now() -> str:
    """Who is asking, recorded on a queued documentation-source row (Brief Z2 round 2) so the drain re-checks
    them, never the service account."""
    from resource_explorer.run_queue import requested_by

    try:
        return requested_by() or ""
    except Exception:  # noqa: BLE001 - no caller: the row is unattributed and follows the no-requester rule
        return ""


def enqueue_doc_source_publish(
    registry, entity_type: str, entity_slug: str, source_id: str, url: str, *, run_id: str = "",
) -> int:
    """Queue a best-effort Egeria `ExternalReference` publish for one
    already-declared documentation source, for a resource that is ALREADY
    published. Egeria publish-state fix (2026-09-29): previously a source
    added (or found un-published on a read) after the resource's own publish
    stayed local-only until the next full re-publish; this queues the write
    for the outbox's next drain instead, same as any other best-effort
    Egeria write in this codebase.

    Called from `web/routes/doc_sources.py`'s `add_doc_source` when the
    resource is already published, and from its `_compute_egeria_state`
    self-heal path when a GET finds a source with no `egeria_external_ref_
    guid` AND no outbox row already tracking it (a source declared before
    this fix, or a narrow race between "declare" and "this resource's
    publish landing"). An unpublished resource's source stays local — same
    as before this fix — and nothing is queued for it; see `_compute_egeria_
    state`'s `local_only` branch.

    `qualified_name` mirrors `doc_source_egeria._qualified_name` exactly
    (`ExternalReference::<url>`) so a retry — or a concurrent full re-publish
    via `publish_local_doc_sources` — converges on the SAME element rather
    than minting a second one under a different identity. `source_id` (not
    derivable from `qualified_name` alone — see `get_doc_source_outbox_row`'s
    docstring) travels in the payload so the creator can resolve and write
    back to the exact local row.
    """
    qualified_name = f"ExternalReference::{url}"
    return registry.enqueue_outbox_element(
        entity_type, entity_slug, "doc_source_publish", qualified_name,
        {"entity_type": entity_type, "entity_slug": entity_slug, "source_id": source_id, "by": _requester_now()},
        run_id=run_id,
    )


def enqueue_doc_source_unpublish(
    registry, entity_type: str, entity_slug: str, ref_guid: str, *, run_id: str = "",
) -> int:
    """Queue a best-effort Egeria `ExternalReference` detach+delete for a
    just-removed documentation source's own reference — only this one GUID,
    same "and only that one" rule `unpublish_doc_source` already documents.

    Called from `web/routes/doc_sources.py`'s DELETE handler right after the
    local `doc_sources` row is gone — there is no more local state to write
    back to; this row exists purely so the Egeria-side removal retries if
    Egeria is unreachable at that moment, rather than a one-shot best-effort
    attempt with no second try (the gap this fix closes on the removal side)."""
    qualified_name = f"ExternalReferenceRemoval::{ref_guid}"
    return registry.enqueue_outbox_element(
        entity_type, entity_slug, "doc_source_unpublish", qualified_name,
        {"entity_type": entity_type, "entity_slug": entity_slug, "ref_guid": ref_guid, "by": _requester_now()},
        run_id=run_id,
    )


def record_drain_outcome(
    registry, summary: dict, entity_type: str = "", entity_slug: str = "",
    troubled_runs: dict[str, str] | None = None,
) -> None:
    """Put a drain's failures where a person will actually see them.

    A log line is not an actionable surface, even now that it is a visible one.
    When this was written nothing in the package configured logging, so INFO was
    discarded outright and WARNING reached only the server's stderr; that was
    fixed on 2026-08-31 by `observability/logging_setup.py`, which the outbox's
    own finding prompted. The conclusion is unchanged and the reason is now the
    better one: a stuck publish needs a place a person returns to and can act
    from, not a line in a stream nobody is watching at 3am. The activity log is
    that place, and a dead letter goes further and raises an RFA.

    So anything worse than "everything worked" is written to the activity log,
    which the Activity tab already reads. A clean pass writes nothing — an
    entry per quarter-hour tick saying "nothing was wrong" is how a log stops
    being read.
    """
    if not summary.get("failed") and not summary.get("dead") and not summary.get("not_retried"):
        return
    from resource_explorer.activity_logger import log_catalog, log_rfa

    not_retried = summary.get("not_retried", 0)
    dead, failed = summary.get("dead", 0) + not_retried, summary.get("failed", 0)
    status = "error" if dead else "warning"
    parts = []
    if not_retried:
        parts.append(f"{not_retried} destructive write(s) failed and were NOT retried (press again to retry)")
    refused = summary.get("security_refused", 0)
    if refused:
        parts.append(f"{refused} element(s) refused by Egeria's security (need a permission or zone change; not retried)")
    if dead:
        if dead - not_retried - refused:
            parts.append(f"{dead - not_retried - refused} element(s) dead-lettered after exhausting retries")
    if failed:
        parts.append(f"{failed} element(s) failed and will retry")
    # The entry is a SUMMARY and says so; the record itself is the outbox rows.
    # `items` carries the pointer the Activity tab turns into a link into
    # Admin → Publish Queue, filtered to the affected run — so "3 elements
    # dead-lettered" is one click from *which* three, their qualifiedNames,
    # their attempt counts and what Egeria actually said. Putting that detail
    # in the entry text instead would make the activity log the log, which it
    # is not: it is an audit trail of operations, one line each.
    runs = troubled_runs or {}
    items = [
        {"kind": "publish_queue", "name": run_id or "(unattributed)",
         "link": "admin-outbox", "run_id": run_id, "entity_slug": slug}
        for run_id, slug in sorted(runs.items())
    ] or [{"kind": "publish_queue", "name": "all pending elements",
           "link": "admin-outbox", "run_id": "", "entity_slug": ""}]
    detail = (
        f"Outbox drain: {summary}. "
        "Full per-element record — qualifiedName, attempts, and Egeria's own "
        "error for each — is in Admin → Publish Queue."
    )
    try:
        if dead:
            # A dead-lettered element will NEVER be retried on its own, so it is
            # a request for human action, not a status line — and it goes to the
            # RFA drawer, which is the surface people actually watch. One entry,
            # not two: log_rfa writes an activity entry as well, so also calling
            # log_catalog would double-report a single event.
            log_rfa(
                registry, entity_type or "repo", entity_slug or "", "",
                status="error",
                summary="Egeria publish stuck — " + "; ".join(parts),
                detail=detail + (
                    " These will not be retried without intervention; re-queue them "
                    "there once the underlying cause is fixed."
                ),
                analysis_name="Egeria publish queue",
                items=items,
            )
        else:
            # Retrying-but-not-yet-stuck is not something to ask a person to act
            # on — the scheduler is already handling it. It stays an audit line.
            log_catalog(
                registry, entity_type or "repo", entity_slug or "", "", "",
                status=status,
                summary="Egeria publish incomplete — " + "; ".join(parts),
                detail=detail,
                items=items,
            )
    except Exception:
        # The activity write is the visibility mechanism, not the work. If it
        # fails, the rows themselves are still correct and still retryable.
        log.exception("Outbox drain: could not record outcome to the activity log")

"""The feedback item, the triage event, and the vocabularies both are checked against.

Design: docs/trellis-feedback-extraction.md §4. Decisions referenced as Q1-Q7 are that
document's §7, all made by the project owner on 2026-11-09. The causes (0.3) are
docs/feedback-closed-loop-design.md §2.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Q1: EA's five plus RE's `triaged` ("seen, no decision yet" -- not the same as known_issue).
#: An item with no triage events is `new`; `new` may also be set explicitly, to reopen one.
STATUSES: tuple[str, ...] = ("new", "triaged", "known_issue", "actioned", "deferred", "not_an_issue")

#: A rating of a chat/RAG answer, or a page-widget product report (§4 item 1).
KINDS: tuple[str, ...] = ("answer", "product")

#: The `source` of a triage event whose subject is a library item. Every other source names a
#: row an app stores itself (RE: `resource_feedback`) and must be declared by that app (§4 item 3).
ITEM_SOURCE = "item"

#: Product-report categories, as RE's page widget (ported from the Portal) already uses them.
PRODUCT_CATEGORIES: tuple[str, ...] = ("bug", "confusing", "suggestion", "praise")

#: +1 helpful, 0 partly right, -1 not helpful: the convention both apps already use.
VOTES: tuple[int, ...] = (1, 0, -1)

#: Why an answer failed, in precedence order: the cause is the EARLIEST stage at fault, the first
#: where fixing that stage alone would have given a right answer (closed-loop design §2.2). Two
#: people reading one failure must pick the same cause, or a count by cause means nothing.
CAUSE_CHAIN: tuple[str, ...] = (
    "catalog_gap", "missing_evidence", "stale", "analysis_defect",
    "wrong_evidence", "over_compressed", "reasoning",
)

#: The chain plus the two causes outside it, which say the failure is not the pipeline's to fix.
CAUSES: tuple[str, ...] = (*CAUSE_CHAIN, "unclear_question", "not_an_issue")

#: The `cause` filter value for answer items no one has diagnosed yet.
UNDIAGNOSED = "undiagnosed"


class FeedbackError(ValueError):
    """A request the store refuses: an unknown status, kind, source or vote, or a missing field."""


def check_cause(status: str, cause: str, secondary_cause: str) -> None:
    """Refuse a diagnosis that contradicts itself or the precedence rule (closed-loop design §2).

    * A secondary cause needs a primary one, is for measurement only, and must come LATER in the
      chain than the primary: the primary is by definition the earliest stage at fault.
    * `unclear_question` and `not_an_issue` sit outside the chain and take no secondary cause.
    * `not_an_issue` as a cause closes the item, so its status must be `not_an_issue` too; and a
      `not_an_issue` status cannot carry a pipeline cause.
    """
    if not cause:
        if secondary_cause:
            raise FeedbackError("a secondary cause needs a cause")
        return
    if cause not in CAUSES:
        raise FeedbackError(f"cause must be one of {list(CAUSES)}, got {cause!r}")
    if secondary_cause:
        if cause not in CAUSE_CHAIN:
            raise FeedbackError(f"{cause!r} is outside the cause chain and takes no secondary cause")
        if secondary_cause not in CAUSE_CHAIN:
            raise FeedbackError(f"secondary_cause must be one of {list(CAUSE_CHAIN)}, got {secondary_cause!r}")
        if CAUSE_CHAIN.index(secondary_cause) <= CAUSE_CHAIN.index(cause):
            raise FeedbackError(
                f"secondary cause {secondary_cause!r} must come after {cause!r} in the chain "
                f"{list(CAUSE_CHAIN)}: the cause is the earliest stage at fault")
    if cause == "not_an_issue" and status != "not_an_issue":
        raise FeedbackError("cause 'not_an_issue' closes the item: its status must be 'not_an_issue'")
    if status == "not_an_issue" and cause in CAUSE_CHAIN:
        raise FeedbackError(f"status 'not_an_issue' contradicts cause {cause!r}, which is a fault in the pipeline")


@dataclass
class FeedbackItem:
    """One piece of feedback. Which optional fields mean anything depends on `kind`.

    `id` is assigned by the store and is never a position in a list (EA's position-keyed triage
    edited the wrong record once a line was skipped -- §1).
    """

    app: str
    kind: str
    user_id: str = ""
    session_id: str = ""
    # answer
    vote: int | None = None
    query_text: str = ""
    response_text: str = ""            # Q4: always the full answer text
    intent_label: str = ""             # a real field, so EA's repurposing of comment fields is not copied
    perspective: str = ""
    routing_agent: str = ""
    compile_id: str = ""
    entity_type: str = ""
    slug: str = ""
    # product
    category: str = ""
    page: str = ""
    element_guid: str = ""
    rating: int | None = None
    email: str = ""
    wants_response: bool = False
    consent_to_contact: bool = False
    build_version: str = ""
    user_agent: str = ""
    viewport: str = ""
    locale: str = ""
    # both
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    # set by the store
    id: str = ""
    created_at: str = ""

    def validate(self) -> None:
        if not self.app:
            raise FeedbackError("app is required")
        if self.kind not in KINDS:
            raise FeedbackError(f"kind must be one of {list(KINDS)}, got {self.kind!r}")
        if self.kind == "answer":
            if self.vote not in VOTES:
                raise FeedbackError(f"an answer item needs vote in {list(VOTES)}, got {self.vote!r}")
            if not self.query_text:
                raise FeedbackError("an answer item needs query_text")
        else:
            if self.category and self.category not in PRODUCT_CATEGORIES:
                raise FeedbackError(f"category must be one of {list(PRODUCT_CATEGORIES)}, got {self.category!r}")
            if not (self.message or self.rating is not None or self.category):
                raise FeedbackError("a product item needs a message, a rating or a category")
        if self.rating is not None and not 1 <= int(self.rating) <= 5:
            raise FeedbackError(f"rating must be 1-5, got {self.rating!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TriageEvent:
    """One status change. Append-only: there is no update, and a note exists only on its event.

    `seq` orders events (a monotonic sequence id, not a timestamp: two admins can act in the same
    second and clocks can disagree). The subject is `(app, source, ref_id)`: a library item when
    `source` is ITEM_SOURCE, otherwise a row the app stores itself.
    """

    seq: int
    app: str
    source: str
    ref_id: str
    status: str
    note: str
    actor: str
    created_at: str
    #: The diagnosis this event records (0.3); "" when it changes status only.
    cause: str = ""
    secondary_cause: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TriageState:
    """The current state of one subject: the latest event's status (or `new` with no events).

    The cause is the latest DIAGNOSIS, not the latest event's: marking a diagnosed item `actioned`
    without restating its cause keeps the cause. A re-diagnosis is a new event with a new cause.
    """

    status: str
    note: str
    actor: str
    updated_at: str
    events: int
    cause: str = ""
    secondary_cause: str = ""

    @classmethod
    def untouched(cls) -> TriageState:
        return cls(status="new", note="", actor="", updated_at="", events=0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

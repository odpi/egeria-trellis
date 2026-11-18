"""Product/UI feedback endpoints — public submission, admin-only triage.

Route path is deliberately /api/feedback, not /api/demo-feedback (the
Portal source's path) — RE has no "demo" concept, so keeping that prefix
would be misleading, not a faithful port.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from trellis_feedback import FeedbackError

from resource_explorer.config import get_config
from resource_explorer.feedback_store import (
    VALID_CATEGORIES,
    VALID_TRIAGE_STATUSES,
    FeedbackEntry,
    FeedbackStore,
)
from resource_explorer.web.admin_auth import is_admin_request

router = APIRouter()


class FeedbackSubmission(BaseModel):
    session_id: str = ""
    page: str = ""
    element_guid: str = ""
    rating: int | None = None
    category: str = ""
    message: str = ""
    email: str = ""
    wants_response: bool = False
    consent_to_contact: bool = False
    build_version: str = ""
    user_agent: str = ""
    viewport: str = ""
    locale: str = ""


class TriageUpdate(BaseModel):
    triage_status: str
    #: Kept on the triage event, never overwritten: each change carries its own note.
    note: str = ""
    #: A diagnosis of an answer item (docs/feedback-closed-loop-design.md §2): one of trellis-feedback's CAUSES.
    #: Blank keeps the current one. Product items take none; the library refuses one with a 400.
    cause: str = ""
    secondary_cause: str = ""
    #: Which charged analysis a gap-routed diagnosis is about (closed-loop step 2a, Q2: the admin picks one).
    #: Blank: the first in the charge set's role order. Must be in the charge set, or the Save is refused.
    analysis_id: str = ""


def _require_admin(request: Request) -> None:
    cfg = get_config().feedback
    if not is_admin_request(request, cfg):
        raise HTTPException(status_code=403, detail="Admin credential required")


@router.post("")
async def submit_feedback(payload: FeedbackSubmission) -> dict:
    if payload.rating is not None and not (1 <= payload.rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be 1-5")
    if payload.category and payload.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {payload.category!r}")

    store = FeedbackStore()
    entry = FeedbackEntry(
        session_id=payload.session_id,
        page=payload.page,
        element_guid=payload.element_guid,
        rating=payload.rating,
        category=payload.category,
        message=payload.message,
        email=payload.email,
        wants_response=payload.wants_response,
        consent_to_contact=payload.consent_to_contact,
        build_version=payload.build_version,
        user_agent=payload.user_agent,
        viewport=payload.viewport,
        locale=payload.locale,
    )
    try:
        store.add(entry)
    except FeedbackError as exc:
        # e.g. nothing said at all: no message, rating or category.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("")
async def list_feedback(
    request: Request, triage_status: str | None = None, limit: int = 200
) -> list[dict]:
    _require_admin(request)
    if triage_status is not None and triage_status not in VALID_TRIAGE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid triage_status: {triage_status!r}")
    store = FeedbackStore()
    return store.list(triage_status=triage_status, limit=limit)


@router.get("/stats")
async def feedback_stats(request: Request) -> dict:
    _require_admin(request)
    store = FeedbackStore()
    return store.stats()


@router.get("/look-counts")
async def look_counts_route(request: Request) -> dict:
    """How many people use each look and how many switched away from each, from the activity rows
    (RULING-THEMES-PORTAL-DARK-LIGHT.md section 1). Admin-gated like the rest of this page."""
    _require_admin(request)
    from resource_explorer.look_stats import OPERATION, look_counts
    from resource_explorer.registry import ProjectRegistry

    rows = ProjectRegistry().list_activity(operation=OPERATION, limit=100000)
    return look_counts(rows)


@router.get("/causes")
def cause_counts_route(request: Request, vote: str = "-1", limit: int = 500) -> dict:
    """Rated answers by cause, and the same counts by the analyses each diagnosed answer is charged to.

    Closed-loop design §3.1 and §8 Q6: at 20-200 votes a week diagnosis is a routine, and the view that makes it one
    is "which causes, on which analyses". `vote` defaults to -1, the thumbs-down the loop is about; `?vote=all`
    counts every vote. The by-analysis half reads each diagnosed item's stored compile, up to `limit` items,
    newest first, and says when it stopped there.

    `counts` and `by_analysis` are different populations, and the response says so: `counts` is every rated answer
    of that vote, all time; `by_analysis` is the diagnosed ones among the newest `limit`.

    A plain `def`, not `async def`: it reads the registry and the question catalog synchronously, which inside an
    async route would block the event loop (and every chat stream with it) while it ran. FastAPI runs a plain def
    in its thread pool.
    """
    _require_admin(request)
    from trellis_feedback import CAUSES, UNDIAGNOSED, VOTES

    vote_n: int | None = None
    if vote.strip() not in ("", "all"):
        try:
            vote_n = int(vote)
        except ValueError:
            vote_n = None
        if vote_n not in VOTES:
            raise HTTPException(status_code=400, detail=f"vote must be one of {list(VOTES)} or 'all', got {vote!r}")
    limit = max(1, min(limit, 1000))

    from resource_explorer.feedback_diagnosis import catalog_analyses_for, charge_set
    from resource_explorer.registry import ProjectRegistry

    store = FeedbackStore()
    counts = store.cause_counts(vote=vote_n)
    listed = store.list(kind="answer", limit=limit)
    diagnosed = [r for r in listed
                 if (r.get("triage") or {}).get("cause") and (vote_n is None or r.get("vote") == vote_n)]
    registry = ProjectRegistry()
    by_analysis: dict[str, dict] = {}
    uncharged = 0
    # One read per compile and one catalog lookup per question, however many votes share them.
    compiles: dict[str, dict] = {}
    catalog: dict[tuple[str, str], list[str]] = {}
    for r in diagnosed:
        cause = r["triage"]["cause"]
        cid = r.get("compile_id") or ""
        if cid and cid not in compiles:
            compiles[cid] = (registry.get_compile(cid) or {}).get("manifest") or {}
        manifest = compiles.get(cid, {})
        ckey = ((manifest.get("coverage") or {}).get("question") or "", r.get("entity_type") or "")
        if ckey not in catalog:
            catalog[ckey] = catalog_analyses_for(manifest, r.get("entity_type") or "") if ckey[0] else []
        charged = charge_set(manifest, catalog[ckey])
        if not charged:
            uncharged += 1
        for c in charged:
            entry = by_analysis.setdefault(c["analysis_id"], {"analysis_id": c["analysis_id"], "total": 0,
                                                              "causes": {}, "roles": {}})
            entry["total"] += 1
            entry["causes"][cause] = entry["causes"].get(cause, 0) + 1
            entry["roles"][c["role"]] = entry["roles"].get(c["role"], 0) + 1
    return {
        "vote": vote_n, "counts": counts, "causes": list(CAUSES), "undiagnosed": UNDIAGNOSED,
        "by_analysis": sorted(by_analysis.values(), key=lambda e: (-e["total"], e["analysis_id"])),
        #: Diagnosed items charged to no analysis: catalog gaps, unclear questions, and answers with no compile.
        "uncharged": uncharged,
        "read": len(listed), "limited": len(listed) >= limit,
        "populations": {"counts": "every rated answer of this vote, all time",
                        "by_analysis": f"the diagnosed answers among the newest {len(listed)} rated answers"},
    }


@router.get("/{feedback_id}/diagnosis")
def feedback_diagnosis(feedback_id: str, request: Request) -> dict:
    """The diagnosis panel of one rated answer: its stored compile (never a re-compile), the causes the manifest
    suggests with the rule behind each, the causes it could not judge and why, and the analyses it is charged to.
    Nothing here records a cause. A plain `def` for the same reason as /causes: synchronous reads."""
    _require_admin(request)
    from resource_explorer.feedback_diagnosis import diagnosis
    from resource_explorer.registry import ProjectRegistry

    found = FeedbackStore().get(feedback_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Feedback record not found")
    if found.get("kind") != "answer":
        raise HTTPException(status_code=400, detail="Only a rated answer is diagnosed; this is a page report")
    return {"id": feedback_id, **diagnosis(found, ProjectRegistry())}


@router.patch("/{feedback_id}")
async def triage_feedback(feedback_id: str, payload: TriageUpdate, request: Request) -> dict:
    _require_admin(request)
    if payload.triage_status not in VALID_TRIAGE_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"Invalid triage_status: {payload.triage_status!r}"
        )
    from resource_explorer import feedback_routing
    from resource_explorer.registry import ProjectRegistry

    store = FeedbackStore()
    actor = _actor(request)
    # Closed-loop step 2a: a Save that sets a cause routes it (BRIEF-CLOSED-LOOP-STEP2-ROUTING.md). The route is
    # planned BEFORE the triage event is written, so a refused analysis pick costs nothing, and carried out after.
    plan, registry = None, None
    if payload.cause.strip():
        before = store.get(feedback_id)
        if before is None:
            raise HTTPException(status_code=404, detail="Feedback record not found")
        if before.get("kind") == "answer":
            registry = ProjectRegistry()
            try:
                plan = feedback_routing.plan(before, payload.cause, analysis_id=payload.analysis_id,
                                             registry=registry)
            except feedback_routing.RouteRefused as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        updated = store.update_triage_status(
            feedback_id, payload.triage_status, note=payload.note.strip(), actor=actor,
            cause=payload.cause.strip(), secondary_cause=payload.secondary_cause.strip())
    except FeedbackError as exc:
        # A diagnosis that contradicts itself or its status, a status that contradicts the item's current cause
        # ("re-diagnose it"), or a cause on a page report. Said on the row as a 400, not a 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Feedback record not found")
    if plan is not None:
        # Never raises: a route that could not be made says why, and the diagnosis stands.
        updated["route"] = feedback_routing.execute(plan, updated, actor=actor, registry=registry)
    return updated


class Reroute(BaseModel):
    analysis_id: str = ""


@router.post("/{feedback_id}/routes/{cause}/reroute")
async def reroute_feedback(feedback_id: str, cause: str, payload: Reroute, request: Request) -> dict:
    """Move one diagnosis's gap to another charged analysis (closed-loop step 2a; the architect's P2). A Save never
    moves a route; this is the explicit action that does. Admin only."""
    _require_admin(request)
    from resource_explorer import feedback_routing
    from resource_explorer.registry import ProjectRegistry

    found = FeedbackStore().get(feedback_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Feedback record not found")
    if found.get("kind") != "answer":
        raise HTTPException(status_code=400, detail="Only a rated answer is routed")
    try:
        return feedback_routing.reroute(found, cause, analysis_id=payload.analysis_id, actor=_actor(request),
                                        registry=ProjectRegistry())
    except feedback_routing.RouteRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{feedback_id}")
async def get_feedback(feedback_id: str, request: Request) -> dict:
    """One item with its current state and every triage event, oldest first."""
    _require_admin(request)
    found = FeedbackStore().get(feedback_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Feedback record not found")
    if found.get("kind") == "answer":
        from resource_explorer import feedback_routing
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
        found["routes"] = [feedback_routing.describe(r, registry)
                           for r in registry.list_feedback_routes([feedback_id]).get(feedback_id, [])]
    return found


# ── Curate's resource_feedback, triaged by reference (design doc §6 step 3 part D, Q2/Q7) ─────────────────
#
# The rows live in the registry; their status lives in trellis-feedback under source "resource_feedback". The
# registry is asked whether the row exists before anything is appended, since the library cannot see it.


def _resource_row(row_id: str) -> dict | None:
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry().get_resource_feedback(row_id)


@router.patch("/resource_feedback/{row_id}")
async def triage_resource_feedback(row_id: str, payload: TriageUpdate, request: Request) -> dict:
    """Set the status of one Curate feedback row (appends an event; the row itself is not touched)."""
    _require_admin(request)
    if payload.triage_status not in VALID_TRIAGE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid triage_status: {payload.triage_status!r}")
    row = _resource_row(row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Resource feedback row not found")
    out = FeedbackStore().triage_resource(
        row_id, payload.triage_status, note=payload.note.strip(), actor=_actor(request))
    return {**row, **out, "triage_status": out["triage"]["status"], "source": "resource"}


@router.get("/resource_feedback/{row_id}")
async def get_resource_feedback_triage(row_id: str, request: Request) -> dict:
    """One Curate feedback row with its status and history. A row that is gone but still has events is returned
    with `gone: true` (Q7); one with neither is a 404."""
    _require_admin(request)
    row = _resource_row(row_id)
    out = FeedbackStore().resource_triage(row_id)
    if row is None and not out["history"]:
        raise HTTPException(status_code=404, detail="Resource feedback row not found")
    return {**(row or {"id": row_id}), **out, "triage_status": out["triage"]["status"],
            "source": "resource", "gone": row is None}


@router.delete("/resource_feedback/{row_id}")
async def clear_gone_resource_feedback(row_id: str, request: Request) -> dict:
    """Clear the triage history of a Curate feedback row that no longer exists (an "item gone" entry). Refused
    with 409 while the row exists: its history is the record of how it was handled."""
    _require_admin(request)
    if _resource_row(row_id) is not None:
        raise HTTPException(status_code=409, detail="The row still exists; only a gone row's history is cleared")
    return {"cleared": FeedbackStore().clear_resource_events(row_id)}


def _actor(request: Request) -> str:
    """Who made a triage change: the signed-in admin, or "admin-token" for the shared token."""
    from resource_explorer.web.admin_auth import _signed_in_sub

    return _signed_in_sub(request) or "admin-token"


# ── Per-answer feedback ──────────────────────────────────────────────────
#
# The floating Feedback button above is about the PRODUCT ("this page is
# confusing"). This is about one ANSWER ("the answer to that question is
# wrong"), and the two must not be one control: product feedback goes to
# whoever builds the UI, a disputed answer goes to whoever wrote the
# analysis, and folding them together is how a real disagreement about a
# measurement ends up in a triage queue for button placement.
#
# A disagreement lands in the gaps collection (gaps.py) marked `ours` —
# destinations.py's rule 1: a finding about the analysis is never charged to
# the repository. Agreement and "partly right" land in the feedback store,
# because they are real signal about the answer and there is nowhere else
# that keeps them; they are deliberately NOT gaps, since nothing is owed.

#: What a person can say about one answer. `disagree` is the only verdict
#: that produces a gap — see the module note above.
AGREE = "agree"
PARTLY = "partly"
DISAGREE = "disagree"
VALID_VERDICTS = {AGREE, PARTLY, DISAGREE}
#: The verdict as a vote, the convention both apps' answer ratings use (+1 / 0 / -1).
VERDICT_VOTE = {AGREE: 1, PARTLY: 0, DISAGREE: -1}


class AnswerFeedback(BaseModel):
    slug: str
    #: The question's text, or blank when `question_id` names it. Text still works: URLs and saved state carry it.
    question: str = ""
    #: The question's stable id (Q-0001), which survives a rewording of the text. Wins over `question`.
    question_id: str = ""
    verdict: str
    comment: str = ""
    # The analysis the person is disputing. Normally omitted — the server
    # resolves it from the question catalog, which is the same mapping the
    # page itself rendered from. A client MAY send it (the row shows several
    # analyses and the person meant one of them); an id the catalog does not
    # list for this question is rejected rather than silently recorded, so a
    # stale page cannot attribute a dispute to an analysis that never
    # answered it.
    analysis_id: str = ""
    session_id: str = ""
    page: str = ""
    # Resource type of `slug` ('repo' | 'database' | 'filesystem'), same
    # convention as `entity_type` elsewhere in this codebase. Defaults to
    # "repo" for existing callers. Before this field existed, the route always
    # resolved `slug` against `registry.get()` (the repo-only `projects`
    # table) and `_analyses_for_question` always read the repo question
    # catalog — so disagreeing with a database/filesystem question's answer
    # 404'd the whole route outright, regardless of what the catalog actually
    # names for that question.
    entity_type: str = "repo"
    #: The `answer` item this verdict was already stored as, when the caller recorded it first: the chat pane's
    #: vote goes to /api/query/feedback (which keeps the question and answer) and then here only to join the
    #: gaps loop. Given, and naming an item, nothing is stored twice.
    feedback_id: str = ""


def _analyses_for_question(question: str, entity_type: str = "repo") -> list[str]:
    """Which analyses the catalog says answer this question. Empty is a real
    answer — several questions are answered by a person or a direct field and
    name no analysis at all (see QuestionChecklistEntry.kind)."""
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    wanted = (question or "").strip().casefold()
    for e in get_questions(entity_type):
        if (e.get("question") or "").strip().casefold() == wanted:
            return list((e.get("answering") or {}).get("analysis_ids") or [])
    return []


@router.post("/answer")
async def submit_answer_feedback(payload: AnswerFeedback) -> dict:
    """Record what a person said about one answer, and raise a gap when they
    disagreed.

    Returns `{"status": "ok", "gap": <gap>|None, "gap_reason": str}`.
    `gap` is null for agree/partly (nothing is owed) — `gap_reason` says
    which of those it was, rather than leaving the caller to infer a failure
    from a null.
    """
    from resource_explorer.gaps import record_disagreement
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    verdict = (payload.verdict or "").strip().lower()
    if verdict not in VALID_VERDICTS:
        raise HTTPException(
            status_code=400,
            detail=f"verdict must be one of {sorted(VALID_VERDICTS)}, got {payload.verdict!r}",
        )
    from resource_explorer.surveyors.question_catalog_reader import (
        find_question,
        find_question_by_id,
    )

    entity_type = (payload.entity_type or "repo").strip()
    question = (payload.question or "").strip()
    question_id = (payload.question_id or "").strip()
    if question_id:
        by_id = find_question_by_id(question_id, entity_type)
        if by_id is None:
            raise HTTPException(status_code=400, detail=f"question_id {question_id!r} is not in the catalog")
        question = by_id["question"]
    elif question:
        question_id = (find_question(question, entity_type) or {}).get("id", "")
    if not question:
        raise HTTPException(status_code=400, detail="question or question_id is required")

    registry = ProjectRegistry()
    if get_adapter(entity_type).get_entity(registry, payload.slug) is None:
        raise HTTPException(
            status_code=404, detail=f"{entity_type} '{payload.slug}' not found"
        )

    catalogued = _analyses_for_question(question, entity_type)
    analysis_id = (payload.analysis_id or "").strip()
    if analysis_id and analysis_id not in catalogued:
        raise HTTPException(
            status_code=400,
            detail=(
                f"analysis_id {analysis_id!r} does not answer that question. "
                f"The catalog names: {catalogued or 'no analysis'}."
            ),
        )
    if not analysis_id:
        # The first catalogued analysis, matching what the row's provenance
        # line names first. When the catalog names none, "" is recorded — the
        # disagreement is real whether or not an analysis can be charged with
        # it, and inventing an id here would put a person's dispute on an
        # analysis that never answered the question.
        analysis_id = catalogued[0] if catalogued else ""

    # Kept whatever the verdict is: this is the record of what was said, and
    # the gap below is the consequence of one kind of saying. Storing only
    # disagreements would make "nobody has questioned this answer" and "one
    # person confirmed it" look identical.
    #
    # An `answer` item (since 2026-11-09; before, a product row with "[verdict]" in its message), so Admin lists it
    # with the question and who said it, beside chat votes.
    store = FeedbackStore()
    from resource_explorer.registry import current_user_id

    user_id = current_user_id()
    linked = (payload.feedback_id or "").strip()
    already = store.get(linked) if linked else None
    # Only the caller's own answer item, about this same resource, stands in for this verdict: a feedback_id
    # naming someone else's item, or another resource's, is ignored and the verdict is stored as usual.
    is_this_vote = (already is not None and already.get("kind") == "answer"
                    and already.get("user_id", "") == user_id and already.get("slug") in ("", payload.slug))
    if not is_this_vote:
        store.add_answer(
            vote=VERDICT_VOTE[verdict], question=question, user_id=user_id,
            session_id=payload.session_id, entity_type=entity_type, slug=payload.slug,
            message=payload.comment or "", page=payload.page,
            # The stable id when the catalog has one, so a reworded question does not orphan the row. Not a GUID,
            # despite the field's name: "answer:<slug>:<question id>", as the page rows before it used it.
            element_guid=f"answer:{payload.slug}:{question_id or question}",
            extra={"verdict": verdict, "question_id": question_id, "analysis_id": analysis_id,
                   "origin": "questions checklist"},
        )

    if verdict != DISAGREE:
        return {
            "status": "ok", "gap": None,
            "gap_reason": f"recorded as {verdict} — no gap is owed",
        }

    gap = record_disagreement(
        registry, payload.slug, question, analysis_id,
        comment=payload.comment, who=payload.session_id,
        extra_evidence={"catalogued_analyses": catalogued}, question_id=question_id,
    )
    return {
        "status": "ok", "gap": gap,
        "gap_reason": (
            f"disagreement recorded against {analysis_id}"
            if analysis_id else
            "disagreement recorded; the catalog names no analysis for this "
            "question, so it is attributed to none"
        ),
    }

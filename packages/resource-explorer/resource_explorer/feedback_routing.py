"""Route a diagnosis of a rated answer to the one consumer that acts on its cause.

docs/feedback-closed-loop-design.md §3.2 and BRIEF-CLOSED-LOOP-STEP2-ROUTING.md (step 2a: the gap consumers; step 2b:
the RFA consumers).
The rules, all decided by the project owner on 2026-11-10:

* **On Save.** A Save that sets or changes the cause routes it, in the same request, after the triage event is
  written. A status change alone routes nothing; the secondary cause never routes.
* **One route per (item, cause).** `feedback_routes` is keyed on the pair, and the route is claimed before any
  work, so a second Save, or two at once, cannot route it twice.
* **A re-diagnosis leaves the earlier route standing.** Its gap is real work someone may have started.
* **A failed route never fails the diagnosis.** A route that cannot be made for a lasting reason (no resource, no
  stored compile, nothing charged) is recorded as `none` with its reason; one that failed for a passing reason
  (a registry error) is released, so the next Save tries again.
* **The admin picks one charged analysis**, defaulting to the first in role order (`feedback_diagnosis.ROLE_ORDER`).
* **The gap sentence includes the question as asked; the asker's id never travels** into a gap.
* **Except in a private context** (Q8's limit; the architect's P1 read-back of 6c050de2): when the answer's resource
  is in a private investigation or restricted by its Egeria zones, or that cannot be told, the gap and the RFA name
  the item reference and the cause only. No question text in a sentence, a summary, the evidence or a key.
  `private_context` is the one test.

Step 2a: the gap consumers (`missing_evidence`, `analysis_defect`, and the gap half of `catalog_gap`).
Step 2c: Automate. A `stale` diagnosis proposes a schedule for the analysis behind the stale section, as an RFA whose
drawer row can save it, only ever making it faster (Q7). One proposal per (resource, analysis, cadence), in the same
`feedback_rfas` table, so a completed weekly proposal does not stop a later daily one; no question text. An analysis
that already runs daily and was still stale is an `analysis_defect` gap instead: the run, or its result's path to the
compile, is what failed (the architect's read-back of 0969661a).
Step 2b: the RFA consumers. `catalog_gap` also raises an RFA, attached to its gap (`analysis_gaps.rfa_activity_id`),
or against the `catalog`/`questions` pseudo-entity when the answer had no resource; `unclear_question` raises an RFA
only. One RFA per (question, cause, entity), claimed in `feedback_rfas` before it is written; a later answer on the
same question joins it. RFAs are raised unassigned (Q5).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from resource_explorer.gaps import (
    ANALYSIS_DEFECT,
    CATALOG_GAP,
    DISAGREEMENT,
    MISSING_EVIDENCE,
    SOURCE_PERSON,
    question_check_name,
)

logger = logging.getLogger(__name__)

#: The causes step 2a routes to a gap, and the gap kind each makes.
GAP_CAUSES = {"missing_evidence": MISSING_EVIDENCE, "analysis_defect": ANALYSIS_DEFECT, "catalog_gap": CATALOG_GAP}

#: The causes step 2b routes to an RFA ("to the catalog owner", design §3.2).
RFA_CAUSES = ("catalog_gap", "unclear_question")

#: The entity an RFA is raised against when the answer had no resource in scope (Q4). Not a registered resource:
#: every RFA reader takes the entity as display text, a slug filter or a dismissal key, and none looks it up.
CATALOG_ENTITY = ("catalog", "questions", "Question catalog")

_RFA_LABEL = {
    "catalog_gap": "Nothing in the question catalog answers this question",
    "unclear_question": "A question could be read another way, or used a name the catalog does not know",
}

#: Routed by a later step of the brief, and not claimed until then. Empty since 2c: every step 2 cause has a consumer.
LATER: dict[str, str] = {}

#: The cause step 2c routes to Automate.
AUTOMATE_CAUSE = "stale"

#: The cadences a proposal offers, slowest first. An analysis already scheduled is offered the next faster one.
CADENCES = ("monthly", "weekly", "daily")
DEFAULT_CADENCE = "weekly"

#: Never routed in step 2 (Q6) or never routed at all.
UNROUTED = {
    "wrong_evidence": "feeds chunk scores, a later step (§3.4); nothing is routed",
    "over_compressed": "waits for the compile spec's editing loop; nothing is routed",
    "reasoning": "is read in the by-cause view; nothing is routed",
    "not_an_issue": "closes the item; nothing is routed",
}

_CAUSE_WORDS = {
    MISSING_EVIDENCE: "had no result for it",
    ANALYSIS_DEFECT: "gave a wrong result for it",
    CATALOG_GAP: "nothing in the catalog answers it",
}


def private_context(item: dict, registry: Any) -> str:
    """Why this answer's question must not travel beyond Admin, or "" when it may.

    Two tests, both failing CLOSED (could not tell counts as private):

    * the answer's resource is in a creator-only investigation (`registry.private_owner_for_entity`, the same read
      born-private publishing uses, which sees investigations the caller cannot);
    * the resource's Egeria element is in zones none of which grants READ to `allUsers` (`_zone_restriction`;
      required in 2b by the architect's read-back, since RFAs reach resources that zones do restrict; made strict
      2026-11-10 when Egeria b49c5ed0's zone rule was found to make an uncontrolled zone private). One read per Save.

    An answer with no resource in scope has no private context to inherit.
    """
    slug = (item.get("slug") or "").strip()
    if not slug:
        return ""
    from resource_explorer.private_publish import PrivateOwnerUnknown, resolve_private_owner

    entity_type = (item.get("entity_type") or "").strip() or "repo"
    try:
        owner = resolve_private_owner(registry, entity_type, slug)
    except PrivateOwnerUnknown:
        return "whether the resource is in a private investigation could not be read"
    if owner:
        return "the resource is in a private investigation"
    return _zone_restriction(entity_type, slug, registry)


def _resource_guid(entity_type: str, slug: str, registry: Any) -> str:
    """The resource's own Egeria element GUID, or "" when it has none (never published)."""
    getter = {"repo": registry.get, "database": registry.get_database,
              "filesystem": registry.get_filesystem}.get(entity_type)
    entity = getter(slug) if getter else None
    return (getattr(entity, "egeria_asset_guid", "") or "").strip() if entity else ""


def _zone_restriction(entity_type: str, slug: str, registry: Any) -> str:
    """Why the resource's Egeria zones restrict who may read it, or "" when they do not.

    Public ONLY when the element has no zones, or one of its zones' READ list (else DEFAULT, as
    `zone_access.security_list_for` reads it) holds `allUsers`. Anything else is restricted (owner's decision D2,
    2026-11-10, BRIEF-ZONE-RULE-NO-CONTROL-IS-PRIVATE):

    * a zone with no security access control, or no READ and no DEFAULT list: Egeria b49c5ed0 lets no one but the
      user it is named after read it ("A zone with no security access control ... grants access to no-one",
      OpenMetadataAccessSecurityConnector.validateZoneAccess :1156-1161); a personal (userId) zone is one of these;
    * a zone readable only through roles, groups or an account type: deliberately counted as restricted (the safe
      side), since who reads the RFA is not known here.

    Not gated by `RE_ZONE_RULE`: on an older platform this is merely stricter than it needs to be. A resource never
    published to Egeria has no zones there, so nothing restricts it. Fails CLOSED: an element or a control that
    cannot be read counts as restricted. The reason never names a zone called after the element's owner or a
    maintainer, nor a zone with no control at all that is not RE's own (`zone_access.withheld_zone_names`: typically a
    personal zone), since it would tell the RFA's readers whose resource it is.
    """
    try:
        guid = _resource_guid(entity_type, slug, registry)
    except Exception as exc:  # noqa: BLE001 -- fail closed: the registry could not say which element it is
        return f"the resource's Egeria zones could not be read ({type(exc).__name__})"
    if not guid:
        return ""
    from resource_explorer import zone_access

    try:
        reader = zone_access.EgeriaAccessReader(cache_elements=True)
        facts = reader.element(guid)
        zones = [z for z in (facts.zones or []) if z]
        if not zones:
            return ""
        no_control = []
        for zone in zones:
            control = reader.control(zone)
            readers = zone_access.security_list_for(control, zone_access.READ_OPERATION)
            if not isinstance(control, dict):
                no_control.append(zone)
            elif readers is not None and zone_access.ALL_USERS_GROUP in readers:
                return ""                     # this zone lets every active user read it: one zone is enough
    except Exception as exc:  # noqa: BLE001 -- fail closed: AccessUnreadable or anything else, could not tell
        return f"the resource's Egeria zones could not be read ({type(exc).__name__})"
    withheld = set(zone_access.withheld_zone_names(zones, no_control, facts.owners, facts.maintainers))
    shown = [z for z in zones if z not in withheld]
    if not shown:
        return "the resource is in a zone private to its owner"
    named = ", ".join(shown) + (" and a zone private to its owner" if len(shown) < len(zones) else "")
    return f"the resource is in a restricted zone ({named})"


def opaque_question_key(question: str, question_id: str = "") -> str:
    """A gap's `check_name` that carries no question text: the catalog id when there is one, else a short hash. The
    same question still keys one gap; reading the key tells nothing about what was asked."""
    qid = (question_id or "").strip()
    if qid:
        return question_check_name("", qid)
    digest = hashlib.sha1((question or "").strip().encode("utf-8")).hexdigest()[:12]
    return f"question:sha1:{digest}"


class DefectGapRefused(Exception):
    """An already-daily stale analysis's defect gap could not be raised: its resource cannot hold gaps. Raised only
    around that `_raise_gap` call, so no other error is mistaken for it (the architect's nit on 190d7aa1)."""


class RouteRefused(ValueError):
    """The Save asked for something routing cannot honour (an analysis outside the charge set). Said as a 400,
    before anything is written."""


@dataclass
class Plan:
    """What a Save will route, worked out before the triage event is written so a refusal costs nothing."""
    cause: str
    gap_kind: str = ""
    analysis_id: str = ""
    role: str = ""
    compile_id: str = ""
    #: Set when nothing will be routed, for a lasting reason; recorded as `none`.
    none_reason: str = ""
    #: Set when the cause is routed by no step, or by a later one; nothing is claimed or recorded.
    words: str = ""
    #: Raise (or join) the question's RFA (step 2b).
    rfa: bool = False
    #: Propose a schedule (step 2c), and the manifest's stale rule when it gave one.
    automate: bool = False
    rule: str = ""
    charge: list[dict] = field(default_factory=list)


def plan(item: dict, cause: str, *, analysis_id: str = "", registry: Any) -> Plan | None:
    """The route a diagnosis of `item` as `cause` will take, or None when the Save sets no cause.

    Raises RouteRefused when `analysis_id` is given and is not in the item's charge set.
    """
    cause = (cause or "").strip()
    if not cause:
        return None
    if cause in UNROUTED:
        return Plan(cause=cause, words=f"Not routed: {cause.replace('_', ' ')} {UNROUTED[cause]}.")
    if cause in LATER:
        return Plan(cause=cause, words=f"Not routed yet: {cause.replace('_', ' ')} is routed by step {LATER[cause]}.")
    if cause not in GAP_CAUSES and cause not in RFA_CAUSES and cause != AUTOMATE_CAUSE:
        return Plan(cause=cause, words=f"Not routed: no consumer is named for {cause!r}.")

    p = Plan(cause=cause, gap_kind=GAP_CAUSES.get(cause, ""), rfa=cause in RFA_CAUSES,
             automate=cause == AUTOMATE_CAUSE, compile_id=(item.get("compile_id") or "").strip())
    picked = (analysis_id or "").strip()
    if cause in RFA_CAUSES:
        if picked:
            raise RouteRefused(f"A {cause.replace('_', ' ')} is charged to no analysis.")
        if not (item.get("slug") or "").strip():
            p.gap_kind = ""                       # no resource: no gap, the RFA goes to the question catalog
        return p
    if not (item.get("slug") or "").strip():
        p.none_reason = ("no resource in scope, so there is nothing to schedule" if p.automate
                         else "no resource in scope, so there is no gap to raise")
        if picked:
            raise RouteRefused("This answer had no resource in scope, so no analysis can be charged.")
        return p

    from resource_explorer.feedback_diagnosis import diagnosis

    d = diagnosis(item, registry)
    p.charge = list(d.get("charge_set") or [])
    if d.get("compile") is None:
        if picked:
            raise RouteRefused("This answer has no stored compile, so no analysis can be charged.")
        p.none_reason = "nothing to charge: the answer has no stored compile"
        return p
    if not p.charge:
        if picked:
            raise RouteRefused("This answer's compile charges no analysis.")
        p.none_reason = "nothing to charge: the compile charges no analysis"
        return p
    if picked:
        chosen = next((c for c in p.charge if c["analysis_id"] == picked), None)
    elif p.automate:
        # The section the manifest found stale, when it found one; else the first charged, as for a gap.
        stale = next((s for s in d.get("suggestions") or [] if s.get("cause") == AUTOMATE_CAUSE), {})
        chosen = next((c for c in p.charge if c["analysis_id"] in (stale.get("sections") or [])), p.charge[0])
        p.rule = stale.get("rule", "")
    else:
        chosen = p.charge[0]
    if chosen is None:
        raise RouteRefused(f"{picked!r} is not in this answer's charge set "
                           f"({', '.join(c['analysis_id'] for c in p.charge)}).")
    p.analysis_id, p.role = chosen["analysis_id"], chosen["role"]
    return p


def _gap_words(route: dict, registry: Any) -> str:
    """How a recorded route reads on the row."""
    consumer, target, cause = route.get("consumer"), route.get("target") or "", route.get("cause", "")
    if consumer == "gap":
        gap = registry.get_gap(int(target)) if target.isdigit() else None
        if gap is None:
            return f"Routed: gap #{target} ({cause.replace('_', ' ')}), no longer stored."
        on = f" on {gap['analysis_id']}" if gap.get("analysis_id") else ""
        state = ", resolved" if gap.get("resolved_at") else ""
        rfa = f", RFA {gap['rfa_activity_id'][:8]} raised" if gap.get("rfa_activity_id") else ""
        return f"Routed: gap #{target}{on} ({cause.replace('_', ' ')}){rfa}{state}."
    if consumer == "automate":
        return f"Routed: schedule proposal RFA {target[:8]} for {cause.replace('_', ' ')}."
    if consumer == "rfa":
        why = f" ({route['reason']})" if route.get("reason") else ""
        return f"Routed: RFA {target[:8]} ({cause.replace('_', ' ')}){why}."
    if consumer == "none":
        return f"Not routed ({cause.replace('_', ' ')}): {route.get('reason') or 'no reason recorded'}."
    if consumer == "pending":
        return f"Routing {cause.replace('_', ' ')}…"
    return f"Routed to {consumer} {target} ({cause.replace('_', ' ')})."


def describe(route: dict, registry: Any) -> dict:
    """A route as the page shows it: the stored row plus its words."""
    return {**route, "words": _gap_words(route, registry)}


def execute(p: Plan | None, item: dict, *, actor: str, registry: Any) -> dict | None:
    """Carry out a plan after the triage event is written. Returns the route as shown on the row, with `state`:
    `routed`, `already` (this item and cause were routed before), `not_routed` (recorded as none), or `skipped`
    (a cause no step 2a consumer takes). Never raises: a failure is a route that says so."""
    if p is None:
        return None
    item_id = item["id"]
    if p.words:
        return {"cause": p.cause, "state": "skipped", "words": p.words}
    if not registry.claim_feedback_route(item_id, p.cause, actor):
        existing = registry.get_feedback_route(item_id, p.cause) or {"cause": p.cause}
        shown = describe(existing, registry)
        return {**shown, "state": "already", "words": "Already " + shown["words"][0].lower() + shown["words"][1:]}
    if p.none_reason:
        registry.finish_feedback_route(item_id, p.cause, "none", reason=p.none_reason)
        return {**describe(registry.get_feedback_route(item_id, p.cause), registry), "state": "not_routed"}
    gap_id, gap_reason, rfa_id = 0, "", ""
    try:
        if p.gap_kind:
            try:
                gap_id = _raise_gap(p, item, actor=actor, registry=registry)
            except ValueError as exc:
                # upsert_gap refuses a slug the projects table does not hold: today, every resource that is not a
                # repository. A catalog gap's RFA still goes; a gap-only cause is not routed.
                gap_reason = f"no gap could be raised: {exc}"
        if p.rfa:
            rfa_id = _raise_rfa(p, item, gap_id=gap_id, registry=registry)
        if p.automate:
            try:
                consumer, target = _propose_schedule(p, item, actor=actor, registry=registry)
            except DefectGapRefused as exc:
                # Already daily, so a defect gap, on a resource that cannot hold gaps (not a repository).
                consumer, target = "", ""
                gap_reason = f"it already runs daily, and no gap could be raised: {exc}"
            if consumer == "gap":
                gap_id = int(target)
            elif consumer:
                rfa_id = target
    except Exception as exc:  # a passing failure: release the claim so the next Save tries again
        logger.exception("routing %s of feedback %s failed", p.cause, item_id)
        registry.release_feedback_route(item_id, p.cause)
        return {"cause": p.cause, "state": "failed",
                "words": f"Not routed this time ({p.cause.replace('_', ' ')}): {exc}. Save again to retry."}
    if gap_id:
        registry.finish_feedback_route(item_id, p.cause, "gap", target=str(gap_id))
    elif rfa_id and p.automate:
        registry.finish_feedback_route(item_id, p.cause, "automate", target=rfa_id)
    elif rfa_id:
        registry.finish_feedback_route(item_id, p.cause, "rfa", target=rfa_id, reason=gap_reason)
    else:
        registry.finish_feedback_route(item_id, p.cause, "none", reason=gap_reason or "nothing to route")
        return {**describe(registry.get_feedback_route(item_id, p.cause), registry), "state": "not_routed"}
    return {**describe(registry.get_feedback_route(item_id, p.cause), registry), "state": "routed"}


def _raise_rfa(p: Plan, item: dict, *, gap_id: int, registry: Any) -> str:
    """Raise the question's RFA, or add this item to it when it exists. Returns the RFA's activity id.

    One per (question, cause, entity): claimed in `feedback_rfas` first, so two Saves on one question cannot raise two.
    The summary carries the question as asked (Q8) unless the context is private, when it names the item and cause
    only and leaves the curator note out too. Raised unassigned (Q5). A catalog gap's RFA is also stamped on its gap,
    so the gap→RFA route sees it was raised.
    """
    from resource_explorer.activity_logger import log_rfa

    slug = (item.get("slug") or "").strip()
    if slug:
        entity_type = (item.get("entity_type") or "").strip() or "repo"
        entity_slug, entity_name = slug, slug
    else:
        entity_type, entity_slug, entity_name = CATALOG_ENTITY
    question = (item.get("query_text") or "").strip()
    question_id = ((item.get("extra") or {}).get("question_id") or "").strip()
    private = private_context(item, registry)
    key = opaque_question_key(question, question_id) if private else question_check_name(question, question_id)
    pointer = {"kind": "feedback", "id": item["id"]}

    if not registry.claim_feedback_rfa(key, p.cause, entity_type, entity_slug):
        rfa_id = registry.get_feedback_rfa(key, p.cause, entity_type, entity_slug)
        if rfa_id and rfa_id != "pending":
            registry.add_activity_item(rfa_id, pointer)
            if gap_id:
                _stamp_gap(registry, gap_id, rfa_id)
        return rfa_id or "pending"
    try:
        label = _RFA_LABEL[p.cause]
        if private:
            summary = f"{label}: feedback item {item['id']} (the question is kept in Admin; {private})"
        else:
            summary = f"{label}: “{question}”"
        detail = [f"An admin diagnosed a rated answer as {p.cause.replace('_', ' ')}."]
        if p.compile_id:
            detail.append(f"The answer was built from compile {p.compile_id}.")
        note = ((item.get("triage") or {}).get("note") or "").strip()
        if p.cause == "unclear_question" and note and not private:
            detail.append(f"Curator note: {note}")
        detail.append("Raised for whoever keeps the question catalog; it is not assigned to anyone yet.")
        items = [pointer] + ([{"kind": "gap", "gap_id": gap_id}] if gap_id else [])
        rfa_id = log_rfa(registry, entity_type, entity_slug, entity_name, "open", summary,
                         detail=" ".join(detail), analysis_name="Question catalog", items=items)
    except Exception:
        registry.release_feedback_rfa(key, p.cause, entity_type, entity_slug)
        raise
    registry.set_feedback_rfa(key, p.cause, entity_type, entity_slug, rfa_id)
    if gap_id:
        _stamp_gap(registry, gap_id, rfa_id)
    return rfa_id


def _offer_cadence(current: dict | None) -> tuple[str, str]:
    """(cadence to offer, words about the current schedule). "" to offer when the analysis already runs daily."""
    if current and current.get("enabled") and current.get("schedule") in CADENCES:
        now = current["schedule"]
        faster = CADENCES[CADENCES.index(now) + 1] if now != CADENCES[-1] else ""
        return faster, f"it is already scheduled {now}"
    return DEFAULT_CADENCE, "it is not scheduled"


def _propose_schedule(p: Plan, item: dict, *, actor: str, registry: Any) -> tuple[str, str]:
    """Automate's answer to a stale diagnosis. Returns (consumer, target): ("automate", rfa id), or ("gap", gap id)
    when the analysis already runs daily.

    The proposal is an RFA naming the analysis and resource, whose drawer row can save the schedule (`offer`; the
    save is `only_if_faster`, so it never slows a schedule someone has since sped up). One per (resource, analysis,
    cadence), claimed in `feedback_rfas`; a later stale answer at the same cadence joins it, and one after the
    schedule was raised gets the next cadence's proposal. No question text: it is about the analysis.

    Already daily and still stale: a faster schedule is not the fix. The run, or the path from its result to the
    compile, failed, which is the analysis owner's: an `analysis_defect` gap (raises DefectGapRefused for a resource
    that cannot hold gaps, which `execute` records as not routed; any other error stays a passing failure).
    """
    from resource_explorer.activity_logger import log_rfa

    entity_type = (item.get("entity_type") or "").strip() or "repo"
    slug = item["slug"].strip()
    current = next((s for s in registry.get_schedules(entity_type, slug)
                    if s.get("analysis_id") == p.analysis_id), None)
    cadence, now = _offer_cadence(current)
    if not cadence:
        defect = Plan(cause=p.cause, gap_kind=ANALYSIS_DEFECT, analysis_id=p.analysis_id, role=p.role,
                      compile_id=p.compile_id)
        try:
            return "gap", str(_raise_gap(defect, item, actor=actor, registry=registry))
        except ValueError as exc:
            raise DefectGapRefused(str(exc)) from exc

    key = f"schedule:{p.analysis_id}:{cadence}"
    pointer = {"kind": "feedback", "id": item["id"]}
    if not registry.claim_feedback_rfa(key, p.cause, entity_type, slug):
        rfa_id = registry.get_feedback_rfa(key, p.cause, entity_type, slug)
        if rfa_id and rfa_id != "pending":
            registry.add_activity_item(rfa_id, pointer)
        return "automate", rfa_id or "pending"
    try:
        summary = f"Schedule {p.analysis_id} {cadence} on {slug}: an answer drew on its out-of-date result"
        detail = [f"An admin diagnosed a rated answer as stale; {now}."]
        if p.rule:
            detail.append(f"The manifest's rule: {p.rule}.")
        if p.compile_id:
            detail.append(f"The answer was built from compile {p.compile_id}.")
        offer = {"kind": "schedule", "entity_type": entity_type, "entity_slug": slug,
                 "analysis_id": p.analysis_id, "cadence": cadence}
        rfa_id = log_rfa(registry, entity_type, slug, slug, "open", summary, detail=" ".join(detail),
                         analysis_name="Automate: schedule proposal", items=[pointer], offer=offer)
    except Exception:
        registry.release_feedback_rfa(key, p.cause, entity_type, slug)
        raise
    registry.set_feedback_rfa(key, p.cause, entity_type, slug, rfa_id)
    return "automate", rfa_id


def _stamp_gap(registry: Any, gap_id: int, rfa_id: str) -> None:
    """Record the RFA on its gap, unless the gap already has one (the gap→RFA route never raises a second)."""
    gap = registry.get_gap(gap_id)
    if gap is not None and not gap.get("rfa_activity_id"):
        registry.mark_gap_rfa(gap_id, rfa_id)


def _raise_gap(p: Plan, item: dict, *, actor: str, registry: Any) -> int:
    """Raise (or refresh) the gap for one diagnosis, and link it to the checklist's disagreement gap on the same
    question when there is one. Several diagnoses of one question on one analysis are one gap, whose evidence lists
    every item; the asker's id is never in it."""
    slug = item["slug"].strip()
    question = (item.get("query_text") or "").strip()
    question_id = ((item.get("extra") or {}).get("question_id") or "").strip()
    private = private_context(item, registry)
    key = opaque_question_key(question, question_id) if private else question_check_name(question, question_id)
    existing = next((g for g in registry.list_gaps(slug)
                     if g["gap_kind"] == p.gap_kind and g["check_name"] == key
                     and (g.get("analysis_id") or "") == p.analysis_id), None)
    evidence = dict((existing or {}).get("evidence") or {})
    items = [i for i in evidence.get("feedback_items") or [] if i != item["id"]] + [item["id"]]
    compiles = [c for c in evidence.get("compile_ids") or [] if c and c != p.compile_id] + (
        [p.compile_id] if p.compile_id else [])
    evidence.update({
        "source": SOURCE_PERSON, "cause": p.cause, "feedback_items": items,
        "compile_ids": compiles, "diagnosed_by": actor,
    })
    if private:
        evidence.pop("question", None)
        evidence["private_context"] = private
    else:
        evidence["question"] = question
    if question_id:
        evidence["question_id"] = question_id
    if p.role:
        evidence["role"] = p.role
    disagreement = next((g for g in registry.list_gaps(slug)
                         if g["gap_kind"] == DISAGREEMENT
                         and g["check_name"] in (key, question_check_name(question, question_id),
                                                 question_check_name(question))), None)
    if disagreement:
        evidence["disagreement_gap"] = disagreement["id"]
    on = p.analysis_id or "the catalog"
    what = f"{on} {_CAUSE_WORDS[p.gap_kind]}." if p.analysis_id else f"{_CAUSE_WORDS[p.gap_kind]}."
    if private:
        sentence = f"An admin diagnosed feedback item {item['id']} ({private}; the question is kept in Admin): {what}"
    else:
        sentence = f"An admin diagnosed the answer to “{question}”: {what}"
    gap_id = registry.upsert_gap(slug, p.analysis_id, p.gap_kind, key, sentence, evidence=evidence)
    if disagreement:
        linked = dict(disagreement.get("evidence") or {})
        diagnosed = [g for g in linked.get("diagnosed_as") or [] if g != gap_id] + [gap_id]
        if linked.get("diagnosed_as") != diagnosed:
            registry.set_gap_evidence(disagreement["id"], {**linked, "diagnosed_as": diagnosed})
    return gap_id


def reroute(item: dict, cause: str, *, analysis_id: str, actor: str, registry: Any) -> dict:
    """An admin's explicit re-route: route this item's `cause` again, on `analysis_id` (the architect's P2: a Save
    keyed on (item, cause) cannot move a default charge, so moving it is its own action).

    Only a finished gap route, or one recorded as not routed, can be re-routed. The earlier gap stays open, since
    other items may hold it, but no longer lists this item: its evidence moves the id to `rerouted_items`. Raises
    RouteRefused for a route that does not exist, is still in flight, or an analysis outside the charge set.
    """
    cause = (cause or "").strip()
    if cause not in GAP_CAUSES:
        raise RouteRefused(f"{cause!r} is not routed to a gap, so it cannot be re-routed.")
    existing = registry.get_feedback_route(item["id"], cause)
    if existing is None:
        raise RouteRefused(f"This item has no {cause.replace('_', ' ')} route to move; save the diagnosis first.")
    if existing["consumer"] not in ("gap", "none"):
        raise RouteRefused("That route is still being made; try again in a moment.")
    p = plan(item, cause, analysis_id=analysis_id, registry=registry)          # validates the pick
    old = existing.get("target") or ""
    if existing["consumer"] == "gap" and old.isdigit():
        gap = registry.get_gap(int(old))
        if gap is not None:
            ev = dict(gap.get("evidence") or {})
            ev["feedback_items"] = [i for i in ev.get("feedback_items") or [] if i != item["id"]]
            ev["rerouted_items"] = [i for i in ev.get("rerouted_items") or [] if i != item["id"]] + [item["id"]]
            registry.set_gap_evidence(int(old), ev)
    registry.drop_feedback_route(item["id"], cause)
    out = execute(p, item, actor=actor, registry=registry) or {}
    return {**out, "rerouted_from": old}


def mark_deleted_items(gaps: list[dict], exists) -> None:
    """A gap's evidence names the feedback items that raised it. When an item has since been deleted, say so rather
    than point at nothing (the architect's A2). `exists(id) -> bool`."""
    for g in gaps:
        ids = (g.get("evidence") or {}).get("feedback_items") or []
        gone = [i for i in ids if not exists(i)]
        if gone:
            g["evidence"] = {**g["evidence"], "feedback_items_deleted": gone}

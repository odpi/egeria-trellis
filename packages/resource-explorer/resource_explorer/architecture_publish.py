"""Publish for a repository's architecture: the one verb that writes accepted components and blueprints to Egeria.

Accept and Reject are decisions only (owner, 2026-10-09: "Publish should be the verb to save to Egeria";
the owner's 2026-10-08 ruling that Publish is the one Egeria verb). This module answers two questions from RE's own rows, with no Egeria call:

* `publish_plan`  what a press would write: every accepted component with no Egeria element yet, and every
  accepted blueprint with no element yet or whose compositions are not all confirmed. Shown BEFORE the press
  and used by the run, so the list and the write cannot drift.
* `last_results`  what the last press did, per item, from the proof rows the run wrote (`architecture_publish`).

`run_publish` is the write. It re-reads the plan at run time, writes only items still in it, and records one
proof row per item: done, skipped (already there), failed (with Egeria's sentence) or partial (written, but
not all of it confirmed). A reject never writes: RE has no delete path, so a rejected item already in Egeria
is only SAID to be still there.

No DDL: a blueprint verdict's shape and identifier choices ride in the verdict row's `retyped_to` (unused by
a blueprint verdict) as JSON.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

P_PUBLISH_ITEM = "architecture_publish"
NODE_PUBLISH_ITEM = "architecture_publish"

# Composition proof statuses that mean the pair is not confirmed (blueprint_materializer.link_sub_components).
_UNCONFIRMED = ("unconfirmed", "error", "unread")

DONE, SKIPPED, FAILED, PARTIAL = "done", "skipped", "failed", "partial"
#: An item the presser may not change (Brief Z): skipped with the reason, the rest of the press goes on.
NOT_PERMITTED = "not_permitted"
NOT_PERMITTED_WORDS = "not permitted"


# ── the choices a blueprint verdict carries ───────────────────────────────────────────────────────────

def encode_blueprint_choices(shape: str, identifier: str) -> str:
    """The shape flip and identifier chosen at Accept, kept for Publish. '' when neither was chosen."""
    choices = {k: v for k, v in (("shape", shape), ("identifier", identifier)) if v}
    return json.dumps(choices, sort_keys=True) if choices else ""


def blueprint_choices(verdict_row: dict | None) -> dict:
    """{shape, identifier} chosen with the blueprint's verdict; empty values when none were chosen or the
    stored text is not ours."""
    raw = (verdict_row or {}).get("retyped_to") or ""
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    return {"shape": str(d.get("shape") or ""), "identifier": str(d.get("identifier") or "")}


# ── the plan ──────────────────────────────────────────────────────────────────────────────────────────

def _blueprint_height(by_name: dict[str, dict], name: str, seen: frozenset = frozenset()) -> int:
    """Children are written before the parent that links them."""
    bp = by_name.get(name)
    if not bp or name in seen:
        return 0
    kids = [c for c in (bp.get("children") or []) if c in by_name]
    return 1 + max((_blueprint_height(by_name, c, seen | {name}) for c in kids), default=-1) if kids else 0


P_ATTACHED = "blueprint_attached"


def planned_handoff(bp: dict, nodes_by_slug: dict, shape: str, proofs: list[dict], key: str) -> dict:
    """What the write WILL hand to this blueprint, derived from the same `plan_shape` the write uses (the latest
    shape choice, the cluster's root, the members that are in Egeria by RE's cache rows). The plan and the write
    cannot then disagree about the root: in the contents shape the root is NOT a member (even when cached), in the
    container shape it is the only member and the container of the compositions.

    Returns {wanted, members, composed, known, container}: `wanted` every GUID to be handed over; `members` the
    ones handed to the blueprint as DIRECT members (and its child blueprints); `composed` the ones linked as
    sub-components of the container (never direct members: a later flip to the contents shape must attach them);
    `known` the GUIDs a composition pair may use (the container and its planned children; empty in the contents
    shape, which has no compositions); `container` the root's GUID."""
    from dataclasses import replace

    from resource_explorer.blueprint_shape import CONTAINER, Node, plan_shape

    guids = {m["slug"]: (m.get("materialized") or {}).get("guid", "") for m in bp.get("member_status") or []}
    nodes = [replace(nodes_by_slug.get(s) or Node(slug=s, name=s), guid=g) for s, g in guids.items()]
    plan = plan_shape(bp["cluster_name"], nodes, requested=shape, composed_into=bp.get("composed_into") or "")
    kids = {(c.get("materialized") or {}).get("guid") for c in bp.get("child_status") or []} - {None, ""}
    root = plan.root
    others = {n.guid for n in nodes if n.guid and (root is None or n.slug != root.slug)}
    # The newest press's own rows: its shape proof, and the compositions it wrote after it.
    last_shape = max((i for i, p in enumerate(proofs) if p["proof"] == "shape" and p["table_name"] == key), default=-1)
    newest = [p for p in proofs[last_shape + 1:] if last_shape >= 0 and p["proof"] == "composition" and p["table_name"] == key]
    pressed_container = last_shape >= 0 and str((proofs[last_shape].get("detail") or {}).get("shape", "")).startswith(CONTAINER)
    # A root with no cache GUID is judged "referenced only" here, but the write may have ADOPTED a content-pack
    # element for it (known only from Egeria): then the newest press's rows say it was written as the container.
    adopted = plan.shape != CONTAINER and root is not None and not root.guid and pressed_container and bool(newest)
    if plan.shape != CONTAINER and not adopted:
        members = {n.guid for n in plan.members if n.guid} | kids
        return {"wanted": sorted(members), "members": sorted(members), "composed": [], "known": [], "container": ""}
    container = root.guid if root is not None and root.guid else (newest[0]["element_guid"] if newest else "")
    pair_kids = others if adopted else {c.guid for _, c in plan.compositions if c.guid}
    members = ({root.guid} if root is not None and root.guid else set()) | kids
    return {"wanted": sorted(members | pair_kids), "members": sorted(members), "composed": sorted(pair_kids),
            "known": sorted(({container} - {""}) | pair_kids), "container": container}


EARLIER_SHAPE_UNKNOWN = "shape of the earlier press unknown \u2014 check Egeria"


def _attached(proofs: list[dict], scope_key: str) -> tuple[set[str], set[str], bool]:
    """(direct members, composed sub-components, earlier shape unknown) the LAST press recorded as actually
    handed over.

    A row written before the two were kept apart has only `guids`. It is split by the shape that same press
    wrote (its `shape` proof row, the newest one before it): contents means every GUID was a direct member;
    container means the GUIDs that press linked as sub-components (its `composition` rows) were composed and
    the rest (the container, child blueprints) were members. With no shape row to read, the GUIDs count as
    both, as they did then, and the third value says the plan cannot tell, so a flip may attach nothing."""
    members: set[str] = set()
    composed: set[str] = set()
    unknown = False
    for i, p in enumerate(proofs):
        if p["proof"] != P_ATTACHED or p["table_name"] != scope_key:
            continue
        d = p.get("detail") or {}
        if "members" in d or "composed" in d:
            members, composed, unknown = set(d.get("members") or []), set(d.get("composed") or []), False
            continue
        guids = set(d.get("guids") or [])
        last_shape = max((j for j in range(i) if proofs[j]["proof"] == "shape"
                          and proofs[j]["table_name"] == scope_key), default=-1)
        shape = str(((proofs[last_shape].get("detail") or {}).get("shape") or "")) if last_shape >= 0 else ""
        if shape.startswith("contents"):
            members, composed, unknown = guids, set(), False
        elif shape.startswith("container"):
            kids = {q["target_guid"] for q in proofs[last_shape + 1:i]
                    if q["proof"] == "composition" and q["table_name"] == scope_key}
            members, composed, unknown = guids - kids, guids & kids, False
        else:
            members, composed, unknown = guids, guids, True
    return members, composed, unknown


def _composition_state(proofs: list[dict], scope_key: str, known: list[str]) -> tuple[int, set[str]]:
    """(how many wanted compositions are not confirmed, the child GUIDs of those), from the LATEST proof per
    pair. Only pairs among `known` count: a stale row for a pair the plan no longer wants must not keep the
    blueprint in the plan for ever."""
    latest: dict[str, dict] = {}
    for p in proofs:
        if p["proof"] == "composition" and p["table_name"] == scope_key:
            if not ({p["element_guid"], p["target_guid"]} <= set(known)):
                continue
            latest[p["qualified_name"]] = p
    bad = [p for p in latest.values() if (p.get("detail") or {}).get("status") in _UNCONFIRMED]
    return len(bad), {p["target_guid"] for p in bad}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def label_for(n_components: int, n_blueprints: int) -> str:
    """'Publish 3 components · 1 blueprint': the object and count on the control; only the parts that exist."""
    parts = []
    if n_components:
        parts.append(_plural(n_components, "component"))
    if n_blueprints:
        parts.append(_plural(n_blueprints, "blueprint"))
    return "Publish " + " · ".join(parts) if parts else "Nothing to publish"


def publish_plan(registry, slug: str) -> dict:
    """What a Publish press would write now, from RE's rows only (no Egeria call)."""
    from resource_explorer.component_tree import (
        _NOT_A_PATH, _component_verdicts, _components, resolve_verdict, sharing_recovery_results)

    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        _architecture_recovery_results, _candidate_blueprints_results, _recovery_snapshot)
    # The snapshot and the recovery results are built once and shared: the results already carry the
    # candidate blueprints, so they are not derived a second time below.
    snapshot = _recovery_snapshot(registry, slug)
    # `__wrapped__`: the memoized form keys on its arguments, and a frozen snapshot is unhashable.
    recovery = getattr(_architecture_recovery_results, "__wrapped__", _architecture_recovery_results)(
        registry, slug, max_depth=None, snapshot=snapshot) or {}
    with sharing_recovery_results(recovery):
        comps = [c for c in _components(registry, slug) if (c.get("path") or "") not in _NOT_A_PATH]
    verdicts = _component_verdicts(registry, slug)
    present = registry.get_materialized_components("repo", slug)
    to_write, in_egeria, rejected_in_egeria = [], 0, 0
    for c in sorted(comps, key=lambda x: x["path"]):
        if c.get("structural"):
            continue
        v = (resolve_verdict(c["path"], verdicts) or {}).get("verdict")
        held = bool((present.get(c["path"]) or {}).get("guid"))
        if v == "accepted":
            if held:
                in_egeria += 1
            else:
                to_write.append({"path": c["path"], "name": c.get("name") or c["path"], "type": c.get("type") or ""})
        elif v == "rejected" and held:
            rejected_in_egeria += 1

    from resource_explorer.blueprint_kinds import blueprint_display_name, repo_label

    blueprints = recovery.get("blueprints")
    if blueprints is None:   # an envelope (never surveyed) carries none: read it the long way
        blueprints = _candidate_blueprints_results(registry, slug, snapshot)
    raw_verdicts = {k: v for k, v in registry.get_component_verdicts("repo", slug).items()
                    if v.get("verdict_target") == "blueprint"}
    proofs = registry.list_catalogue_commit_proofs(slug)
    from resource_explorer.blueprint_shape import component_nodes
    nodes_by_slug = component_nodes(registry, slug, snapshot)
    project = registry.get(slug)
    label = repo_label(slug, getattr(project, "display_name", "") or "")
    by_name = {b["cluster_name"]: b for b in blueprints}
    bp_write, bp_present, bp_rejected_in_egeria, bp_held_back, bp_check = [], 0, 0, [], []
    for b in blueprints:
        key = f"{b['perspective']}::{b['cluster_name']}"
        verdict = (b.get("verdict") or {}).get("verdict")
        held = bool((b.get("materialized") or {}).get("guid"))
        if verdict == "rejected":
            bp_rejected_in_egeria += 1 if held else 0
            continue
        if verdict != "accepted":
            continue
        choices = blueprint_choices(raw_verdicts.get(key))
        identity = b.get("identity") or {}
        if not held and identity.get("needs_identifier") and not choices["identifier"]:
            # Another blueprint of this kind holds (or takes at this press) the repository's plain identity: the
            # write would be refused, so it is held back and said, never attempted.
            bp_held_back.append({"key": key, "perspective": b["perspective"], "cluster_name": b["cluster_name"],
                                 "name": b["cluster_name"], "words": identity.get("sentence") or ""})
            continue
        hand = planned_handoff(b, nodes_by_slug, choices["shape"], proofs, key)
        wanted = hand["wanted"]
        unfinished, unconfirmed_kids = _composition_state(proofs, key, hand["known"]) if held else (0, set())
        # A child whose composition is unconfirmed is counted once, as a composition to confirm. A direct member
        # and a composed sub-component are different handoffs: one never stands in for the other.
        att_members, att_composed, shape_unknown = _attached(proofs, key) if held else (set(), set(), False)
        if shape_unknown:
            bp_check.append({"key": key, "name": b["cluster_name"], "words": EARLIER_SHAPE_UNKNOWN})
        unattached = (len(set(hand["members"]) - att_members)
                      + len(set(hand["composed"]) - att_composed - unconfirmed_kids)) if held else 0
        if held and not unfinished and not unattached:
            bp_present += 1
            continue
        sole = (sum(1 for x in blueprints if x["perspective"] == b["perspective"] and not x.get("parent")) == 1
                and not b.get("parent"))
        bp_write.append({
            "key": key, "perspective": b["perspective"], "cluster_name": b["cluster_name"],
            "name": blueprint_display_name(label, b["perspective"], b["cluster_name"], sole_root=sole,
                                           identifier=choices["identifier"]),
            "state": "finish" if held else "new", "unconfirmed_compositions": unfinished,
            "unattached": unattached, "wanted": wanted,
            "shape": choices["shape"], "identifier": choices["identifier"],
            "_height": _blueprint_height(by_name, b["cluster_name"]),
        })
    bp_write.sort(key=lambda x: (x["_height"], x["key"]))
    for x in bp_write:
        x.pop("_height")

    return {
        "slug": slug,
        "components": {"to_write": to_write, "in_egeria": in_egeria, "rejected_in_egeria": rejected_in_egeria},
        "blueprints": {"to_write": bp_write, "in_egeria": bp_present, "rejected_in_egeria": bp_rejected_in_egeria,
                       "needs_identifier": bp_held_back,
                       # Last pressed by code that did not keep members and sub-components apart, with no shape
                       # row to split them by: the plan cannot tell what a flip still has to attach.
                       "check_egeria": bp_check},
        "label": label_for(len(to_write), len(bp_write)),
        "nothing": not to_write and not bp_write,
    }


def last_results(registry, slug: str) -> dict:
    """The newest press's per-item results, from the proof rows its run wrote: {run, at, items:[...]}.
    Empty when nothing was ever published from here."""
    rows = [p for p in registry.list_catalogue_commit_proofs(slug)
            if p["proof"] == P_PUBLISH_ITEM and p["node_kind"] == NODE_PUBLISH_ITEM]
    mine = [r for r in registry.list_runs(kind="publish_architecture", limit=50)
            if (json.loads(r.get("target") or "{}") or {}).get("slug") == slug]
    newest = mine[0] if mine else None
    if newest and newest.get("result_ref") and (not rows or rows[-1]["curation_id"] != newest["result_ref"]) \
            and newest.get("state") in ("succeeded", "failed", "cancelled"):
        # The newest press ended without writing any result rows (it crashed first): the rows below belong to
        # an EARLIER press and must not be shown as current.
        return {"run": newest["result_ref"], "at": "", "items": [], "missing": True}
    if not rows:
        return {"run": "", "at": "", "items": []}
    run = rows[-1]["curation_id"]
    items = [{"key": p["table_name"], **(p.get("detail") or {})} for p in rows if p["curation_id"] == run]
    return {"run": run, "at": rows[-1]["read_at"], "items": items}


# ── the press ─────────────────────────────────────────────────────────────────────────────────────────

class PublishAlreadyRunning(Exception):
    """A publish for this repository is queued, claimed or running: a second press is refused."""


def enqueue_publish(registry, project, plan: dict, *, requested_by: str, authorize,
                    only: set[str] | None = None) -> dict:
    """Queue ONE publish_architecture run for the items in `plan` (all of them, or those whose path/key is in
    `only`), with a 'running' activity row the run closes. Shared by the web press and the CLI, so both write
    exactly what the plan lists. `authorize(kind, key)` raises `CurationDenied` for an item the caller may not
    publish (workflows.curate.publish_item_access: every operation the write performs): that item is NOT written and is reported as its own result row,
    "not permitted · <reason>", and the rest are queued (Brief Z). Only when nothing is permitted does this
    raise CurationDenied (the whole-press 403). Returns {run_id, activity_id, queued, refused, plan}; queues
    nothing when nothing is selected. Raises PublishAlreadyRunning."""
    from resource_explorer.activity_logger import log_survey
    from resource_explorer.workflows.curate import CURATOR_ROLES, CurationDenied, _caller_identity

    slug = project.slug
    comps = [c for c in plan["components"]["to_write"] if only is None or c["path"] in only]
    bps = [b for b in plan["blueprints"]["to_write"] if only is None or b["key"] in only]
    if not comps and not bps:
        return {"run_id": None, "activity_id": None, "queued": 0, "refused": [], "plan": plan}
    for state in ("queued", "claimed", "running"):
        if any((r.get("target") and slug == json.loads(r["target"]).get("slug"))
               for r in registry.list_runs(kind="publish_architecture", state=state, limit=50)):
            raise PublishAlreadyRunning("a publish is already running for this repository")
    refused: list[dict] = []

    def permitted(kind: str, key: str, name: str) -> bool:
        try:
            authorize(kind, key)
            return True
        except CurationDenied as exc:
            refused.append({"kind": kind, "key": key, "name": name, "words": str(exc)})
            return False
    paths = [c["path"] for c in comps if permitted("component", c["path"], c.get("name") or c["path"])]
    keys = [b["key"] for b in bps if permitted("blueprint", b["key"], b.get("name") or b["key"])]
    if not paths and not keys:
        reasons = sorted({r["words"] for r in refused})
        raise CurationDenied(reasons[0] if len(reasons) == 1 else "; ".join(reasons))
    label = plan["label"] if only is None and not refused else label_for(len(paths), len(keys))
    # The run re-checks each item against the element it resolves (Brief Z round 2). It runs as the requester
    # with no JWT, so a Portal curator/admin override seen HERE is carried to it; nothing else is.
    ident = _caller_identity()
    portal_role = bool(ident and (ident.role or "").lower() in CURATOR_ROLES)
    activity_id = log_survey(
        registry, entity_type="repo", entity_slug=slug,
        entity_name=project.display_name, entity_location=project.github_url,
        intent="curate", status="running",
        summary=f"Publishing {label.removeprefix('Publish ')} of {project.display_name}…")
    try:
        # Queued: committed by RE's daemon on the person's behalf, Ownership = them (Brief I).
        run_id = registry.enqueue_run("publish_architecture", {"slug": slug, "paths": paths, "blueprints": keys,
                                                               **({"not_permitted": refused} if refused else {}),
                                                               **({"portal_role": True} if portal_role else {})},
                                      result_ref=activity_id, requested_by=requested_by)
    except Exception as exc:
        # Opened 'running' above; nothing will run to close it.
        registry.update_activity_status(
            activity_id, "error", summary=f"Publishing {project.display_name} was not started: {type(exc).__name__}: {exc}"[:400])
        raise
    return {"run_id": run_id, "activity_id": activity_id, "queued": len(paths) + len(keys), "refused": refused,
            "plan": plan}


# ── the write ─────────────────────────────────────────────────────────────────────────────────────────

def _record(registry, slug: str, run: str, key: str, guid: str, detail: dict) -> None:
    """One result row per item. A failed proof write is logged; it never undoes the Egeria write."""
    try:
        registry.append_catalogue_commit_proof(
            slug, proof=P_PUBLISH_ITEM, node_kind=NODE_PUBLISH_ITEM, table_name=key, curation_id=run,
            element_guid=guid, detail=detail)
    except Exception as exc:
        log.warning("could not record the publish result for %s: %s", key, exc)


def _publish_component(registry, slug: str, path: str, check=None) -> tuple[str, str, str]:
    """(status, words, guid) for one accepted component. `check(kind, key, guid)` returns a refusal reason
    ("" = permitted) for an element the write RESOLVED rather than created (adopted from the content pack, or
    found already in Egeria): it runs before the first Egeria write to that element (the promotion)."""
    from resource_explorer.secret_redaction import scrub_text
    from resource_explorer.workflows.curate import (
        NODE_PROMOTION_COMPONENT,
        materialize_component_if_accepted,
        promote_to_publish_zones,
        record_promotion,
    )
    res = materialize_component_if_accepted(registry, "repo", slug, path, "accepted")
    if not res or res.get("status") == "error":
        return FAILED, scrub_text(str((res or {}).get("error") or "Egeria gave no answer"))[:300], ""
    guid = res.get("guid", "")
    if res.get("status") == "skipped" or not guid:
        # Nothing was written (a private element's zones were not confirmed, say): its own word, never "done".
        return SKIPPED, scrub_text(str(res.get("reason") or res.get("words") or "Egeria returned no element"))[:300], guid
    if guid and check is not None and res.get("status") != "materialized":
        why = check("component", path, guid)
        if why:
            return NOT_PERMITTED, scrub_text(f"{NOT_PERMITTED_WORDS} · {why}")[:300], guid
    if guid:
        promotion = promote_to_publish_zones(guid)
        record_promotion(registry, slug, path, NODE_PROMOTION_COMPONENT, promotion)
        if promotion.get("status") == "error":
            return PARTIAL, scrub_text(f"written, but not promoted: {promotion.get('error') or promotion.get('words')}")[:300], guid
    if res.get("requester_not_recorded"):
        return PARTIAL, scrub_text(f"partial · {res['requester_not_recorded']}")[:300], guid
    return DONE, "created in Egeria" if res.get("status") == "materialized" else "in Egeria", guid


def _publish_blueprint(registry, slug: str, item: dict, check_resolved=None) -> tuple[str, str, str]:
    """(status, words, guid) for one accepted blueprint: created or adopted, members attached, compositions linked.
    `check_resolved(guids)` (the run's access re-check) sees every element the write resolved, adopted ones
    included, immediately before the first link or membership is written."""
    from resource_explorer.secret_redaction import scrub_text
    from resource_explorer.workflows.curate import (
        NODE_PROMOTION_BLUEPRINT,
        materialize_blueprint_if_accepted,
        promote_to_publish_zones,
        record_promotion,
    )
    res = materialize_blueprint_if_accepted(
        registry, "repo", slug, item["perspective"], item["cluster_name"], "accepted",
        **({"shape": item["shape"]} if item.get("shape") else {}),
        **({"identifier": item["identifier"]} if item.get("identifier") else {}),
        **({"check_resolved": check_resolved} if check_resolved is not None else {}))
    if not res:
        return FAILED, "nothing was attempted", ""
    if res.get("status") == "error":
        return FAILED, scrub_text(str(res.get("error") or "Egeria gave no answer"))[:300], ""
    if res.get("status") == NOT_PERMITTED:
        return NOT_PERMITTED, scrub_text(f"{NOT_PERMITTED_WORDS} · {res.get('error') or ''}")[:300], res.get("guid", "")
    guid = res.get("guid", "")
    if res.get("status") == "skipped" or not guid:
        return SKIPPED, scrub_text(str(res.get("reason") or res.get("words") or "Egeria returned no element"))[:300], guid
    problems = []
    if guid:
        # What this press handled: a later plan keeps the blueprint only while it wants more than this.
        try:
            registry.append_catalogue_commit_proof(
                slug, proof=P_ATTACHED, node_kind=NODE_PUBLISH_ITEM, table_name=item["key"], element_guid=guid,
                detail={"guids": list(res.get("attached_guids") or []),
                        # Kept apart when the write said which was which (workflows.curate does).
                        **({"members": list(res.get("attached_members") or []),
                            "composed": list(res.get("attached_composed") or [])}
                           if "attached_members" in res or "attached_composed" in res else {})})
        except Exception as exc:
            log.warning("could not record what was attached for %s: %s", item["key"], exc)
            # Said, not swallowed: without the record the plan will offer this blueprint again.
            problems.append(f"what was attached could not be recorded ({type(exc).__name__}: {scrub_text(str(exc))[:120]})")
        promotion = promote_to_publish_zones(guid)
        record_promotion(registry, slug, item["key"], NODE_PROMOTION_BLUEPRINT, promotion)
        if promotion.get("status") == "error":
            problems.append(f"not promoted: {promotion.get('error') or promotion.get('words')}")
    comps = res.get("compositions") or []
    bad = [c for c in comps if c.get("status") in _UNCONFIRMED]
    if bad:
        problems.append(f"{len(bad)} of {len(comps)} compositions not confirmed")
    for field, words in (("unmaterialized_members", "components not in Egeria yet"),
                         ("unmaterialized_children", "child blueprints not linked"),
                         ("children_gone", "child blueprints no longer in Egeria"),
                         ("children_unreadable", "child blueprints could not be read")):
        if res.get(field):
            problems.append(f"{len(res[field])} {words}")
    if res.get("adopted_unproven"):
        problems.append("adopted, but the re-key proof was not written")
    if res.get("requester_not_recorded"):
        problems.append(res["requester_not_recorded"])
    if problems:
        return PARTIAL, scrub_text("written, but " + "; ".join(problems))[:400], guid
    return DONE, "in Egeria · compositions confirmed" if comps else "in Egeria", guid


def run_publish(registry, slug: str, target: dict, run: str) -> list[dict]:
    """Write what is still in the plan out of what the press listed. Returns the per-item results
    ({kind, key, name, status, words}); each is also recorded as a proof row under `run`. Never raises for
    an item: one failing item does not stop the next."""
    from resource_explorer.secret_redaction import scrub_text

    plan = publish_plan(registry, slug)
    want_paths = set(target.get("paths") or [])
    want_bps = set(target.get("blueprints") or [])
    todo_c = {c["path"]: c for c in plan["components"]["to_write"]}
    todo_b = {b["key"]: b for b in plan["blueprints"]["to_write"]}
    held_back = {b["key"]: b for b in plan["blueprints"].get("needs_identifier") or []}
    results: list[dict] = []
    from resource_explorer.workflows.curate import publish_item_access, resolved_elements_access
    from resource_explorer.zone_access import EgeriaAccessReader

    # Brief Z round 2: the press checked each item, but the write runs later and may resolve elements the press
    # could not see (a content-pack component, an element found by qualifiedName, members adopted mid-write).
    # So the run checks again, as the requester (run_queue sets the caller), immediately before each write.
    # A Portal curator/admin override seen at the press is carried in the target.
    reader = EgeriaAccessReader()
    override = bool(target.get("portal_role"))

    def refused(kind: str, key: str, guid: str | None = None) -> str:
        if override:
            return ""
        try:
            d = publish_item_access(registry, slug, kind, key, reader=reader, element_guid=guid)
        except Exception as exc:  # an unexpected failure is a refusal with its reason, never a pass
            return f"could not check access ({type(exc).__name__}: {exc})"
        return "" if d.allowed else d.reason

    def resolved_refused(key: str, guids: list[str]) -> str:
        if override:
            return ""
        try:
            d = resolved_elements_access(registry, slug, key, guids, reader=reader)
        except Exception as exc:
            return f"could not check access ({type(exc).__name__}: {exc})"
        return "" if d.allowed else d.reason

    def add(kind: str, key: str, name: str, status: str, words: str, guid: str = "") -> None:
        row = {"kind": kind, "key": key, "name": name, "status": status, "words": words}
        results.append(row)
        _record(registry, slug, run, key, guid, {k: row[k] for k in ("kind", "name", "status", "words")})

    # Refused at the press (the presser may not change them): one row each, said with the reason, never written.
    for r in target.get("not_permitted") or []:
        add(r.get("kind") or "component", r["key"], r.get("name") or r["key"], NOT_PERMITTED,
            f"{NOT_PERMITTED_WORDS} · {r.get('words') or ''}".rstrip(" ·"))
    for path in sorted(want_paths):
        c = todo_c.get(path)
        if c is None:                  # present now, or no longer accepted: nothing to write
            held = bool((registry.get_materialized_component("repo", slug, path) or {}).get("guid"))
            add("component", path, path.rsplit("/", 1)[-1], SKIPPED,
                "already in Egeria" if held else "no longer accepted")
            continue
        why = refused("component", path)
        if why:
            add("component", path, c["name"], NOT_PERMITTED, f"{NOT_PERMITTED_WORDS} · {why}")
            continue
        try:
            status, words, guid = _publish_component(registry, slug, path, check=refused)
        except Exception as exc:
            status, words, guid = FAILED, scrub_text(f"{type(exc).__name__}: {exc}")[:300], ""
        add("component", path, c["name"], status, words, guid)
    # Components first: a blueprint attaches only members that are already in Egeria.
    order = [k for k in (b["key"] for b in plan["blueprints"]["to_write"]) if k in want_bps]
    order += sorted(want_bps - set(order))
    for key in order:
        b = todo_b.get(key)
        if b is None and key in held_back:      # the write would be refused: said, not attempted
            add("blueprint", key, held_back[key]["name"], SKIPPED, held_back[key]["words"] or "needs an identifier")
            continue
        if b is None:       # present and confirmed now, or no longer accepted: say which, from the cache row
            persp, _, cluster = key.partition("::")
            held = bool((registry.get_materialized_blueprint("repo", slug, persp, cluster) or {}).get("guid"))
            accepted = (registry.get_component_verdicts("repo", slug).get(key) or {}).get("verdict") == "accepted"
            add("blueprint", key, cluster, SKIPPED,
                "already in Egeria · compositions confirmed" if held and accepted else "no longer accepted")
            continue
        why = refused("blueprint", key)
        if why:
            add("blueprint", key, b["name"], NOT_PERMITTED, f"{NOT_PERMITTED_WORDS} · {why}")
            continue
        try:
            status, words, guid = _publish_blueprint(registry, slug, b,
                                                     check_resolved=lambda guids, k=key: resolved_refused(k, guids))
        except Exception as exc:
            status, words, guid = FAILED, scrub_text(f"{type(exc).__name__}: {exc}")[:300], ""
        add("blueprint", key, b["name"], status, words, guid)
    return results

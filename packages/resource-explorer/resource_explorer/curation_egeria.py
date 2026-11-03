"""Tags become public Egeria InformalTags; RE groups become Folio collections (Brief T, 2026-10-10).

The project owner, 2026-10-10, verbatim: "tags should be public", "Use Folio", "Yes, removing a tag removes it
in Egeria".

What goes to Egeria, and through what:

    a tag on a resource in Egeria   -> a public InformalTag (found by name, created only when absent) linked to
                                       the resource's asset by AttachedTag      outbox kind `informal_tag_link`
    removing that tag in RE         -> the AttachedTag link is removed          outbox kind `informal_tag_detach`
    the resource's RE group         -> a Folio `Folio::RE::group::<slug>` (found by qualifiedName, created only
                                       when absent) with the asset as a member  outbox kind `group_folio_membership`
    leaving or changing the group   -> that membership is removed               `group_folio_membership_detach`

**Never a delete of an InformalTag or a Folio element.** Removing a tag only removes its link to the asset;
deleting an RE group only removes memberships (and reports the Folio). Deleting either element would be a delete
under the archive-cascade block, and the owner deletes those by hand. Nothing in this module calls `delete_tag`,
`delete_collection` or any other element delete, and a test fails if it does.

"Public" here: Egeria 6's InformalTag carries no public/private flag. `InformalTagProperties` adds nothing to
`ReferenceableProperties` (frameworks/openmetadata/properties/feedback/InformalTagProperties.java:32), and a
"private" tag is only `findMyTags`' filter on the creator (handlers/InformalTagHandler.java:267-290). Every tag is
therefore readable by anyone who can read it; what makes RE's tags shared rather than per-person is that RE finds
an existing tag BY NAME before it creates one, and creates with a name-based qualifiedName
(`InformalTag::<tag>`) rather than pyegeria's per-user, timestamped default (`make_feedback_qn`).

**One Publish per page (Brief F, owner 2026-10-11).** "I think there should be one publish button, but each section
can have a save or indication that it will be published when the user presses publish". A Save in Findable (a tag
added or removed, the group changed) records the change in RE ONLY; the band shows "saved · will publish"; the
page's Publish sends it (`sync_curation`, called ONLY from `single_publish.send_with_publish`, which a person's
press runs: the repository's Publish, the database and file system publish routes). Nothing on Save, and no
background publish (analysis auto-publish, bulk ops, resync, CLI), talks to Egeria for tags or groups (round 2).

**One source of truth (section 5b rule 1).** `curation_plan` is the only function that decides what each tag and
the group should be in Egeria and what state each is in; the Findable band and the Publish band's list read it
(GET), and `sync_curation` enqueues exactly the actions it names. After a fully successful sync the plan has nothing
left to do.

**The state is read from persisted outbox rows**, never from what a click assumed: "in Egeria" means a `done`
link row for this asset, "unlinked" a `done` detach row, and so on. Rows for an asset GUID that is no longer the
resource's (an Egeria wipe and re-publish) are history and are ignored: they are never detached, since that
element is gone.

**Unlinks are destructive outbox kinds** (`egeria_outbox.DESTRUCTIVE_OUTBOX_KINDS`): a failed or lapsed unlink is
dead on its first failure, never re-sent by the drain, and never re-sent by a later sync (a later Publish) either.
The band shows "not sent · <reason>" with a retry a person presses (`retry_item`), which queues a NEW row.

**Identity (Brief I).** A sync inside a person's request drains inline as that person (`current_principal()`, the
Caller), who is checked per item (`per_item_access`). Rows carry the requester in `by`, so the background loop (a
Daemon drain) applies a left-over row as `Daemon(OUTBOX, requested_by=<by>)`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

TAG_LINK = "informal_tag_link"
TAG_DETACH = "informal_tag_detach"
GROUP_LINK = "group_folio_membership"
GROUP_DETACH = "group_folio_membership_detach"
KINDS = (TAG_LINK, TAG_DETACH, GROUP_LINK, GROUP_DETACH)
_LINK_OF = {"tag": TAG_LINK, "group": GROUP_LINK}
_DETACH_OF = {"tag": TAG_DETACH, "group": GROUP_DETACH}

#: Item states (the band draws a glyph and the word for each; `reason` follows "failed ·").
IN_EGERIA = "in_egeria"
NOT_YET = "not_yet"            # the resource is not in Egeria: the page's Publish creates it, then sends this
NOT_SENT = "not_sent"          # saved in RE, not in Egeria yet: the page's next Publish sends it
PENDING = "pending"            # queued or being sent
RETRYING = "retrying"          # a non-destructive link failed and the drain will try again
FAILED = "failed"              # dead: needs a person (retry)
UNLINKED = "unlinked"          # the link was removed in Egeria
NOT_PERMITTED = "not_permitted"  # the presser's Publish was refused this change (RE's check); the next Publish asks again
#: Brief F round 2: the note a refused change's (never sent) row is retired with; the plan reads the refusal from it.
REFUSAL_NOTE = "not permitted · "
#: Brief F: the words are the single Publish's ("saved · will publish", "published 10-11", "not sent · <reason>").
STATE_WORDS = {
    IN_EGERIA: "published",
    NOT_YET: "saved · will publish",
    NOT_SENT: "saved · will publish",
    PENDING: "sending",
    RETRYING: "sending · retrying",
    FAILED: "not sent",
    UNLINKED: "unlinked",
    NOT_PERMITTED: "not permitted",
}

#: The Egeria operations each action needs, as Egeria's access connector checks them
#: (OpenMetadataAccessSecurityConnector.java, egeria-v6/egeria): a tag link is feedback,
#: `validateUserForElementAddFeedback` :1696-1730 (ADD_FEEDBACK on the tagged element); a tag unlink is
#: `validateUserForElementDeleteFeedback` :1748-1770 (DELETE_FEEDBACK); a membership is an attach/detach of the
#: member (`validateUserForElementAttach` :1582-1620, ATTACH; DETACH for the removal). Every action is also an
#: UPDATE_PROPERTIES of the resource (Brief T: "UPDATE_PROPERTIES on the resource's element").
ADD_FEEDBACK_OPERATION = "ADD_FEEDBACK"
DELETE_FEEDBACK_OPERATION = "DELETE_FEEDBACK"
DETACH_OPERATION = "DETACH"
_OPERATIONS = {
    ("tag", "link"): ADD_FEEDBACK_OPERATION,
    ("tag", "unlink"): DELETE_FEEDBACK_OPERATION,
    ("group", "link"): "ATTACH",
    ("group", "unlink"): DETACH_OPERATION,
}

_UNSENT_CANCEL_NOTE = "superseded by a later change in RE before it was sent"


def tag_qualified_name(tag: str) -> str:
    """The qualifiedName RE creates a public InformalTag with: name-based and lower-case, so every RE user (and
    every spelling of the same word) converges on one. The displayName keeps the case it was typed in."""
    return f"InformalTag::{tag.lower()}"


def folio_qualified_name(group_slug: str) -> str:
    return f"Folio::RE::group::{group_slug}"


def tag_link_key(tag: str, element_guid: str) -> str:
    """The outbox identity key of one tag on one asset (a relationship has no qualifiedName of its own)."""
    return f"AttachedTag::{element_guid}::{tag}"


def group_link_key(group_slug: str, element_guid: str) -> str:
    return f"CollectionMembership::{folio_qualified_name(group_slug)}::{element_guid}"


def operations_for(kind: str, action: str) -> tuple[str, ...]:
    """What `curation_access` checks before RE records (or sends) one change."""
    return ("UPDATE_PROPERTIES", _OPERATIONS[(kind, action)])


# ── what RE wants ─────────────────────────────────────────────────────────────────────────────────────────

def _entity(registry, entity_type: str, slug: str):
    if entity_type == "repo":
        return registry.get(slug)
    if entity_type == "database":
        return registry.get_database(slug, allow_unreadable=True)
    if entity_type == "filesystem":
        return registry.get_filesystem(slug)
    return None


def current_group(registry, entity_type: str, slug: str) -> str:
    return str(getattr(_entity(registry, entity_type, slug), "group_slug", "") or "")


def element_guid(registry, entity_type: str, slug: str) -> str:
    """The resource's own asset GUID from RE's cache, "" when it is not in Egeria."""
    from resource_explorer.workflows.curate import element_guid_for

    return element_guid_for(registry, entity_type, slug, "")


# ── the plan: one function decides state and action ──────────────────────────────────────────────────────

@dataclass
class CurationItem:
    kind: str                 # "tag" | "group"
    name: str                 # the tag, or the group slug
    label: str                # what the band shows (the group's display name)
    key: str                  # the outbox qualified_name ("" when the resource is not in Egeria)
    desired: bool             # RE has this tag / this is the resource's group
    state: str
    reason: str = ""
    action: str = ""          # "" | "link" | "unlink": what a sync will queue
    cancel_ids: list = field(default_factory=list)   # unsent rows a sync retires first
    row_id: int | None = None
    retry: bool = False       # dead: a person may press retry (a NEW row)
    retry_action: str = ""    # "link" | "unlink"
    egeria_guid: str = ""     # the tag's / Folio's GUID, from the newest done link row
    published_at: str = ""    # when the newest live row landed (a done row's completed_at)

    @property
    def word(self) -> str:
        word = STATE_WORDS.get(self.state, self.state)
        if self.state in (IN_EGERIA, UNLINKED) and len(self.published_at) >= 10:
            return f"{word} {self.published_at[5:10]}"
        if self.state in (FAILED, NOT_PERMITTED) and self.reason:
            return f"{word} · {self.reason}"
        if self.state == PENDING and self.reason.startswith("waiting to re-check access"):
            return self.reason.split(":", 1)[0]
        return word

    def as_dict(self) -> dict:
        d = asdict(self)
        d["word"] = self.word
        return d


def _payload(row: dict) -> dict:
    try:
        p = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return p if isinstance(p, dict) else {}


def _evaluate(kind: str, desired: bool, rows: list[dict]) -> dict | None:
    """State, action and cancellations for one key from its rows (newest first, retired rows excluded).

    Returns None when there is nothing to show (never linked, and not wanted)."""
    link, detach = _LINK_OF[kind], _DETACH_OF[kind]
    if desired:
        # A removal that was never sent is superseded by the re-add.
        cancel = [r["id"] for r in rows if r["element_kind"] == detach and r["status"] == "pending"
                  and int(r.get("attempts") or 0) == 0]
    else:
        # A link that was never applied (pending, or failed and backing off) is retired rather than raced by an
        # unlink: a failed link retried after the unlink would leave the tag on in Egeria.
        cancel = [r["id"] for r in rows if r["element_kind"] == link and r["status"] in ("pending", "failed")]
    live = [r for r in rows if r["id"] not in cancel]
    newest = live[0] if live else None
    out = {"cancel_ids": cancel, "row_id": newest["id"] if newest else None, "action": "",
           "reason": "", "retry": False, "retry_action": "",
           "published_at": (newest.get("completed_at") or "") if newest and newest["status"] == "done" else ""}
    if desired:
        if newest is None or newest["element_kind"] == detach:
            return {**out, "state": NOT_SENT, "action": "link"}
        status = newest["status"]
        if cancel:
            return {**out, "state": NOT_SENT}
        if status == "done":
            return {**out, "state": IN_EGERIA}
        if status in ("pending", "running"):
            return {**out, "state": PENDING, "reason": _wait_reason(newest)}
        if status == "failed":
            return {**out, "state": RETRYING, "reason": newest.get("last_error") or ""}
        return {**out, "state": FAILED, "reason": newest.get("last_error") or "", "retry": True,
                "retry_action": "link"}
    if newest is None:
        return {**out, "state": NOT_SENT} if cancel else None
    if newest["element_kind"] == link:
        if newest["status"] in ("done", "running"):
            return {**out, "state": NOT_SENT, "action": "unlink"}
        return {**out, "state": NOT_SENT} if cancel else None         # a dead link never landed
    status = newest["status"]
    if cancel:
        return {**out, "state": NOT_SENT}
    if status == "done":
        return {**out, "state": UNLINKED}
    if status in ("pending", "running"):
        return {**out, "state": PENDING, "reason": _wait_reason(newest) or "unlink"}
    return {**out, "state": FAILED, "reason": newest.get("last_error") or "", "retry": True,
            "retry_action": "unlink"}


def _wait_reason(row: dict) -> str:
    """Round 4: a pending row deferred because access could not be re-checked says so (not "sending")."""
    from resource_explorer.egeria_outbox import ACCESS_WAIT_WORDS

    err = str(row.get("last_error") or "")
    return err if row.get("status") == "pending" and err.startswith(ACCESS_WAIT_WORDS) else ""


def _done_link_guid(rows: list[dict], link_kind: str) -> str:
    for r in rows:
        if r["element_kind"] == link_kind and r["status"] == "done" and r.get("egeria_guid"):
            return str(r["egeria_guid"])
    return ""


def curation_plan(registry, entity_type: str, slug: str, *, asset_guid: str | None = None) -> dict:
    """THE decision: per tag and per group, what it is in Egeria and what a sync would do.

    `asset_guid` overrides RE's cached GUID (a publish hook that has just created the asset). Read-only."""
    guid = (asset_guid if asset_guid is not None else element_guid(registry, entity_type, slug)) or ""
    tags = list(registry.list_resource_tags(entity_type, slug))
    group = current_group(registry, entity_type, slug)
    items: list[CurationItem] = []

    def label_of(group_slug: str) -> str:
        try:
            g = registry.get_group(group_slug)
        except Exception:
            g = None
        return (getattr(g, "display_name", "") or group_slug) if g else group_slug

    if not guid:
        items += [CurationItem("tag", t, t, "", True, NOT_YET) for t in tags]
        if group:
            items.append(CurationItem("group", group, label_of(group), "", True, NOT_YET))
        return {"entity_type": entity_type, "slug": slug, "element_guid": "", "in_egeria": False,
                "items": items}

    by_key: dict[tuple[str, str], list[dict]] = {}
    refused: dict[tuple[str, str], dict] = {}
    for r in registry.list_outbox_rows_for_kinds(entity_type, slug, KINDS):
        p = _payload(r)
        if (p.get("element_guid") or "") != guid:
            continue                      # history for an asset that is no longer this resource's
        if r["status"] in ("cancelled", "superseded"):
            # Brief F round 2: a change the presser's Publish was refused is a retired (never sent) row whose note
            # starts "not permitted · "; the newest such row per key is the refusal the band shows.
            err = str(r.get("last_error") or "")
            if r["status"] == "cancelled" and err.startswith("cancelled: " + REFUSAL_NOTE):
                k = ("tag" if r["element_kind"] in (TAG_LINK, TAG_DETACH) else "group",
                     p.get("tag") if r["element_kind"] in (TAG_LINK, TAG_DETACH) else p.get("group_slug"))
                if k[1] and k not in refused:
                    refused[k] = r
            continue
        kind = "tag" if r["element_kind"] in (TAG_LINK, TAG_DETACH) else "group"
        name = p.get("tag") if kind == "tag" else p.get("group_slug")
        if name:
            by_key.setdefault((kind, name), []).append(r)

    wanted = [("tag", t) for t in tags] + ([("group", group)] if group else [])
    seen = set(wanted)
    others = sorted(k for k in by_key if k not in seen)
    for kind, name in wanted + others:
        rows = by_key.get((kind, name), [])
        ev = _evaluate(kind, (kind, name) in seen, rows)
        if ev is None:
            continue
        key = tag_link_key(name, guid) if kind == "tag" else group_link_key(name, guid)
        no = refused.get((kind, name))
        if no and ev["action"] and int(no["id"]) > max([int(r["id"]) for r in rows] or [0]):
            # Refused at the last Publish and nothing sent since: said as "not permitted"; the action stays, so the
            # next Publish asks again (a permission may have changed).
            ev = {**ev, "state": NOT_PERMITTED,
                  "reason": str(no["last_error"])[len("cancelled: " + REFUSAL_NOTE):]}
        items.append(CurationItem(
            kind=kind, name=name, label=name if kind == "tag" else label_of(name), key=key,
            desired=(kind, name) in seen, egeria_guid=_done_link_guid(rows, _LINK_OF[kind]), **ev))
    return {"entity_type": entity_type, "slug": slug, "element_guid": guid, "in_egeria": True, "items": items}


def plan_as_dict(plan: dict) -> dict:
    return {**plan, "items": [i.as_dict() for i in plan["items"]],
            "to_send": sum(1 for i in plan["items"] if i.action or i.cancel_ids)}


# ── the write: queue what the plan names, then drain it as the caller ─────────────────────────────────────

def run_id_for(entity_type: str, slug: str) -> str:
    return f"Curation::{entity_type}::{slug}"


def requester_of(identity) -> str:
    """Who asked, for a write made on a publish's behalf: the signed-in person, or the person a Daemon job was
    queued for (`requested_by`); "" when neither, and then the row runs as the plain daemon and nothing is stamped.
    Round 2: never another decision's author (a tag unlink once took the last group change's author)."""
    if identity is None:
        return ""
    if getattr(identity, "kind", "") == "daemon":
        return (getattr(identity, "requested_by", "") or "").strip()
    return (getattr(identity, "user_id", "") or "").strip()


def _enqueue(registry, entity_type: str, slug: str, item: CurationItem, action: str, guid: str, by: str,
             display: dict, retired: "tuple[str, str] | None" = None) -> int:
    """Queue one row. `by` is THIS row's requester, recorded at queue time and never inferred later."""
    kind = item.kind
    element_kind = _LINK_OF[kind] if action == "link" else _DETACH_OF[kind]
    payload = {"entity_type": entity_type, "entity_slug": slug, "element_guid": guid, "by": by}
    if by and _caller_is_curator(by):
        # Round 5: the verified caller's Portal curator/admin role, so the drain re-check judges them as the press
        # did. From the session's ContextVar only (never a request body), and only for the row's own requester.
        payload.update({"portal_role": True})
    if kind == "tag":
        payload["tag"] = item.name
        payload["display_name"] = display.get("display_name") or item.name
        if action == "unlink" and item.egeria_guid:
            payload["tag_guid"] = item.egeria_guid
    else:
        payload.update(group_slug=item.name, folio_qualified_name=folio_qualified_name(item.name),
                       display_name=display.get("display_name") or item.name,
                       description=display.get("description") or "")
        if action == "unlink" and item.egeria_guid:
            payload["folio_guid"] = item.egeria_guid
    return registry.enqueue_outbox_element(entity_type, slug, element_kind, item.key, payload,
                                           run_id=run_id_for(entity_type, slug), retired=retired)


def _caller_is_curator(by: str) -> bool:
    from resource_explorer.workflows.curate import CURATOR_ROLES, _caller_identity

    who = _caller_identity()
    return bool(who is not None and who.auth_source != "anonymous" and (who.user_id or "") == by
                and (who.role or "").lower() in CURATOR_ROLES)


def _group_display(registry, group_slug: str) -> dict:
    try:
        g = registry.get_group(group_slug)
    except Exception:
        g = None
    return {"display_name": getattr(g, "display_name", "") or group_slug,
            "description": getattr(g, "description", "") or
            f"Resources grouped as {getattr(g, 'display_name', '') or group_slug} in Resource Explorer."}


def _caller_user() -> str:
    try:
        from resource_explorer.a2a_auth import caller

        c = caller()
    except Exception:
        return ""
    return (getattr(c, "user_id", "") or "") if c is not None else ""


def plan_operations(plan: dict) -> tuple[str, ...]:
    ops: list[str] = []
    for i in plan["items"]:
        if i.action:
            for op in operations_for(i.kind, i.action):
                if op not in ops:
                    ops.append(op)
    return tuple(ops)


def typed_spellings(registry, entity_type: str, slug: str) -> dict:
    """Brief F round 2: the newest spelling each tag was TYPED in, from the "curate_tag_added" activity entries the
    add route writes (RE keeps tags lower-case; no column holds the typed case). {tag: typed}."""
    out: dict = {}
    try:
        entries = registry.list_activity(entity_type=entity_type, entity_slug=slug, operation="curate_tag_added",
                                         limit=500)
    except Exception:  # noqa: BLE001 - the spelling is cosmetic; the key is used instead
        return out
    for e in entries:                                   # newest first
        for a in e.get("annotations") or []:
            tag, typed = (a or {}).get("tag"), (a or {}).get("typed")
            if tag and typed and tag not in out and typed.lower() == tag:
                out[tag] = typed
    return out


def sync_curation(registry, entity_type: str, slug: str, *, by: str = "", asset_guid: str | None = None,
                  check_access: bool = True, drain: bool = True, identity=None,
                  display_names: dict | None = None, per_item_access: bool = False) -> dict:
    """Queue what `curation_plan` says is not yet in Egeria (links and unlinks), retire unsent rows it supersedes,
    then drain this resource's curation rows inline. Returns the plan as re-read afterwards, with `refused` when
    curation access said no (nothing is queued then) and `drain` (the drain summary).

    Never raises for an Egeria failure: the rows carry it. `check_access` is the Brief Z decision for the
    operations the queued actions perform, all or nothing; `per_item_access` (Brief F round 2) checks the person
    asking per item instead and refuses only those items. The page's Publish passes `check_access=False,
    per_item_access=True`: other people's saved changes go out under the presser's name, so it is the presser who
    is checked, item by item; the inline drain runs as the presser, and the drain re-checks each row as its
    requester (`egeria_outbox._curation_refusal`).

    `display_names` maps a tag (RE's lower-case key) to the spelling the person typed, for the InformalTag's
    displayName. Round 2: a cancel that loses to a drain's claim means the plan was stale, so the plan is read
    again and decides afresh (a fresh link behind a landing unlink, or an unlink behind a landing link)."""
    by = by or _caller_user()
    display_names = {**typed_spellings(registry, entity_type, slug), **(display_names or {})}
    plan = curation_plan(registry, entity_type, slug, asset_guid=asset_guid)
    result: dict = {"refused": "", "queued": [], "cancelled": [], "drain": {}, "not_permitted": []}
    if not any(i.action or i.cancel_ids for i in plan["items"]):
        return {**plan_as_dict(plan), **result}
    checked: set[str] = set()

    def refused(p: dict) -> str:
        ops = tuple(op for op in plan_operations(p) if op not in checked)
        if not check_access or not ops:
            return ""
        from resource_explorer.workflows.curate import curation_access

        d = curation_access(registry, entity_type, slug, "", operations=tuple(checked) + ops)
        checked.update(ops)
        return "" if d.allowed else d.reason

    if (why := refused(plan)):
        result["refused"] = why
        return {**plan_as_dict(plan), **result}
    for _ in range(4):                       # each pass retires what the plan supersedes, then reads it again
        cancel_ids = [cid for i in plan["items"] for cid in i.cancel_ids]
        if not cancel_ids:
            break
        for cid in cancel_ids:
            if registry.cancel_outbox_row(cid, _UNSENT_CANCEL_NOTE):
                result["cancelled"].append(cid)
        plan = curation_plan(registry, entity_type, slug, asset_guid=asset_guid)
    if (why := refused(plan)):
        result["refused"] = why
        return {**plan_as_dict(plan), **result}
    guid = plan["element_guid"]
    decided: dict = {}

    def item_refusal(item) -> str:
        """Brief F round 2 (`per_item_access`): the PRESSER's own right to send this change, per item, with the
        operations Egeria checks for it; "" = allowed."""
        ops = operations_for(item.kind, item.action)
        if ops not in decided:
            from resource_explorer.workflows.curate import curation_access

            from resource_explorer.workflows.curate import BASIS_UNREADABLE

            d = curation_access(registry, entity_type, slug, "", operations=ops, element_guid=guid)
            # Round 4: access that cannot be READ is not a refusal: the row is queued normally and the drain
            # re-checks it as its requester (deferring it unsent while Egeria cannot answer).
            decided[ops] = "" if d.allowed or d.basis == BASIS_UNREADABLE else (d.reason or "refused")
        return decided[ops]

    for item in plan["items"]:
        if item.action and not item.cancel_ids:
            display = (_group_display(registry, item.name) if item.kind == "group"
                       else {"display_name": display_names.get(item.name, "")})
            # Round 3: the refusal is decided BEFORE any row exists. A refused change is recorded as a row born
            # cancelled with its words (no drain can ever claim it), so the band reads "not permitted" from a row,
            # nothing is sent (a destructive unlink included), and the next Publish asks again.
            why = item_refusal(item) if per_item_access else ""
            if why:
                _enqueue(registry, entity_type, slug, item, item.action, guid, by, display,
                         retired=("cancelled", "cancelled: " + REFUSAL_NOTE + why))
                result["not_permitted"].append({"kind": item.kind, "name": item.name, "reason": why})
                continue
            result["queued"].append(_enqueue(registry, entity_type, slug, item, item.action, guid, by, display))
    if drain and result["queued"]:
        result["drain"] = _drain(registry, entity_type, slug, len(result["queued"]), identity)
    after = curation_plan(registry, entity_type, slug, asset_guid=asset_guid)
    return {**plan_as_dict(after), **result}


def _drain(registry, entity_type: str, slug: str, n: int, identity) -> dict:
    from resource_explorer.egeria_outbox import drain_outbox

    try:
        return drain_outbox(registry, run_id=run_id_for(entity_type, slug), limit=max(n, 1) + 20,
                            identity=identity)
    except Exception as exc:          # drain_outbox never raises; belt and braces for the request path
        log.warning("Curation sync drain for %s %s did not run: %s", entity_type, slug, exc)
        return {"error": str(exc)}


class RetryRefused(ValueError):
    """The item named has nothing a person can retry (it is not dead)."""


def retry_item(registry, entity_type: str, slug: str, kind: str, name: str, *, by: str = "",
               check_access: bool = True, identity=None) -> dict:
    """A person's retry of one dead link or unlink: a NEW row (a destructive row is never revived), drained
    inline. Raises `RetryRefused` when the item is not dead, `CurationDenied` (workflows.curate) when access
    says no."""
    by = by or _caller_user()
    plan = curation_plan(registry, entity_type, slug)
    item = next((i for i in plan["items"] if i.kind == kind and i.name == name), None)
    if item is None or not item.retry:
        raise RetryRefused(f"{kind} {name!r} has no failed write to retry")
    if check_access:
        from resource_explorer.workflows.curate import CurationDenied, curation_access

        d = curation_access(registry, entity_type, slug, "", operations=operations_for(kind, item.retry_action))
        if not d.allowed:
            raise CurationDenied(d.reason)
    display = _group_display(registry, name) if kind == "group" else {"display_name": ""}
    row_id = _enqueue(registry, entity_type, slug, item, item.retry_action, plan["element_guid"], by, display)
    summary = _drain(registry, entity_type, slug, 1, identity)
    return {**plan_as_dict(curation_plan(registry, entity_type, slug)), "queued": [row_id], "drain": summary,
            "refused": "", "cancelled": []}


def group_change_refusal(registry, entity_type: str, slug: str, new_group: str) -> str:
    """Brief Z's answer for moving one resource from its current group to `new_group` ("" = ungrouped): "" when
    allowed (or nothing changes), else the reason. UPDATE_PROPERTIES, plus ATTACH to join a Folio and DETACH to
    leave one. The web route, the CLI and the batch import all ask this one function."""
    from resource_explorer.workflows.curate import curation_access

    new_group = registry._normalize_slug(new_group) if new_group else ""
    before = current_group(registry, entity_type, slug)
    ops: list[str] = []
    if new_group and new_group != before:
        ops += list(operations_for("group", "link"))
    if before and before != new_group:
        ops += [op for op in operations_for("group", "unlink") if op not in ops]
    if not ops:
        return ""
    d = curation_access(registry, entity_type, slug, "", operations=tuple(ops))
    return "" if d.allowed else d.reason


def group_members(registry, group_slug: str) -> list[tuple[str, str]]:
    return ([("repo", p.slug) for p in registry.list_projects_in_group(group_slug)]
            + [("database", d.slug) for d in registry.list_databases_in_group(group_slug)]
            + [("filesystem", f.slug) for f in registry.list_filesystems_in_group(group_slug)])


def group_delete_refusals(registry, group_slug: str, members: "list[tuple[str, str]] | None" = None) -> list[dict]:
    """A group delete removes every member from the Folio: DETACH (with UPDATE_PROPERTIES) on each member,
    checked BEFORE anything changes. `members` is the list the caller read once and will delete with (round 3);
    None reads it here. Returns the refused members with their reasons ([] = allowed)."""
    out = []
    for entity_type, member in (members if members is not None else group_members(registry, group_slug)):
        why = group_change_refusal(registry, entity_type, member, "")
        if why:
            out.append({"entity_type": entity_type, "slug": member, "reason": why})
    return out


def refusal_words(refused: list[dict]) -> str:
    return "not permitted · " + "; ".join(f"{r['entity_type']} {r['slug']}: {r['reason']}" for r in refused)


def group_deleted_report(group_slug: str) -> dict:
    """What a group delete says about Egeria: the Folio stays, and the owner deletes it by hand."""
    return {"folio_qualified_name": folio_qualified_name(group_slug), "left_in_egeria": True,
            "words": ("The Folio stays in Egeria: RE never deletes it. Each member's membership is removed at that "
                      "member's next Publish; the owner deletes the Folio by hand.")}


# ── the Egeria side: creators the outbox drain calls (egeria_outbox._CREATORS) ─────────────────────────────

def _elements(result) -> list[dict]:
    """pyegeria's JSON find/get answers a list of elements, or a string ("No elements found") for none."""
    return [e for e in result if isinstance(e, dict)] if isinstance(result, list) else []


def _guid(el: dict) -> str:
    return str((el.get("elementHeader") or {}).get("guid") or el.get("guid") or "")


def _props(el: dict) -> dict:
    p = el.get("properties")
    return p if isinstance(p, dict) else {}


def find_informal_tag(fb, tag: str) -> str:
    """The GUID of the InformalTag whose displayName is `tag` IGNORING CASE, or "" (round 2).

    `find_tags` with starts_with and ends_with set is an exact-string search, and `ignore_case` widens it to any
    case; the answer is then compared with `lower()` here, so a looser server match cannot pick another word.
    When several match, RE's own qualifiedName wins, then the lowest GUID, so every caller picks the same one."""
    hits = [e for e in _elements(fb.find_tags(search_string=tag, starts_with=True, ends_with=True, ignore_case=True))
            if str(_props(e).get("displayName") or _props(e).get("name") or "").strip().lower() == tag.lower()
            and _guid(e)]
    if not hits:
        return ""
    ours = [e for e in hits if _props(e).get("qualifiedName") == tag_qualified_name(tag)]
    return _guid(sorted(ours or hits, key=_guid)[0])


def find_informal_tag_by_qualified_name(fb, qualified_name: str) -> str:
    """The InformalTag holding this exact qualifiedName, whatever its displayName, or ""."""
    hits = [e for e in _elements(fb.find_tags(search_string=qualified_name, starts_with=True, ends_with=True,
                                              ignore_case=False))
            if _props(e).get("qualifiedName") == qualified_name and _guid(e)]
    return _guid(sorted(hits, key=_guid)[0]) if hits else ""


def ensure_informal_tag(fb, tag: str, display_name: str = "") -> str:
    """Find the public tag by name (any case); create it only when absent, with the displayName as typed; never
    a second one. A qualifiedName already taken under another displayName is adopted, not a dead row."""
    from resource_explorer.egeria_outbox import OutboxApplyError, _guid_of, _is_duplicate_qualified_name

    found = find_informal_tag(fb, tag)
    if found:
        return found
    shown = display_name or tag
    try:
        guid = _guid_of(fb.create_informal_tag(display_name=shown, qualified_name=tag_qualified_name(tag),
                                               description=f"Tag “{shown}” from Resource Explorer."))
    except Exception as exc:
        if not _is_duplicate_qualified_name(exc):
            raise
        guid = find_informal_tag_by_qualified_name(fb, tag_qualified_name(tag)) or find_informal_tag(fb, tag)
    if not guid:
        raise OutboxApplyError(f"InformalTag {tag!r} was neither found nor created")
    return guid


def _wait_for_earlier_opposite(clients, payload: dict, opposite_kind: str) -> None:
    """Round 2: a link and an unlink of the same tag (or membership) on the same asset apply in the order they
    were queued. While an EARLIER row of the opposite kind for the same key is still pending, being sent or
    backing off, this row is not attempted: `OutboxNotReadyError` hands it back to pending with no attempt burned
    (so a destructive unlink is deferred, never sent and never re-sent). An earlier row that is done, dead or
    retired does not hold it. So "unlinked" can never be shown while the tag is still on the asset."""
    from resource_explorer.egeria_outbox import OutboxNotReadyError

    registry, row_id, key = getattr(clients, "registry", None), payload.get("_outbox_id"), payload.get("_key")
    if registry is None or row_id is None or not key:
        return
    for r in registry.list_outbox_rows_for_kinds(payload.get("entity_type", ""), payload.get("entity_slug", ""),
                                                 (opposite_kind,)):
        if r["qualified_name"] == key and int(r["id"]) < int(row_id) and r["status"] in ("pending", "running", "failed"):
            raise OutboxNotReadyError(f"waiting for earlier {opposite_kind} row {r['id']} ({r['status']}) on {key}")


def find_group_folio(cm, qualified_name: str) -> str:
    """The Folio with this qualifiedName (`get_collections_by_name` matches qualifiedName among its name
    properties, CollectionHandler.java:283), or ""."""
    hits = [e for e in _elements(cm.get_collections_by_name(qualified_name, metadata_element_type_name="Folio"))
            if _props(e).get("qualifiedName") == qualified_name and _guid(e)]
    return _guid(sorted(hits, key=_guid)[0]) if hits else ""


def ensure_group_folio(cm, payload: dict) -> str:
    from resource_explorer.egeria_outbox import OutboxApplyError, _guid_of, _is_duplicate_qualified_name
    from resource_explorer.surveyors.egeria_investigation_publisher import _create_typed_collection

    qn = payload.get("folio_qualified_name") or folio_qualified_name(payload["group_slug"])
    found = find_group_folio(cm, qn)
    if found:
        return found
    try:
        guid = _guid_of(_create_typed_collection(cm, "Folio", payload.get("display_name") or payload["group_slug"],
                                                 payload.get("description") or "", qualified_name=qn))
    except Exception as exc:
        if not _is_duplicate_qualified_name(exc):
            raise
        guid = find_group_folio(cm, qn)
    if not guid:
        raise OutboxApplyError(f"Folio {qn!r} was neither found nor created")
    return guid


def create_tag_link(clients, payload: dict) -> str:
    """Link the public tag to the asset; returns the tag's GUID (recorded on the row for a later unlink).

    AttachedTag is declared without `setMultiLink(true)` (OpenMetadataTypesArchive1_2.java:3908-3946), so it is a
    uni-link relationship and a repeated add converges on one link (read from source; not measured live)."""
    _wait_for_earlier_opposite(clients, payload, TAG_DETACH)
    fb = clients.require("feedback")
    tag_guid = ensure_informal_tag(fb, payload["tag"], payload.get("display_name") or "")
    fb.add_tag_to_element(payload["element_guid"], tag_guid, is_public=True)
    return tag_guid


def create_tag_detach(clients, payload: dict) -> str:
    """Remove the tag's link from the asset. NEVER deletes the InformalTag itself (ISSUE-117: the owner deletes
    elements by hand). `removeTagFromElement` removes EVERY AttachedTag relationship between the two ends
    (InformalTagHandler.java:334-358), including one somebody added directly in Egeria. No tag of that name
    means no link to remove."""
    _wait_for_earlier_opposite(clients, payload, TAG_LINK)
    fb = clients.require("feedback")
    tag_guid = payload.get("tag_guid") or find_informal_tag(fb, payload["tag"])
    if not tag_guid:
        return ""
    fb.remove_tag_from_element(payload["element_guid"], tag_guid)
    return tag_guid


def create_group_membership(clients, payload: dict) -> str:
    """Make the asset a member of the group's Folio (found by qualifiedName or created); returns the Folio GUID.
    CollectionMembership is uni-link (measured 2026-08-25), so a repeat converges on one member."""
    _wait_for_earlier_opposite(clients, payload, GROUP_DETACH)
    cm = clients.require("collection_manager")
    folio_guid = ensure_group_folio(cm, payload)
    cm.add_to_collection(folio_guid, payload["element_guid"])
    return folio_guid


def create_group_membership_detach(clients, payload: dict) -> str:
    """Remove the asset from the group's Folio. NEVER deletes the Folio (ISSUE-117: by hand)."""
    _wait_for_earlier_opposite(clients, payload, GROUP_LINK)
    cm = clients.require("collection_manager")
    folio_guid = payload.get("folio_guid") or find_group_folio(
        cm, payload.get("folio_qualified_name") or folio_qualified_name(payload["group_slug"]))
    if not folio_guid:
        return ""
    cm.remove_from_collection(folio_guid, payload["element_guid"])
    return folio_guid

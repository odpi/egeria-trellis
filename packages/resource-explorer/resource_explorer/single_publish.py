"""One Publish per page (Brief F, owner 2026-10-11): what the press sends besides the resource's own report.

The project owner, 2026-10-11, verbatim: "I think there should be one publish button, but each section can have a
save or indication that it will be published when the user presses publish - I thought we were going with a single
publish per page."

The press, in order, as the signed-in person (Caller):

1. the resource's own publish (a repository's survey report, a database's catalog, a file system's catalog);
2. its tags and its group's Folio membership (`curation_egeria.sync_curation`, Brief T's kinds and rules);
3. its documentation sources not yet in Egeria, as ExternalReferences (§10: the same call the database and file
   system publishes already make, `publish_local_doc_sources`);
4. the PRESSER's own pending rating and notes (`feedback_egeria.publish_my_feedback`): never anyone else's.

**One source (§5b rule 1).** `goes_out` is what the Publish band lists before the press. Each part of it comes from
the SAME function the write acts on: `curation_plan` (sync_curation enqueues exactly its actions),
`feedback_egeria.feedback_plan` / `to_send` (publish_my_feedback sends exactly those), and
`unpublished_doc_sources` (publish_local_doc_sources publishes exactly those). Nothing here re-derives a list.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def unpublished_doc_sources(registry, entity_type: str, slug: str) -> list[dict]:
    """The documentation sources declared in RE that have no ExternalReference in Egeria yet: what a Publish sends
    (and the ONLY list `publish_local_doc_sources` publishes)."""
    return [row for row in registry.list_doc_sources(entity_type, slug) if not row.get("egeria_external_ref_guid")]


def goes_out(registry, entity_type: str, slug: str, viewer: str) -> dict:
    """What `viewer`'s next Publish sends besides the resource's own report, each with its state. Read-only."""
    from resource_explorer import curation_egeria as ce
    from resource_explorer import feedback_egeria as fe

    curation = ce.curation_plan(registry, entity_type, slug)
    findable = [{"kind": i.kind, "name": i.name, "label": i.label, "state": i.state, "word": i.word,
                 "action": i.action or ("link" if i.state == ce.NOT_YET else "")}
                for i in curation["items"]
                if i.action or i.cancel_ids or i.state == ce.NOT_YET]
    fplan = fe.feedback_plan(registry, entity_type, slug, viewer=viewer)
    mine = [{"kind": i.kind, "id": i.id, "state": i.state, "word": i.word} for i in fe.to_send(fplan)]
    docs = [{"id": d.get("id"), "label": d.get("label") or d.get("url") or "", "url": d.get("url") or "",
             "word": "saved · will publish"} for d in unpublished_doc_sources(registry, entity_type, slug)]
    waiting = sum(1 for i in fplan["ratings"] + fplan["notes"] if i.state == fe.WAITING)
    return {"viewer": viewer, "in_egeria": bool(curation["element_guid"]),
            "findable": findable, "feedback": mine, "doc_sources": docs,
            "waiting_for_others": waiting,
            "count": len(findable) + len(mine) + len(docs)}


def send_with_publish(registry, entity_type: str, slug: str, asset_guid: str, presser: str, *,
                      doc_sources: bool = True) -> dict:
    """Everything after the resource's own publish, as the presser (the request's Caller). Best-effort per part:
    a part that fails says so in its own key and never fails the press; each item's state is read from its rows."""
    out: dict = {}
    if not asset_guid:
        out["skipped"] = "the resource is not in Egeria, so nothing else was sent"
        return out
    from resource_explorer import curation_egeria as ce
    from resource_explorer import feedback_egeria as fe

    try:
        # Round 2: the PRESSER sends other people's saved tags and group under their own name, so the presser is
        # checked per item (`per_item_access`, the operations Egeria checks for each change); a refusal is that
        # item's "not permitted" (from its retired row), never the whole press. The inline drain runs as the
        # presser, so Egeria checks again itself.
        res = ce.sync_curation(registry, entity_type, slug, asset_guid=asset_guid, check_access=False,
                               per_item_access=True, by=presser)
        queued = set(res.get("queued") or [])
        failed = [{"kind": i["kind"], "name": i["name"], "word": i["word"]} for i in res.get("items") or []
                  if i.get("row_id") in queued and i.get("state") in (ce.FAILED, ce.RETRYING)]
        failed += [{"kind": n["kind"], "name": n["name"], "word": f"not permitted · {n['reason']}"}
                   for n in res.get("not_permitted") or []]
        out["findable"] = {"queued": len(queued), "to_send": res.get("to_send", 0), "failed": failed}
    except Exception as exc:  # noqa: BLE001
        log.warning("Publish: tags/group for %s %s failed: %s", entity_type, slug, exc)
        out["findable"] = {"error": f"{type(exc).__name__}: {exc}"}
    # `doc_sources=False` when the resource's own publish already sent them this press (a repository's
    # EgeriaPublisher does): a source that failed there is not sent a second time in the same press (round 3).
    if doc_sources and unpublished_doc_sources(registry, entity_type, slug):
        try:
            from resource_explorer.web.routes.doc_sources import publish_local_doc_sources

            res = publish_local_doc_sources(entity_type, slug, asset_guid, registry=registry)
            out["doc_sources"] = {"published": sum(1 for r in res if r.get("ok") and not r.get("skipped")),
                                  "failed": [r for r in res if not r.get("ok")]}
        except Exception as exc:  # noqa: BLE001
            log.warning("Publish: documentation sources for %s %s failed: %s", entity_type, slug, exc)
            out["doc_sources"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        res = fe.publish_my_feedback(registry, entity_type, slug, asset_guid, presser)
        out["feedback"] = {"queued": len(res.get("queued") or []), "refused": res.get("refused", ""),
                           "not_permitted": res.get("not_permitted") or [], "unread": res.get("unread") or [],
                           "to_send": res.get("to_send", 0)}
    except Exception as exc:  # noqa: BLE001
        log.warning("Publish: feedback for %s %s failed: %s", entity_type, slug, exc)
        out["feedback"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out

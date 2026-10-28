"""Egeria linkage for declared documentation sources — the `egeria_linkage`
reuse the brief asks for (`BRIEF-DATABASE-DOCUMENTATION-SOURCES.md`, "Reuse,
not rebuild"; slice 1, "Declare and probe").

A declared source becomes an `ExternalReference` element attached to the
resource's Egeria asset when the resource is published — mirrors
`surveyors/egeria_publisher.py`'s `_publish_homepage_reference`, which does
exactly this for a project's homepage URL, down to the `ExternalReference::
<url>` qualified-name convention (so the two features never double-catalog
the same URL under two different names). That function lives on
`EgeriaPublisher` and is reached only through a repo survey publish; this
module is the standalone equivalent for a database/filesystem's
person-declared sources, called directly from `web/routes/doc_sources.py`
(on add + at publish time) rather than folded into a surveyor.

**Best-effort, deliberately.** A doc source failing to publish to Egeria
must not fail the resource's own publish, and removing one locally must not
fail because Egeria is unreachable — same rule `_publish_homepage_reference`
follows, for the same reason: the primary operation is the point, this is
an extra. Callers get a result dict with `ok`/`error` rather than an
exception for the common failure paths; only a bad GUID (nothing to detach)
is a programming error worth raising for.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_SOURCE_TYPE_LABEL = {
    "data_dictionary": "data dictionary",
    "design_notes": "design notes",
    "runbook": "runbook",
    "wiki": "wiki",
    "installation_guide": "installation guide",
    "user_manual": "user manual",
    "api_reference": "api reference",
    "release_notes": "release notes",
    "other": "documentation",
}


class RepoEgeriaConnectionView:
    """The Egeria connection a repo publishes doc sources through.

    E2 (2026-09-29). `Project` (a repo) carries no per-entity Egeria credentials the way
    `DatabaseEntity`/`FileSystemEntity` do. This is a read-only VIEW exposing the attribute
    names every doc-source call site reads, so those sites need no repo special case.

    **Carries no credential** (Brief I, 2026-10-09). It used to read `EGERIA_USER` /
    `EGERIA_USER_PASSWORD` from the environment (defaulting to a literal password); who acts is
    now the factory's business (`egeria_clients`): the signed-in Caller on a route, the outbox
    drain's daemon identity otherwise. `egeria_url`/`egeria_server` are empty, so the factory
    addresses the configured Egeria.
    """

    __slots__ = ("slug", "display_name", "egeria_asset_guid", "egeria_server", "egeria_url")

    def __init__(self, project, asset_guid: str = ""):
        self.slug = project.slug
        self.display_name = project.display_name
        self.egeria_asset_guid = asset_guid or getattr(project, "egeria_asset_guid", "") or ""
        self.egeria_server = ""
        self.egeria_url = ""

    def __repr__(self) -> str:
        return (f"RepoEgeriaConnectionView(slug={self.slug!r}, "
                f"asset_guid={self.egeria_asset_guid!r})")

    __str__ = __repr__


def resolve_entity_for_doc_source(registry, entity_type: str, entity_slug: str):
    """The three entity kinds this feature supports (database, filesystem, repo), resolved the same way
    everywhere it's needed — `web/routes/doc_sources.py`'s `_resolve_entity`
    (which additionally turns a miss into an HTTP 404) and
    `egeria_outbox.py`'s `doc_source_publish`/`doc_source_unpublish`
    creators, which resolve the entity fresh at APPLY time rather than
    carrying its Egeria credentials in the outbox payload — a payload
    snapshot would go stale if the entity's credentials were edited between
    enqueue and a later retry, and this codebase already stores these
    credentials in the entity row itself (`registry.py`'s `DatabaseEntity`/
    `FileSystemEntity`), so re-reading them is one lookup, not a new store.
    Returns `None` for an entity_type this feature doesn't support or a slug
    that no longer resolves."""
    if entity_type == "database":
        return registry.get_database(entity_slug)
    if entity_type == "filesystem":
        return registry.get_filesystem(entity_slug)
    if entity_type == "repo":
        project = registry.get(entity_slug)
        if project is None:
            return None
        return RepoEgeriaConnectionView(project, registry.get_egeria_asset_guid(entity_slug) or "")
    return None


def entity_clients(entity, identity):
    """`EgeriaClients` for `identity`, addressed at the Egeria the entity names (its stored
    `egeria_url`/`egeria_server`, else the configured one). Who acts is `identity`'s business:
    a route passes `Caller()`, the outbox drain `current_principal()` (Caller inline, else the
    daemon). An entity's stored Egeria user/password are never used (owner, 2026-10-09)."""
    from resource_explorer.egeria_clients import egeria_client

    return egeria_client(identity, purpose="doc sources",
                         view_server=getattr(entity, "egeria_server", "") or None,
                         platform_url=getattr(entity, "egeria_url", "") or None)


class DocSourceLookupError(RuntimeError):
    """A look-up before a create could not be made: neither found nor a real miss. Nothing is created on it;
    `publish_doc_source` reports it as its ordinary `ok: False` failure."""


def _client(clients):
    from pyegeria import ExternalReferences

    return clients.of(ExternalReferences)


def _expert(clients):
    from pyegeria.omvs.metadata_expert import MetadataExpert

    return clients.of(MetadataExpert)


def _link_once(clients, client, asset_guid: str, ref_guid: str):
    """Link `asset_guid` to `ref_guid` unless that ExternalReferenceLink already exists
    (`egeria_extref_link`). Never raises; the outcome carries the relationship GUID or the words."""
    from resource_explorer.egeria_extref_link import COULD_NOT_CHECK, LinkOutcome, link_external_reference_once

    try:
        expert = _expert(clients)
    except Exception as exc:  # noqa: BLE001 - no reader means no check, so no link
        words = f"could not check for an existing ExternalReferenceLink ({type(exc).__name__}: {exc}); no link made"
        log.warning("doc source: %s", words)
        return LinkOutcome(COULD_NOT_CHECK, error=words[:400])
    return link_external_reference_once(expert, client, asset_guid, ref_guid)


def _qualified_name(url: str) -> str:
    # Same convention `_publish_homepage_reference` uses for the identical
    # element type, so a homepage and a declared doc source pointing at the
    # same URL resolve to the one Egeria element rather than two.
    return f"ExternalReference::{url}"


def ref_guid_exists(ref_guid: str, *, clients) -> bool:
    """True when `ref_guid` still resolves to a real element in Egeria.

    Round 5 fix (2026-09-29): a stuck-row bug found live (b9925119…, database
    8813) traced to `publish_doc_source` trusting a stored `egeria_external_
    ref_guid` just because the row carried one — including one that had been
    genuinely DELETED from Egeria by an unrelated unpublish before the row's
    own self-heal re-queued it. The self-heal's publish attempt reused the
    dead guid, the link call against a nonexistent element failed silently
    (caught by the best-effort `try/except` around `link_external_reference`),
    and the outbox row still completed `done` — a permanent, silent no-op
    loop (see `_compute_egeria_state`'s self-heal, which re-queues the exact
    same shape every time it renders).

    This is the fix: before trusting ANY ref guid (whether `known_ref_guid`
    or one `_find_ref_guid` just looked up) as reusable, actually ask Egeria
    whether it still exists. Reuses `egeria_linkage.is_unknown_guid_error` —
    the same "does this cached GUID still resolve" detection
    `recheck_all_linkages` already uses for every other cached-GUID
    staleness check in this codebase — rather than inventing a second
    existence-check heuristic. `MetadataExpert.get_metadata_element_by_guid`
    is the same type-agnostic client `recheck_all_linkages` settled on (an
    `ExternalReference` is not an Asset, so `AssetMaker` would be the wrong
    client here too).

    On an UNRELATED failure (timeout, auth refusal, transient network error,
    or no answer at all — not a confirmed "not known to the repository"
    answer), this RAISES `DocSourceLookupError`. It used to return False,
    treating "could not establish the guid is good" as "gone", and every
    caller then created a fresh ExternalReference: a duplicate whenever the
    old one was fine and Egeria merely did not answer (AUDIT-PYEGERIA-6.2.2.md,
    "Not in scope"). `publish_doc_source` turns the raise into its ordinary
    `{"ok": False, "error": ...}`, which the outbox retries.
    """
    if not ref_guid:
        return False
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer.egeria_linkage import is_unknown_guid_error

    from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

    try:
        element = clients.of(MetadataExpert).get_metadata_element_by_guid(ref_guid)
    except Exception as exc:
        if is_unknown_guid_error(exc) or lookup_exception_is_absent(exc):
            log.info("doc source: ref %r no longer resolves in Egeria (confirmed not found)",
                      ref_guid)
            return False
        raise DocSourceLookupError(lookup_could_not_be_made(ref_guid, exc)) from exc
    from resource_explorer.egeria_absence import ABSENT, is_absent

    if element is None:
        raise DocSourceLookupError(lookup_could_not_be_made(ref_guid, None))
    # MetadataExpert's own "not found" sentinel is a bare string rather than an exception for some call
    # shapes. Only that exact string is absence; {} or any other odd shape is unreadable, never "gone".
    if isinstance(element, str) and is_absent(element) == ABSENT:
        log.info("doc source: ref %r no longer resolves in Egeria (Egeria's own no-element answer)", ref_guid)
        return False
    if not isinstance(element, dict) or not element or not _guid_in(element):
        raise DocSourceLookupError(lookup_could_not_be_made(
            ref_guid, ValueError(f"unexpected answer {element!r:.80}")))
    return True


def _guid_in(element: dict) -> str:
    """LIVE: get_metadata_element_by_guid answers an OpenMetadataElement with a top-level `elementGUID`
    (`elementHeader.guid` is the asset-graph shape) -- see catalogue_gateway's reader of the same."""
    return str((element.get("elementHeader") or {}).get("guid") or element.get("elementGUID")
               or element.get("guid") or "")


def _find_ref_guid(clients, qualified_name: str) -> str:
    """Existing ExternalReference by qualified name, or '' — mirrors
    `EgeriaPublisher._find_element_guid`, duplicated rather than imported
    because that one lives on a class built around a repo survey's own
    clients and identity, not something this standalone module should
    depend on just to reuse eleven lines.

    **Bounded via `concurrency.run_sync`, deliberately** — live-verified
    2026-09-28 (this slice's own gate check) that `AutomatedCuration.
    get_guid_for_name` can hang this exact way: `docs/process-model.md`
    §1.3's incident is `resolve_question_guid()` hung 15+s inside this
    same pyegeria call -> nest_asyncio -> run_until_complete frame, fixed
    everywhere else in this package by routing through the shared bounded
    pool rather than calling pyegeria synchronously from the caller's own
    thread. This function had the identical unguarded call and reproduced
    the identical hang (a doc-source publish holding a worker with no CPU
    activity, well past the 4s the probe budget alone would explain). Not
    a pyegeria fix — `feedback_pyegeria_gaps_tracking` policy is to log
    upstream gaps and wait for approval, not patch pyegeria directly — this
    is RE's own established containment for RE's own call site."""
    import re as _re

    from pyegeria import AutomatedCuration

    from resource_explorer.concurrency import run_sync

    uuid_re = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", _re.IGNORECASE
    )

    def _lookup():
        return clients.of(AutomatedCuration).get_guid_for_name(qualified_name)

    from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

    try:
        result = run_sync(_lookup, timeout=15.0)
    except Exception as exc:
        # Only a real miss may lead to a create; a look-up that could not be made raises (it used to
        # return '' and the caller created a second ExternalReference).
        if lookup_exception_is_absent(exc):
            return ""
        raise DocSourceLookupError(lookup_could_not_be_made(qualified_name, exc)) from exc
    if result is None:
        raise DocSourceLookupError(lookup_could_not_be_made(qualified_name, None))
    if isinstance(result, list) and result:
        candidate = result[0] if isinstance(result[0], str) else result[0].get("guid", "")
        return candidate if uuid_re.match(candidate or "") else ""
    if isinstance(result, str) and uuid_re.match(result):
        return result
    return ""


def publish_doc_source(source: dict, asset_guid: str, *, clients, display_name: str = "",
                        is_ref_unpublishing=None, known_ref_guid: str = "") -> dict:
    """Create (or reuse) an `ExternalReference` for one declared source and
    link it to `asset_guid`. Returns `{"ok": bool, "ref_guid": str,
    "link_guid": str, "error": str}` — never raises for an ordinary Egeria
    failure, so a batch of several sources can publish the ones that
    succeed and report the ones that didn't rather than aborting the whole
    publish (see module docstring).

    `is_ref_unpublishing` (optional `Callable[[str], bool]`) — the adoption-
    race guard (2026-09-29): `_find_ref_guid` matches by `qualified_name`
    alone (`ExternalReference::<url>`), which is GLOBAL — it does not check
    whether the match it found is currently being detached/deleted by a
    concurrent `doc_source_unpublish`. Passed by `egeria_outbox.py`'s
    `_create_doc_source_publish` as `registry.has_pending_unpublish_for_ref`
    bound to this entity, so this module stays standalone (no registry
    import — see module docstring) while still refusing to reuse a
    reference someone else is mid-way through deleting. A caller that
    doesn't pass one (e.g. `publish_local_doc_sources`'s full-publish sweep,
    which is not exposed to this specific race the same way) gets the old
    always-reuse behavior.

    `known_ref_guid` (round 4, 2026-09-29) — when the caller's own local row
    already carries a `egeria_external_ref_guid` (typically: adopted by a
    read-back, or a prior attempt that created the reference but crashed
    before linking it), pass it here to skip the `_find_ref_guid` lookup and
    go straight to linking THAT reference — the self-heal path
    (`_compute_egeria_state`) re-queues exactly this shape (ref present,
    link missing) and must NOT mint a second, independent `ExternalReference`
    for the same row when the first one is still perfectly good. Still
    subject to the same `is_ref_unpublishing` guard: a known ref_guid that
    turns out to have a pending/running unpublish is abandoned (falls
    through to the normal find-or-create flow below) rather than linked to a
    reference that may be deleted out from under it."""
    url = (source.get("url") or "").strip()
    if not url:
        return {"ok": False, "ref_guid": "", "link_guid": "", "error": "source has no URL"}
    qualified_name = _qualified_name(url)
    label = source.get("label") or urlparse(url).netloc or url
    source_type = source.get("source_type") or "other"
    type_label = _SOURCE_TYPE_LABEL.get(source_type, "documentation")
    try:
        client = _client(clients)
        ref_guid = (known_ref_guid or "").strip()
        if ref_guid and is_ref_unpublishing is not None and is_ref_unpublishing(ref_guid):
            log.info("doc source: known ref %r for %s has a pending/running unpublish — "
                      "abandoning it, looking up/creating fresh", ref_guid, qualified_name)
            ref_guid = ""
        if ref_guid and not ref_guid_exists(ref_guid, clients=clients):
            # Round 5 fix (2026-09-29): the row's own stored ref guid may
            # have been genuinely deleted from Egeria by something else
            # entirely (the b9925119… incident — an unrelated unpublish beat
            # this row's self-heal to it). A dead guid must not be reused —
            # see ref_guid_exists's own docstring.
            log.info("doc source: known ref %r for %s no longer exists in Egeria — "
                      "clearing it, looking up/creating fresh", ref_guid, qualified_name)
            ref_guid = ""
        if not ref_guid:
            ref_guid = _find_ref_guid(clients, qualified_name)
        if ref_guid and is_ref_unpublishing is not None and is_ref_unpublishing(ref_guid):
            # This reference exists but is concurrently being torn down by a
            # pending/running unpublish — not safe to adopt (it may vanish
            # out from under the link we're about to create, or the unpublish
            # may run AFTER our link and delete the reference we just linked
            # to, leaving this source pointed at nothing). Treat it as
            # not-found: a fresh, independent ExternalReference is created
            # below instead of racing the one being deleted.
            log.info("doc source: found %r (%s) but it has a pending/running unpublish — "
                      "creating a new reference instead of adopting it", qualified_name, ref_guid)
            ref_guid = ""
        if ref_guid and not ref_guid_exists(ref_guid, clients=clients):
            # Same guard as above, applied to whatever _find_ref_guid just
            # found by qualifiedName — that lookup can also return a guid
            # that no longer resolves (e.g. deleted between the lookup index
            # and this read).
            log.info("doc source: found %r (%s) no longer exists in Egeria — "
                      "creating a new reference instead", qualified_name, ref_guid)
            ref_guid = ""
        if not ref_guid:
            body = {
                "class": "NewElementRequestBody",
                "properties": {
                    "class": "ExternalReferenceProperties",
                    "typeName": "ExternalReference",
                    "qualifiedName": qualified_name,
                    "displayName": label,
                    "description": (
                        f"{type_label.capitalize()} for "
                        f"{display_name or source.get('entity_slug', '')}, declared in "
                        "Resource Explorer."
                    ),
                    "url": url,
                    "referenceTitle": label,
                },
            }
            ref_guid = client.create_external_reference(body=body)
            log.info("Created ExternalReference %s for doc source %s", ref_guid, url)
        # ExternalReferenceLink is multi-link: a retry (or re-publish) that links again makes a
        # second relationship. Link only when none exists between this asset and this reference;
        # an existing link's GUID comes back as this publish's proof, and a check that cannot be
        # made links nothing. A link step that did not complete is recorded in `error` (distinct
        # from failing `ok`): the reference itself was created/reused, but a caller reading only
        # `ok` must still see the link did not happen (the outbox then refuses to mark done).
        outcome = _link_once(clients, client, asset_guid, ref_guid)
        return {"ok": True, "ref_guid": ref_guid, "link_guid": outcome.relationship_guid,
                "error": outcome.error}
    except Exception as exc:
        log.warning("doc source: could not publish ExternalReference for %s: %s", url, exc)
        return {"ok": False, "ref_guid": "", "link_guid": "", "error": str(exc)[:500]}


def unpublish_doc_source(ref_guid: str, asset_guid: str, *, clients, delete: bool = True) -> dict:
    """Detach (and, by default, delete) the `ExternalReference` a removed
    doc source created. **Only this one reference** — `web/routes/
    doc_sources.py`'s DELETE handler calls this for exactly the GUID the
    removed row carried, never a sweep, so removing one source cannot take
    another's reference with it even if two sources happen to share a URL
    (an edge case this leaves alone rather than trying to detect, since a
    shared URL is not this slice's problem to solve)."""
    if not ref_guid:
        return {"ok": True, "error": ""}
    detach_error = ""
    try:
        client = _client(clients)
        if asset_guid:
            try:
                client.detach_external_reference(asset_guid, ref_guid)
            except Exception as exc:
                # Recorded, not only logged — same reasoning as
                # publish_doc_source's link_error above: the delete below
                # can still succeed, so this must not disappear from the
                # returned dict just because it didn't stop the overall
                # removal.
                detach_error = str(exc)[:300]
                log.debug("doc source: detach %s from %s failed (continuing to delete): %s",
                          ref_guid, asset_guid, exc)
        if delete:
            client.delete_external_reference(ref_guid)
        return {"ok": True, "error": detach_error}
    except Exception as exc:
        log.warning("doc source: could not remove ExternalReference %s: %s", ref_guid, exc)
        return {"ok": False, "error": str(exc)[:500]}


def read_back_doc_sources(asset_guid: str, *, clients) -> list[dict]:
    """Every `ExternalReference` currently linked to `asset_guid` in Egeria
    — including one declared there by someone/something else, which is the
    read-back the brief asks for ("a source declared in Egeria by someone
    else appears here too"). Returns `[]`, not an exception, when the asset
    has no such links or Egeria cannot be reached — this is read-back for
    display, and a failed read-back should fall back to RE's own local
    rows, not break the Enrichment pane.
    """
    if not asset_guid:
        return []
    try:
        from pyegeria.omvs.metadata_expert import MetadataExpert

        result = clients.of(MetadataExpert).get_related_metadata_elements(
            asset_guid, "ExternalReferenceLink", {"class": "ResultsRequestBody"},
        )
    except Exception as exc:
        log.debug("doc source: read-back for %s failed: %s", asset_guid, exc)
        return []
    if isinstance(result, str) or not result:
        return []
    # Shape confirmed live against a real platform (this slice's gate check,
    # 2026-09-28) rather than assumed from the client's docstring, which
    # only sketches the response. `get_related_metadata_elements` returns
    # `{"startingElement": ..., "elementList": [...], "mermaidGraph": ...}`
    # -- NOT a bare list and NOT `{"elements": [...]}`, both of which an
    # earlier version of this function guessed and which silently returned
    # zero references every time (caught because the gate check asserts
    # presence, not absence -- see find-absence-as-answer). Each entry in
    # `elementList` is the RELATIONSHIP (`ExternalReferenceLink`) with the
    # far-end element nested under `element`, and that element's properties
    # live at `elementProperties.propertiesAsStrings` -- pyegeria's own
    # flattened string view of `propertyValueMap`, used here instead of
    # walking the typed `propertyValueMap` structure by hand.
    entries = result.get("elementList") if isinstance(result, dict) else result
    out = []
    for entry in entries or []:
        el = (entry or {}).get("element") or entry or {}
        guid = el.get("elementGUID", "")
        props = (el.get("elementProperties") or {}).get("propertiesAsStrings", {})
        url = props.get("url") or ""
        if not url:
            continue
        out.append({
            "ref_guid": guid,
            "url": url,
            "label": props.get("displayName") or props.get("referenceTitle") or "",
        })
    return out

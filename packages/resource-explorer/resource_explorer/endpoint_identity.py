"""D1-a: a database's identity in Egeria is its endpoint, and Egeria's duplicate links are read, not guessed.

DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md §2-§3 and §7-§9 (the owner's rulings). Read-only: nothing here writes to
Egeria, and nothing here writes to RE's registry except `record_resolution`, which appends one proof row on a
read-back the person pressed.

- **Identity is the endpoint** (host, port, database). Two qualified names that point at one endpoint describe one
  database; two databases with different endpoints stay apart even when their names match.
- **Host aliases** (D1-2): `EGERIA_HOST_ALIASES` (declared in `config.EgeriaConfig.host_aliases`, read here by
  `host_aliases` and nowhere else) lists the names that all mean this machine. A host not listed compares as itself.
- **Families**: every element found by the endpoint belongs to the family its qualifiedName's server segment names
  (`PostgreSQL Relational Database Schema::<server>::<database>.<schema>`). RE's family is the one RE's own template
  names (`server_name_for`); any other is someone else's (today: the content pack's "Coco PostgreSQL Server 1").
- **Duplicate links** (D1-3, owner "Ok"): a PeerDuplicateLink counts as in Egeria only when CONFIRMED, which is
  Egeria's own test (`DuplicateEntityIterator`: `statusIdentifier` = VALIDATED, 3). Any other status is shown as
  "possible duplicate · not confirmed" and makes nothing read "in Egeria".
- **The re-anchor plan** (§8 step 1, D1-1): for each of RE's schemas on an endpoint another family also describes,
  the other family's element of the same schema name, and what hangs off RE's copy today. A plan only: no write.

The walk per Endpoint is the one the live elements carry (read 2026-10-30): Endpoint -ConnectToEndpoint-> Connection
-ResourceConnection-> the asset; then one relationships read of each asset, for its duplicate links, its catalog
target and its database link.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlsplit

from resource_explorer.catalogue_gateway import GatewayError, server_name_for

log = logging.getLogger(__name__)

#: Egeria's StatusIdentifier.VALIDATED: the only status Egeria's own duplicate processing treats as a confirmed link.
CONFIRMED_STATUS = 3
#: Deprecated and obsolete links are retired by a steward: neither a duplicate nor a possible one.
RETIRED_STATUSES = frozenset({4, 5})

DUPLICATE_LINK = "PeerDuplicateLink"
CONSOLIDATED_LINK = "ConsolidatedDuplicateLink"
#: Endpoint -> Connection (v6 name, and the older one), Connection -> asset (v6 name, and the older one).
ENDPOINT_LINKS = frozenset({"ConnectToEndpoint", "ConnectionEndpoint"})
ASSET_LINKS = frozenset({"ResourceConnection", "ConnectionToAsset"})
SCHEMA_TYPE = "DeployedDatabaseSchema"
DATABASE_TYPE = "RelationalDatabase"
CATALOG_TARGET = "CatalogTarget"
ANNOTATION_LINK = "AssociatedAnnotation"

PROOF_KIND = "endpoint_resolved"

POSSIBLE_DUPLICATE = "possible duplicate · not confirmed"
LINKED_DUPLICATE = "linked as a duplicate"
NO_PAIR = "pack element: none · the pack has no schema of this name · RE's addition, kept"
NEITHER = "not cataloged by RE or the pack"
KEPT_UNTIL = "RE's copy kept until soft delete is cleared"
NOT_READ = "not read"

#: How long one resolution is reused in this process (the Contents preview, publish-rules and the read-back share it).
CACHE_SECONDS = 60.0


# ── the alias table and the endpoint ─────────────────────────────────────────

def host_aliases() -> frozenset[str]:
    """The configured names for this machine, lower case. The ONE reader of `EGERIA_HOST_ALIASES`."""
    from resource_explorer.config import get_config

    raw = get_config().egeria.host_aliases or ""
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def canonical_host(host: str, aliases: frozenset[str] | None = None) -> str:
    """`this-machine` for any configured alias, else the host itself (lower case)."""
    h = (host or "").strip().lower()
    return "this-machine" if h in (host_aliases() if aliases is None else aliases) else h


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: str
    database: str
    schema: str = ""          # `?currentSchema=` (a schema's endpoint); "" for the database's own

    def key(self, aliases: frozenset[str] | None = None) -> tuple[str, str, str]:
        return canonical_host(self.host, aliases), self.port, self.database

    def words(self) -> str:
        return f"{self.host}:{self.port}/{self.database}"


_JDBC = re.compile(r"^jdbc:(?P<kind>[a-z0-9]+):(?P<rest>//.*)$", re.IGNORECASE)


def parse_network_address(address: str) -> Endpoint | None:
    """`jdbc:postgresql://host:port/db[?currentSchema=s]` -> Endpoint; anything else -> None (not this kind)."""
    m = _JDBC.match((address or "").strip())
    if not m:
        return None
    parts = urlsplit(m.group("rest"), scheme="jdbc")
    db = unquote(parts.path.lstrip("/"))
    if not parts.hostname or not db:
        return None
    try:
        port = str(parts.port or "")
    except ValueError:
        return None
    schema = (parse_qs(parts.query).get("currentSchema") or [""])[0]
    return Endpoint(parts.hostname, port, db, schema)


def database_endpoint(db_entity) -> Endpoint:
    """The endpoint RE knows a database by: the host Egeria reaches it under, else its own host."""
    host = getattr(db_entity, "egeria_host", "") or db_entity.host
    return Endpoint(host, str(db_entity.port), db_entity.database_name)


def same_endpoint(a: Endpoint, b: Endpoint, aliases: frozenset[str] | None = None) -> bool:
    return a.key(aliases) == b.key(aliases)


# ── families ─────────────────────────────────────────────────────────────────

def family_of(qualified_name: str) -> str:
    """The server segment of a template qualifiedName (`<type>::<server>::<database>[.<schema>]`); "" when the name
    has another shape (then the element is shown by its own name, never assigned a family)."""
    parts = (qualified_name or "").split("::")
    return parts[1] if len(parts) >= 3 else ""


def schema_name_of(qualified_name: str, database: str) -> str:
    """`<schema>` from `...::<database>.<schema>`; "" when the name does not end that way."""
    tail = (qualified_name or "").split("::")[-1]
    head = f"{database}."
    return tail[len(head):] if tail.startswith(head) else ""


def duplicate_status(rel) -> int | None:
    raw = str((rel.properties or {}).get("statusIdentifier") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def is_confirmed(status: int | None) -> bool:
    return status == CONFIRMED_STATUS


# ── the resolution ───────────────────────────────────────────────────────────

def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def resolve(gateway, db_entity, *, aliases: frozenset[str] | None = None) -> dict:
    """Every database and schema element Egeria holds for `db_entity`'s endpoint, by family, with their duplicate links.
    JSON-safe (it is stored as a proof row's detail). A read that cannot be made gives `{"error": ...}` and no
    elements: never "nothing there".

    Returns {endpoint, aliases, own_family, families: [{name, own, schemas, databases}], schemas: {name: [element]},
    databases: [element], duplicates: [...], read_at}; an element is {guid, qualified_name, type, family, own,
    schema, endpoint, duplicates: [{guid, qualified_name, status, confirmed}], catalog_target, database_links}."""
    aliases = host_aliases() if aliases is None else aliases
    want = database_endpoint(db_entity)
    own = server_name_for(db_entity)
    out = {"endpoint": want.words(), "aliases": sorted(aliases), "own_family": own, "read_at": _stamp(),
           "families": [], "schemas": {}, "databases": [], "duplicates": [], "error": ""}
    try:
        endpoints = gateway.find_endpoints(f":{want.port}/{want.database}")
        assets: dict[str, dict] = {}
        for ep in endpoints:
            got = parse_network_address(ep.network_address)
            if got is None or not same_endpoint(got, want, aliases):
                continue
            for c in gateway.relationships(ep.guid):
                if c.type_name not in ENDPOINT_LINKS or not c.other_guid:
                    continue
                for a in gateway.relationships(c.other_guid):
                    if a.type_name in ASSET_LINKS and a.other_type in (SCHEMA_TYPE, DATABASE_TYPE) and a.other_guid:
                        assets.setdefault(a.other_guid, {
                            "guid": a.other_guid, "qualified_name": a.other_qualified_name, "type": a.other_type,
                            "family": family_of(a.other_qualified_name),
                            "schema": got.schema or schema_name_of(a.other_qualified_name, want.database),
                            "endpoint": ep.network_address})
        for el in assets.values():
            el["own"] = el["family"] == own
            el["duplicates"], el["catalog_target"], el["database_links"] = [], "", []
            for r in gateway.relationships(el["guid"]):
                if r.type_name in (DUPLICATE_LINK, CONSOLIDATED_LINK) and r.other_guid not in assets:
                    status = duplicate_status(r) if r.type_name == DUPLICATE_LINK else CONFIRMED_STATUS
                    if status in RETIRED_STATUSES:
                        continue
                    el["duplicates"].append({"guid": r.other_guid, "qualified_name": r.other_qualified_name,
                                             "type": r.type_name, "status": status,
                                             "confirmed": is_confirmed(status)})
                elif r.type_name == CATALOG_TARGET:
                    el["catalog_target"] = r.guid
                elif el["type"] == SCHEMA_TYPE and r.other_type == DATABASE_TYPE:
                    el["database_links"].append({"type": r.type_name, "guid": r.other_guid})
    except GatewayError as exc:
        out["error"] = str(exc)[:400]
        return out
    for el in sorted(assets.values(), key=lambda e: (not e["own"], e["family"], e["schema"])):
        if el["type"] == DATABASE_TYPE:
            out["databases"].append(el)
        else:
            out["schemas"].setdefault(el["schema"], []).append(el)
        out["duplicates"].extend({**d, "of": el["guid"]} for d in el["duplicates"])
    fams: dict[str, dict] = {}
    for el in assets.values():
        f = fams.setdefault(el["family"], {"name": el["family"], "own": el["own"], "schemas": 0, "databases": 0})
        f["schemas" if el["type"] == SCHEMA_TYPE else "databases"] += 1
    out["families"] = sorted(fams.values(), key=lambda f: (not f["own"], f["name"]))
    return out


def other_families(resolution: dict | None) -> list[dict]:
    return [f for f in (resolution or {}).get("families") or [] if not f["own"]]


def held_elsewhere(resolution: dict | None, schema: str) -> dict | None:
    """The other family's element for `schema` at this endpoint (the first by family name), or None."""
    return next((e for e in ((resolution or {}).get("schemas") or {}).get(schema) or [] if not e["own"]), None)


def own_element(resolution: dict | None, schema: str) -> dict | None:
    return next((e for e in ((resolution or {}).get("schemas") or {}).get(schema) or [] if e["own"]), None)


def schema_state_overlay(resolution: dict | None, schema: str) -> dict | None:
    """What the IN EGERIA column says from the endpoint for a schema RE's own rows say nothing about:
    `{"state", "words", "second"}`, or None (nothing at this endpoint beyond RE's own). An element found by endpoint
    is in Egeria; one reached only over a duplicate link is in Egeria only when the link is confirmed."""
    other = held_elsewhere(resolution, schema)
    at = str((resolution or {}).get("read_at") or "")[:16].replace("T", " ")
    if other is not None:
        return {"state": "in_egeria_other", "words": f"in Egeria under '{other['family']}'",
                "second": f"element {other['guid'][:8]} · found by endpoint {at} · not RE's element, RE adds to it only",
                "element_guid": other["guid"], "family": other["family"]}
    mine = own_element(resolution, schema)
    for d in (mine or {}).get("duplicates") or []:
        if d["confirmed"]:
            return {"state": "in_egeria_duplicate", "words": f"in Egeria · also described by {d['qualified_name']} "
                    f"({LINKED_DUPLICATE})", "second": f"read {at}", "element_guid": d["guid"]}
    possible = [d for d in (mine or {}).get("duplicates") or [] if not d["confirmed"]]
    if possible:
        return {"state": "possible_duplicate", "words": f"{POSSIBLE_DUPLICATE} · {possible[0]['qualified_name']}",
                "second": "a proposed duplicate link does not make this read in Egeria", "element_guid": ""}
    return None


def element_qualified_name_prefixes(resolution: dict | None, schema: str) -> list[str]:
    """The schema qualifiedNames Egeria holds for `schema` at this endpoint, RE's own first. Publish-rules looks a
    table or column up under each in turn: whichever family holds the schema holds its tables."""
    els = ((resolution or {}).get("schemas") or {}).get(schema) or []
    return [e["qualified_name"] for e in sorted(els, key=lambda e: not e["own"])]


# ── the re-anchor plan (§8 step 1) ───────────────────────────────────────────

def _annotations_naming(rels, database: str, schema: str) -> int:
    pat = re.compile(rf"::{re.escape(database)}\.{re.escape(schema)}(?:[.:]|$)")
    return sum(1 for r in rels if r.type_name == ANNOTATION_LINK and pat.search(r.other_qualified_name or ""))


def reanchor_plan(gateway, db_entity, resolution: dict, *, rule_proofs: dict | None = None) -> dict | None:
    """One row per RE schema on an endpoint another family also describes; None when no other family is there
    (a unique endpoint has no plan and nothing changes). Reads RE's database element's relationships once, for the
    relationship type it links its schemas by (READ, never guessed) and for the annotations naming each schema.

    A row: {schema, re_guid, re_qualified_name, pack_guid, pack_qualified_name, pack_family, hangs_off, words}.
    `hangs_off` counts what is on RE's copy today: annotations naming the schema (on RE's database element, the
    survey's subject), RE's published rules (RE's own proof rows), and the catalog target (its relationship GUID)."""
    if not other_families(resolution) or resolution.get("error"):
        return None
    from resource_explorer.surveyors.database.quality.egeria_rules import (
        PUBLISHED_OUTCOMES,
        check_path,
    )

    own_db = next((d for d in resolution.get("databases") or [] if d["own"]), None)
    db_rels, link_type, read_error = [], "", ""
    if own_db is not None:
        try:
            db_rels = gateway.relationships(own_db["guid"])
        except GatewayError as exc:
            read_error = str(exc)[:200]
    rows = []
    own_schemas = {name: own_element(resolution, name) for name in resolution.get("schemas") or {}}
    own_schemas = {k: v for k, v in own_schemas.items() if v is not None}
    seen_types = {r.type_name for r in db_rels if r.other_type == SCHEMA_TYPE
                  and r.other_guid in {e["guid"] for e in own_schemas.values()}}
    link_type = next(iter(seen_types)) if len(seen_types) == 1 else ""
    for name, mine in sorted(own_schemas.items()):
        other = held_elsewhere(resolution, name)
        rules = sum(1 for k, p in (rule_proofs or {}).items()
                    if check_path(k).split(".")[0] == name and p.get("outcome") in PUBLISHED_OUTCOMES)
        hangs = {"annotations": None if read_error or own_db is None else _annotations_naming(
                     db_rels, db_entity.database_name, name),
                 "rules": rules, "catalog_target": mine.get("catalog_target") or ""}
        words = (f"re-anchor onto {other['guid'][:8]} under '{other['family']}' · {KEPT_UNTIL}" if other
                 else NO_PAIR)
        rows.append({"schema": name, "re_guid": mine["guid"], "re_qualified_name": mine["qualified_name"],
                     "pack_guid": (other or {}).get("guid", ""),
                     "pack_qualified_name": (other or {}).get("qualified_name", ""),
                     "pack_family": (other or {}).get("family", ""), "hangs_off": hangs, "words": words})
    pairs = sum(1 for r in rows if r["pack_guid"])
    return {"rows": rows, "pairs": pairs, "database_guid": (own_db or {}).get("guid", ""),
            "database_row": database_row(own_db, db_rels, link_type, seen_types, read_error, resolution),
            "database_link_type": link_type or NOT_READ,
            "database_link_types_seen": sorted(seen_types), "read_error": read_error,
            "families": resolution.get("families") or [], "read_at": resolution.get("read_at", ""),
            "words": plan_words(rows, pairs, resolution)}


def database_row(own_db, db_rels, link_type: str, seen_types: set, read_error: str, resolution: dict) -> dict | None:
    """§8 step 2, the level where the two families meet (coordinator + Architect, 2026-10-30, design §10): RE's database
    element and every other-family schema not yet its member, with what a press would add and the member count after
    it. The relationship type is the ONE type RE's database already links its own schemas by, read off those links;
    when it cannot be read (no RE database element, a failed read, none or several types) nothing is proposed."""
    if own_db is None:
        return None
    links = [r for r in db_rels if r.other_type == SCHEMA_TYPE and r.type_name == link_type]
    members = {r.other_guid for r in links}
    # Which end the schema is at, read off the same links (live 2026-10-31: the schema is end 1, the data set; the
    # database is end 2, its content). All must agree, else no end is proposed and a press refuses.
    ends = {r.other_at_end1 for r in links}
    schema_end = 1 if ends == {True} else 2 if ends == {False} else None
    others = [e for es in (resolution.get("schemas") or {}).values() for e in es
              if not e["own"] and e["guid"] not in members]
    others.sort(key=lambda e: (e["family"], e["schema"]))
    fams = sorted({e["family"] for e in others})
    whose = " and ".join(f"'{f}'" for f in fams) or "the other family"
    if read_error or not link_type:
        why = read_error or (f"several types seen ({', '.join(sorted(seen_types))})" if seen_types
                             else "RE's database links none of its schemas")
        words = f"relationship type {NOT_READ} · {why} · nothing proposed"
    elif schema_end is None:
        words = f"which end of {link_type} the schema takes is {NOT_READ} (the existing links disagree or do not say) · " \
                "nothing proposed"
    elif not others:
        words = f"every other-family schema is already a {link_type} member of {own_db['guid'][:8]} · nothing to add"
    else:
        words = (f"add {link_type} from {own_db['guid'][:8]} to the pack's {len(others)} schema"
                 f"{'s' if len(others) != 1 else ''} under {whose} · after the press: {len(members) + len(others)} "
                 f"members · only adding, the pack's elements are not changed · nothing is written until the press")
    return {"re_guid": own_db["guid"], "re_qualified_name": own_db["qualified_name"], "link_type": link_type or NOT_READ,
            "schema_end": schema_end,
            "members_now": len(members) if link_type else None,
            "members_after": len(members) + len(others) if link_type and not read_error and schema_end else None,
            "to_add": [{"guid": e["guid"], "schema": e["schema"], "family": e["family"]} for e in others],
            "words": words}


def neither_line(resolution: dict | None, names: list[str]) -> dict | None:
    """The schemas RE's survey lists that no family at this endpoint catalogs (only when there is a plan to read
    it beside). Derived where the survey's list is known (Contents), from the same resolution."""
    held = set((resolution or {}).get("schemas") or {})
    left = sorted(n for n in names if n not in held)
    return {"schemas": left, "words": f"{', '.join(left)}: {NEITHER}"} if left else None


def plan_words(rows: list[dict], pairs: int, resolution: dict) -> str:
    def one(f: dict) -> str:
        whose = f"RE's ({f['name']})" if f["own"] else f"under '{f['name']}'"
        return f"{f['schemas']} schema{'s' if f['schemas'] != 1 else ''} {whose}"

    said = " · ".join(one(f) for f in resolution.get("families") or [])
    head = f"this endpoint ({resolution.get('endpoint', '')}): {said}"
    if not rows:
        return f"{head} · RE has no schema element here · plan only, nothing is written"
    return (f"{head} · re-anchor plan: {pairs} of {len(rows)} RE schema{'s' if len(rows) != 1 else ''} "
            f"{'has' if pairs == 1 else 'have'} a pack element of the same name · plan only, nothing is written")


def log_plan(slug: str, plan: dict | None) -> None:
    if plan is None:
        log.info("reanchor_plan slug=%s no other family at this endpoint: no plan", slug)
        return
    log.info("reanchor_plan slug=%s pairs=%d rows=%d database=%s link_type=%s · %s", slug, plan["pairs"],
             len(plan["rows"]), plan["database_guid"], plan["database_link_type"], plan["words"])
    db = plan.get("database_row")
    if db:
        log.info("reanchor_plan slug=%s database=%s link_type=%s members_now=%s members_after=%s to_add=%s · %s", slug,
                 db["re_guid"], db["link_type"], db["members_now"], db["members_after"],
                 ",".join(x["guid"] for x in db["to_add"]), db["words"])
    for r in plan["rows"]:
        log.info("reanchor_plan slug=%s schema=%s re=%s pack=%s family=%r hangs_off=%s · %s", slug, r["schema"],
                 r["re_guid"], r["pack_guid"] or "-", r["pack_family"], r["hangs_off"], r["words"])


# ── one cached resolution per database, and its proof row ────────────────────

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def cached_resolve(gateway, db_entity, *, now: float | None = None) -> dict:
    """`resolve`, reused for CACHE_SECONDS per database and platform. A failed read is never cached."""
    key = (db_entity.slug, getattr(gateway, "platform_url", ""), getattr(gateway, "view_server", ""))
    t = time.monotonic() if now is None else now
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and t - hit[0] < CACHE_SECONDS:
            return hit[1]
    res = resolve(gateway, db_entity)
    if not res.get("error"):
        with _cache_lock:
            _cache[key] = (t, res)
    return res


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def record_resolution(registry, gateway, db_entity, *, curation_id: str = "", by: str = "") -> dict:
    """The read-back's step: resolve the endpoint, build the plan, log it, and append ONE proof row
    (`endpoint_resolved`, the database node) that Contents derives its words from. Returns the detail."""
    res = resolve(gateway, db_entity)
    plan = None if res.get("error") else reanchor_plan(
        gateway, db_entity, res, rule_proofs=registry.latest_quality_rule_proofs(db_entity.slug))
    log_plan(db_entity.slug, plan)
    detail = {"resolution": res, "plan": plan}
    registry.append_catalogue_commit_proof(db_entity.slug, proof=PROOF_KIND, node_kind="database",
                                           curation_id=curation_id, recorded_by=by, detail=detail)
    if not res.get("error"):
        with _cache_lock:
            _cache[(db_entity.slug, getattr(gateway, "platform_url", ""), getattr(gateway, "view_server", ""))] = (
                time.monotonic(), res)
    return detail


# ── D1-b: the database-level press (design §13) ─────────────────────────────
# One press on the plan's database row adds the relationship the row names (read from Egeria: today DataSetContent,
# the database at end 2, as the JDBC cataloguer links schemas) from RE's database element to every schema in the row's
# `to_add`. Nothing else: no element is created, no pack element changes, no RE schema is touched.

APPLIED_KIND = "reanchor_applied"
ADDED, ALREADY_LINKED, FAILED = "added", "already_linked", "failed"
READ_AGAIN = "read Egeria again first"
UNDO_CALL = "AssetMaker.detach_data_set_content_relationship"
UNDO_NOTE = (f"to undo, remove each relationship listed as added by its own GUID with {UNDO_CALL}(<relationship "
             "GUID>): it detaches that one link and nothing else (docs/design-notes/D1B-DATABASE-LINK-PRESS-UNDO.md)")


class ReanchorRefused(Exception):
    """The press cannot go ahead; nothing was written. `status` is the HTTP status the route answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message, self.status = message, status


_pressing: set[str] = set()
_pressing_lock = threading.Lock()


def _newest(registry, slug: str, kind: str) -> dict | None:
    rows = [p for p in registry.list_catalogue_commit_proofs(slug) if p["proof"] == kind]
    return rows[-1] if rows else None


def _link_guids(gateway, schema_guid: str, link_type: str, database_guid: str, schema_end: int) -> list[str]:
    """The `link_type` relationships between a schema and RE's database, read from the SCHEMA's side, with the
    database at the end the plan read (the schema at end 1 means the database is end 2)."""
    db_at_end1 = schema_end == 2
    return [r.guid for r in gateway.relationships(schema_guid)
            if r.type_name == link_type and r.other_guid == database_guid and r.other_at_end1 is db_at_end1]


def apply_words(counts: dict, outcomes: list[dict], members: int | None, read_at: str) -> str:
    """Acknowledge + validate (§13): "26 members · 22 added · read back <time>", with the schemas not linked."""
    at = str(read_at or "")[5:16].replace("T", " ")      # the page's one form, "MM-DD HH:MM" (W25)
    parts = [f"{members} members" if members is not None else "members not read back", f"{counts[ADDED]} added"]
    if counts[ALREADY_LINKED]:
        parts.append(f"{counts[ALREADY_LINKED]} already linked")
    failed = [o for o in outcomes if o["outcome"] == FAILED]
    if failed:
        parts.append(f"{len(failed)} not linked: " + "; ".join(f"{o['schema']} ({o['error']})" for o in failed))
    return " · ".join(parts) + (f" · read back {at}" if at else "")


def apply_reanchor(registry, gateway, db_entity, *, by: str, now=None) -> dict:
    """The press. Source of truth: the newest `endpoint_resolved` row's `plan.database_row` (no second derivation of
    the list); refused, writing nothing, when that row is missing, older than the read-back staleness
    (`catalogue_commit.READ_BACK_NOT_CHECKED_AFTER_S`), from a failed read, or its row proposes nothing.

    For each `to_add` schema: read its relationships first and skip a `link_type` link to RE's database that already
    exists (`already_linked`); else ONE Egeria call creates it, and the schema is read again for the new link's GUID
    (`added` only when seen; pyegeria's wrapper returns no GUID). One `reanchor_applied` proof row with every outcome,
    then `record_resolution` again so the plan re-derives. One press per database at a time."""
    slug = db_entity.slug
    with _pressing_lock:
        if slug in _pressing:
            raise ReanchorRefused("a press for this database is already running · wait for it, then read Egeria again")
        _pressing.add(slug)
    try:
        return _apply(registry, gateway, db_entity, by=by, now=now)
    finally:
        with _pressing_lock:
            _pressing.discard(slug)


def _apply(registry, gateway, db_entity, *, by: str, now=None) -> dict:
    from resource_explorer.catalogue_commit import READ_BACK_NOT_CHECKED_AFTER_S, _age_s

    slug = db_entity.slug
    started = time.perf_counter()
    row = _newest(registry, slug, PROOF_KIND)
    if row is None:
        raise ReanchorRefused(f"no plan has been read for this database · {READ_AGAIN} · nothing written")
    age = _age_s(row["read_at"], now)
    if age is None or age > READ_BACK_NOT_CHECKED_AFTER_S:
        raise ReanchorRefused(f"the plan was read {str(row['read_at'])[:16].replace('T', ' ')}, more than "
                              f"{READ_BACK_NOT_CHECKED_AFTER_S} s ago · {READ_AGAIN} · nothing written")
    detail = row.get("detail") or {}
    if (detail.get("resolution") or {}).get("error"):
        raise ReanchorRefused(f"the last read of this endpoint failed · {READ_AGAIN} · nothing written")
    db = (detail.get("plan") or {}).get("database_row")
    if not db:
        raise ReanchorRefused("no database-level row in the plan · nothing to press · nothing written")
    link_type, schema_end, database_guid = db.get("link_type"), db.get("schema_end"), db.get("re_guid", "")
    if link_type in (None, "", NOT_READ) or schema_end not in (1, 2) or db.get("members_after") is None:
        raise ReanchorRefused(f"{db.get('words', '')} · nothing written")
    outcomes: list[dict] = []
    for x in db.get("to_add") or []:
        out = {"schema": x["schema"], "guid": x["guid"], "family": x.get("family", ""), "outcome": "", "error": "",
               "relationship_guid": ""}
        try:
            before = _link_guids(gateway, x["guid"], link_type, database_guid, schema_end)
            if before:
                out.update(outcome=ALREADY_LINKED, relationship_guid=before[0])
            else:
                data_set, content = (x["guid"], database_guid) if schema_end == 1 else (database_guid, x["guid"])
                gateway.link_data_set_content(data_set, content)
                seen = _link_guids(gateway, x["guid"], link_type, database_guid, schema_end)
                if seen:
                    out.update(outcome=ADDED, relationship_guid=seen[0])
                else:
                    out.update(outcome=FAILED, error="sent, but the link is not on the read-back")
        except GatewayError as exc:
            out.update(outcome=FAILED, error=str(exc)[:300])
        outcomes.append(out)
    counts = {k: sum(1 for o in outcomes if o["outcome"] == k) for k in (ADDED, ALREADY_LINKED, FAILED)}
    fresh = record_resolution(registry, gateway, db_entity, by=by)
    fresh_db = (fresh.get("plan") or {}).get("database_row") or {}
    members = fresh_db.get("members_now")
    words = apply_words(counts, outcomes, members, fresh["resolution"].get("read_at", ""))
    applied = {"database_guid": database_guid, "link_type": link_type, "schema_end": schema_end,
               "plan_read_at": row["read_at"], "outcomes": outcomes, "counts": counts, "members_after": members,
               "to_add_after": len(fresh_db.get("to_add") or []), "words": words, "undo": UNDO_NOTE}
    registry.append_catalogue_commit_proof(slug, proof=APPLIED_KIND, node_kind="database", element_guid=database_guid,
                                           recorded_by=by, detail=applied)
    for o in outcomes:
        if o["outcome"] == ADDED:
            log.info("reanchor_applied slug=%s added relationship=%s schema=%s (%s) · undo: %s(%s)", slug,
                     o["relationship_guid"], o["guid"], o["schema"], UNDO_CALL, o["relationship_guid"])
    log.info("reanchor_timing slug=%s to_add=%d added=%d already=%d failed=%d total_ms=%.0f", slug, len(outcomes),
             counts[ADDED], counts[ALREADY_LINKED], counts[FAILED], (time.perf_counter() - started) * 1000)
    return {**applied, "plan": fresh.get("plan")}

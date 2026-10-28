"""The API reference tables (api_reference_documents / _operations / _schemas; DDL in registry.API_REFERENCE_DDL).

Everything that reads or writes them lives here. A described document (a committed spec file or the registered
running instance) is stored as a header row, one row per operation with the filterable columns lifted out, and
one row per schema; `load_interface` rebuilds the same `Interface` the parsers produce, so nothing downstream
knows where it came from. Reads are type-strict (a registry that is not a real ProjectRegistry, or a row that is
not what was written, reads as "nothing"), and writes replace a document's children in one transaction.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from resource_explorer.api_reference.compare import normalize_path
from resource_explorer.api_reference.model import Interface, Operation

log = logging.getLogger(__name__)

RESOURCE_TYPE = "repo"
SEARCH_TEXT_CAP = 2000


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _real(registry) -> bool:
    from resource_explorer.registry import ProjectRegistry
    return isinstance(registry, ProjectRegistry)


def _lock(registry, conn, key: str) -> None:
    """Serialise writers of one repository's documents. SQLite already serialises writers; on Postgres a
    transaction-scoped advisory lock keyed on the repository and source does it, so two registrations cannot both
    leave a current row."""
    if str(getattr(registry, "database_url", "")).startswith("postgresql"):
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))


def _like(term: str) -> str:
    """A lower-cased LIKE pattern for a literal term: backslash, percent and underscore are matched as
    themselves (used with ESCAPE of a backslash)."""
    t = " ".join(term.lower().split())
    return "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _slug(registry, slug: str) -> str:
    return registry._normalize_slug(slug)


def _rows(cur) -> list:
    got = cur.fetchall()
    return [dict(r) for r in got] if isinstance(got, list) else []


# ── writing ──────────────────────────────────────────────────────────────────

def _search_text(op: Operation) -> str:
    bits = [op.path, op.name, op.summary, op.description, op.operation_id, " ".join(op.tags),
            " ".join(str(p.get("name", "")) for p in op.parameters if isinstance(p, dict))]
    return " ".join(" ".join(bits).lower().split())[:SEARCH_TEXT_CAP]


def _op_row(doc_id: str, slug: str, source: str, seq: int, op: Operation) -> tuple:
    rb = op.request_body
    return (doc_id, RESOURCE_TYPE, slug, source, seq, op.kind, op.kind_detail, op.key, op.name, op.method, op.path,
            normalize_path(op.path) if op.path else "", op.operation_id, op.summary, op.description,
            json.dumps(op.tags), 1 if op.deprecated else 0, json.dumps(op.security), json.dumps(op.parameters),
            json.dumps(rb) if rb is not None else None, (rb or {}).get("schema_name", ""),
            json.dumps(op.responses), " ".join(sorted({r.get("schema_name", "") for r in op.responses if r.get("schema_name")})),
            json.dumps(op.input_schema) if op.input_schema is not None else None,
            op.handler, op.handler_signature, op.location, _search_text(op))


_OP_COLS = ("document_id, resource_type, slug, source, seq, kind, kind_detail, op_key, name, method, path, "
            "normalised_path, operation_id, summary, description, tags_json, deprecated, security_json, "
            "parameters_json, request_body_json, request_schema, responses_json, response_schemas, "
            "input_schema_json, handler, handler_signature, location, search_text")


def _write_children(conn, doc_id: str, slug: str, source: str, iface: Interface) -> None:
    conn.execute("DELETE FROM api_reference_operations WHERE document_id = ?", (doc_id,))
    conn.execute("DELETE FROM api_reference_schemas WHERE document_id = ?", (doc_id,))
    if iface.operations:
        conn.executemany(
            f"INSERT INTO api_reference_operations ({_OP_COLS}) VALUES ({', '.join('?' * 28)})",
            [_op_row(doc_id, slug, source, i, op) for i, op in enumerate(iface.operations)])
    if iface.schemas:
        conn.executemany("INSERT INTO api_reference_schemas (document_id, name, schema_json) VALUES (?, ?, ?)",
                         [(doc_id, n, json.dumps(s, default=str)) for n, s in iface.schemas.items()])


def _meta(iface: Interface) -> str:
    return json.dumps({"base_paths": iface.base_paths, "security_schemes": iface.security_schemes,
                       "groups": iface.groups, "notes": iface.notes})


def _supersede(conn, slug: str, source: str, now: str) -> None:
    for r in conn.execute("SELECT id FROM api_reference_documents WHERE resource_type = ? AND slug = ? AND source = ? "
                          "AND superseded_at IS NULL", (RESOURCE_TYPE, slug, source)).fetchall():
        conn.execute("DELETE FROM api_reference_operations WHERE document_id = ?", (r["id"],))
        conn.execute("DELETE FROM api_reference_schemas WHERE document_id = ?", (r["id"],))
    conn.execute("UPDATE api_reference_documents SET superseded_at = ? WHERE resource_type = ? AND slug = ? "
                 "AND source = ? AND superseded_at IS NULL", (now, RESOURCE_TYPE, slug, source))


_DOC_COLS = ("id, resource_type, slug, source, origin, kind, status, reason, spec_version, title, api_version, "
             "description, operation_count, total_operations, content_type, byte_count, captured_at, attempted_at, "
             "captured_by, registered_by, registered_at, meta_json")


def _insert_doc(conn, *, slug, source, origin, status, reason, captured_at, iface=None, captured_by="",
                registered_by="", registered_at="", content_type="", byte_count=None, attempted_at="") -> str:
    doc_id = secrets.token_hex(8)
    conn.execute(
        f"INSERT INTO api_reference_documents ({_DOC_COLS}) VALUES ({', '.join('?' * 22)})",
        (doc_id, RESOURCE_TYPE, slug, source, origin, iface.kind if iface else "http_api", status, reason,
         iface.spec_version if iface else "", iface.title if iface else "", iface.version if iface else "",
         (iface.description if iface else "")[:2000], len(iface.operations) if iface else 0,
         iface.total_operations if iface else 0, content_type, byte_count, captured_at, attempted_at or captured_at,
         captured_by, registered_by, registered_at, _meta(iface) if iface else "{}"))
    if iface is not None and status == "ok":
        _write_children(conn, doc_id, slug, source, iface)
    return doc_id


def replace_committed(registry, slug: str, results: list, *, captured_at: str, captured_by: str = "") -> None:
    """One capture of a repository's committed specs, replacing the previous one. `results` are
    {origin, status ('ok'|'unreadable'|'fragment'), reason, interface}. A marker row is always written."""
    slug = _slug(registry, slug)
    with registry._conn() as conn:
        _lock(registry, conn, f"api_reference:{slug}:committed_spec")
        _supersede(conn, slug, "committed_spec", captured_at)
        for r in results:
            _insert_doc(conn, slug=slug, source="committed_spec", origin=r["origin"], status=r["status"],
                        reason=r.get("reason", ""), captured_at=captured_at, iface=r.get("interface"),
                        captured_by=captured_by)
        parsed = sum(1 for r in results if r["status"] == "ok")
        _insert_doc(conn, slug=slug, source="committed_spec", origin="__capture__", status="captured",
                    reason=f"{len(results)} spec file(s) found, {parsed} parsed", captured_at=captured_at,
                    captured_by=captured_by)


def save_instance(registry, slug: str, url: str, iface: Interface, *, fetched_at: str, by: str,
                  content_type: str = "", byte_count: int | None = None) -> None:
    """Register (or replace) the running instance: a new current row; the previous one is superseded."""
    slug = _slug(registry, slug)
    now = _now()
    with registry._conn() as conn:
        _lock(registry, conn, f"api_reference:{slug}:running_instance")
        _supersede(conn, slug, "running_instance", now)
        _insert_doc(conn, slug=slug, source="running_instance", origin=url, status="ok", reason="",
                    captured_at=fetched_at, iface=iface, captured_by=by, registered_by=by, registered_at=now,
                    content_type=content_type, byte_count=byte_count, attempted_at=now)


def update_instance(registry, slug: str, iface: Interface | None, *, fetched_at: str = "", reason: str = "",
                    content_type: str = "", byte_count: int | None = None, by: str = "",
                    document_id: int | str | None = None) -> bool:
    """A re-check of the registered instance, by `by`: success replaces the content; failure keeps it and records
    why. `document_id` is the row the caller loaded and fetched for: the update touches that row only, so a
    registration replaced meanwhile (a different URL, a different row) is never written with the old URL's
    document. False when no current registration is left to update (it was cleared or replaced meanwhile)."""
    slug = _slug(registry, slug)
    now = _now()
    with registry._conn() as conn:
        _lock(registry, conn, f"api_reference:{slug}:running_instance")
        sql = ("SELECT id FROM api_reference_documents WHERE resource_type = ? AND slug = ? AND "
               "source = 'running_instance' AND superseded_at IS NULL AND status IN ('ok', 'failed')")
        args: tuple = (RESOURCE_TYPE, slug)
        if document_id is not None:
            sql += " AND id = ?"
            args += (document_id,)
        row = conn.execute(sql, args).fetchone()
        if not row:
            return False
        if iface is None:
            conn.execute("UPDATE api_reference_documents SET status = 'failed', reason = ?, attempted_at = ?, "
                         "attempted_by = ? WHERE id = ?", (reason, now, by, row["id"]))
            return True
        conn.execute(
            "UPDATE api_reference_documents SET status = 'ok', reason = '', attempted_at = ?, attempted_by = ?, "
            "captured_at = ?, captured_by = ?, spec_version = ?, title = ?, api_version = ?, description = ?, "
            "operation_count = ?, total_operations = ?, content_type = ?, byte_count = ?, meta_json = ? WHERE id = ?",
            (now, by, fetched_at, by, iface.spec_version, iface.title, iface.version, iface.description[:2000],
             len(iface.operations), iface.total_operations, content_type, byte_count, _meta(iface), row["id"]))
        _write_children(conn, row["id"], slug, "running_instance", iface)
        return True


def clear_instance(registry, slug: str, by: str) -> bool:
    """Forget the instance: content deleted, the header row stays as a tombstone naming who and when."""
    slug = _slug(registry, slug)
    now = _now()
    with registry._conn() as conn:
        _lock(registry, conn, f"api_reference:{slug}:running_instance")
        row = conn.execute("SELECT id FROM api_reference_documents WHERE resource_type = ? AND slug = ? AND "
                           "source = 'running_instance' AND superseded_at IS NULL AND status IN ('ok', 'failed')",
                           (RESOURCE_TYPE, slug)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM api_reference_operations WHERE document_id = ?", (row["id"],))
        conn.execute("DELETE FROM api_reference_schemas WHERE document_id = ?", (row["id"],))
        conn.execute("UPDATE api_reference_documents SET status = 'cleared', cleared_by = ?, cleared_at = ?, "
                     "operation_count = 0, meta_json = '{}' WHERE id = ?", (by, now, row["id"]))
        return True


# ── reading ──────────────────────────────────────────────────────────────────

def documents(registry, slug: str, source: str) -> list:
    """The current header rows of a source, newest capture first."""
    if not _real(registry):
        return []
    with registry._conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM api_reference_documents WHERE resource_type = ? AND slug = ? AND source = ? "
            "AND superseded_at IS NULL ORDER BY captured_at DESC, origin", (RESOURCE_TYPE, _slug(registry, slug), source)))


def load_interface(registry, doc: dict):
    """(Interface | None, reason): the document rebuilt from its rows."""
    try:
        meta = json.loads(doc.get("meta_json") or "{}")
        with registry._conn() as conn:
            ops = _rows(conn.execute("SELECT * FROM api_reference_operations WHERE document_id = ? ORDER BY seq",
                                     (doc["id"],)))
            schemas = _rows(conn.execute("SELECT name, schema_json FROM api_reference_schemas WHERE document_id = ?",
                                         (doc["id"],)))
        operations = []
        for o in ops:
            operations.append(Operation(
                key=o["op_key"], name=o["name"], kind=o["kind"], kind_detail=o["kind_detail"], method=o["method"],
                path=o["path"], summary=o["summary"], description=o["description"], operation_id=o["operation_id"],
                tags=json.loads(o["tags_json"]), parameters=json.loads(o["parameters_json"]),
                request_body=json.loads(o["request_body_json"]) if o["request_body_json"] else None,
                responses=json.loads(o["responses_json"]), security=json.loads(o["security_json"]),
                deprecated=bool(o["deprecated"]),
                input_schema=json.loads(o["input_schema_json"]) if o["input_schema_json"] else None,
                handler=o["handler"], handler_signature=o["handler_signature"], location=o["location"]))
        return Interface(
            kind=doc.get("kind") or "http_api", source=doc["source"], origin=doc.get("origin", ""),
            as_of=doc.get("captured_at", ""), title=doc.get("title", ""), version=doc.get("api_version", ""),
            spec_version=doc.get("spec_version", ""), description=doc.get("description", ""),
            base_paths=meta.get("base_paths") or [], operations=operations,
            total_operations=doc.get("total_operations") or len(operations),
            schemas={s["name"]: json.loads(s["schema_json"]) for s in schemas},
            security_schemes=meta.get("security_schemes") or {}, groups=meta.get("groups") or {},
            notes=meta.get("notes") or []), ""
    except (ValueError, KeyError, TypeError, RecursionError):
        log.warning("stored API reference rows for %s no longer read", doc.get("origin"), exc_info=True)
        return None, f"the stored rows for {doc.get('origin', '')} can no longer be read"


def committed_state(registry, slug: str) -> dict:
    """{'captured': bool, 'at': ISO} from the capture marker."""
    for d in documents(registry, slug, "committed_spec"):
        if d["status"] == "captured":
            return {"captured": True, "at": d["captured_at"]}
    return {"captured": False, "at": ""}


def find_operations(registry, slug: str | None = None, *, source: str | None = None, text: str | None = None,
                    path_contains: str | None = None, method: str | None = None, limit: int = 200,
                    offset: int = 0) -> tuple:
    """(rows, more): operations across repositories, filtered by source, a path fragment, a method or a word
    anywhere in the searchable text; current documents only. `%`, `_` and backslash in a term match themselves.
    The analysis hook the tables exist for."""
    if not _real(registry):
        return [], False
    where = ["o.document_id = d.id", "d.superseded_at IS NULL", "d.status IN ('ok', 'failed')"]
    args: list = []
    if slug:
        where.append("o.slug = ?"); args.append(_slug(registry, slug))
    if source:
        where.append("o.source = ?"); args.append(source)
    if method:
        where.append("o.method = ?"); args.append(method.upper())
    if path_contains:
        where.append("LOWER(o.normalised_path) LIKE ? ESCAPE '\\'"); args.append(_like(path_contains))
    if text:
        where.append("LOWER(o.search_text) LIKE ? ESCAPE '\\'"); args.append(_like(text))
    limit = max(1, int(limit))
    args += [limit + 1, max(0, int(offset))]
    with registry._conn() as conn:
        rows = _rows(conn.execute(
            "SELECT o.slug, o.source, d.origin, o.method, o.path, o.operation_id, o.summary, o.tags_json, o.deprecated "
            "FROM api_reference_operations o, api_reference_documents d WHERE " + " AND ".join(where)
            + " ORDER BY o.slug, o.source, o.seq LIMIT ? OFFSET ?", tuple(args)))
    return rows[:limit], len(rows) > limit

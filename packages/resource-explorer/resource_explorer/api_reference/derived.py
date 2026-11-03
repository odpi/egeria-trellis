"""The interface read from the code: stored route registrations joined to the handler's signature and docstring.

Same operation shape as a spec, with only what the code states: method and path (from the registration), the
handler, its signature and first docstring line, and where it is. Path parameters come from the path template;
nothing else (request bodies, responses, auth) is invented, because a registration does not state it. Labelled
"derived from code, not a published contract" everywhere it is shown.
"""
from __future__ import annotations

from resource_explorer.api_reference.compare import path_params
from resource_explorer.api_reference.model import SOURCE_DERIVED, Interface, Operation
from resource_explorer.endpoints import read_endpoints


def derive_from_code(registry, slug: str, *, max_operations: int = 3000) -> Interface | None:
    """None when no markers were ever captured (nothing was measured, so there is no interface to describe,
    not an empty one) or the registry cannot be read. An Interface with no operations when markers were
    captured and none is an HTTP route outside tests."""
    es = read_endpoints(registry, slug, ("http_api",))
    if es is None or not es.measured:
        return None
    ops: list = []
    for e in es.rows:
        ops.append(Operation(
            key=e.label or e.path, name=e.label or e.path, kind="http_api", method=e.method, path=e.path,
            summary=e.doc, tags=[e.file_path] if e.file_path else [],
            parameters=[{"name": n, "in": "path", "required": True, "type": "", "description": ""}
                        for n in path_params(e.path)],
            handler=e.handler, handler_signature=e.signature, location=e.location))
    total = len(ops)
    notes = []
    if es.excluded_tests:
        notes.append(f"{es.excluded_tests} registration(s) under test paths left out")
    if es.excluded_vendored:
        notes.append(f"{es.excluded_vendored} under vendored code left out")
    if total > max_operations:
        notes.append(f"showing {max_operations} of {total} operations (cap)")
        ops = ops[:max_operations]
    as_of = str((registry.query_metrics(slug, "symbol_extraction") or {}).get("surveyed_at") or "")
    return Interface(kind="http_api", source=SOURCE_DERIVED, origin="", as_of=as_of, operations=ops,
                     total_operations=total, notes=notes,
                     groups={}, title="", description="Route registrations found in the code. Not a published contract.")

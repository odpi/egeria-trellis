"""The endpoints a repository exposes: one typed reader over `project_code_markers`.

`api_structure` counts symbols and `interface_surface` summarises four
findings; neither names a single endpoint. The endpoints themselves live in
`project_code_markers` (decorator / annotation registrations written by
repo_symbol_extraction): method + path in `detail` ("GET /authorize"), the
handler in `qualified_name`, `file_path` + `start_line`. Test files are left
out (a `unittest.mock` patch of a route is not a route), by `file_role` when
the row has one and by `is_test_path` when it predates the column.

Three outcomes, kept apart:

- `measured` False  -- extraction never captured markers for this repo. The
  list is empty because nobody looked, not because there is nothing there.
- `measured` True, `rows` empty -- looked, none outside tests/vendored.
- `rows` -- the endpoints, ordered by file then line.

Consumed by context_compile (what the model is shown) and members (the
"open the full list" drill-down) so the two agree by construction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from resource_explorer.ingestion.test_paths import is_test_path

log = logging.getLogger(__name__)

#: Roles that are not the repository's own exposed surface.
_EXCLUDED_ROLES = frozenset({"test", "vendored"})


@dataclass
class Endpoint:
    kind: str            # interface_kind: http_api, messaging, grpc ...
    method: str          # "GET" for http_api; "" when the detail has none
    path: str            # "/authorize", or the detail text for non-HTTP kinds
    handler: str         # qualified_name
    file_path: str
    start_line: int
    framework: str = ""
    signature: str = ""
    doc: str = ""        # first docstring line

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}".strip()

    @property
    def location(self) -> str:
        return f"{self.file_path}:{self.start_line}" if self.start_line else self.file_path

    def to_dict(self) -> dict:
        return {"kind": self.kind, "method": self.method, "path": self.path, "handler": self.handler,
                "file_path": self.file_path, "start_line": self.start_line,
                "framework": self.framework, "signature": self.signature, "doc": self.doc}


@dataclass
class EndpointSet:
    measured: bool
    rows: list = field(default_factory=list)       # [Endpoint], file then line
    excluded_tests: int = 0
    excluded_vendored: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)


_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "ANY"})


def _split_detail(detail: str, interface_kind: str) -> tuple[str, str]:
    """'GET /authorize' -> ('GET', '/authorize'). A detail with no method
    ('PATCH' alone, a celery task name) keeps the whole text as the path so
    nothing is invented or dropped."""
    text = (detail or "").strip()
    head, _, rest = text.partition(" ")
    if interface_kind == "http_api" and head.upper() in _METHODS and rest.strip():
        return head.upper(), rest.strip()
    return "", text


def _first_line(doc: str | None) -> str:
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


def _markers_captured(registry, slug: str) -> bool:
    """Same backfill rule as interface_surface: the symbol_extraction metric's
    `markers_captured` flag, not the metric row merely existing (a repo
    extracted before markers shipped has the row and no flag)."""
    try:
        metrics = registry.query_metrics(slug, "symbol_extraction")
    except Exception:
        return False
    detail = (metrics or {}).get("detail") if isinstance(metrics, dict) else None
    return bool(isinstance(detail, dict) and detail.get("markers_captured"))


def read_endpoints(registry, slug: str, interface_kinds: tuple[str, ...] | None = None) -> EndpointSet | None:
    """The endpoints of `slug`, or None when the registry cannot be read (a
    reader that fails is not evidence of absence; the caller says so)."""
    sql = ("SELECT m.file_path, m.start_line, m.marker_kind, m.framework, m.interface_kind, m.detail, "
           "m.qualified_name, m.file_role, s.signature, s.docstring "
           "FROM project_code_markers m LEFT JOIN project_code_symbols s "
           "ON s.project_slug = m.project_slug AND s.file_path = m.file_path "
           "AND s.qualified_name = m.qualified_name WHERE m.project_slug = ?")
    try:
        norm = registry._normalize_slug(slug)
        with registry._conn() as conn:
            raw = [dict(r) for r in conn.execute(sql, (norm,)).fetchall()]
    except Exception:
        log.warning("endpoint reader failed on %s", slug, exc_info=True)
        return None
    rows: list[Endpoint] = []
    tests = vendored = 0
    for r in raw:
        kind = r.get("interface_kind") or ""
        if interface_kinds and kind not in interface_kinds:
            continue
        path = r.get("file_path") or ""
        role = r.get("file_role")
        is_test = role == "test" or (role is None and is_test_path(path))
        if is_test:
            tests += 1
            continue
        if role in _EXCLUDED_ROLES:
            vendored += 1
            continue
        method, ep_path = _split_detail(r.get("detail") or "", kind)
        rows.append(Endpoint(
            kind=kind, method=method, path=ep_path, handler=r.get("qualified_name") or "",
            file_path=path, start_line=int(r.get("start_line") or 0), framework=r.get("framework") or "",
            signature=(r.get("signature") or "").strip(), doc=_first_line(r.get("docstring"))))
    # HTTP first (what "REST API" asks about), then the rest; file then line.
    rows.sort(key=lambda e: (e.kind != "http_api", e.kind, e.file_path, e.start_line, e.label))
    measured = bool(raw) or _markers_captured(registry, slug)
    return EndpointSet(measured=measured, rows=rows, excluded_tests=tests, excluded_vendored=vendored)


def _line(e: Endpoint, *, with_kind: bool) -> str:
    kind = f"[{e.kind}] " if with_kind else ""
    handler = f" -> {e.handler}" if e.handler else ""
    sig = f" {e.signature}" if e.signature and e.signature not in e.handler else ""
    return f"{kind}{e.label}{handler}{sig} ({e.location})"


def render_lines(es: EndpointSet, limit: int) -> list[str]:
    """The section text for the model. ORDER is file then line within a kind;
    the opening line carries the total, the shown count and what was left
    out, so a prefix is never mistaken for the whole list."""
    if not es.measured:
        return ["- endpoints: not measured (no code markers were captured for this repository; "
                "that is not the same as having none)"]
    n = es.total
    left_out = []
    if es.excluded_tests:
        left_out.append(f"{es.excluded_tests} registration(s) under test paths left out")
    if es.excluded_vendored:
        left_out.append(f"{es.excluded_vendored} under vendored code left out")
    tail = f" · {'; '.join(left_out)}" if left_out else ""
    if n == 0:
        return [f"- endpoints: 0 found outside tests (measured){tail}"]
    shown = min(n, limit)
    head = (f"- endpoints: {n} found, {shown} of {n} shown to the model{tail}" if n > limit
            else f"- endpoints: {n} found, all shown{tail}")
    multi = len({e.kind for e in es.rows}) > 1
    return [head] + [f"  - {_line(e, with_kind=multi)}" for e in es.rows[:limit]]

"""Compare two interfaces of the same kind: what each lists that the other does not.

Matching is by (method, normalised path): `{id}`, `<int:id>` and `:id` are the same segment. An operation that
matches nothing exactly but matches exactly one operation on the other side once a path PREFIX is ignored
(code registers `/jobs`, the running spec says `/api/v1/jobs`) is a *probable* match, listed with the prefix,
not counted as two absences. Everything left is listed. The comparison says the counts and the differences and
does NOT name a cause: a route can be missing from a running spec for several reasons, and which one applies is
for a person to say.
"""
from __future__ import annotations

import re

from resource_explorer.api_reference.model import SOURCE_WORDS, Interface, Operation

_BRACE = re.compile(r"\{[^}/]*\}")
_ANGLE = re.compile(r"<[^>/]*>")
_COLON = re.compile(r"(?<=/):[A-Za-z_][A-Za-z0-9_]*")


def normalize_path(path: str) -> str:
    p = (path or "").strip()
    p = _BRACE.sub("{}", p)
    p = _ANGLE.sub("{}", p)
    p = _COLON.sub("{}", p)
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return p


def path_params(path: str) -> list:
    """Parameter names written into a path template, in order."""
    out = []
    for m in re.finditer(r"\{([^}/]+)\}|<(?:[^>:/]+:)?([^>/]+)>|(?<=/):([A-Za-z_][A-Za-z0-9_]*)", path or ""):
        name = next(g for g in m.groups() if g)
        if name not in out:
            out.append(name)
    return out


def _ident(op: Operation) -> tuple:
    return (op.method.upper(), normalize_path(op.path))


def _brief(op: Operation) -> dict:
    return {"key": op.key, "name": op.name, "summary": op.summary, "group": op.group, "location": op.location}


def _suffixes(path: str):
    """(prefix, suffix) for every proper suffix of a normalised path that keeps at least one LITERAL segment
    ('/{}' alone is not evidence of anything). '/api/v1/jobs/{}' -> ('/api', '/v1/jobs/{}'), ('/api/v1', '/jobs/{}')..."""
    segs = [x for x in path.split("/") if x]
    for k in range(1, len(segs)):
        rest = segs[k:]
        if any(x != "{}" for x in rest):
            yield "/" + "/".join(segs[:k]), "/" + "/".join(rest)


def compare_interfaces(a: Interface, b: Interface) -> dict:
    a_ids = [(_ident(op), op) for op in a.operations]       # identities computed once
    b_ids = [(_ident(op), op) for op in b.operations]
    a_by: dict = {}
    b_by: dict = {}
    for ident, op in a_ids:
        a_by.setdefault(ident, []).append(op)
    for ident, op in b_ids:
        b_by.setdefault(ident, []).append(op)
    both = sum(min(len(a_by[k]), len(b_by[k])) for k in a_by.keys() & b_by.keys())
    only_a = [op for k, v in a_by.items() for op in (v[len(b_by[k]):] if k in b_by else v)]
    only_b = [op for k, v in b_by.items() for op in (v[len(a_by[k]):] if k in a_by else v)]

    # Probable matches: same method, one path is the other plus a prefix. Indexed by (method, path) so each
    # operation looks up its own suffixes -- no pairwise scan.
    a_left: dict = {}
    b_left: dict = {}
    for op in only_a:
        a_left.setdefault(_ident(op), []).append(op)
    for op in only_b:
        b_left.setdefault(_ident(op), []).append(op)
    cands_a: dict = {}      # id(a op) -> {id(b op): (b op, prefix)}   at most 2 kept: 2 already means "ambiguous"
    cands_b: dict = {}

    def note(ao, bo, prefix):
        ca = cands_a.setdefault(id(ao), {})
        cb = cands_b.setdefault(id(bo), {})
        if len(ca) < 2:
            ca[id(bo)] = (bo, prefix)
        if len(cb) < 2:
            cb[id(ao)] = (ao, prefix)

    # A path shape that more than one operation has, on either side, cannot be paired uniquely: skip it outright
    # (3000 operations that all normalise to /v1/p/{} against 3000 that all normalise to /p/{} would otherwise
    # be 9M candidate pairs).
    for (m, path), ops in a_left.items():          # a is the longer: its suffix is a b path
        if len(ops) != 1:
            continue
        for prefix, suffix in _suffixes(path):
            hit = b_left.get((m, suffix), [])
            if len(hit) == 1:
                note(ops[0], hit[0], prefix)
    for (m, path), ops in b_left.items():          # b is the longer
        if len(ops) != 1:
            continue
        for prefix, suffix in _suffixes(path):
            hit = a_left.get((m, suffix), [])
            if len(hit) == 1:
                note(hit[0], ops[0], prefix)
    probable = []
    a_taken: set = set()
    b_taken: set = set()
    for ao in only_a:
        hits = cands_a.get(id(ao), {})
        if len(hits) != 1:
            continue
        (bo, prefix), = hits.values()
        if len(cands_b.get(id(bo), {})) != 1:
            continue
        probable.append({"a": ao.key, "b": bo.key, "prefix": prefix})
        a_taken.add(id(ao))
        b_taken.add(id(bo))
    only_a = [o for o in only_a if id(o) not in a_taken]
    only_b = [o for o in only_b if id(o) not in b_taken]

    def by_file(ops):
        out: dict = {}
        for o in ops:
            if o.location:
                f = o.location.rsplit(":", 1)[0]
                out[f] = out.get(f, 0) + 1
        return out

    na, nb = len(a.operations), len(b.operations)
    parts = [f"{a.source_phrase} lists {na} operation(s); {b.source_phrase} lists {nb} operation(s).",
             f"{both} match exactly" + (f", {len(probable)} more match once a path prefix is ignored" if probable else "")
             + f"; {len(only_a)} only in the first, {len(only_b)} only in the second.",
             "Why they differ is not decided here; the differences are listed below."]
    return {
        "a": {"source": a.source, "word": SOURCE_WORDS.get(a.source, a.source), "count": na, "origin": a.origin},
        "b": {"source": b.source, "word": SOURCE_WORDS.get(b.source, b.source), "count": nb, "origin": b.origin},
        "both": both, "probable": probable,
        "only_a": [_brief(o) for o in only_a], "only_b": [_brief(o) for o in only_b],
        "only_a_by_file": by_file(only_a), "only_b_by_file": by_file(only_b),
        "summary": " ".join(parts),
    }

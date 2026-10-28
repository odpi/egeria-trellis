"""A spec committed in the repository: found and parsed during the survey, from the checkout already held.

No new download: `repo_symbol_extraction` already holds the zipball root, so it hands that root to
`capture_committed_specs`, which finds the repository's own OpenAPI/Swagger files (same rule as
`interface_surface`'s `published_spec`: a basename `openapi|swagger.(yaml|yml|json)` or a file in an
`openapi/` or `swagger/` directory; never vendored, generated, test or example trees), parses each, and stores
the result in the api_reference_* tables (`store.py`; one document per file). Each capture supersedes the
previous one, so a spec that was deleted stops answering, and each capture writes a marker document
(`__capture__`) so "never captured" and "captured, found none" can be told apart.

Hostile input: a symlink is never followed out of the checkout; a file that cannot be read as a spec for ANY
reason is stored as an `unreadable` document (it replaces the previous capture, it does not leave it
answering); a file under `openapi/` that is a fragment of a split spec (no root `openapi`/`swagger` key) is
stored as a `fragment`, which is not a fault.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from resource_explorer.api_reference import store
from resource_explorer.api_reference.model import SOURCE_COMMITTED
from resource_explorer.api_reference.openapi import (
    bounded_dumps, load_document, openapi_rejection, parse_openapi)
from resource_explorer.ingestion.vendored import is_vendored_abs

log = logging.getLogger(__name__)

MAX_SPEC_BYTES = 5_000_000
MAX_SPEC_FILES = 12
MAX_FINDING_BYTES = 4_000_000
_SKIP_DIRS = {".git", "node_modules", "vendor", "third_party", "thirdparty", ".venv", "venv", "site-packages",
              "dist", "build", "target", "out", "generated", "__pycache__"}
_ROOT_NAME = re.compile(r"(^|/)(openapi|swagger)\.(ya?ml|json)$", re.I)
FRAGMENT_REASON = "fragment, not a root document"


def find_spec_files(root) -> list:
    """Relative POSIX paths of the repository's own OpenAPI/Swagger files under `root`. Root documents
    (basename openapi|swagger.*) rank before fragments, then shallower before deeper, then by name; the cap
    applies after that ranking, so a split spec's fragments cannot push the root out."""
    from resource_explorer.surveyors.sub_surveyors.interface_surface import _SPEC_PATTERNS, _VENDORED

    root = Path(root)
    real_root = root.resolve()
    patterns = [_SPEC_PATTERNS["openapi"], _SPEC_PATTERNS["openapi_dir"]]
    found: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if _VENDORED.search(rel) or is_vendored_abs(full, root):
                continue
            if not any(p.search(rel) for p in patterns):
                continue
            if full.is_symlink() or not full.resolve().is_relative_to(real_root):
                continue                      # never read through a link that leaves (or hides in) the checkout
            found.append(rel)
    found.sort(key=lambda r: (0 if _ROOT_NAME.search(r) else 1, r.count("/"), r))
    return found[:MAX_SPEC_FILES]


def _read_one(root: Path, rel: str, surveyed_at: str) -> dict:
    full = root / rel
    try:
        if full.stat().st_size > MAX_SPEC_BYTES:
            raise ValueError(f"not an OpenAPI document: the file is larger than {MAX_SPEC_BYTES} bytes")
        doc = load_document(full.read_text(encoding="utf-8", errors="replace"), hint=rel)
        reason = openapi_rejection(doc)
        if reason:
            if "no 'openapi' or 'swagger' key" in reason and not _ROOT_NAME.search(rel):
                return {"origin": rel, "status": "fragment", "reason": FRAGMENT_REASON, "interface": None}
            raise ValueError(reason)
        iface = parse_openapi(doc, source=SOURCE_COMMITTED, origin=rel, as_of=surveyed_at)
        bounded_dumps(iface.to_dict(), MAX_FINDING_BYTES)           # too big to keep is refused here, not stored
        return {"origin": rel, "status": "ok", "reason": "", "interface": iface}
    except Exception as exc:  # noqa: BLE001  -- ANY failure on one file becomes that file's "unreadable" document
        msg = str(exc) if isinstance(exc, ValueError) else f"the file could not be read ({type(exc).__name__})"
        if "too large" in msg and not msg.startswith("not an OpenAPI"):
            msg = "not an OpenAPI document: too large to keep"
        if not msg.startswith("not an OpenAPI"):
            msg = f"not an OpenAPI document: {msg}"
        return {"origin": rel, "status": "unreadable", "reason": msg, "interface": None}


def capture_committed_specs(registry, slug: str, root, *, surveyed_at: str) -> dict:
    root = Path(root)
    paths = find_spec_files(root)
    results = [_read_one(root, rel, surveyed_at) for rel in paths]
    store.replace_committed(registry, slug, results, captured_at=surveyed_at)
    return {"found": len(paths), "parsed": sum(1 for r in results if r["status"] == "ok"), "paths": paths}


def capture_state(registry, slug: str) -> dict:
    """{'captured': bool, 'at': ISO}: whether spec capture has ever run for this repo (the marker document), so
    'none found' and 'never looked' read differently."""
    return store.committed_state(registry, slug)


def load_committed(registry, slug: str):
    """([Interface], [problem]) from the latest capture. Interfaces: most operations first. Problems are
    {path, reason, kind} with kind 'unreadable' or 'fragment' (a fragment is information, not a fault)."""
    ifaces: list = []
    problems: list = []
    for d in store.documents(registry, slug, SOURCE_COMMITTED):
        if d["status"] == "captured":
            continue
        if d["status"] == "ok":
            iface, why = store.load_interface(registry, d)
            if iface is not None:
                ifaces.append(iface)
                continue
            problems.append({"path": d["origin"], "kind": "unreadable", "reason": why})
        else:
            problems.append({"path": d["origin"], "kind": d["status"], "reason": d["reason"]})
    ifaces.sort(key=lambda i: (-len(i.operations), i.origin))
    return ifaces, problems
